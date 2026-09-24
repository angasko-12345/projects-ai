"""Mocked ExternalGameEnv <-> PPO end-to-end tests. No real game."""
import unittest

import numpy as np

try:
    import torch

    from agent.model import ActorCritic
    from environment.external_game import ExternalGameEnv
    from environment.reward import CompositeReward, EventReward, NullRewardProvider, SurvivalReward
    from environment.termination import NeverTerminateProvider, StepLimitTermination
    from interface.adapter import GameInterface
    from interface.capture import ScreenCapture, SyntheticBackend
    from interface.controller import ActionDef, ActionMapper, RecordingBackend
    from training.curiosity import CuriosityConfig, CuriosityModule
    from training.evaluate import evaluate
    from training.ppo import PPOConfig, PPOTrainer

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def _game(frames=((10, 20, 30)), num_actions=2):
    capture = ScreenCapture(
        SyntheticBackend([np.full((48, 64, 3), v, dtype=np.uint8) for v in frames]),
        0, 0, 64, 48, 64, 48)
    table = [ActionDef("NOOP", hold_ms=0), ActionDef("GO", kind="key", vk=0x57, hold_ms=0)]
    return GameInterface(capture, ActionMapper(RecordingBackend(), table[:num_actions]))


def _trainer(game=None, reward=None, term=None, total=16, rollout=8, curiosity=None,
             env_config=None):
    torch.manual_seed(0)
    game = game or _game()
    env = ExternalGameEnv(game, reward or NullRewardProvider(),
                          term or StepLimitTermination(max_steps=1000),
                          lifecycle=None)
    model = ActorCritic(num_actions=int(env.action_space.n), in_channels=4,
                        feature_dim=32, hidden_size=16)
    config = PPOConfig(rollout_length=rollout, minibatch_size=rollout, update_epochs=1,
                       total_timesteps=total, checkpoint_every_updates=1000)
    return PPOTrainer(env, model, config, curiosity=curiosity, env_config=env_config)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestExternalTraining(unittest.TestCase):
    def test_update_through_external_env(self):
        trainer = _trainer()
        before = [p.clone() for p in trainer.model.parameters()]
        buf, *_ = trainer.collect_rollout()
        self.assertEqual(tuple(buf["obs"].shape), (8, 4, 84, 84))
        stats = trainer.update(buf)
        for key in ("policy_loss", "value_loss", "entropy"):
            self.assertTrue(abs(stats[key]) != float("inf") and stats[key] == stats[key])
        self.assertTrue(any(not torch.equal(a, b) for a, b in zip(before, trainer.model.parameters())))

    def test_composite_external_reward_flows(self):
        composite = CompositeReward({
            "event": EventReward(lambda p, c: True, 1.0),
            "survival": SurvivalReward(0.5),
        })
        trainer = _trainer(reward=composite)
        buf, *_ = trainer.collect_rollout()
        self.assertTrue(bool((buf["rewards"] == 1.5).all()))
        self.assertEqual(trainer.env.last_breakdown.components, {"event": 1.0, "survival": 0.5})

    def test_curiosity_separate_on_external(self):
        curiosity = CuriosityModule(num_actions=2, in_channels=4,
                                    config=CuriosityConfig(scale=0.5))
        trainer = _trainer(curiosity=curiosity)
        buf, *_ = trainer.collect_rollout()
        self.assertTrue(torch.allclose(buf["rewards"], buf["ext"] + buf["int_rewards"]))
        self.assertTrue(bool((buf["int_rewards"] >= 0).all()))

    def test_short_train_and_eval(self):
        trainer = _trainer(total=16)
        history = trainer.train()
        self.assertEqual(history["timesteps"][-1], 16)
        train_captures = trainer.env.interface.capture_source.backend.calls
        model = trainer.model
        model.eval()
        rep = evaluate(model, _env_fresh, episodes=2, seeds=[0, 1])
        self.assertEqual(len(rep["episode_rewards"]), 2)
        self.assertEqual(trainer.env.interface.capture_source.backend.calls, train_captures)

    def test_checkpoint_env_config_roundtrip(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            trainer = _trainer(env_config={"type": "external", "custom": 1})
            path = trainer.save_checkpoint(str(Path(tmp) / "ckpt.pt"))
            env2 = ExternalGameEnv(_game(), NullRewardProvider(),
                                   StepLimitTermination(max_steps=1000), lifecycle=None)
            resumed = PPOTrainer.load_checkpoint(path, env2)
            self.assertEqual(resumed.env_config, {"type": "external", "custom": 1})
            buf, *_ = resumed.collect_rollout()
            resumed.update(buf)
            self.assertGreater(resumed.num_timesteps, 0)
            legacy = _trainer()
            legacy_path = legacy.save_checkpoint(str(Path(tmp) / "legacy.pt"))
            import torch as _t

            ckpt = _t.load(legacy_path, map_location="cpu", weights_only=True)
            del ckpt["env_config"]
            _t.save(ckpt, legacy_path)
            resumed_legacy = PPOTrainer.load_checkpoint(legacy_path, env2)
            self.assertIsNone(resumed_legacy.env_config)


def _env_fresh():
    return ExternalGameEnv(_game(), NullRewardProvider(),
                           StepLimitTermination(max_steps=16), lifecycle=None)


if __name__ == "__main__":
    unittest.main()
