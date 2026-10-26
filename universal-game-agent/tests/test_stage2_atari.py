"""Stage 2 on ALE: EpisodicLife / FireReset flags, game-score telemetry, uint8 obs,
benchmark config, and a tiny multi-game runner smoke (train -> resume -> eval -> HNS).

Needs the optional ``ale-py`` extra and torch; skips cleanly otherwise.
No window, no SendInput.
"""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import ale_py  # noqa: F401

    _HAS_ALE = True
except ImportError:
    _HAS_ALE = False

try:
    import torch  # noqa: F401

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(_HAS_ALE, "ale-py not installed (pip install -r requirements-atari.txt)")
class TestEpisodicLifeFireReset(unittest.TestCase):
    def test_life_loss_ends_training_episode_but_not_the_game(self):
        from environment.atari_env import AtariGame

        env = AtariGame("breakout", sticky=0.0, noop_max=0, episodic_life=True, fire_reset=True)
        obs, info = env.reset(seed=0)
        self.assertEqual(info, {})
        start_lives = env._lives_now()
        terminated = False
        for _ in range(5000):
            obs, _, terminated, truncated, info = env.step(0)  # NOOP: ball is lost
            self.assertEqual(info, {})
            if terminated or truncated:
                break
        self.assertTrue(terminated)
        self.assertEqual(env._lives_now(), start_lives - 1)
        self.assertEqual(env.drain_game_scores(), [])  # the game is not over
        env.reset()  # soft reset: same game continues
        self.assertEqual(env._lives_now(), start_lives - 1)
        env.close()

    def test_full_game_score_is_drained_once(self):
        from environment.atari_env import AtariGame

        env = AtariGame("pong", sticky=0.0, noop_max=0, episodic_life=True, fire_reset=True)
        env.reset(seed=1)
        rng = np.random.default_rng(0)
        total = 0.0
        while True:
            _, r, te, tr, _ = env.step(int(rng.integers(0, env.action_space.n)))
            total += r
            if te or tr:
                break
        scores = env.drain_game_scores()
        self.assertEqual(scores, [total])
        self.assertEqual(env.drain_game_scores(), [])
        env.close()

    def test_eval_kwargs_drop_training_only_flags(self):
        from environment.atari_env import atari_kwargs

        cfg = {"type": "atari", "game": "pong", "episodic_life": True, "fire_reset": True,
               "reward_transform": "sign", "obs_dtype": "uint8"}
        train, ev = atari_kwargs(cfg, train=True), atari_kwargs(cfg, train=False)
        self.assertTrue(train["episodic_life"] and train["fire_reset"])
        self.assertNotIn("episodic_life", ev)
        self.assertNotIn("fire_reset", ev)
        self.assertEqual(ev["reward_transform"], "none")
        self.assertEqual(ev["obs_dtype"], "uint8")
        legacy = atari_kwargs({"type": "atari", "game": "pong"})
        for key in ("obs_dtype", "episodic_life", "fire_reset"):
            self.assertNotIn(key, legacy)

    def test_fire_reset_skipped_without_fire(self):
        from environment.atari_env import AtariGame

        env = AtariGame("freeway", sticky=0.0, noop_max=0, fire_reset=True)
        self.assertFalse(env.fire_reset)
        env.close()

    def test_uint8_pipeline_matches_float_pipeline(self):
        from environment.atari_env import make_atari_factory

        a = make_atari_factory("pong", sticky=0.0, obs_dtype="uint8")()
        b = make_atari_factory("pong", sticky=0.0)()
        oa, _ = a.reset(seed=3)
        ob, _ = b.reset(seed=3)
        for _ in range(20):
            self.assertEqual(oa.dtype, np.uint8)
            self.assertLessEqual(float(np.abs(oa / 255.0 - ob).max()), 0.5 / 255 + 1e-4)
            oa, *_ = a.step(1)
            ob, *_ = b.step(1)
        a.close()
        b.close()


