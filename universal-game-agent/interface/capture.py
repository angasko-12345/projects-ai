"""Screen capture returning rendered pixels only (numpy uint8 HxWx3 RGB).

Sources: :class:`MSSBackend` (real screen, needs the ``mss`` package),
:class:`SyntheticBackend` (scripted frames for tests/demos without a display).
:class:`ScreenCapture` grabs a fixed region; :class:`WindowCapture` re-reads
the window rect on every frame so moves/resizes are followed. ``capture()``
returns nothing but pixels -- no titles, rects, or other metadata.
"""
from __future__ import annotations

import numpy as np


class CaptureError(RuntimeError):
    pass


def resize_rgb(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """Bilinear resize of a uint8 RGB frame to (height, width, 3)."""
    if width <= 0 or height <= 0:
        raise ValueError(f"target size must be positive, got {(width, height)!r}")
    h, w, _ = frame.shape
    if (h, w) == (height, width):
        return frame.copy()
    src = frame.astype(np.float64)
    rows = np.clip((np.arange(height) + 0.5) * h / height - 0.5, 0, h - 1)
    cols = np.clip((np.arange(width) + 0.5) * w / width - 0.5, 0, w - 1)
    r0 = np.floor(rows).astype(int)
    c0 = np.floor(cols).astype(int)
    r1 = np.minimum(r0 + 1, h - 1)
    c1 = np.minimum(c0 + 1, w - 1)
    dr, dc = (rows - r0)[:, None, None], (cols - c0)[None, :, None]
    out = (
        src[r0[:, None], c0[None, :]] * (1 - dr) * (1 - dc)
        + src[r0[:, None], c1[None, :]] * (1 - dr) * dc
        + src[r1[:, None], c0[None, :]] * dr * (1 - dc)
        + src[r1[:, None], c1[None, :]] * dr * dc
    )
    return np.clip(out, 0, 255).astype(np.uint8)


class MSSBackend:
    """Real screen grabber. ``bbox`` is (left, top, width, height)."""

    def __init__(self):
        try:
            import mss
        except ImportError as exc:
            raise CaptureError("the 'mss' package is required (pip install mss)") from exc
        self._mss = mss.mss()

    def grab(self, bbox: tuple[int, int, int, int]) -> np.ndarray:
        left, top, width, height = (int(v) for v in bbox)
        if width <= 0 or height <= 0:
            raise ValueError(f"bbox must have positive size, got {bbox!r}")
        shot = self._mss.grab({"left": left, "top": top, "width": width, "height": height})
        return np.asarray(shot, dtype=np.uint8)[:, :, :3][:, :, ::-1].copy()  # BGRA -> RGB

    def close(self) -> None:
        self._mss.close()


class SyntheticBackend:
    """Cycling scripted frames. No display needed; for tests and demos."""

    def __init__(self, frames: list[np.ndarray]):
        if not frames:
            raise ValueError("need at least one frame")
        self._frames = [np.ascontiguousarray(f, dtype=np.uint8) for f in frames]
        self.calls = 0

    def grab(self, bbox) -> np.ndarray:
        frame = self._frames[self.calls % len(self._frames)]
        self.calls += 1
        return frame.copy()


def _check_pixels(frame: np.ndarray) -> np.ndarray:
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"backend must return HxWx3 RGB, got {type(frame)} {getattr(frame, 'shape', None)}")
    if frame.dtype != np.uint8:
        raise ValueError(f"backend must return uint8, got {frame.dtype}")
    return frame


class ScreenCapture:
    """Fixed screen region -> RGB pixels.

    With out_width/out_height set, frames are resized to that resolution;
    with both None (default), native-resolution frames pass through so
    downstream detectors see full detail.
    """

    def __init__(self, backend, x: int, y: int, width: int, height: int,
                 out_width: int | None = None, out_height: int | None = None):
        if width <= 0 or height <= 0:
            raise ValueError(f"region must be positive, got {(width, height)!r}")
        if (out_width is None) != (out_height is None):
            raise ValueError("out_width and out_height must both be set or both None")
        self.backend = backend
        self.region = (int(x), int(y), int(width), int(height))
        self.out_size = None if out_width is None else (int(out_width), int(out_height))

    def capture(self) -> np.ndarray:
        frame = _check_pixels(self.backend.grab(self.region))
        return frame if self.out_size is None else resize_rgb(frame, *self.out_size)


class WindowCapture:
    """Target top-level window -> RGB pixels; rect re-read every frame.

    Uses the full window frame (GetWindowRect, including title bar and
    borders), not the client area alone. With out size unset, native
    frames pass through for full-detail reward/termination detection;
    the model observation path resizes downstream.
    """

    def __init__(self, window, backend, out_width: int | None = None, out_height: int | None = None):
        if (out_width is None) != (out_height is None):
            raise ValueError("out_width and out_height must both be set or both None")
        self.window = window
        self.backend = backend
        self.out_size = None if out_width is None else (int(out_width), int(out_height))

    def capture(self) -> np.ndarray:
        left, top, width, height = self.window.rect()  # raises if the window is gone
        if width <= 0 or height <= 0:
            raise CaptureError(f"window has no area: {(width, height)!r}")
        frame = _check_pixels(self.backend.grab((left, top, width, height)))
        return frame if self.out_size is None else resize_rgb(frame, *self.out_size)
