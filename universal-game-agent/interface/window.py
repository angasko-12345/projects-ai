"""Game-window/session management (Windows only, stdlib ctypes).

Finds windows by title substring, reports liveness and geometry, best-effort
focus, and external process restart. Never reads game memory or APIs --
only OS window handles and rectangles.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes


class WindowNotFoundError(LookupError):
    pass


class WindowLostError(RuntimeError):
    pass


def _user32():
    if os.name != "nt":
        raise OSError("WindowManager needs Windows (os.name=%r)" % os.name)
    return ctypes.windll.user32


class _Rect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class WindowManager:
    """Owns at most one target window handle. All geometry is re-queried."""

    def __init__(self, title_substring: str):
        if not title_substring:
            raise ValueError("title_substring must be non-empty")
        self.title_substring = title_substring
        self._hwnd: int | None = None

    def attach(self) -> int:
        """Bind to the first visible window whose title contains the substring."""
        user32 = _user32()
        found: list[int] = []
        needle = self.title_substring.lower()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if needle in buf.value.lower():
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(callback, 0)
        if not found:
            raise WindowNotFoundError(f"no visible window titled like {self.title_substring!r}")
        self._hwnd = found[0]
        return self._hwnd

    def detach(self) -> None:
        self._hwnd = None

    @property
    def attached(self) -> bool:
        return self._hwnd is not None

    def is_alive(self) -> bool:
        return self._hwnd is not None and bool(_user32().IsWindow(self._hwnd))

    def _require(self) -> int:
        if self._hwnd is None:
            raise WindowLostError("no window attached")
        if not _user32().IsWindow(self._hwnd):
            raise WindowLostError("target window is gone")
        return self._hwnd

    def rect(self) -> tuple[int, int, int, int]:
        """Current (left, top, width, height); follows moves/resizes."""
        hwnd = self._require()
        rect = _Rect()
        if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
            raise WindowLostError("could not read window rect")
        return (rect.left, rect.top, max(0, rect.right - rect.left), max(0, rect.bottom - rect.top))

    def focus(self) -> bool:
        """Bring to foreground. False (never an exception) if that fails."""
        try:
            hwnd = self._require()
        except WindowLostError:
            return False
        try:
            return bool(_user32().SetForegroundWindow(hwnd))
        except OSError:
            return False

    def restart(self, command: list[str]) -> subprocess.Popen:
        """Relaunch the game externally; caller re-attaches when it appears."""
        if not command:
            raise ValueError("command must be a non-empty argv list")
        self.detach()
        return subprocess.Popen(command)  # noqa: S603 -- explicit local launch
