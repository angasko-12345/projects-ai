"""Tests for the external game interface. No real input or windows touched."""
import unittest

import numpy as np

from environment.preprocessing import FrameStack, preprocess_frame
from interface.adapter import GameInterface
from interface.capture import ScreenCapture, SyntheticBackend, WindowCapture, resize_rgb
from interface.controller import (
    VK,
    ActionDef,
    ActionMapper,
    RecordingBackend,
    action_names,
    pc_action_table,
)
from interface.window import WindowLostError, WindowManager, WindowNotFoundError


def _rgb(h=48, w=64, color=(10, 20, 30)):
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :] = color
    return frame


class FakeWindow:
    def __init__(self, rect=(0, 0, 64, 48), alive=True):
        self._rect, self._alive = rect, alive
        self.focus_calls = 0

    def rect(self):
        if not self._alive:
            raise WindowLostError("gone")
        return self._rect

    def is_alive(self):
        return self._alive

    def focus(self):
        self.focus_calls += 1
        return self._alive


class TestActionMapper(unittest.TestCase):
    def test_default_table_covers_required_actions(self):
        names = action_names(pc_action_table())
        for required in ("NOOP", "PRESS_W", "PRESS_A", "PRESS_S", "PRESS_D",
                         "PRESS_SPACE", "PRESS_SHIFT", "MOUSE_LEFT", "MOUSE_RIGHT", "MOUSE_MOVE"):
            self.assertIn(required, names)

    def test_key_press_hold_release_sequence(self):
        backend = RecordingBackend()
        table = [ActionDef("NOOP"), ActionDef("PRESS_W", kind="key", vk=VK["W"], hold_ms=0)]
        mapper = ActionMapper(backend, table)
        self.assertEqual(mapper.execute(1), "PRESS_W")
        self.assertEqual(backend.calls, [("key_down", VK["W"]), ("key_up", VK["W"])])
        self.assertEqual(mapper.execute(0), "NOOP")
        self.assertEqual(len(backend.calls), 2)  # noop emits nothing

    def test_mouse_actions(self):
        backend = RecordingBackend()
        table = [
            ActionDef("NOOP"),
            ActionDef("CLICK", kind="mouse_button", button="right", hold_ms=0),
            ActionDef("LOOK", kind="mouse_move", dx=-15, dy=7),
        ]
        mapper = ActionMapper(backend, table)
        mapper.execute(1)
        mapper.execute(2)
        self.assertEqual(backend.calls, [("mouse_down", "right"), ("mouse_up", "right"), ("mouse_move", -15, 7)])

    def test_invalid_actions_rejected(self):
        mapper = ActionMapper(RecordingBackend())
        for bad in (-1, 99, None, "1", 1.5, True):
            with self.assertRaises(ValueError, msg=f"action={bad!r}"):
                mapper.execute(bad)

    def test_custom_table_swaps_games(self):
        table = [ActionDef("NOOP"), ActionDef("JUMP", kind="key", vk=VK["SPACE"], hold_ms=0)]
        mapper = ActionMapper(RecordingBackend(), table)
        self.assertEqual(mapper.num_actions, 2)
        self.assertEqual(mapper.execute(1), "JUMP")

    def test_bad_tables_rejected(self):
        with self.assertRaises(ValueError):
            ActionMapper(RecordingBackend(), [])
        with self.assertRaises(ValueError):
            ActionMapper(RecordingBackend(), [ActionDef("X"), ActionDef("X")])
        with self.assertRaises(ValueError):
            ActionDef("BAD", kind="teleport")
        with self.assertRaises(ValueError):
            ActionDef("NEG", hold_ms=-1)


