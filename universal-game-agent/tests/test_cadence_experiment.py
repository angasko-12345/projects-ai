"""Synthetic cadence experiment contract: press timing, probe discrimination,
settle behavior, and a tiny end-to-end matrix run (virtual time, no OS)."""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    import yaml

    import training.cadence_experiment as ce

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


def _tiny_config(probe_episodes=1, eval_episodes=1):
    return {
        "game": {"seed": 0, "fps": 60},
        "probe": {"episodes": probe_episodes},
        "cells": [{"name": "smoke_fast", "decision_period_ms": 16.667,
                   "hold_ms": 16.667}],
        "env": {"max_episode_steps": 200, "max_episode_seconds": 120.0},
        "model": {"in_channels": 4, "frame_size": 84, "feature_dim": 32,
                  "hidden_size": 16, "num_layers": 1},
        "ppo": {"total_timesteps": 128, "rollout_length": 64,
                "minibatch_size": 32, "update_epochs": 1,
                "learning_rate": 0.00025, "gamma": 0.99, "gae_lambda": 0.95,
                "clip_range": 0.2, "entropy_coef": 0.01, "value_coef": 0.5,
                "max_grad_norm": 0.5, "seed": 0, "checkpoint_every_updates": 0},
        "curiosity": {"enabled": False},
        "eval": {"episodes": eval_episodes},
    }


@unittest.skipUnless(_HAS_DEPS, "cadence experiment deps missing")
class TestPressTiming(unittest.TestCase):
    """Decision -> key hold -> paddle displacement, measured in virtual time."""

    @staticmethod
    def _press_displacements(period_ms, hold_ms, presses, action=1):
        session = ce.SyntheticPongSession(seed=0)
        iface = ce.SyntheticGameInterface(session, period_ms, hold_ms)
        xs = [session.logic.paddle_x]
        for _ in range(presses):
            iface.execute(action)
            xs.append(session.logic.paddle_x)
        return xs

    def test_current_cadence_press_moves_15_to_20_px(self):
        # Measured real default: 147.6 ms period, 60 ms hold at 60 fps is
        # 3-4 held ticks x 5 px, phase-dependent. Seven presses from the
        # centre (x=136) never reach the wall.
        xs = self._press_displacements(147.6, 60, 7, action=1)
        disps = [a - b for a, b in zip(xs, xs[1:])]  # moving left -> positive
        self.assertEqual(len(disps), 7)
        for d in disps:
            self.assertIn(d, (15, 20), f"unexpected left-press displacement: {disps}")

    def test_fast_cadence_press_moves_5_px_after_the_first_decision(self):
        # 16.667 ms window: one held tick per decision in steady state.
        xs = self._press_displacements(16.667, 16.667, 5, action=1)
        disps = [a - b for a, b in zip(xs, xs[1:])]
        for d in disps[1:]:
            self.assertEqual(d, 5, f"fast-cell displacements: {disps}")

    def test_zero_hold_never_moves_the_paddle(self):
        xs = self._press_displacements(147.6, 0.0, 5, action=1)
        self.assertEqual(xs, [xs[0]] * len(xs))

    def test_each_decision_can_reverse_direction(self):
        session = ce.SyntheticPongSession(seed=0)
        iface = ce.SyntheticGameInterface(session, 147.6, 60)
        xs = [session.logic.paddle_x]
        for action in (1, 2, 1, 2, 1, 2):  # LEFT, RIGHT, LEFT, ...
            iface.execute(action)
            xs.append(session.logic.paddle_x)
        for i, action in enumerate((1, 2, 1, 2, 1, 2)):
            delta = xs[i + 1] - xs[i]
            if action == 1:
                self.assertLess(delta, 0, f"LEFT must move left at step {i}: {xs}")
            else:
                self.assertGreater(delta, 0, f"RIGHT must move right at step {i}: {xs}")

    def test_invalid_hold_and_period_rejected(self):
        with self.assertRaises(ValueError):
            ce.SyntheticGameInterface(ce.SyntheticPongSession(), 0.0, 0.0)
        with self.assertRaises(ValueError):
            ce.SyntheticGameInterface(ce.SyntheticPongSession(), 100.0, -1.0)
        with self.assertRaises(ValueError):
            ce.SyntheticGameInterface(ce.SyntheticPongSession(), 100.0, 150.0)


