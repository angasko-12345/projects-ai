"""Tests for the curiosity module + PPO wiring (requires torch)."""
import unittest

try:
    import torch

    from agent.model import ActorCritic
    from environment.preprocessing import PreprocessingWrapper
    from environment.toy_pong import ToyPongEnv
    from training.curiosity import CuriosityConfig, CuriosityModule, ForwardModel, FeatureEncoder, RunningMeanStd
    from training.ppo import PPOConfig, PPOTrainer

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def _obs_batch(t=8):
    return torch.rand(t, 4, 84, 84)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestCuriosityModule(unittest.TestCase):
    def test_encoder_and_forward_shapes(self):
        enc = FeatureEncoder().eval()
        fwd = ForwardModel(feature_dim=128, num_actions=3).eval()
        with torch.no_grad():
            feats = enc(_obs_batch())
            pred = fwd(feats, torch.tensor([0, 1, 2, 0, 1, 2, 0, 1]))
        self.assertEqual(tuple(feats.shape), (8, 128))
        self.assertEqual(tuple(pred.shape), (8, 128))

    def test_intrinsic_nonnegative_and_masked(self):
        mod = CuriosityModule(num_actions=3)
        obs, nxt = _obs_batch(6), _obs_batch(6)
        actions = torch.zeros(6, dtype=torch.long)
        valid = torch.tensor([1, 1, 1, 0, 0, 1])
        scaled, raw = mod.intrinsic(obs, actions, nxt, valid)
        self.assertEqual(tuple(scaled.shape), (6,))
        self.assertTrue(bool((scaled >= 0).all()))
        self.assertTrue(bool((raw >= 0).all()))
    def test_normalizer_stabilizes_scale(self):
        mod = CuriosityModule(num_actions=3, config=CuriosityConfig(scale=1.0))
        big = torch.rand(32, 4, 84, 84) * 10.0
        actions = torch.zeros(32, dtype=torch.long)
        scaled, _ = mod.intrinsic(big, actions, big, torch.ones(32))
        self.assertTrue(bool(scaled.isfinite().all()))
        self.assertLessEqual(float(scaled.max()), 5.0)  # reward_clip binds regardless of input magnitude

    def test_predictor_update_changes_params(self):
        mod = CuriosityModule(num_actions=3)
        obs, nxt = _obs_batch(), _obs_batch()
        actions = torch.randint(0, 3, (8,))
        before = [p.clone() for p in mod.forward_model.parameters()]
        loss = mod.update(obs, actions, nxt, torch.ones(8))
        self.assertTrue(loss >= 0.0)
        self.assertTrue(any(not torch.equal(a, b) for a, b in zip(before, mod.forward_model.parameters())))

    def test_empty_valid_mask_is_noop(self):
        mod = CuriosityModule(num_actions=3)
        self.assertEqual(mod.update(_obs_batch(4), torch.zeros(4, dtype=torch.long), _obs_batch(4), torch.zeros(4)), 0.0)

    def test_running_stats(self):
        rms = RunningMeanStd()
        rms.update(torch.tensor([1.0, 2.0, 3.0]))
        self.assertAlmostEqual(rms.mean, 2.0)
        out = rms.normalize(torch.tensor([2.0]))
        self.assertTrue(out.isfinite().all())

    def test_bad_configs_rejected(self):
        with self.assertRaises(ValueError):
            CuriosityConfig(scale=-1.0)
    def _trainer(self, **overrides):
        from training.curiosity import CuriosityConfig as CC

        env = PreprocessingWrapper(ToyPongEnv(max_steps=64))
        model = ActorCritic(num_actions=int(env.action_space.n))
        cur_args = {"scale": 0.1}
        cur_args.update(overrides)
        cur = CuriosityModule(num_actions=int(env.action_space.n), config=CC(**cur_args))
        cfg = PPOConfig(rollout_length=32, minibatch_size=16, update_epochs=1,
                        total_timesteps=64, checkpoint_every_updates=100)
        return PPOTrainer(env, model, cfg, curiosity=cur)

    def test_disabled_by_default(self):
        env = PreprocessingWrapper(ToyPongEnv(max_steps=64))
        trainer = PPOTrainer(env, ActorCritic(num_actions=3),
                             PPOConfig(rollout_length=8, minibatch_size=8, update_epochs=1, total_timesteps=8))
        buf, *_ = trainer.collect_rollout()
        self.assertTrue(bool((buf["int_rewards"] == 0).all()))
        self.assertTrue(torch.equal(buf["rewards"], buf["ext"]))

    def test_enabled_splits_rewards(self):
        trainer = self._trainer()
        buf, rewards, lengths, ext, intr = trainer.collect_rollout()
        self.assertEqual(len(buf["rewards"]), 32)
        self.assertTrue(bool((buf["int_rewards"] >= 0).all()))
        self.assertTrue(torch.allclose(buf["rewards"], buf["ext"] + buf["int_rewards"]))
        self.assertEqual(len(rewards), len(ext))
        self.assertEqual(len(rewards), len(intr))

    def test_update_trains_predictor_and_logs(self):
        trainer = self._trainer()
        buf, *_ = trainer.collect_rollout()
        stats = trainer.update(buf)
        self.assertIn("predictor_loss", stats)
        self.assertGreaterEqual(stats["predictor_loss"], 0.0)

    def test_scale_zero_matches_no_curiosity(self):
        torch.manual_seed(0)
        curious = self._trainer(scale=0.0)
        buf, *_ = curious.collect_rollout()
        self.assertTrue(torch.equal(buf["rewards"], buf["ext"]))


if __name__ == "__main__":
    unittest.main()
