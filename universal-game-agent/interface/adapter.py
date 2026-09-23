"""Game-agnostic adapter: pixels in, abstract actions out.

Composes a capture source, an action mapper, and an optional window manager::

    capture = WindowCapture(window, MSSBackend(), 320, 240)
    game = GameInterface(capture, ActionMapper(SendInputBackend()), window)
    pixels = game.capture()      # uint8 HxWx3 RGB only -- no game metadata
    game.execute(3)              # abstract action index

Swap games by constructing a different capture/mapper pair; the network
and training code never change.
"""
from __future__ import annotations

import numpy as np


class GameInterface:
    def __init__(self, capture, controller, window=None):
        self.capture_source = capture
        self.controller = controller
        self.window = window

    def capture(self) -> np.ndarray:
        frame = self.capture_source.capture()
        if not isinstance(frame, np.ndarray):
            raise ValueError("capture source must return pixels")
        return frame

    def execute(self, action: int) -> str:
        return self.controller.execute(action)

    @property
    def num_actions(self) -> int:
        return self.controller.num_actions

    def ensure_focused(self) -> bool:
        """Best-effort foreground; True when there is nothing to focus."""
        if self.window is None:
            return True
        return bool(self.window.focus())

    def alive(self) -> bool:
        if self.window is None:
            return True
        return bool(self.window.is_alive())
