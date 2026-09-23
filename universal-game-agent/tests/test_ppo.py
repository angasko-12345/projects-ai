"""Tests for PPO (requires torch; uses the toy game only)."""
import tempfile
import unittest
from pathlib import Path

try:
    import torch

    from agent.model import ActorCritic
    from environment.preprocessing import PreprocessingWrapper
    from environment.toy_pong import ToyPongEnv
    from training.ppo import PPOConfig, PPOTrainer, compute_gae

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def _tiny_config(**overrides):
    args = {
        "rollout_length": 32,
        "minibatch_size": 16,
        "update_epochs": 1,
        "total_timesteps": 64,
        "checkpoint_every_updates": 100,
    }
    args.update(overrides)
    return PPOConfig(**args)


def _trainer(**overrides):
    env = PreprocessingWrapper(ToyPongEnv(max_steps=64))
    model = ActorCritic(num_actions=int(env.action_space.n))
    return PPOTrainer(env, model, _tiny_config(**overrides))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestGAE(unittest.TestCase):
    def test_hand_computed_returns(self):
        rewards = torch.tensor([1.0, 1.0, 1.0])
        values = torch.tensor([0.0, 0.0, 0.0])
        adv, ret = compute_gae(rewards, values, torch.zeros(3), torch.tensor(0.0), gamma=1.0, gae_lambda=1.0)
        self.assertTrue(torch.allclose(adv, torch.tensor([3.0, 2.0, 1.0])))
        self.assertTrue(torch.allclose(ret, torch.tensor([3.0, 2.0, 1.0])))

    def test_terminal_masks_bootstrap(self):
        rewards = torch.tensor([1.0, 5.0])
        values = torch.tensor([0.0, 0.0])
        adv, _ = compute_gae(rewards, values, torch.tensor([1.0, 0.0]), torch.tensor(9.0), gamma=1.0, gae_lambda=1.0)
        self.assertAlmostEqual(adv[0].item(), 1.0)  # next_value ignored across termination
        self.assertAlmostEqual(adv[1].item(), 14.0)  # 5 + bootstrap 9

    def test_discounted(self):
        rewards = torch.tensor([0.0, 1.0])
        values = torch.tensor([0.0, 0.0])
        adv, _ = compute_gae(rewards, values, torch.zeros(2), torch.tensor(0.0), gamma=0.5, gae_lambda=1.0)
        self.assertAlmostEqual(adv[0].item(), 0.5)
        self.assertAlmostEqual(adv[1].item(), 1.0)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestPPOTrainer(unittest.TestCase):
    def test_rollout_shapes(self):
        trainer = _trainer()
        buf, *_ = trainer.collect_rollout()
        self.assertEqual(tuple(buf["obs"].shape), (32, 4, 84, 84))
        self.assertEqual(tuple(buf["actions"].shape), (32,))
        self.assertEqual(tuple(buf["logprobs"].shape), (32,))
        self.assertTrue(set(buf["actions"].tolist()) <= {0, 1, 2})

    def test_update_runs_and_changes_params(self):
        trainer = _trainer()
        before = [p.clone() for p in trainer.model.parameters()]
        buf, *_ = trainer.collect_rollout()
        stats = trainer.update(buf)
        for key in ("policy_loss", "value_loss", "entropy", "timesteps"):
            self.assertIn(key, stats)
            self.assertTrue(abs(stats[key]) != float("inf"))
        self.assertTrue(any(not torch.equal(a, b) for a, b in zip(before, trainer.model.parameters())))

    def test_checkpoint_roundtrip_and_resume(self):
        trainer = _trainer()
        buf, *_ = trainer.collect_rollout()
        trainer.update(buf)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "ckpt.pt")
            trainer.save_checkpoint(path)
            self.assertTrue(Path(path).exists())
            env2 = PreprocessingWrapper(ToyPongEnv(max_steps=64))
            resumed = PPOTrainer.load_checkpoint(path, env2)
        self.assertEqual(resumed.num_timesteps, trainer.num_timesteps)
        for a, b in zip(trainer.model.parameters(), resumed.model.parameters()):
            self.assertTrue(torch.equal(a, b))
        buf2, *_ = resumed.collect_rollout()  # training continues after restore
        resumed.update(buf2)
        self.assertGreater(resumed.num_timesteps, trainer.num_timesteps)

    def test_short_training_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = PreprocessingWrapper(ToyPongEnv(max_steps=64))
            model = ActorCritic(num_actions=int(env.action_space.n))
            config = _tiny_config(total_timesteps=128, checkpoint_dir=tmp)
            history = PPOTrainer(env, model, config).train()
            for key in ("timesteps", "fps", "policy_loss", "value_loss", "entropy", "mean_reward", "episodes"):
                self.assertIn(key, history)
                self.assertTrue(len(history[key]) > 0)
            self.assertEqual(history["timesteps"][-1], 128)
            self.assertTrue((Path(tmp) / "ppo_final.pt").exists())
            self.assertGreaterEqual(history["episodes"][-1], 1)  # short env => episodes finish

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            PPOConfig(learning_rate=-1.0)
        with self.assertRaises(ValueError):
            PPOConfig(rollout_length=0)
        with self.assertRaises(ValueError):
            PPOConfig(gamma=1.5)
        cfg = PPOConfig.from_dict({"learning_rate": 1e-3, "unknown_key": 1})
        self.assertAlmostEqual(cfg.learning_rate, 1e-3)


if __name__ == "__main__":
    unittest.main()
