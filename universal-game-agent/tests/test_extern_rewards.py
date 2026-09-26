"""Tests for screen-only extern-Pong reward/termination (synthetic frames)."""
import unittest

import numpy as np

from environment.extern_pong_rewards import ExternPongReward, ExternPongTermination, red_mask
from environment.reward import RewardProvider
from environment.termination import NaturalTerminationProvider


def _frame(h=48, w=64):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _paint(frame, top, left, h, w, color):
    frame[top:top + h, left:left + w] = color
    return frame


def _ball(color=(255, 255, 255)):
    return _paint(_frame(), 10, 10, 6, 6, color)


def _banner():
    return _paint(_frame(), 10, 5, 30, 54, (255, 0, 0))


class TestRedMask(unittest.TestCase):
    def test_selective(self):
        frame = _frame()
        self.assertEqual(red_mask(frame).sum(), 0)
        white = _paint(_frame(), 0, 0, 6, 6, (255, 255, 255))
        self.assertEqual(red_mask(white).sum(), 0)  # white ball is not red
        gray = _paint(_frame(), 0, 0, 6, 6, (128, 128, 128))
        self.assertEqual(red_mask(gray).sum(), 0)  # score text is not red
        red = _paint(_frame(), 0, 0, 6, 6, (255, 0, 0))
        self.assertEqual(red_mask(red).sum(), 36)


class TestExternPongReward(unittest.TestCase):
    def setUp(self):
        self.detector = ExternPongReward()

    def test_interfaces(self):
        self.assertIsInstance(self.detector, RewardProvider)
        self.assertIsInstance(ExternPongTermination(), NaturalTerminationProvider)

    def test_normal_play_zero(self):
        self.assertEqual(self.detector.reward(_ball(), _ball(), 0), 0.0)
        self.assertEqual(self.detector.reward(_frame(), _frame(), 2), 0.0)

    def test_hit_rising_edge_only(self):
        white, red = _ball(), _ball((255, 0, 0))
        self.assertEqual(self.detector.reward(white, red, 1), 1.0)   # hit: exactly once
        self.assertEqual(self.detector.reward(red, red, 1), 0.0)     # latched: no farm
        self.assertEqual(self.detector.reward(red, white, 1), 0.0)   # serve clears latch: no event
        self.assertEqual(self.detector.reward(white, white, 1), 0.0)

    def test_miss_edge_exactly_once(self):
        white, banner = _ball(), _banner()
        self.assertEqual(self.detector.reward(white, banner, 0), -1.0)  # rising edge pays
        self.assertEqual(self.detector.reward(banner, banner, 0), 0.0)  # held banner pays nothing
        self.assertEqual(self.detector.reward(banner, white, 0), 0.0)   # reset pays nothing

    def test_native_frames_hit_and_miss(self):
        # Native 320x240 game frames: 6px ball in band, banner terminal (never hit).
        plain = np.zeros((240, 320, 3), dtype=np.uint8)
        hit = plain.copy()
        hit[100:106, 100:106] = (255, 0, 0)
        banner = plain.copy()
        banner[100:130, 60:260] = (255, 0, 0)
        self.assertEqual(self.detector.reward(plain, hit, 0), 1.0)
        self.assertEqual(self.detector.reward(hit, hit, 0), 0.0)
        self.assertEqual(self.detector.reward(plain, banner, 0), -1.0)
        term = ExternPongTermination()
        self.assertTrue(term.terminated(banner, 0))
        self.assertFalse(term.terminated(hit, 0))
        self.assertFalse(term.terminated(plain, 0))

    def test_miss(self):
        self.assertEqual(self.detector.reward(_ball(), _banner(), 0), -1.0)
        term = ExternPongTermination()
        self.assertTrue(term.terminated(_banner(), 0))
        self.assertFalse(term.terminated(_ball((255, 0, 0)), 0))  # flash is not terminal

    def test_reset_after_banner(self):
        self.assertEqual(self.detector.reward(_banner(), _frame(), 0), 0.0)
        self.assertFalse(ExternPongTermination().terminated(_frame(), 0))

    def test_env_native_banner_terminates_once(self):
        from environment.external_game import ExternalGameEnv
        from interface.adapter import GameInterface
        from interface.capture import ScreenCapture, SyntheticBackend
        from interface.controller import ActionDef, ActionMapper, RecordingBackend

        plain = np.zeros((240, 320, 3), dtype=np.uint8)
        banner = plain.copy()
        banner[100:130, 60:260] = (255, 0, 0)
        capture = ScreenCapture(SyntheticBackend([plain, banner, plain]), 0, 0, 320, 240)
        game = GameInterface(capture, ActionMapper(RecordingBackend(), [ActionDef("NOOP")]))
        env = ExternalGameEnv(game, ExternPongReward(), ExternPongTermination(), lifecycle=None)
        env.reset(seed=0)
        _, reward, terminated, truncated, _ = env.step(0)
        self.assertEqual(reward, -1.0)
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        env.reset(seed=1)  # new episode: no stale termination or reward
        _, reward2, terminated2, _, _ = env.step(0)
        self.assertEqual(reward2, 0.0)
        self.assertFalse(terminated2)

    def test_noise_ignored(self):
        noisy = _paint(_frame(), 0, 0, 1, 3, (255, 0, 0))  # 3 px < hit_min
        self.assertEqual(self.detector.reward(_frame(), noisy, 0), 0.0)

    def test_ambiguous_size_is_neither(self):
        blob = _paint(_frame(), 0, 0, 15, 15, (255, 0, 0))  # 225 px: between hit_max and miss_min
        self.assertEqual(self.detector.reward(_frame(), blob, 0), 0.0)
        self.assertFalse(ExternPongTermination().terminated(blob, 0))

    def test_context_ignored(self):
        white, red = _ball(), _ball((255, 0, 0))
        self.assertEqual(self.detector.reward(white, red, 0), self.detector.reward(white, red, 2))

    def test_bad_thresholds_rejected(self):
        with self.assertRaises(ValueError):
            ExternPongReward(hit_min=10, hit_max=5, miss_min=300)
        with self.assertRaises(ValueError):
            ExternPongTermination(miss_min=0)

    def test_hit_band_across_capture_sizes(self):
        for size in (48, 96, 128):
            frame = np.zeros((size, size, 3), dtype=np.uint8)
            side = round(6 * size / 48)
            frame[0:side, 0:side] = (255, 0, 0)
            count = int(red_mask(frame).sum())
            if size <= 96:
                self.assertTrue(8 <= count <= 200, (size, count))
            else:
                self.assertGreater(count, 200)  # documents the upper limit

