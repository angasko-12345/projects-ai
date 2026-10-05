"""Tests for configurable num_envs through the UGA config system.

Covers: config field defaults/validation, make_env_for_training factory
selection, timestep accounting (total samples, not per-slot), telemetry
CSV, checkpoint round-trip, single-env regression, multi-env training,
and CLI integration.  No display, no window.
"""

try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# --- Config-file test (no torch dependency) ------------------------------


class TestNumEnvsConfigDefaults(unittest.TestCase):
    """The config field exists, defaults to 1, and is documented in YAML."""

    def test_default_yaml_has_num_envs(self):
        from configs import load_config

        cfg = load_config(str(ROOT / "configs" / "default.yaml"))
        self.assertIn("num_envs", cfg["ppo"])
        self.assertEqual(cfg["ppo"]["num_envs"], 1)


# --- Torch-gated tests --------------------------------------------------

try:
    import csv as _csv
    import io as _io
    from contextlib import redirect_stdout

    import numpy as np
    import torch

    from agent.model import ActorCritic
    from environment.vec import SyncVectorEnv
    from training import ppo as ppo_mod
    from training.experiment import make_env_for_training, make_env_from_config
    from training.ppo import PPOConfig, PPOTrainer

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


OBS_SHAPE = (4, 84, 84)


class _ScriptedEnv:
    """Deterministic scripted env: (reward, terminated, truncated) per step.

    Mirrors the fixtures in test_ppo_vec_multi.py and test_ppo.py so
    timestep accounting can be checked without a real game.
    """

    def __init__(self, script, slot=0):
        self.script = list(script)
        self.slot = slot
        self.t = 0
        self.resets = 0
        self.action_space = type("Space", (), {"n": 2})()

    def reset(self, *, seed=None, options=None):
        self.resets += 1
        self.t = 0
        return np.full(OBS_SHAPE, -10.0 - self.slot, dtype=np.float32), {}

    def step(self, action):
        if self.t < len(self.script):
            reward, terminated, truncated = self.script[self.t]
        else:
            reward, terminated, truncated = 0.0, False, False
        self.t += 1
        obs = np.full(OBS_SHAPE, 100.0 * self.t + self.slot, dtype=np.float32)
        return obs, float(reward), bool(terminated), bool(truncated), {}

    def close(self):
        pass


class _FakeEnv:
    """Minimal stateless env for make_env_for_training factory tests."""

    def __init__(self):
        self.action_space = type("Space", (), {"n": 2})()

    def reset(self, *, seed=None, options=None):
        return np.zeros(OBS_SHAPE, dtype=np.float32), {}

    def step(self, action):
        return np.zeros(OBS_SHAPE, dtype=np.float32), 0.0, False, False, {}

    def close(self):
        pass


def _config(**overrides):
    """Small PPOConfig for tests; rollout_length == minibatch_size == 4."""
    kwargs = {
        "rollout_length": 4,
        "minibatch_size": 4,
        "update_epochs": 1,
        "total_timesteps": 16,
        "learning_rate": 1e-3,
        "checkpoint_dir": "/tmp/never_num_envs",
        "checkpoint_every_updates": 10 ** 9,
    }
    kwargs.update(overrides)
    return PPOConfig(**kwargs)


