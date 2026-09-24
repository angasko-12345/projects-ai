"""Tests for evaluation + experiment wiring (requires torch)."""
import tempfile
import unittest
from pathlib import Path

try:
    import torch

    from agent.model import ActorCritic
    from environment.preprocessing import PreprocessingWrapper
    from environment.toy_pong import ToyPongEnv
    from training.evaluate import evaluate
    from training.experiment import load_experiment, make_env_from_config

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def _make_env():
    return PreprocessingWrapper(ToyPongEnv(max_steps=64))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestEvaluate(unittest.TestCase):
    def test_report_keys_and_episodes_finish(self):
        torch.manual_seed(0)
        model = ActorCritic().eval()
        rep = evaluate(model, _make_env, episodes=4, seeds=[0, 1, 2, 3])
        for key in ("mean_reward", "std_reward", "mean_length", "episode_rewards", "episode_lengths"):
            self.assertIn(key, rep)
        self.assertEqual(len(rep["episode_rewards"]), 4)
        self.assertTrue(all(s >= 1 for s in rep["episode_lengths"]))

    def test_deterministic_same_seeds(self):
        torch.manual_seed(0)
        model = ActorCritic().eval()
        first = evaluate(model, _make_env, episodes=4, seeds=[5, 6, 7, 8])
        second = evaluate(model, _make_env, episodes=4, seeds=[5, 6, 7, 8])
        self.assertEqual(first["episode_rewards"], second["episode_rewards"])

    def test_invalid_args(self):
        model = ActorCritic().eval()
        with self.assertRaises(ValueError):
            evaluate(model, _make_env, episodes=0)
        with self.assertRaises(ValueError):
            evaluate(model, _make_env, episodes=2, seeds=[1])

    def test_experiment_config_loads(self):
        cfg = load_experiment(Path(__file__).resolve().parent.parent / "experiments" / "exp_toy_ppo_01.yaml")
        for section in ("env", "model", "ppo", "eval"):
            self.assertIn(section, cfg)
        env = make_env_from_config(cfg["env"])()
        obs, _ = env.reset(seed=0)
        self.assertEqual(obs.shape, (4, 84, 84))

    def test_experiment_config_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.yaml"
            bad.write_text("ppo: {}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_experiment(bad)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestEvalMode(unittest.TestCase):
    def test_mode_preserved(self):
        from training.evaluate import evaluate as evaluate_fn

        torch.manual_seed(0)
        model = ActorCritic()
        model.train()
        evaluate_fn(model, _make_env, episodes=2, seeds=[0, 1])
        self.assertTrue(model.training)
        model.eval()
        evaluate_fn(model, _make_env, episodes=2, seeds=[0, 1])
        self.assertFalse(model.training)
