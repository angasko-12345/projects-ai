"""Tests for the game-agnostic visual observation pipeline."""
import unittest

import numpy as np

from environment.preprocessing import (
    FrameStack,
    PreprocessingWrapper,
    preprocess_frame,
)


def _rgb(h=48, w=64, color=(0, 0, 0)):
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :] = color
    return frame


class TestPreprocessFrame(unittest.TestCase):
    def test_output_shape_dtype_range(self):
        obs = preprocess_frame(_rgb())
        self.assertEqual(obs.shape, (84, 84))
        self.assertEqual(obs.dtype, np.float32)
        self.assertGreaterEqual(obs.min(), 0.0)
        self.assertLessEqual(obs.max(), 1.0)

    def test_custom_size(self):
        self.assertEqual(preprocess_frame(_rgb(), size=42).shape, (42, 42))

    def test_grayscale_weights(self):
        red = _rgb(h=8, w=8, color=(255, 0, 0))
        green = _rgb(h=8, w=8, color=(0, 255, 0))
        blue = _rgb(h=8, w=8, color=(0, 0, 255))
        self.assertAlmostEqual(float(preprocess_frame(red, size=8).mean()), 0.299, places=2)
        self.assertAlmostEqual(float(preprocess_frame(green, size=8).mean()), 0.587, places=2)
        self.assertAlmostEqual(float(preprocess_frame(blue, size=8).mean()), 0.114, places=2)

    def test_black_white_endpoints(self):
        self.assertEqual(preprocess_frame(_rgb()).max(), 0.0)
        self.assertEqual(preprocess_frame(_rgb(color=(255, 255, 255))).min(), 1.0)

    def test_resize_uniform_stays_uniform(self):
        for h, w in ((48, 64), (100, 30), (84, 84)):
            obs = preprocess_frame(_rgb(h, w, color=(128, 128, 128)))
            self.assertEqual(obs.shape, (84, 84))
            self.assertTrue(np.allclose(obs, 128 / 255, atol=1e-5), (h, w))

    def test_resize_preserves_spatial_info(self):
        frame = _rgb()
        frame[:, 32:] = (255, 255, 255)  # bright right half
        obs = preprocess_frame(frame)
        self.assertGreater(obs[:, 42:].mean(), obs[:, :42].mean())

    def test_invalid_inputs(self):
        with self.assertRaises(TypeError):
            preprocess_frame([[0, 0, 0]])
        with self.assertRaises(ValueError):
            preprocess_frame(np.zeros((84, 84), dtype=np.uint8))  # 2D
        with self.assertRaises(ValueError):
            preprocess_frame(np.zeros((84, 84, 4), dtype=np.uint8))  # RGBA
        with self.assertRaises(ValueError):
            preprocess_frame(np.zeros((84, 84, 3), dtype=np.float32))  # not uint8
        with self.assertRaises(ValueError):
            preprocess_frame(np.zeros((0, 64, 3), dtype=np.uint8))  # empty
        with self.assertRaises(ValueError):
            preprocess_frame(_rgb(), size=0)
        with self.assertRaises(ValueError):
            preprocess_frame(_rgb(), size=-4)

    def test_no_game_semantics_produced(self):
        obs = preprocess_frame(_rgb())
        self.assertIsInstance(obs, np.ndarray)
        self.assertEqual(obs.ndim, 2)  # plain intensity grid, nothing symbolic


class TestFrameStack(unittest.TestCase):
    def test_reset_fills_and_shape(self):
        stack = FrameStack(num_stack=4)
        obs = stack.reset(_rgb())
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(obs.dtype, np.float32)
        self.assertTrue(np.all(obs[0] == obs[1]))

    def test_push_orders_newest_last(self):
        stack = FrameStack(num_stack=3)
        stack.reset(_rgb())
        white = _rgb(color=(255, 255, 255))
        obs = stack.push(white)
        self.assertTrue(np.all(obs[-1] == 1.0))
        self.assertTrue(np.all(obs[0] == 0.0))

    def test_push_before_reset_pads(self):
        stack = FrameStack(num_stack=4)
        obs = stack.push(_rgb(color=(255, 255, 255)))
        self.assertEqual(obs.shape, (4, 84, 84))

    def test_get_before_reset_raises(self):
        with self.assertRaises(RuntimeError):
            FrameStack().get()

    def test_invalid_depth(self):
        with self.assertRaises(ValueError):
            FrameStack(num_stack=0)


class TestPreprocessingWrapper(unittest.TestCase):
    def _wrapped(self, **kwargs):
        from environment.toy_pong import ToyPongEnv

        return PreprocessingWrapper(ToyPongEnv(), **kwargs)

    def test_obs_is_network_ready(self):
        env = self._wrapped()
        obs, info = env.reset(seed=0)
        self.assertEqual(obs.shape, (4, 84, 84))
    def test_skip_repeats_and_sums(self):
        three = self._wrapped(skip=3)
        three.reset(seed=7)
        _, r3, _, _, _ = three.step(0)
        probe = self._wrapped(skip=1)
        probe.reset(seed=7)
        parts = [probe.step(0)[1] for _ in range(3)]
        self.assertAlmostEqual(r3, sum(parts))

    def test_skip_episode_finishes(self):
        env = self._wrapped(skip=4)
        obs, _ = env.reset(seed=0)
        for _ in range(300):
            obs, _, terminated, truncated, _ = env.step(0)
            if terminated or truncated:
                break
        else:
            self.fail("skipped episode never finished")
        self.assertEqual(obs.shape, (4, 84, 84))

    def test_toy_frames_drive_stack(self):
        from environment.toy_pong import ToyPongEnv

        raw_env = ToyPongEnv()
        raw, _ = raw_env.reset(seed=0)
        stack = FrameStack(num_stack=2)
        stacked = stack.reset(raw)
        self.assertEqual(stacked.shape, (2, 84, 84))
        self.assertGreater(stacked.max(), 0.0)  # paddle/ball pixels survive

    def test_to_torch_optional(self):
        import importlib.util

        if importlib.util.find_spec("torch") is None:
            self.skipTest("torch not installed")
        from environment.preprocessing import to_torch

        t = to_torch(np.zeros((4, 84, 84), dtype=np.float32))
        self.assertEqual(tuple(t.shape), (4, 84, 84))


if __name__ == "__main__":
    unittest.main()
