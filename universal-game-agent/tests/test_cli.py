"""Tests for the main.py CLI. Heavy ops mocked; one tiny real integration run."""
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import main as cli_main

ROOT = Path(__file__).resolve().parent.parent


def _run(argv):
    with redirect_stdout(io.StringIO()) as buf:
        try:
            code = cli_main.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, buf.getvalue()


class FakeTrainer:
    def __init__(self, *args, **kwargs):
        self.num_timesteps = 0
        self.curiosity = None
        self.optimizer = type("Opt", (), {"param_groups": [{"lr": 1e-3}]})()

    def train(self):
        self.num_timesteps = 5
        return {"mean_reward": [0.5], "episodes": [2]}

    @classmethod
    def load_checkpoint(cls, path, env):
        inst = cls()
        inst.num_timesteps = 5
        return inst


@unittest.skipUnless(__import__("importlib").util.find_spec("torch"), "torch not installed")
class TestCLIDispatch(unittest.TestCase):
    def test_train_dispatch(self):
        with patch("training.ppo.PPOTrainer", FakeTrainer):
            code, out = _run(["train", "--config", str(ROOT / "configs" / "default.yaml"),
                              "--timesteps", "16", "--checkpoint-dir", str(ROOT / "checkpoints")])
        self.assertEqual(code, 0)
        self.assertIn("train done", out)

    def test_train_resume_missing(self):
        code, _ = _run(["train", "--config", str(ROOT / "configs" / "default.yaml"),
                        "--resume", "nope.pt"])
        self.assertEqual(code, 2)

    def test_evaluate_dispatch(self):
        fake = {"mean_reward": 1.0, "std_reward": 0.0, "min_reward": 1.0,
                "max_reward": 1.0, "mean_length": 10.0}
        with patch("training.evaluate.evaluate", return_value=fake):
            code, out = _run(["evaluate", "--config", str(ROOT / "configs" / "default.yaml"),
                              "--episodes", "2"])
        self.assertEqual(code, 0)
        self.assertIn("mean=1.00", out)

    def test_evaluate_missing_checkpoint(self):
        code, _ = _run(["evaluate", "--config", str(ROOT / "configs" / "default.yaml"),
                        "--checkpoint", "nope.pt"])
        self.assertEqual(code, 2)

    def test_experiment_dispatch(self):
        fake = {"initial_mean_episode_reward": 0.0, "final_train_rolling_mean_reward": 0.0,
                "final_eval_mean_reward": 0.0, "training_steps": 8, "fps": 100.0}
        with patch("training.experiment.run_experiment", return_value=fake) as m:
            code, out = _run(["experiment", "--config",
                              str(ROOT / "experiments" / "exp_toy_ppo_01.yaml")])
        self.assertEqual(code, 0)
        m.assert_called_once()
        self.assertIn("experiment done", out)

    def test_train_resume_dispatch(self):
        with patch("training.ppo.PPOTrainer", FakeTrainer):
            code, out = _run(["train", "--config", str(ROOT / "configs" / "default.yaml"),
                              "--timesteps", "16",
                              "--resume", str(ROOT / "checkpoints" / ".gitkeep")])
        self.assertEqual(code, 0)
        self.assertIn("resumed from", out)


class TestCLIHelpAndErrors(unittest.TestCase):
    def test_top_help(self):
        code, out = _run(["--help"])
        self.assertEqual(code, 0)
        for cmd in ("smoke-test", "train", "evaluate", "experiment"):
            self.assertIn(cmd, out)

    def test_subcommand_helps(self):
        for cmd in ("smoke-test", "train", "evaluate", "experiment"):
            code, out = _run([cmd, "--help"])
            self.assertEqual(code, 0, cmd)
            self.assertIn("--config", out)

    def test_help_mentions_options(self):
        _, out = _run(["train", "--help"])
        self.assertIn("--timesteps", out)
    def test_invalid_numerics(self):
        code, _ = _run(["train", "--timesteps", "0"])
        self.assertEqual(code, 2)
        code, _ = _run(["train", "--timesteps", "abc"])
        self.assertEqual(code, 2)
        code, _ = _run(["evaluate", "--episodes", "-3"])
        self.assertEqual(code, 2)

    def test_unknown_command(self):
        code, _ = _run(["frobnicate"])
        self.assertEqual(code, 2)

    def test_no_command(self):
        code, _ = _run([])
        self.assertEqual(code, 2)