@unittest.skipUnless(_HAS_ALE and _HAS_TORCH, "needs ale-py and torch")
class TestStage2Runner(unittest.TestCase):
    def test_benchmark_yaml_is_valid(self):
        from training import stage2

        cfg = stage2.load_config(ROOT / "experiments" / "stage2" / "benchmark.yaml")
        self.assertEqual(len(cfg["suite"]["train"]), 5)
        self.assertEqual(len(cfg["suite"]["heldout"]), 3)
        multi = stage2.slot_games(cfg, "multi", None)
        self.assertEqual(len(multi), cfg["ppo"]["num_envs"])
        self.assertEqual(set(multi), set(cfg["suite"]["train"]))
        from training import hns

        for g in stage2.games(cfg):
            self.assertTrue(hns.has_reference(g), g)
        with self.assertRaises(ValueError):
            stage2.slot_games(cfg, "scratch", cfg["suite"]["train"][0])

    def test_tiny_multi_game_end_to_end(self):
        from training import stage2

        cfg = stage2.load_config(ROOT / "experiments" / "stage2" / "benchmark.yaml")
        cfg = copy.deepcopy(cfg)
        cfg["suite"] = {"train": ["pong", "breakout"], "heldout": ["freeway"],
                        "mix": {"pong": 1, "breakout": 1}}
        cfg["ppo"].update({"num_envs": 2, "rollout_length": 8, "update_epochs": 1, "num_minibatches": 2,
                           "device": "cpu", "torch_threads": 0})
        cfg["budgets"] = {"multi": 48, "specialist": 16, "scratch": 16, "finetune": 16, "chunk_every": 16,
                          "curve_every": {"finetune": 16}}
        cfg["eval"].update({"episodes": 1, "max_episode_frames": 80})
        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp)
            # pause after the first chunk, then resume to the end
            with self.assertRaises(stage2.Paused):
                stage2.train_run(cfg, "multi", 0, art, max_hours=-1.0)
            progress = art / "runs" / "multi_multi_s0" / "progress.json"
            self.assertFalse(progress.exists())
            res = stage2.train_run(cfg, "multi", 0, art)
            self.assertEqual(res["total_timesteps"], 48)
            self.assertEqual(res["slot_games"], ["pong", "breakout"])
            for g in ("pong", "breakout", "freeway"):
                self.assertIn("hns", res["final_eval"][g])
            ft = stage2.train_run(cfg, "finetune", 0, art, game="freeway")
            self.assertTrue(ft["init_from"].endswith("ppo_final.pt"))
            stage2.train_run(cfg, "scratch", 0, art, game="freeway")
            (art / "random.json").write_text(json.dumps(
                {"0": stage2.random_baseline(cfg, 0, ["pong", "breakout", "freeway"])}), encoding="utf-8")
            out = stage2.aggregate(cfg, art, art / "summary")
            arms = {(t["arm"], t["game"]) for t in out["table"]}
            for cell in (("multi", "pong"), ("zeroshot", "freeway"), ("finetune", "freeway"),
                         ("scratch", "freeway"), ("random", "breakout")):
                self.assertIn(cell, arms)
            self.assertIn("freeway", out["transfer"])
            self.assertTrue((art / "summary" / "summary.csv").exists())

    def test_resume_from_chunk_checkpoint(self):
        from training import stage2

        cfg = copy.deepcopy(stage2.load_config(ROOT / "experiments" / "stage2" / "benchmark.yaml"))
        cfg["suite"] = {"train": ["pong"], "heldout": ["freeway"], "mix": {"pong": 2}}
        cfg["ppo"].update({"num_envs": 2, "rollout_length": 8, "update_epochs": 1, "num_minibatches": 1,
                           "device": "cpu"})
        cfg["budgets"] = {"specialist": 48, "chunk_every": 16}
        cfg["eval"].update({"episodes": 1, "max_episode_frames": 40})
        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp)
            with self.assertRaises(stage2.Paused):
                stage2.train_run(cfg, "specialist", 0, art, game="pong", max_chunks=2)
            prog = json.loads((art / "runs" / "specialist_pong_s0" / "progress.json").read_text())
            self.assertGreaterEqual(len(prog["chunks"]), 1)
            res = stage2.train_run(cfg, "specialist", 0, art, game="pong")
            self.assertEqual(res["total_timesteps"], 48)
            self.assertEqual(res["resumes"], 1)


if __name__ == "__main__":
    unittest.main()
