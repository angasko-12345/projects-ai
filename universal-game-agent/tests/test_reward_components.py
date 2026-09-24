"""Tests for composable external rewards. Exact numbers, no live capture."""
import unittest

import numpy as np

from environment.reward import (
    CompositeReward,
    EventReward,
    NullRewardProvider,
    ProgressReward,
    RewardProvider,
    RewardResult,
    SurvivalReward,
    TerminalPenalty,
)


def _frame(value=0):
    return np.full((16, 16, 3), value, dtype=np.uint8)


def _bright(value=255):
    return np.full((16, 16, 3), value, dtype=np.uint8)


class StatefulDouble(RewardProvider):
    """Counts calls; proves reset propagation. Test-only stand-in."""

    def __init__(self):
        self.calls = 0

    def reward(self, previous_frame, current_frame, context: int) -> float:
        self.calls += 1
        return 0.0

    def reset(self) -> None:
        self.calls = 0


class TestComponents(unittest.TestCase):
    def test_event_exact(self):
        comp = EventReward(lambda p, c: bool((c.mean() - p.mean()) > 10), 2.5)
        self.assertEqual(comp.reward(_frame(0), _frame(100), 0), 2.5)
        self.assertEqual(comp.reward(_frame(0), _frame(5), 0), 0.0)

    def test_terminal_exact(self):
        comp = TerminalPenalty(lambda p, c: bool(c.mean() > 200), -5.0)
        self.assertEqual(comp.reward(_frame(0), _frame(255), 0), -5.0)
        self.assertEqual(comp.reward(_frame(0), _frame(10), 0), 0.0)

    def test_survival_exact(self):
        comp = SurvivalReward(0.1, terminal_detector=lambda p, c: bool(c.mean() > 200))
        self.assertEqual(comp.reward(_frame(0), _frame(10), 0), 0.1)
        self.assertEqual(comp.reward(_frame(0), _frame(255), 0), 0.0)  # terminal: no pay
        self.assertEqual(SurvivalReward(0.1).reward(_frame(0), _frame(255), 0), 0.1)

    def test_progress_exact(self):
        comp = ProgressReward(lambda f: float(f.mean()), scale=0.5)
        self.assertAlmostEqual(comp.reward(_frame(0), _frame(100), 0), 50.0)
        self.assertEqual(comp.reward(_frame(100), _frame(0), 0), 0.0)  # regress clamped

    def test_composite_accounting_exact(self):
        composite = CompositeReward({
            "event": EventReward(lambda p, c: True, 2.0),
            "survival": SurvivalReward(0.5),
            "terminal": TerminalPenalty(lambda p, c: False, -10.0),
        })
        result = composite.breakdown(_frame(0), _frame(0), 1)
        self.assertIsInstance(result, RewardResult)
        self.assertEqual(result.components, {"event": 2.0, "survival": 0.5, "terminal": 0.0})
        self.assertEqual(result.total, 2.5)
        self.assertEqual(composite.reward(_frame(0), _frame(0), 1), 2.5)

    def test_composite_negative_total(self):
        composite = CompositeReward({
            "event": EventReward(lambda p, c: False, 2.0),
            "terminal": TerminalPenalty(lambda p, c: True, -3.0),
        })
        self.assertEqual(composite.reward(_frame(0), _frame(0), 0), -3.0)

    def test_empty_composite_rejected(self):
        with self.assertRaises(ValueError):
            CompositeReward({})
        with self.assertRaises(ValueError):
            CompositeReward({"x": object()})

    def test_reset_propagates_to_stateful(self):
        double = StatefulDouble()
        composite = CompositeReward({"s": double, "z": NullRewardProvider()})
        composite.reward(_frame(0), _frame(0), 0)
        composite.reward(_frame(0), _frame(0), 0)
        self.assertEqual(double.calls, 2)
        composite.reset()
        self.assertEqual(double.calls, 0)

    def test_env_reset_calls_provider_reset(self):
        from environment.external_game import ExternalGameEnv
        from environment.termination import NeverTerminateProvider
        from interface.adapter import GameInterface
        from interface.capture import ScreenCapture, SyntheticBackend
        from interface.controller import ActionDef, ActionMapper, RecordingBackend

        double = StatefulDouble()
        capture = ScreenCapture(SyntheticBackend([_frame(10), _frame(20)]), 0, 0, 16, 16, 16, 16)
        game = GameInterface(capture, ActionMapper(RecordingBackend(), [ActionDef("NOOP")]))
        env = ExternalGameEnv(game, double, NeverTerminateProvider(), lifecycle=None)
        env.reset(seed=0)
        env.step(0)
        env.step(0)
        self.assertEqual(double.calls, 2)
        env.reset(seed=1)  # state must not leak into the new episode
        self.assertEqual(double.calls, 0)

    def test_env_reports_composite_total(self):
        from environment.external_game import ExternalGameEnv
        from environment.termination import NeverTerminateProvider
        from interface.adapter import GameInterface
        from interface.capture import ScreenCapture, SyntheticBackend
        from interface.controller import ActionDef, ActionMapper, RecordingBackend

        composite = CompositeReward({
            "event": EventReward(lambda p, c: True, 1.0),
            "survival": SurvivalReward(0.25),
        })
        capture = ScreenCapture(SyntheticBackend([_frame(10), _frame(20)]), 0, 0, 16, 16, 16, 16)
        game = GameInterface(capture, ActionMapper(RecordingBackend(), [ActionDef("NOOP")]))
        env = ExternalGameEnv(game, composite, NeverTerminateProvider(), lifecycle=None)
        env.reset(seed=0)
        _, reward, _, _, info = env.step(0)
        self.assertEqual(reward, 1.25)  # external total only; curiosity lives in the trainer
        self.assertEqual(info, {})
        self.assertFalse(hasattr(env, "curiosity"))


if __name__ == "__main__":
    unittest.main()