@unittest.skipUnless(__import__("importlib").util.find_spec("torch"), "torch not installed")
class TestCLIIntegration(unittest.TestCase):
    def test_tiny_train_evaluate_experiment(self):
        import tempfile

        import yaml

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "env": {"width": 64, "height": 48, "max_steps": 16, "obs_size": 84,
                        "num_stack": 4, "skip": 1},
                "model": {"in_channels": 4, "frame_size": 84, "feature_dim": 32,
                          "hidden_size": 16, "num_layers": 1},
                "ppo": {"total_timesteps": 16, "rollout_length": 8, "minibatch_size": 8,
                        "update_epochs": 1, "learning_rate": 1e-3, "seed": 0,
                        "checkpoint_dir": str(Path(tmp) / "ckpt"),
                        "checkpoint_every_updates": 100},
                "eval": {"episodes": 1},
            }
            cfg_path = str(Path(tmp) / "tiny.yaml")
            Path(cfg_path).write_text(yaml.safe_dump(cfg), encoding="utf-8")

            code, _ = _run(["smoke-test", "--config", str(ROOT / "configs" / "default.yaml")])
            self.assertEqual(code, 0)
            code, _ = _run(["train", "--config", cfg_path])
            self.assertEqual(code, 0)
            ckpt = str(Path(tmp) / "ckpt" / "ppo_final.pt")
            self.assertTrue(Path(ckpt).exists())
            code, out = _run(["evaluate", "--config", cfg_path, "--checkpoint", ckpt,
                              "--episodes", "1"])
            self.assertEqual(code, 0)
            self.assertIn("eval (greedy, 1 episodes)", out)
            from agent.model import ActorCritic as _AC

            model_only = str(Path(tmp) / "model_only.pt")
            _AC(num_actions=3, in_channels=4, feature_dim=32, hidden_size=16).save(model_only)
            code, _ = _run(["evaluate", "--config", cfg_path, "--checkpoint", model_only,
                            "--episodes", "1"])
            self.assertEqual(code, 0)
            code, _ = _run(["train", "--config", cfg_path, "--resume", ckpt,
                            "--timesteps", "24"])
            self.assertEqual(code, 0)
            exp_cfg = dict(cfg, ppo=dict(cfg["ppo"], total_timesteps=16,
                                         checkpoint_dir=str(Path(tmp) / "ckpt2")))
            exp_path = str(Path(tmp) / "tiny_exp.yaml")
            Path(exp_path).write_text(yaml.safe_dump(exp_cfg), encoding="utf-8")
            code, out = _run(["experiment", "--config", exp_path])
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "tiny_exp_results.json").exists())


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(__import__("importlib").util.find_spec("torch"), "torch not installed")
class TestCLIValidation(unittest.TestCase):
    def test_negative_seed_rejected_cleanly(self):
        code, _ = _run(["train", "--config", str(ROOT / "configs" / "default.yaml"),
                        "--seed", "-1", "--timesteps", "8"])
        self.assertEqual(code, 2)

    def test_malformed_episodes_rejected_cleanly(self):
        import tempfile

        import yaml

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = str(Path(tmp) / "bad.yaml")
            Path(cfg_path).write_text(yaml.safe_dump({"eval": {"episodes": "many"}}),
                                      encoding="utf-8")
            code, _ = _run(["evaluate", "--config", cfg_path])
            self.assertEqual(code, 2)

    def test_resume_syncs_optimizer_lr(self):
        import tempfile

        import torch
        from agent.model import ActorCritic
        from environment.preprocessing import PreprocessingWrapper
        from environment.toy_pong import ToyPongEnv
        from main import _sync_optimizer_lr
        from training.ppo import PPOConfig, PPOTrainer

        with tempfile.TemporaryDirectory() as tmp:
            torch.manual_seed(0)
            env = PreprocessingWrapper(ToyPongEnv(max_steps=16))
            model = ActorCritic(num_actions=3, feature_dim=32, hidden_size=16)
            trainer = PPOTrainer(env, model, PPOConfig(rollout_length=8, minibatch_size=8,
                                                      update_epochs=1, total_timesteps=8,
                                                      learning_rate=1e-3,
                                                      checkpoint_dir=tmp))
            buf, *_ = trainer.collect_rollout()
            trainer.update(buf)  # populate momentum state before saving
            path = trainer.save_checkpoint(str(Path(tmp) / "ckpt.pt"))
            resumed = PPOTrainer.load_checkpoint(path, PreprocessingWrapper(ToyPongEnv(max_steps=16)))
            before_params = [p.clone() for p in resumed.model.parameters()]
            before_momentum = [{k: (v.clone() if torch.is_tensor(v) else v)
                                for k, v in per.items()}
                               for per in resumed.optimizer.state_dict()["state"].values()]
            self.assertTrue(before_momentum)  # setup: nonempty optimizer state
            _sync_optimizer_lr(resumed, 0.05)
            after = resumed.optimizer.state_dict()
            self.assertAlmostEqual(after["param_groups"][0]["lr"], 0.05)
            after_momentum = [{k: (v.clone() if torch.is_tensor(v) else v)
                               for k, v in per.items()}
                              for per in after["state"].values()]
            self.assertEqual(len(after_momentum), len(before_momentum))
            for a, b in zip(before_momentum, after_momentum):
                self.assertEqual(set(a.keys()), set(b.keys()))
                for k in a:
                    if torch.is_tensor(a[k]):
                        self.assertTrue(torch.equal(a[k], b[k]))
                    else:
                        self.assertEqual(a[k], b[k])
            for a, b in zip(before_params, resumed.model.parameters()):
                self.assertTrue(torch.equal(a, b))

    def test_resume_syncs_curiosity_scale(self):
        import tempfile

        import torch
        from agent.model import ActorCritic
        from environment.preprocessing import PreprocessingWrapper
        from environment.toy_pong import ToyPongEnv
        from main import _sync_curiosity_scale
        from training.curiosity import CuriosityConfig, CuriosityModule
        from training.ppo import PPOConfig, PPOTrainer

        with tempfile.TemporaryDirectory() as tmp:
            torch.manual_seed(1)
            env = PreprocessingWrapper(ToyPongEnv(max_steps=16))
            model = ActorCritic(num_actions=3, feature_dim=32, hidden_size=16)
            curiosity = CuriosityModule(num_actions=3, in_channels=4,
                                        config=CuriosityConfig(scale=0.1))
            trainer = PPOTrainer(env, model, PPOConfig(rollout_length=8, minibatch_size=8,
                                                      update_epochs=1, total_timesteps=8,
                                                      checkpoint_dir=tmp),
                                 curiosity=curiosity)
            before_encoder = [p.clone() for p in curiosity.encoder.parameters()]
            path = trainer.save_checkpoint(str(Path(tmp) / "ckpt.pt"))
            resumed = PPOTrainer.load_checkpoint(path, PreprocessingWrapper(ToyPongEnv(max_steps=16)))
            self.assertAlmostEqual(resumed.curiosity.config.scale, 0.1)
            _sync_curiosity_scale(resumed, {"curiosity": {"scale": 0.7}})
            self.assertAlmostEqual(resumed.curiosity.config.scale, 0.7)
            for a, b in zip(before_encoder, resumed.curiosity.encoder.parameters()):
                self.assertTrue(torch.equal(a, b))
            _sync_curiosity_scale(resumed, {})  # absent section leaves scale alone
            self.assertAlmostEqual(resumed.curiosity.config.scale, 0.7)
            _sync_curiosity_scale(PPOTrainer(env, model, PPOConfig(
                rollout_length=8, minibatch_size=8, update_epochs=1,
                total_timesteps=8, checkpoint_dir=tmp)), {})  # no module: no-op