if __name__ == "__main__":
    unittest.main()


class TestExternTerminationIntegration(unittest.TestCase):
    def _env(self, frames, termination, **overrides):
        from interface.adapter import GameInterface
        from interface.capture import ScreenCapture, SyntheticBackend
        from interface.controller import ActionDef, ActionMapper, RecordingBackend
        from environment.external_game import ExternalGameEnv
        from environment.reward import NullRewardProvider

        capture = ScreenCapture(SyntheticBackend(frames), 0, 0, 64, 48, 64, 48)
        game = GameInterface(capture, ActionMapper(RecordingBackend(), [ActionDef("NOOP")]))
        args = {"interface": game, "reward_provider": NullRewardProvider(),
                "termination_provider": termination, "lifecycle": None}
        args.update(overrides)
        return ExternalGameEnv(**args)

    def test_termination_beats_timeout(self):
        from environment.extern_pong_rewards import ExternPongTermination

        env = self._env([_ball(), _banner()], ExternPongTermination(), max_episode_steps=1)
        env.reset(seed=0)
        _, _, terminated, truncated, _ = env.step(0)
        self.assertTrue(terminated)
        self.assertFalse(truncated)  # distinction preserved, not converted

    def test_timeout_without_termination(self):
        from environment.extern_pong_rewards import ExternPongTermination

        env = self._env([_ball(), _ball()], ExternPongTermination(), max_episode_steps=1)
        env.reset(seed=0)
        _, _, terminated, truncated, _ = env.step(0)
        self.assertFalse(terminated)
        self.assertTrue(truncated)

    def test_factory_extern_pong_providers(self):
        from environment.external_game import make_external_env_from_config
        from environment.extern_pong_rewards import ExternPongReward, ExternPongTermination

        cfg = {"capture": {"mode": "synthetic", "out_width": 64, "out_height": 48},
               "reward": {"provider": "extern_pong"},
               "termination": {"provider": "extern_pong"}}
        env = make_external_env_from_config(cfg)()
        self.assertIsInstance(env.reward_provider, ExternPongReward)
        self.assertIsInstance(env.termination_provider, ExternPongTermination)
        env.reset(seed=0)
        obs, _, _, _, _ = env.step(0)
        self.assertEqual(obs.shape, (4, 84, 84))


class TestClassifyHelper(unittest.TestCase):
    def test_classify_boundaries(self):
        from training.reward_diagnostic import classify

        detector = ExternPongReward()
        self.assertEqual(classify(0, detector), "normal")
        self.assertEqual(classify(7, detector), "normal")
        self.assertEqual(classify(8, detector), "hit")
        self.assertEqual(classify(200, detector), "hit")
        self.assertEqual(classify(299, detector), "normal")
        self.assertEqual(classify(300, detector), "terminal")
