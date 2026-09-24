"""Tests for config-driven reward construction. No live capture."""
import unittest

import numpy as np

from environment.reward import (
    CompositeReward,
    NullRewardProvider,
    make_detector,
    make_reward_from_config,
)


def _frame(value=0):
    return np.full((32, 32, 3), value, dtype=np.uint8)


def _red(h=6, w=6):
    frame = _frame()
    frame[0:h, 0:w] = (255, 0, 0)
    return frame


PONG_LIKE = {
    "provider": "composite",
    "components": [
        {"type": "event", "name": "paddle_hit", "value": 1.0,
         "detector": {"kind": "red_edge", "min": 8, "max": 200}},
        {"type": "terminal", "name": "miss", "value": -1.0,
         "detector": {"kind": "red_present", "min": 300}},
        {"type": "survival", "name": "alive", "value": 0.0},
    ],
}


class TestRewardFactory(unittest.TestCase):
    def test_null_default(self):
        self.assertIsInstance(make_reward_from_config({}), NullRewardProvider)
        self.assertIsInstance(make_reward_from_config({"provider": "null"}), NullRewardProvider)
        self.assertEqual(make_reward_from_config({}).reward(_frame(), _frame(), 0), 0.0)

    def test_pong_like_config(self):
        import copy

        provider = make_reward_from_config(copy.deepcopy(PONG_LIKE))
        self.assertIsInstance(provider, CompositeReward)
        white, red = _frame(), _red()
        self.assertEqual(provider.reward(white, red, 0), 1.0)   # hit edge
        self.assertEqual(provider.reward(red, red, 0), 0.0)     # latched
        banner = np.full((32, 32, 3), 0, dtype=np.uint8)
        banner[:, :] = (255, 0, 0)  # 1024 red px -> miss
        self.assertEqual(provider.reward(white, banner, 0), -1.0)
        parts = provider.breakdown(white, banner, 0).components
        self.assertEqual(parts, {"paddle_hit": 0.0, "alive": 0.0, "miss": -1.0})

    def test_reconfig_without_code_change(self):
        import copy

        generous = copy.deepcopy(PONG_LIKE)
        generous["components"][0]["value"] = 5.0
        self.assertEqual(make_reward_from_config(generous).reward(_frame(), _red(), 0), 5.0)
        self.assertEqual(make_reward_from_config(copy.deepcopy(PONG_LIKE)).reward(_frame(), _red(), 0), 1.0)

    def test_invalid_type_rejected(self):
        with self.assertRaises(ValueError):
            make_reward_from_config({"provider": "karma"})
        with self.assertRaises(ValueError):
            make_reward_from_config({"components": [{"type": "teleport", "value": 1.0}]})
        with self.assertRaises(ValueError):
            make_detector({"kind": "aura"})
        with self.assertRaises(ValueError):
            make_detector("red_edge")

    def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            make_reward_from_config({"components": []})
        with self.assertRaises(ValueError):
            make_reward_from_config({"components": [
                {"type": "event", "name": "x", "value": 1.0}]})  # detector missing
        with self.assertRaises(ValueError):
            make_detector({"kind": "red_edge", "min": 50, "max": 5})
        with self.assertRaises(ValueError):
            make_reward_from_config({"components": "just vibes"})

    def test_unknown_keys_warn(self):
        with self.assertWarnsRegex(UserWarning, "reward.*bogus"):
            make_reward_from_config({"provider": "null", "bogus": 1})
        with self.assertWarnsRegex(UserWarning, "detector.*bogus"):
            make_detector({"kind": "red_present", "bogus": 1})
        with self.assertWarnsRegex(UserWarning, "component h.*bogus"):
            make_reward_from_config({"components": [
                {"type": "survival", "name": "h", "value": 0.0, "bogus": 2}]})

    def test_progress_from_config(self):
        provider = make_reward_from_config({"components": [
            {"type": "progress", "name": "advance", "scale": 0.5,
             "meter": {"kind": "brightness"}}]})
        self.assertAlmostEqual(provider.reward(_frame(0), _frame(100), 0), 50.0)

    def test_factory_matches_handbuilt_extern_pong(self):
        import copy

        from environment.extern_pong_rewards import ExternPongReward

        mine = make_reward_from_config(copy.deepcopy(PONG_LIKE))
        ref = ExternPongReward()
        pairs = [(_frame(), _red()), (_red(), _red()), (_frame(), _frame()),
                 (_frame(), np.full((32, 32, 3), 0, dtype=np.uint8))]
        banner = np.full((32, 32, 3), 0, dtype=np.uint8)
        banner[:, :] = (255, 0, 0)
        pairs.append((_frame(), banner))
        for prev, cur in pairs:
            self.assertEqual(mine.reward(prev, cur, 0), ref.reward(prev, cur, 0))

    def test_env_reports_breakdown(self):
        from environment.external_game import ExternalGameEnv
        from environment.termination import NeverTerminateProvider
        from interface.adapter import GameInterface
        from interface.capture import ScreenCapture, SyntheticBackend
        from interface.controller import ActionDef, ActionMapper, RecordingBackend

        import copy

        frames = [np.full((48, 64, 3), 10, dtype=np.uint8),
                  np.full((48, 64, 3), 200, dtype=np.uint8)]
        capture = ScreenCapture(SyntheticBackend(frames), 0, 0, 64, 48, 64, 48)
        game = GameInterface(capture, ActionMapper(RecordingBackend(), [ActionDef("NOOP")]))
        provider = make_reward_from_config(copy.deepcopy(PONG_LIKE))
        env = ExternalGameEnv(game, provider, NeverTerminateProvider(), lifecycle=None)
        self.assertIsNone(env.last_breakdown)
        env.reset(seed=0)
        _, reward, _, _, _ = env.step(0)
        self.assertIsNotNone(env.last_breakdown)
        self.assertAlmostEqual(env.last_breakdown.total, reward)
        self.assertEqual(set(env.last_breakdown.components), {"paddle_hit", "miss", "alive"})


if __name__ == "__main__":
    unittest.main()
