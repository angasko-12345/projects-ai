"""Tests for the external OS-loop driver (fakes only, no windows/input)."""
import unittest

import numpy as np


def _frame(value=0):
    return np.full((48, 64, 3), value, dtype=np.uint8)


class FakeInterface:
    def __init__(self, frames, num_actions=3):
        self.frames = list(frames)
        self.captures = 0
        self.executed = []
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
        self.executed.append(action)
        return "x"


def _needs_torch(test):
    import importlib.util

    if importlib.util.find_spec("torch") is None:
        return unittest.skip("torch not installed")(test)
    return test


class TestDriveLoop(unittest.TestCase):
    def _driver(self, steps=8, **kwargs):
        import torch
        from agent.model import ActorCritic
        from environment.external_game import ExternalGameEnv, NullRewardProvider, StepLimitTermination
        from training.external_smoke import drive_loop

        torch.manual_seed(0)
        env = ExternalGameEnv(
            FakeInterface([_frame(10), _frame(200)]),
            NullRewardProvider(), StepLimitTermination(max_steps=3),
            lifecycle=None)
        model = ActorCritic(num_actions=3, in_channels=4, feature_dim=32, hidden_size=16)
        return drive_loop(env, model, steps, **kwargs), env

    @_needs_torch
    def test_fixed_sequence_respected(self):
        rep, env = self._driver(steps=8, mode="fixed", seed=0)
        self.assertEqual(rep["actions_sent"], {0: 3, 1: 3, 2: 2})
        self.assertEqual(env.interface.executed,
                         [0, 1, 1, 2, 0, 2, 1, 0][:8])

    @_needs_torch
    def test_report_structure(self):
        rep, _ = self._driver(steps=7, mode="fixed", seed=1)
        self.assertEqual(rep["steps"], 7)
        self.assertEqual(rep["decisions"], 7)
        self.assertEqual(rep["frames_captured"], 8)
        self.assertEqual(rep["episodes"], 2)  # truncations at steps 3 and 6
        self.assertEqual(rep["truncated"], 2)
        self.assertEqual(rep["terminated"], 0)
        self.assertEqual(rep["logits_shape"], (1, 3))
        self.assertGreaterEqual(rep["mean_pixel_delta"], 0.0)
        self.assertGreater(rep["elapsed_s"], 0.0)
        self.assertGreater(rep["fps"], 0.0)

    @_needs_torch
    def test_random_mode_completes(self):
        rep, _ = self._driver(steps=6, mode="random", seed=2)
        self.assertEqual(sum(rep["actions_sent"].values()), 6)
        self.assertTrue(all(a in (0, 1, 2) for a in rep["actions_sent"]))

    def test_action_table_layout(self):
        from training.external_smoke import build_action_table

        table = build_action_table()
        self.assertEqual([a.name for a in table], ["NOOP", "PRESS_LEFT", "PRESS_RIGHT"])
        self.assertEqual((table[1].vk, table[2].vk), (0x25, 0x27))

    @_needs_torch
    def test_drive_loop_restores_train_mode(self):
        import torch
        from agent.model import ActorCritic
        from environment.external_game import ExternalGameEnv, NullRewardProvider, StepLimitTermination
        from training.external_smoke import drive_loop

        torch.manual_seed(0)
        env = ExternalGameEnv(
            FakeInterface([_frame(10), _frame(200)]),
            NullRewardProvider(), StepLimitTermination(max_steps=10),
            lifecycle=None)
        model = ActorCritic(num_actions=3, in_channels=4, feature_dim=32, hidden_size=16)
        model.train()
        drive_loop(env, model, steps=2, mode="fixed", seed=0)
        self.assertTrue(model.training)

    @_needs_torch
    def test_drive_loop_rejects_nonpositive_steps(self):
        import torch
        from agent.model import ActorCritic
        from environment.external_game import ExternalGameEnv, NullRewardProvider, StepLimitTermination
        from training.external_smoke import drive_loop

        torch.manual_seed(0)
        env = ExternalGameEnv(
            FakeInterface([_frame(10)]),
            NullRewardProvider(), StepLimitTermination(max_steps=10),
            lifecycle=None)
        model = ActorCritic(num_actions=3, in_channels=4, feature_dim=32, hidden_size=16)
        with self.assertRaises(ValueError):
            drive_loop(env, model, steps=0)

if __name__ == "__main__":
    unittest.main()