# =====================================================================
# PPOConfig field tests
# =====================================================================


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestPPOConfigNumEnvs(unittest.TestCase):
    """num_envs field: default, explicit, absent, and validation."""

    def test_default_is_one(self):
        self.assertEqual(PPOConfig().num_envs, 1)

    def test_explicit_one(self):
        self.assertEqual(_config(num_envs=1).num_envs, 1)

    def test_explicit_two(self):
        self.assertEqual(_config(num_envs=2).num_envs, 2)

    def test_explicit_four(self):
        self.assertEqual(_config(num_envs=4).num_envs, 4)

    def test_from_dict_absent_defaults_to_one(self):
        cfg = PPOConfig.from_dict({"rollout_length": 8, "minibatch_size": 8,
                                   "update_epochs": 1, "total_timesteps": 16,
                                   "seed": 0, "checkpoint_every_updates": 100})
        self.assertEqual(cfg.num_envs, 1)

    def test_from_dict_with_num_envs(self):
        cfg = PPOConfig.from_dict({"num_envs": 2, "rollout_length": 8,
                                   "minibatch_size": 8, "update_epochs": 1,
                                   "total_timesteps": 16, "seed": 0,
                                   "checkpoint_every_updates": 100})
        self.assertEqual(cfg.num_envs, 2)

    def test_invalid_values(self):
        for bad in (0, -1, -10, 1.5, "2", None, False, True):
            with self.assertRaises((ValueError, TypeError), msg=f"num_envs={bad!r}"):
                PPOConfig(num_envs=bad, rollout_length=8, minibatch_size=8,
                          update_epochs=1, total_timesteps=16,
                          checkpoint_every_updates=100)


# =====================================================================
# make_env_for_training tests
# =====================================================================


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestMakeEnvForTraining(unittest.TestCase):
    """num_envs=1 -> plain env; num_envs>1 -> SyncVectorEnv."""

    def test_single_returns_plain_env(self):
        env = make_env_for_training(lambda: _FakeEnv(), 1)
        self.assertNotIsInstance(env, SyncVectorEnv)
        env.close()

    def test_two_returns_vec_env(self):
        env = make_env_for_training(lambda: _FakeEnv(), 2)
        self.assertIsInstance(env, SyncVectorEnv)
        self.assertEqual(env.num_envs, 2)
        env.close()

    def test_four_returns_vec_env(self):
        env = make_env_for_training(lambda: _FakeEnv(), 4)
        self.assertIsInstance(env, SyncVectorEnv)
        self.assertEqual(env.num_envs, 4)
        env.close()

    def test_factories_called_once_each(self):
        counter = [0]

        def make():
            counter[0] += 1
            return _FakeEnv()

        env = make_env_for_training(make, 3)
        self.assertEqual(counter[0], 3)  # SyncVectorEnv calls each factory in __init__
        env.close()

    def test_toy_config_produces_vec_env(self):
        """make_env_from_config + make_env_for_training -> SyncVectorEnv(2)."""
        make = make_env_from_config({"max_steps": 32})
        env = make_env_for_training(make, 2)
        self.assertIsInstance(env, SyncVectorEnv)
        self.assertEqual(env.num_envs, 2)
        env.close()

    def test_toy_config_single_env_still_plain(self):
        make = make_env_from_config({"max_steps": 32})
        env = make_env_for_training(make, 1)
        self.assertNotIsInstance(env, SyncVectorEnv)
        env.close()


