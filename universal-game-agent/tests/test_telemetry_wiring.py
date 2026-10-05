"""Telemetry wiring tests: PPOConfig.telemetry_path -> CSV per PPO update.

Tiny real trainer runs on the toy game (torch-gated like test_ppo.py).
Every path is an absolute temp path, so a passing suite cannot leave
artifacts in the repository working directory.
"""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import csv
import tempfile
import unittest
from pathlib import Path

try:
    import torch  # noqa: F401

    from agent.model import ActorCritic
    from environment.preprocessing import PreprocessingWrapper
    from environment.toy_pong import ToyPongEnv
    from training.ppo import PPOConfig, PPOTrainer
    from training.telemetry import FIELDNAMES

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def _trainer(tmp, **overrides):
    args = {
        "rollout_length": 8,
        "minibatch_size": 8,
        "update_epochs": 1,
        "total_timesteps": 16,
        "learning_rate": 1e-3,
        "checkpoint_dir": str(Path(tmp) / "ckpt"),
        "checkpoint_every_updates": 100,
    }
    args.update(overrides)
    config = PPOConfig(**args)
    env = PreprocessingWrapper(ToyPongEnv(max_steps=16))
    model = ActorCritic(num_actions=int(env.action_space.n),
                        feature_dim=32, hidden_size=16)
    return PPOTrainer(env, model, config)


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestTelemetryWiring(unittest.TestCase):
    def test_disabled_by_default_produces_no_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(PPOConfig().telemetry_path)
            history = _trainer(tmp).train()
            self.assertGreater(len(history["timesteps"]), 0, "setup: must run updates")
            self.assertEqual(list(Path(tmp).rglob("*.csv")), [])

    def test_enabled_writes_one_row_per_update_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "tel" / "updates.csv")
            history = _trainer(tmp, telemetry_path=path).train()
            self.assertTrue(Path(path).exists())
            with open(path, newline="", encoding="utf-8") as fh:
                lines = list(csv.reader(fh))
            self.assertEqual(lines[0], list(FIELDNAMES))
            rows = _rows(path)
            self.assertEqual(len(rows), len(history["timesteps"]))
            self.assertEqual([int(r["timesteps"]) for r in rows],
                             list(history["timesteps"]))

    def test_zero_update_training_produces_header_only_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "tel" / "updates.csv")
            trainer = _trainer(tmp, telemetry_path=path)
            trainer.num_timesteps = trainer.config.total_timesteps
            history = trainer.train()
            self.assertEqual(history["timesteps"], [])
            with open(path, newline="", encoding="utf-8") as fh:
                lines = list(csv.reader(fh))
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0], list(FIELDNAMES))

    def test_configured_output_directory_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b" / "updates.csv"
            _trainer(tmp, telemetry_path=str(path)).train()
            self.assertTrue(path.exists())
            self.assertGreater(len(_rows(path)), 0)

    def test_training_does_not_pollute_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = set(Path.cwd().glob("*.csv"))
            _trainer(tmp, telemetry_path=str(Path(tmp) / "updates.csv")).train()
            self.assertEqual(set(Path.cwd().glob("*.csv")), before)

    def test_telemetry_path_flows_through_from_dict(self):
        cfg = PPOConfig.from_dict({"telemetry_path": "some/dir/updates.csv"})
        self.assertEqual(cfg.telemetry_path, "some/dir/updates.csv")
        with self.assertRaises(ValueError):
            PPOConfig(telemetry_path=123)


if __name__ == "__main__":
    unittest.main()
