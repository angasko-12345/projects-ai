"""Diagnostics aggregation tests: exact episode/outcome/component numbers."""
import unittest

try:
    import torch

    from agent.model import ActorCritic
    from environment.external_game import ExternalGameEnv
    from environment.reward import CompositeReward, EventReward, SurvivalReward
    from environment.termination import NeverTerminateProvider
    from tests.test_ppo import ScriptedEnv
    from training.curiosity import CuriosityConfig, CuriosityModule
    from training.ppo import PPOConfig, PPOTrainer

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def _trainer(script, rollout_length, total=None, curiosity=None):
    env = ScriptedEnv(script)
    model = ActorCritic(num_actions=2, in_channels=1, feature_dim=16, hidden_size=8)
    config = PPOConfig(rollout_length=rollout_length, minibatch_size=rollout_length,
                       update_epochs=1, total_timesteps=total or rollout_length,
                       checkpoint_every_updates=1000)
    return PPOTrainer(env, model, config, curiosity=curiosity)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestDiagnostics(unittest.TestCase):
    def test_exact_episode_aggregation(self):
        script = [(1.0, False, False), (0.0, False, False), (-1.0, True, False)]
        trainer = _trainer(script, rollout_length=3)
        buf, rewards, lengths, ext, intr, term = trainer.collect_rollout()
        self.assertEqual(rewards, [0.0])  # +1, 0, -1
        self.assertEqual(lengths, [3])
        self.assertEqual(ext, [0.0])
        self.assertEqual(intr, [0.0])
        self.assertEqual(term, [True])
        self.assertTrue(torch.equal(buf["rewards"], buf["ext"] + buf["int_rewards"]))

    def test_episode_split(self):
        script = [(2.0, False, False), (3.0, True, False),
                  (4.0, False, False), (5.0, True, False)]
        trainer = _trainer(script, rollout_length=4)
        _, rewards, lengths, ext, _, term = trainer.collect_rollout()
        self.assertEqual(rewards, [5.0, 9.0])
        self.assertEqual(lengths, [2, 2])
        self.assertEqual(ext, [5.0, 9.0])
        self.assertEqual(term, [True, True])

    def test_termination_vs_truncation_counts(self):
        script = [(0.0, True, False), (0.0, False, True),
                  (0.0, True, False), (0.0, False, True)]
        trainer = _trainer(script, rollout_length=2, total=4)
        history = trainer.train()
        self.assertEqual(sum(history["upd_terminated"]), 2)
        self.assertEqual(sum(history["upd_truncated"]), 2)
        self.assertEqual(history["episodes"][-1], 4)
        self.assertEqual(history["upd_mean_length"][-1], 1.0)

    def test_curiosity_kept_separate(self):
        script = [(1.0, False, False), (0.0, True, False)]
        curiosity = CuriosityModule(num_actions=2, in_channels=1,
                                    config=CuriosityConfig(scale=0.5))
        trainer = _trainer(script, rollout_length=2, curiosity=curiosity)
        buf, rewards, _, ext, intr, _ = trainer.collect_rollout()
        self.assertTrue(bool((buf["int_rewards"] >= 0).all()))
        self.assertTrue(torch.allclose(buf["rewards"], buf["ext"] + buf["int_rewards"]))
        self.assertAlmostEqual(rewards[0], ext[0] + intr[0])
        stats = trainer.update(buf)
        self.assertGreaterEqual(stats["predictor_loss"], 0.0)

    def test_component_means(self):
        import numpy as np

        from interface.adapter import GameInterface
        from interface.capture import ScreenCapture, SyntheticBackend
        from interface.controller import ActionDef, ActionMapper, RecordingBackend

        frames = [np.full((48, 64, 3), v, dtype=np.uint8) for v in (10, 20)]
        capture = ScreenCapture(SyntheticBackend(frames), 0, 0, 64, 48, 64, 48)
        game = GameInterface(capture, ActionMapper(RecordingBackend(), [ActionDef("NOOP")]))
        composite = CompositeReward({
            "event": EventReward(lambda p, c: True, 2.0),
            "survival": SurvivalReward(0.5),
        })
        import torch as _torch

        _torch.manual_seed(0)
        model = ActorCritic(num_actions=1, in_channels=4, feature_dim=16, hidden_size=8)
        config = PPOConfig(rollout_length=4, minibatch_size=4, update_epochs=1,
                           total_timesteps=4, checkpoint_every_updates=1000)
        trainer = PPOTrainer(
            ExternalGameEnv(game, composite, NeverTerminateProvider(), lifecycle=None),
            model, config)
        history = trainer.train()
        self.assertEqual(history["components"][-1], {"event": 2.0, "survival": 0.5})
        self.assertEqual(history["upd_episodes"][-1], 0)  # no boundaries crossed
        self.assertEqual(history["upd_mean_total"][-1], 0.0)

    def test_history_keys_present(self):
        script = [(1.0, True, False)] * 4
        trainer = _trainer(script, rollout_length=2, total=4)
        history = trainer.train()
        for key in ("upd_episodes", "upd_mean_length", "upd_terminated", "upd_truncated",
                    "upd_mean_ext", "upd_mean_int", "upd_mean_total", "components",
                    "mean_ext_reward", "mean_int_reward"):
            self.assertIn(key, history)
            self.assertTrue(len(history[key]) > 0)
        self.assertEqual(history["upd_terminated"], [2, 2])
        self.assertEqual(history["upd_truncated"], [0, 0])


if __name__ == "__main__":
    unittest.main()
