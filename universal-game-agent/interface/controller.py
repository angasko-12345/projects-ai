"""Generic discrete action -> OS input mapper (Windows SendInput backend).

Action table entries are plain data, so games are swapped by passing a new
table -- never by touching the agent. :class:`RecordingBackend` records
calls for tests. Holds press for ``hold_ms`` then release; mouse moves use
a fixed per-action pixel delta.
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass, field

# Virtual-key codes.
VK = {
    "W": 0x57, "A": 0x41, "S": 0x53, "D": 0x44,
    "SPACE": 0x20, "SHIFT": 0x10, "ESC": 0x1B,
}
MOUSE_BUTTONS = ("left", "right", "middle")


@dataclass(frozen=True)
class ActionDef:
    name: str
    kind: str = "noop"  # noop | key | mouse_button | mouse_move
    vk: int = 0
    button: str = "left"
    dx: int = 0
    dy: int = 0
    hold_ms: int = 80

    def __post_init__(self):
        if self.kind not in ("noop", "key", "mouse_button", "mouse_move"):
            raise ValueError(f"unknown action kind {self.kind!r}")
        if self.kind == "mouse_button" and self.button not in MOUSE_BUTTONS:
            raise ValueError(f"unknown mouse button {self.button!r}")
        if self.hold_ms < 0:
            raise ValueError(f"hold_ms must be >= 0, got {self.hold_ms!r}")


def pc_action_table(mouse_move_delta: tuple[int, int] = (20, 0), hold_ms: int = 80) -> list[ActionDef]:
    """Default generic PC-game table: NOOP, WASD, SPACE, SHIFT, mouse buttons, one mouse move."""
    keys = ["W", "A", "S", "D", "SPACE", "SHIFT"]
    table = [ActionDef("NOOP")]
    table += [ActionDef(f"PRESS_{k}", kind="key", vk=VK[k], hold_ms=hold_ms) for k in keys]
    table += [ActionDef("MOUSE_LEFT", kind="mouse_button", button="left", hold_ms=hold_ms)]
    table += [ActionDef("MOUSE_RIGHT", kind="mouse_button", button="right", hold_ms=hold_ms)]
    dx, dy = mouse_move_delta
    table += [ActionDef("MOUSE_MOVE", kind="mouse_move", dx=dx, dy=dy)]
    return table


def action_names(table: list[ActionDef]) -> list[str]:
    return [a.name for a in table]


# -- backends -------------------------------------------------------------
class _KeyBd(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _Mouse(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyBd), ("mi", _Mouse)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


_KEYEVENTF_KEYUP = 0x0002
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040


class SendInputBackend:
    """Real OS input via SendInput. Constructing on non-Windows raises."""

    def __init__(self):
        if os.name != "nt":
            raise OSError("SendInputBackend needs Windows")
        self._send = ctypes.windll.user32.SendInput

    def _emit(self, inputs: list[_Input]) -> None:
        n = len(inputs)
        arr = (_Input * n)(*inputs)
        if self._send(n, arr, ctypes.sizeof(_Input)) != n:
            raise OSError("SendInput delivered nothing")

    def _key(self, vk: int, up: bool) -> _Input:
        ki = _KeyBd(wVk=vk, wScan=0, dwFlags=_KEYEVENTF_KEYUP if up else 0,
                    time=0, dwExtraInfo=None)
        return _Input(type=1, u=_InputUnion(ki=ki))

    def key_down(self, vk: int) -> None:
        self._emit([self._key(vk, False)])

    def key_up(self, vk: int) -> None:
        self._emit([self._key(vk, True)])

    def mouse_down(self, button: str) -> None:
        self._emit([self._mouse(button, True)])

    def mouse_up(self, button: str) -> None:
        self._emit([self._mouse(button, False)])

    def mouse_move(self, dx: int, dy: int) -> None:
        mi = _Mouse(dx=dx, dy=dy, mouseData=0, dwFlags=_MOUSEEVENTF_MOVE, time=0, dwExtraInfo=None)
        self._emit([_Input(type=0, u=_InputUnion(mi=mi))])

    def _mouse(self, button: str, down: bool) -> _Input:
        flag = {
            ("left", True): _MOUSEEVENTF_LEFTDOWN, ("left", False): _MOUSEEVENTF_LEFTUP,
            ("right", True): _MOUSEEVENTF_RIGHTDOWN, ("right", False): _MOUSEEVENTF_RIGHTUP,
            ("middle", True): _MOUSEEVENTF_MIDDLEDOWN, ("middle", False): _MOUSEEVENTF_MIDDLEUP,
        }[(button, down)]
        mi = _Mouse(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=None)
        return _Input(type=0, u=_InputUnion(mi=mi))


class RecordingBackend:
    """In-memory backend recording every call. For tests and dry runs."""

    def __init__(self):
        self.calls: list[tuple] = []

    def key_down(self, vk: int) -> None:
        self.calls.append(("key_down", vk))

    def key_up(self, vk: int) -> None:
        self.calls.append(("key_up", vk))

    def mouse_down(self, button: str) -> None:
        self.calls.append(("mouse_down", button))

    def mouse_up(self, button: str) -> None:
        self.calls.append(("mouse_up", button))

    def mouse_move(self, dx: int, dy: int) -> None:
        self.calls.append(("mouse_move", dx, dy))


class ActionMapper:
    """Discrete index -> backend input sequence. Index space is the agent's action space."""

    def __init__(self, backend, table: list[ActionDef] | None = None):
        self.backend = backend
        self.table = list(table) if table is not None else pc_action_table()
        if not self.table:
            raise ValueError("action table must be non-empty")
        names = action_names(self.table)
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate action names: {names}")

    @property
    def num_actions(self) -> int:
        return len(self.table)

    def execute(self, action: int) -> str:
        """Perform one discrete action; returns its name."""
        if isinstance(action, bool) or not isinstance(action, int) or not 0 <= action < len(self.table):
            raise ValueError(f"invalid action {action!r}: expected int in [0, {len(self.table)})")
        spec = self.table[action]
        if spec.kind == "noop":
            return spec.name
        if spec.kind == "key":
            self.backend.key_down(spec.vk)
            if spec.hold_ms:
                time.sleep(spec.hold_ms / 1000.0)
            self.backend.key_up(spec.vk)
        elif spec.kind == "mouse_button":
            self.backend.mouse_down(spec.button)
            if spec.hold_ms:
                time.sleep(spec.hold_ms / 1000.0)
            self.backend.mouse_up(spec.button)
        elif spec.kind == "mouse_move":
            self.backend.mouse_move(spec.dx, spec.dy)
        return spec.name
