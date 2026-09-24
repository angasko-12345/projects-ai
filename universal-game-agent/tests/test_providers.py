"""Tests for the split reward/termination provider contracts."""
import unittest

import numpy as np

from environment.external_game import ExternalGameEnv
from environment.reward import NullReward, NullRewardProvider, RewardProvider
from environment.termination import (
    NaturalTerminationProvider,
    NeverTerminateProvider,
    StepLimitTermination,
    TerminationProvider,
)


def _frame(value=0):
    return np.full((48, 64, 3), value, dtype=np.uint8)


class FakeInterface:
    def __init__(self, frames, num_actions=2):
        self.frames = list(frames)
        self.captures = 0
        self._num_actions = num_actions

    @property
    def num_actions(self):
        return self._num_actions

    def capture(self):
        frame = self.frames[min(self.captures, len(self.frames) - 1)]
        self.captures += 1
        return frame.copy()

    def execute(self, action):
        if isinstance(action, bool) or not isinstance(action, int) or not 0 <= action < self._num_actions:
            raise ValueError(f"invalid action {action!r}")
        return "x"


class BrightFrameTermination(NaturalTerminationProvider):
    """Natural termination when the frame is bright (pixels only, no game state)."""

    def terminated(self, frame, context: int) -> bool:
        return bool(frame.mean() > 127)


class TestProviders(unittest.TestCase):
    def _env(self, frames, reward, term, **overrides):
        args = {"interface": FakeInterface(frames), "reward_provider": reward,
                "termination_provider": term, "lifecycle": None}
        args.update(overrides)
        return ExternalGameEnv(**args)

    def test_null_reward_is_zero(self):
        self.assertIs(NullReward, NullRewardProvider)
        self.assertTrue(issubclass(NullRewardProvider, RewardProvider))
        env = self._env([_frame(10), _frame(20)], NullRewardProvider(), NeverTerminateProvider())
        env.reset(seed=0)
        _, reward, _, _, _ = env.step(0)
        self.assertEqual(reward, 0.0)

    def test_custom_reward_provider(self):
        class ChangeReward(RewardProvider):
            def reward(self, previous_frame, current_frame, context: int) -> float:
                return float(abs(int(current_frame.mean()) - int(previous_frame.mean())))

        env = self._env([_frame(10), _frame(60)], ChangeReward(), NeverTerminateProvider())
        env.reset(seed=0)
        _, reward, _, _, _ = env.step(1)
        self.assertEqual(reward, 50.0)

    def test_natural_termination(self):
        env = self._env([_frame(10), _frame(200)], NullRewardProvider(), BrightFrameTermination())
        self.assertTrue(issubclass(BrightFrameTermination, TerminationProvider))
        env.reset(seed=0)
        _, _, terminated, truncated, _ = env.step(0)
        self.assertTrue(terminated)
        self.assertFalse(truncated)

    def test_never_terminate_with_timeout(self):
        env = self._env([_frame(10)] * 4, NullRewardProvider(), NeverTerminateProvider(),
                        max_episode_steps=2)
        env.reset(seed=0)
        _, _, term1, trunc1, _ = env.step(0)
        _, _, term2, trunc2, _ = env.step(0)
        self.assertEqual((term1, trunc1), (False, False))
        self.assertEqual((term2, trunc2), (False, True))  # timeout, not termination

    def test_reward_and_termination_together(self):
        class HitReward(RewardProvider):
            def reward(self, previous_frame, current_frame, context: int) -> float:
                return 1.0 if current_frame.mean() > 127 else 0.0

        env = self._env([_frame(10), _frame(200)], HitReward(), BrightFrameTermination())
        env.reset(seed=0)
        _, reward, terminated, truncated, _ = env.step(0)
        self.assertEqual((reward, terminated, truncated), (1.0, True, False))

    def test_reset_state(self):
        env = self._env([_frame(10), _frame(20)], NullRewardProvider(),
                        StepLimitTermination(max_steps=2))
        env.reset(seed=0)
        env.step(0)
        _, _, _, trunc, _ = env.step(0)
        self.assertTrue(trunc)
        obs, info = env.reset(seed=1)  # fresh stack, cleared counters
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(info, {})
        _, _, _, trunc2, _ = env.step(0)
        self.assertFalse(trunc2)  # step budget restarted

    def test_provider_replacement(self):
        env = self._env([_frame(10), _frame(20)], NullRewardProvider(), NeverTerminateProvider())
        env.reset(seed=0)
        _, r0, _, _, _ = env.step(0)
        env.reward_provider = NullRewardProvider()
        env.termination_provider = StepLimitTermination(max_steps=100)
        _, r1, term1, trunc1, _ = env.step(0)
        self.assertEqual((r0, r1, term1, trunc1), (0.0, 0.0, False, False))

    def test_step_limit_is_truncation_only(self):
        term = StepLimitTermination(max_steps=1)
        self.assertEqual(term.done(_frame(0), _frame(0), 0, 1), (False, True))
        with self.assertRaises(ValueError):
            StepLimitTermination(max_steps=0)


if __name__ == "__main__":
    unittest.main()