class TestCapture(unittest.TestCase):
    def test_fixed_region_resizes(self):
        cap = ScreenCapture(SyntheticBackend([_rgb()]), 0, 0, 64, 48, 32, 24)
        frame = cap.capture()
        self.assertEqual(frame.shape, (24, 32, 3))
        self.assertEqual(frame.dtype, np.uint8)

    def test_window_capture_follows_rect(self):
        window = FakeWindow(rect=(10, 10, 64, 48))
        cap = WindowCapture(window, SyntheticBackend([_rgb(48, 64)]), 64, 48)
        self.assertEqual(cap.capture().shape, (48, 64, 3))
        window._rect = (200, 150, 32, 24)  # moved + resized
        self.assertEqual(cap.capture().shape, (48, 64, 3))

    def test_lost_window_raises(self):
        cap = WindowCapture(FakeWindow(alive=False), SyntheticBackend([_rgb()]), 16, 16)
        with self.assertRaises(WindowLostError):
            cap.capture()

    def test_non_pixel_backend_rejected(self):
        cap = ScreenCapture(SyntheticBackend([np.zeros((10, 10), dtype=np.uint8)]), 0, 0, 10, 10, 10, 10)
        with self.assertRaises(ValueError):
            cap.capture()

    def test_resize_rgb_uniform_and_shape(self):
        out = resize_rgb(_rgb(48, 64, color=(200, 100, 50)), 84, 84)
        self.assertEqual(out.shape, (84, 84, 3))
        self.assertTrue(np.allclose(out, (200, 100, 50), atol=2))

    def test_capture_returns_pixels_only(self):
        frame = ScreenCapture(SyntheticBackend([_rgb()]), 0, 0, 64, 48, 64, 48).capture()
        self.assertIsInstance(frame, np.ndarray)
        self.assertEqual(frame.ndim, 3)


class TestObservationPipeline(unittest.TestCase):
    def test_capture_to_network_obs(self):
        cap = ScreenCapture(SyntheticBackend([_rgb(48, 64, color=(255, 255, 255))]), 0, 0, 64, 48, 64, 48)
        stack = FrameStack(num_stack=4)
        obs = stack.reset(cap.capture())
        for _ in range(3):
            obs = stack.push(cap.capture())
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(obs.dtype, np.float32)
        self.assertTrue(np.all(obs == 1.0))

    def test_pixels_only_no_metadata(self):
        frame = ScreenCapture(SyntheticBackend([_rgb()]), 0, 0, 64, 48, 64, 48).capture()
        processed = preprocess_frame(frame)
        self.assertEqual(processed.shape, (84, 84))
        self.assertNotIsInstance(processed, dict)


class TestAdapter(unittest.TestCase):
    def _game(self):
        window = FakeWindow()
        capture = ScreenCapture(SyntheticBackend([_rgb()]), 0, 0, 64, 48, 64, 48)
        backend = RecordingBackend()
        controller = ActionMapper(backend, [ActionDef("NOOP"), ActionDef("GO", kind="key", vk=VK["W"], hold_ms=0)])
        return GameInterface(capture, controller, window), backend, window

    def test_capture_and_execute(self):
        game, backend, _ = self._game()
        self.assertEqual(game.capture().shape, (48, 64, 3))
        self.assertEqual(game.num_actions, 2)
        self.assertEqual(game.execute(1), "GO")
        self.assertEqual(backend.calls, [("key_down", VK["W"]), ("key_up", VK["W"])])

    def test_focus_and_liveness(self):
        game, _, window = self._game()
        self.assertTrue(game.ensure_focused())
        self.assertTrue(game.alive())
        window._alive = False
        self.assertFalse(game.ensure_focused())
        self.assertFalse(game.alive())

    def test_no_window_adapter(self):
        game, _, _ = self._game()
        game.window = None
        self.assertTrue(game.ensure_focused())
        self.assertTrue(game.alive())


class TestWindowManager(unittest.TestCase):
    def test_missing_window_raises(self):
        with self.assertRaises(WindowNotFoundError):
            WindowManager("definitely-not-a-real-window-xyz-123").attach()

    def test_detached_state(self):
        manager = WindowManager("anything")
        self.assertFalse(manager.attached)
        self.assertFalse(manager.is_alive())
        with self.assertRaises(WindowLostError):
            manager.rect()

    def test_empty_title_rejected(self):
        with self.assertRaises(ValueError):
            WindowManager("")

    def test_vk_table(self):
        self.assertEqual((VK["W"], VK["A"], VK["S"], VK["D"]), (0x57, 0x41, 0x53, 0x44))
        self.assertEqual(VK["SPACE"], 0x20)


if __name__ == "__main__":
    unittest.main()