@unittest.skipUnless(_HAS_DEPS, "cadence experiment deps missing")
class TestProbeDiscrimination(unittest.TestCase):
    def test_reward_separates_perfect_play_from_random_at_current_cadence(self):
        session = ce.SyntheticPongSession(seed=0)
        probe = ce.run_probe(ce.make_synthetic_env_factory(session, 147.6, 60),
                             episodes=3, seed=0)
        self.assertEqual(set(probe), {
            "oracle_lookahead", "oracle_reactive", "random",
            "constant_left", "constant_right", "no_op"})
        self.assertGreater(probe["oracle_lookahead"]["mean_reward"],
                           probe["random"]["mean_reward"],
                           "the reward must distinguish perfect from random")
        for name, summary in probe.items():
            for value in summary["episode_rewards"]:
                self.assertIn(value, (-1.0, 0.0, 1.0),
                              f"{name}: per-episode reward outside protocol")

    def test_oracle_lookahead_never_misses(self):
        session = ce.SyntheticPongSession(seed=0)
        env = ce.make_synthetic_env_factory(session, 147.6, 60)()
        summary = ce.run_policy(env, ce._oracle_lookahead, episodes=2)
        env.close()
        for length in summary["episode_lengths"]:
            self.assertEqual(length, 200, "perfect play must reach the step cap")


@unittest.skipUnless(_HAS_DEPS, "cadence experiment deps missing")
class TestResetSettleInSynthetic(unittest.TestCase):
    def test_reset_waits_out_the_miss_banner_instead_of_starting_a_phantom(self):
        # Regression guard for the phantom-episode class: a reset that lands
        # on the lingering MISS banner starts a 1-step terminal episode.
        session = ce.SyntheticPongSession(seed=0)
        env = ce.make_synthetic_env_factory(session, 147.6, 60)()
        rng = np.random.default_rng(0)
        env.reset()
        for _ in range(1000):
            _, _, terminated, truncated, _ = env.step(int(rng.integers(0, 3)))
            if terminated or truncated:
                break
        else:
            self.fail("random play never ended an episode")
        self.assertGreater(session.banner_until, session.t, "episode must end on a banner")
        t_before = session.t
        env.reset()
        env.close()
        advanced = session.t - t_before
        self.assertGreaterEqual(advanced, 0.9, "settle did not wait out the 1.0 s banner")
        self.assertLessEqual(session.banner_until, session.t,
                             "reset started while the MISS banner was still visible")


@unittest.skipUnless(_HAS_DEPS, "cadence experiment deps missing")
class TestMatrixSmoke(unittest.TestCase):
    def test_matrix_writes_results_and_checkpoints_outside_the_cwd(self):
        try:
            import torch  # noqa: F401 -- matrix training needs torch
        except ImportError:
            self.skipTest("torch not installed")

        tmp = tempfile.TemporaryDirectory(prefix="uga-cadence-")
        self.addCleanup(tmp.cleanup)
        cfg = _tiny_config()
        cfg["ppo"]["checkpoint_dir"] = str(Path(tmp.name) / "ckpt")
        config_path = Path(tmp.name) / "exp_smoke.yaml"
        config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        cwd_before = set(os.listdir())
        report = ce.run_matrix(config_path)
        self.assertEqual(set(os.listdir()), cwd_before,
                         "the matrix must not write into the caller's CWD")

        results_path = Path(tmp.name) / "exp_smoke_results.json"
        self.assertTrue(results_path.is_file())
        on_disk = json.loads(results_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["kind"], "synthetic-cadence-matrix")
        self.assertEqual(len(report["cells"]), 1)
        cell = report["cells"][0]
        self.assertEqual(cell["name"], "smoke_fast")
        self.assertGreater(cell["training_updates"], 0)
        self.assertIn("probe", cell)
        # Action-share diagnostic travels with the history tail.
        shares = cell["history_tail"]["upd_action_share"]
        self.assertEqual(len(shares), cell["training_updates"])
        for share in shares:
            self.assertTrue(share)
            self.assertAlmostEqual(sum(share.values()), 1.0, places=6)
        self.assertTrue((Path(tmp.name) / "ckpt" / "smoke_fast" / "ppo_final.pt").is_file())


if __name__ == "__main__":
    unittest.main()
