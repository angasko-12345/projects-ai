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

    def test_hit_rising_and_falling_edges(self):
        white, red = _ball(), _ball((255, 0, 0))
        self.assertEqual(self.detector.reward(white, red, 1), 1.0)   # toggle on
        self.assertEqual(self.detector.reward(red, white, 1), 1.0)   # toggle off
        self.assertEqual(self.detector.reward(red, red, 1), 0.0)     # latched: no farm
        self.assertEqual(self.detector.reward(white, white, 1), 0.0)

    def test_miss(self):
        self.assertEqual(self.detector.reward(_ball(), _banner(), 0), -1.0)
        term = ExternPongTermination()
        self.assertTrue(term.terminated(_banner(), 0))
        self.assertFalse(term.terminated(_ball((255, 0, 0)), 0))  # flash is not terminal

    def test_reset_after_banner(self):
        self.assertEqual(self.detector.reward(_banner(), _frame(), 0), 0.0)
        self.assertFalse(ExternPongTermination().terminated(_frame(), 0))

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


if __name__ == "__main__":
    unittest.main()
