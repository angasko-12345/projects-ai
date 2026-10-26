"""Generalization runner: tiny end-to-end run, resume/skip, aggregation, GIF.

Budgets are shrunk to a few hundred steps via ``--scale``; no window.
"""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import json
import tempfile
import unittest
from pathlib import Path

try:
    import torch  # noqa: F401

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

try:
    import PIL  # noqa: F401 - optional: only GIF footage needs Pillow

    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


def _tiny_cfg():
    from training.generalization import load_config

    cfg = load_config()
    cfg["eval"]["episodes"] = 1
    cfg["env"]["max_steps"] = 60
    cfg["ppo"]["rollout_length"] = 16
    cfg["ppo"]["minibatch_size"] = 8
    cfg["ppo"]["update_epochs"] = 1
    cfg["budgets"] = {"multi": 96, "specialist": 48, "scratch": 96, "finetune": 96,
                      "curve_every": {"scratch": 48, "finetune": 48}}
    return cfg


class TestConfig(unittest.TestCase):
    def test_shipped_config_is_consistent(self):
        from training.generalization import load_config

        cfg = load_config()
        self.assertEqual(cfg["model"]["frame_size"], cfg["env"]["obs_size"])
        unit = cfg["ppo"]["num_envs"] * cfg["ppo"]["rollout_length"]
        for arm in ("multi", "specialist", "scratch", "finetune"):
            self.assertEqual(cfg["budgets"][arm] % unit, 0, arm)
        self.assertEqual(cfg["budgets"]["multi"], 3 * cfg["budgets"]["specialist"])
        self.assertEqual(cfg["budgets"]["scratch"], cfg["budgets"]["finetune"])


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestTinyPipeline(unittest.TestCase):
    def test_end_to_end_tiny(self):
        from training.generalization import aggregate, record_gif, train_run

        cfg = _tiny_cfg()
        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp)
            multi = train_run(cfg, "multi", 0, art)
            self.assertEqual(multi["slot_games"], ["pong", "catch", "breakout"])
            self.assertEqual(multi["total_timesteps"], 96)
            self.assertEqual(set(multi["final_eval"]), {"pong", "catch", "breakout", "volley", "dodge"})
            spec = train_run(cfg, "specialist", 0, art, game="catch")
            self.assertEqual(spec["train_games"], ["catch"])
            ft = train_run(cfg, "finetune", 0, art, game="volley")
            self.assertTrue(ft["init_from"].endswith("multi_multi_s0/ckpt/ppo_final.pt"))
            self.assertEqual([c["steps"] for c in ft["curve"]], [0, 48, 96])
            sc = train_run(cfg, "scratch", 0, art, game="volley")
            self.assertIsNone(sc["init_from"])
            # Second call is a no-op skip that returns the stored result.
            again = train_run(cfg, "scratch", 0, art, game="volley")
            self.assertEqual(again["timestamp_utc"], sc["timestamp_utc"])
            out = aggregate(art, art / "summary")
            self.assertIn("finetune", out["heldout_curves"])
            self.assertEqual(out["heldout_curves"]["scratch"]["steps"], [0, 48, 96])
            self.assertTrue((art / "summary" / "summary.csv").exists())
            rows = json.loads((art / "summary" / "summary.json").read_text())["per_game"]
            self.assertIn("multi", rows["volley"])
            if _HAS_PIL:  # GIF footage is optional; Pillow is not a runtime dependency
                gif = record_gif(art / "g.gif", "volley", 0, cfg, max_frames=10)
                self.assertLess(gif["bytes"], 1_000_000)
            self.assertNotIn(".pt", "".join(p.suffix for p in (art / "summary").iterdir()))

    def test_finetune_requires_multi_checkpoint(self):
        from training.generalization import train_run

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FileNotFoundError):
            train_run(_tiny_cfg(), "finetune", 0, Path(tmp), game="volley")


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestChunkedRun(unittest.TestCase):
    def test_chunk_every_checkpoints_without_changing_training(self):
        """``budgets.chunk_every`` adds resume points; weights and eval are unchanged."""
        import torch

        from training.generalization import train_run

        def run(chunk):
            cfg = _tiny_cfg()
            if chunk:
                cfg["budgets"]["chunk_every"] = 48
            with tempfile.TemporaryDirectory() as tmp:
                res = train_run(cfg, "multi", 0, Path(tmp))
                ck = Path(tmp) / "runs" / "multi_multi_s0" / "ckpt" / "ppo_final.pt"
                state = torch.load(ck, weights_only=True)["model"]
            return res, state

        plain, w_plain = run(False)
        chunked, w_chunked = run(True)
        self.assertEqual(chunked["total_timesteps"], plain["total_timesteps"])
        self.assertEqual([c["steps"] for c in chunked["curve"]], [96])  # no eval at chunk stops
        for k in w_plain:
            self.assertTrue(torch.equal(w_plain[k], w_chunked[k]), k)
        self.assertEqual(chunked["final_eval"]["pong"]["episode_rewards"],
                         plain["final_eval"]["pong"]["episode_rewards"])


if __name__ == "__main__":
    unittest.main()