# =====================================================================
# Timestep accounting tests
# =====================================================================


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestTimestepAccounting(unittest.TestCase):
    """num_timesteps counts total env samples, not update steps."""

    def test_single_env_one_rollout(self):
        env = _ScriptedEnv([(0.0, False, False)] * 8)
        trainer = PPOTrainer(env, ActorCritic(num_actions=2), _config(num_envs=1))
        trainer.collect_rollout()
        self.assertEqual(trainer.num_timesteps, 4)  # 4 steps * 1 env
        self.assertFalse(trainer._vec)
        self.assertEqual(trainer._num_slots, 1)

    def test_multi_env_one_rollout(self):
        env = SyncVectorEnv(
            [lambda: _ScriptedEnv([(0.0, False, False)] * 8, slot=s) for s in range(2)]
        )
        trainer = PPOTrainer(env, ActorCritic(num_actions=2), _config(num_envs=2))
        trainer.collect_rollout()
        self.assertEqual(trainer.num_timesteps, 8)  # 4 steps * 2 envs
        self.assertTrue(trainer._vec)
        self.assertEqual(trainer._num_slots, 2)

    def test_history_total_samples_multi(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = SyncVectorEnv(
                [lambda: _ScriptedEnv([(0.0, False, False)] * 16, slot=s) for s in range(2)]
            )
            trainer = PPOTrainer(env, ActorCritic(num_actions=2),
                                _config(num_envs=2, total_timesteps=16,
                                        checkpoint_dir=str(Path(tmp) / "ckpt")))
            with redirect_stdout(_io.StringIO()):
                trainer.train()
            # 8 steps/update (4 * 2), 2 updates -> [8, 16]
            self.assertEqual(trainer.history["timesteps"], [8, 16])
            self.assertEqual(trainer.num_timesteps, 16)

    def test_history_total_samples_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _ScriptedEnv([(0.0, False, False)] * 16)
            trainer = PPOTrainer(env, ActorCritic(num_actions=2),
                                _config(num_envs=1, total_timesteps=16,
                                        checkpoint_dir=str(Path(tmp) / "ckpt")))
            with redirect_stdout(_io.StringIO()):
                trainer.train()
            # 4 steps/update (4 * 1), 4 updates -> [4, 8, 12, 16]
            self.assertEqual(trainer.history["timesteps"], [4, 8, 12, 16])
            self.assertEqual(trainer.num_timesteps, 16)


# =====================================================================
# Telemetry CSV tests
# =====================================================================


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestTelemetryAccounting(unittest.TestCase):
    """CSV timesteps column == total env samples (not per-slot)."""

    def _train_and_read_csv(self, num_envs, total_timesteps, rollout_length=4):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = str(Path(tmp) / "tel.csv")
            if num_envs == 1:
                env = _ScriptedEnv([(0.0, False, False)] * 32)
            else:
                env = SyncVectorEnv(
                    [lambda s=s: _ScriptedEnv([(0.0, False, False)] * 32, slot=s)
                     for s in range(num_envs)]
                )
            config = _config(num_envs=num_envs, total_timesteps=total_timesteps,
                             rollout_length=rollout_length,
                             checkpoint_dir=str(Path(tmp) / "ckpt"),
                             telemetry_path=csv_path)
            trainer = PPOTrainer(env, ActorCritic(num_actions=2), config)
            with redirect_stdout(_io.StringIO()):
                trainer.train()
            with open(csv_path) as fh:
                rows = list(_csv.DictReader(fh))
            return trainer, rows

    def test_csv_timesteps_multi_env(self):
        trainer, rows = self._train_and_read_csv(2, 16)
        self.assertEqual([int(r["timesteps"]) for r in rows], [8, 16])

    def test_csv_timesteps_single_env(self):
        trainer, rows = self._train_and_read_csv(1, 16)
        self.assertEqual([int(r["timesteps"]) for r in rows], [4, 8, 12, 16])


# =====================================================================
# Checkpoint tests
# =====================================================================


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestCheckpointNumEnvs(unittest.TestCase):
    """Checkpoint preserves num_envs; resume reconstructs the correct env."""

    def test_checkpoint_stores_num_envs(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = SyncVectorEnv(
                [lambda: _ScriptedEnv([(0.0, False, False)] * 16, slot=s) for s in range(2)]
            )
            config = _config(num_envs=2, total_timesteps=8,
                             checkpoint_dir=str(Path(tmp) / "ckpt"))
            trainer = PPOTrainer(env, ActorCritic(num_actions=2), config,
                                env_config={"max_steps": 16})
            with redirect_stdout(_io.StringIO()):
                trainer.train()
            ckpt_path = Path(tmp) / "ckpt" / "ppo_final.pt"
            payload = torch.load(str(ckpt_path), map_location="cpu", weights_only=True)
            self.assertEqual(payload["ppo_config"]["num_envs"], 2)
            self.assertEqual(payload["env_config"], {"max_steps": 16})

    def test_resume_reconstructs_vec_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            torch.manual_seed(0)
            env = SyncVectorEnv(
                [lambda: _ScriptedEnv([(0.0, False, False)] * 16, slot=s) for s in range(2)]
            )
            config = _config(num_envs=2, total_timesteps=8,
                             checkpoint_dir=str(Path(tmp) / "ckpt"),
                             checkpoint_every_updates=100)
            trainer = PPOTrainer(env, ActorCritic(num_actions=2), config)
            with redirect_stdout(_io.StringIO()):
                trainer.train()
            ckpt_path = str(Path(tmp) / "ckpt" / "ppo_final.pt")

            # Resume: caller rebuilds the 2-env SyncVectorEnv
            env2 = SyncVectorEnv([lambda: _ScriptedEnv([]), lambda: _ScriptedEnv([])])
            resumed = PPOTrainer.load_checkpoint(ckpt_path, env2)
            self.assertEqual(resumed.config.num_envs, 2)
            self.assertIsInstance(resumed.env, SyncVectorEnv)
            self.assertEqual(resumed.num_timesteps, 8)
            self.assertTrue(resumed.is_complete())
            self.assertEqual(resumed.num_updates, trainer.num_updates)


# =====================================================================
# Single-env regression
# =====================================================================


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestSingleEnvRegression(unittest.TestCase):
    """num_envs=1 matches legacy single-env behavior."""

    def test_not_vector(self):
        env = _ScriptedEnv([(0.0, False, False)] * 8)
        trainer = PPOTrainer(env, ActorCritic(num_actions=2), _config(num_envs=1))
        self.assertFalse(trainer._vec)
        self.assertEqual(trainer._num_slots, 1)

    def test_training_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _ScriptedEnv([(0.0, False, False)] * 32)
            config = _config(num_envs=1, total_timesteps=8,
                             checkpoint_dir=str(Path(tmp) / "ckpt"),
                             checkpoint_every_updates=100)
            trainer = PPOTrainer(env, ActorCritic(num_actions=2), config)
            with redirect_stdout(_io.StringIO()):
                trainer.train()
            self.assertEqual(trainer.num_timesteps, 8)
            self.assertTrue((Path(tmp) / "ckpt" / "ppo_final.pt").exists())

    def test_toy_run_experiment_num_envs_one(self):
        """The ppo.py toy helper with num_envs=1 runs the legacy path."""
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(_io.StringIO()):
                history = ppo_mod.run_experiment(
                    total_timesteps=8, rollout_length=4, seed=0,
                    checkpoint_dir=str(Path(tmp) / "ckpt"), num_envs=1)
        self.assertTrue(len(history["timesteps"]) >= 1)


# =====================================================================
# Multi-env PPO training tests
# =====================================================================


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestMultiEnvPPOTraining(unittest.TestCase):
    """Full train + checkpoint round-trip with 2 and 4 envs."""

    def test_two_env_training_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            torch.manual_seed(0)
            env = SyncVectorEnv(
                [lambda: _ScriptedEnv([(0.0, False, False)] * 16, slot=s) for s in range(2)]
            )
            config = _config(num_envs=2, total_timesteps=16,
                             checkpoint_dir=str(Path(tmp) / "ckpt"),
                             checkpoint_every_updates=100)
            trainer = PPOTrainer(env, ActorCritic(num_actions=2), config)
            with redirect_stdout(_io.StringIO()):
                history = trainer.train()
            self.assertEqual(len(history["timesteps"]), 2)
            self.assertEqual(history["timesteps"], [8, 16])
            self.assertEqual(trainer.num_timesteps, 16)

            ckpt_path = str(Path(tmp) / "ckpt" / "ppo_final.pt")
            env2 = SyncVectorEnv([lambda: _ScriptedEnv([]), lambda: _ScriptedEnv([])])
            resumed = PPOTrainer.load_checkpoint(ckpt_path, env2)
            self.assertEqual(resumed.config.num_envs, 2)
            self.assertIsInstance(resumed.env, SyncVectorEnv)
            self.assertTrue(resumed.is_complete())

    def test_four_env_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            torch.manual_seed(0)
            script = [(0.0, False, False)] * 8
            env = SyncVectorEnv(
                [lambda: _ScriptedEnv(script, slot=s) for s in range(4)]
            )
            config = _config(num_envs=4, total_timesteps=16,
                             checkpoint_dir=str(Path(tmp) / "ckpt"))
            trainer = PPOTrainer(env, ActorCritic(num_actions=2), config)
            with redirect_stdout(_io.StringIO()):
                trainer.train()
            self.assertEqual(trainer.num_timesteps, 16)  # 4 steps * 4 envs = 16
            self.assertTrue(trainer._vec)


# =====================================================================
# CLI integration tests
# =====================================================================


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestCLIWithNumEnvs(unittest.TestCase):
    """main.py train reads num_envs from config and builds the right env."""

    # NOTE: the env2 lambda in the original draft captured ``s`` by reference;
    # the version above uses ``lambda s=s`` to bind each index.  These CLI
    # tests use the real toy game (no window) and are intentionally tiny.

    def _train_and_check(self, num_envs, total_timesteps, tmp):
        import main as cli_main
        import yaml

        cfg = {
            "env": {"max_steps": 32, "obs_size": 84, "num_stack": 4, "skip": 1},
            "model": {"in_channels": 4, "frame_size": 84, "feature_dim": 32,
                      "hidden_size": 16, "num_layers": 1},
            "ppo": {"total_timesteps": total_timesteps, "rollout_length": 4,
                    "minibatch_size": 4, "update_epochs": 1, "learning_rate": 1e-3,
                    "seed": 0,
                    "checkpoint_dir": str(Path(tmp) / "ckpt"),
                    "checkpoint_every_updates": 100,
                    "num_envs": num_envs},
            "eval": {"episodes": 1},
        }
        cfg_path = str(Path(tmp) / "tiny.yaml")
        Path(cfg_path).write_text(yaml.safe_dump(cfg), encoding="utf-8")
        buf = _io.StringIO()
        with redirect_stdout(buf):
            code = cli_main.main(["train", "--config", cfg_path])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertIn("train done", buf.getvalue())
        ckpt_path = Path(tmp) / "ckpt" / "ppo_final.pt"
        self.assertTrue(ckpt_path.exists())
        payload = torch.load(str(ckpt_path), map_location="cpu", weights_only=True)
        return payload

    def test_train_num_envs_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._train_and_check(1, 8, tmp)
            self.assertEqual(payload["ppo_config"]["num_envs"], 1)

    def test_train_num_envs_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._train_and_check(2, 8, tmp)
            self.assertEqual(payload["ppo_config"]["num_envs"], 2)

    def test_train_num_envs_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._train_and_check(4, 16, tmp)
            self.assertEqual(payload["ppo_config"]["num_envs"], 4)

    def test_train_resume_with_num_envs_two(self):
        """Resume path also picks up num_envs from config."""
        import main as cli_main
        import yaml

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "env": {"max_steps": 32, "obs_size": 84, "num_stack": 4, "skip": 1},
                "model": {"in_channels": 4, "frame_size": 84, "feature_dim": 32,
                          "hidden_size": 16, "num_layers": 1},
                "ppo": {"total_timesteps": 8, "rollout_length": 4, "minibatch_size": 4,
                        "update_epochs": 1, "learning_rate": 1e-3, "seed": 0,
                        "checkpoint_dir": str(Path(tmp) / "ckpt"),
                        "checkpoint_every_updates": 100, "num_envs": 2},
                "eval": {"episodes": 1},
            }
            cfg_path = str(Path(tmp) / "tiny.yaml")
            Path(cfg_path).write_text(yaml.safe_dump(cfg), encoding="utf-8")

            # First: train to completion
            buf = _io.StringIO()
            with redirect_stdout(buf):
                cli_main.main(["train", "--config", cfg_path])
            ckpt_path = str(Path(tmp) / "ckpt" / "ppo_final.pt")
            self.assertTrue(Path(ckpt_path).exists())

            # Resume with same config (already complete -> no-op, no crash)
            buf2 = _io.StringIO()
            with redirect_stdout(buf2):
                code = cli_main.main(["train", "--config", cfg_path, "--resume", ckpt_path])
            self.assertEqual(code, 0, buf2.getvalue())
            self.assertIn("already complete", buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
