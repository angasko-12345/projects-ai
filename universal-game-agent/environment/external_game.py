"""Gymnasium-compatible boundary between the RL system and an external PC game.

Abstract action -> GameInterface -> screen capture -> preprocessing ->
model-ready stacked observation. Game-specific knowledge lives OUTSIDE this
module, behind three small interfaces: :class:`GameLifecycle` (session),
:class:`RewardProvider` (pixels -> scalar), :class:`TerminationProvider`.
The learning path sees only pixels, action indices, rewards, and dones --
window titles, rects, PIDs, and game internals never enter observations.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import time

import numpy as np

try:
    import gymnasium as gym

    _Base = gym.Env
except ImportError:  # pragma: no cover - only on minimal installs
    gym = None  # type: ignore[assignment]
    _Base = object

from environment.preprocessing import FrameStack
from environment.reward import NullReward, NullRewardProvider, RewardProvider
from environment.termination import (
    NaturalTerminationProvider,
    NeverTerminateProvider,
    StepLimitTermination,
    TerminationProvider,
)


class Clock(ABC):
    """Injectable time source. Real runs use SystemClock; tests use fakes."""

    @abstractmethod
    def now(self) -> float:
        """Monotonic seconds."""

    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """Wait. Must be a no-op-able mock point (never raw time.sleep)."""


class SystemClock(Clock):
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class GameLifecycle(ABC):
    """Owns the external game session. Knows windows/processes; emits none."""

    @abstractmethod
    def attach(self) -> bool:
        """Bind to (or start) the target game. True on success."""

    @abstractmethod
    def focus(self) -> bool:
        """Best-effort foreground. Never raises for a lost game."""

    @abstractmethod
    def is_available(self) -> bool:
        """Whether the game session currently exists."""

    @abstractmethod
    def reset_session(self) -> bool:
        """Best-effort session reset/restart. False when unsupported."""

    @abstractmethod
    def close(self) -> None:
        """Release handles. No game input or capture here."""


class WindowLifecycle(GameLifecycle):
    """GameLifecycle backed by the existing WindowManager."""

    def __init__(self, manager, launch_command: list[str] | None = None):
        self.manager = manager
        self.launch_command = list(launch_command) if launch_command else None

    def attach(self) -> bool:
        from interface.window import WindowNotFoundError

        try:
            self.manager.attach()
            return True
        except WindowNotFoundError:
            if self.launch_command is None:
                return False
            self.manager.restart(self.launch_command)
            try:
                self.manager.attach()
                return True
            except WindowNotFoundError:
                return False

    def focus(self) -> bool:
        return bool(self.manager.focus())

    def is_available(self) -> bool:
        return bool(self.manager.is_alive())

    def reset_session(self) -> bool:
        if self.launch_command is None:
            return False
        self.manager.restart(self.launch_command)
        return True

    def close(self) -> None:
        self.manager.detach()

__all__ = [
    "ExternalGameEnv",
    "GameLifecycle",
    "WindowLifecycle",
    "RewardProvider",
    "NullReward",
    "NullRewardProvider",
    "TerminationProvider",
    "NaturalTerminationProvider",
    "NeverTerminateProvider",
    "StepLimitTermination",
    "Clock",
    "SystemClock",
]

class ExternalGameEnv(_Base):
    """External PC game as a Gymnasium env of stacked pixel frames.

    reset() -> ((num_stack, size, size) float32 obs, {}).
    step(action) -> (obs, reward, terminated, truncated, {}).
    ``info`` is always empty: no window/process metadata leaks out.
    """

    metadata = {"render_modes": ["rgb_array"]}

    def __init__(self, interface, reward_provider: RewardProvider,
                 termination_provider: TerminationProvider,
                 lifecycle: GameLifecycle | None = None,
                 num_stack: int = 4, size: int = 84,
                 post_action_delay_ms: float = 0.0,
                 startup_delay_ms: float = 0.0,
                 reset_delay_ms: float = 0.0,
                 max_episode_steps: int | None = None,
                 max_episode_seconds: float | None = None,
                 clock: Clock | None = None):
        """Timing semantics (separate concepts, no hidden stacking):

        1. hold time: per ActionDef, applied once by ActionMapper.
        2. post-action observation delay: slept once per step, after input
           and before capture, so the game can react (default 0).
        3. startup delay: slept once, right after the first successful
           session attach, so a freshly started game can load.
        4. reset delay: slept on every reset(), after the session check.
        5. decision interval: the caller's stepping pace; nothing added.
        Timeouts (env-level, in addition to the termination provider):
        ``max_episode_steps`` / ``max_episode_seconds`` report
        ``terminated=False, truncated=True``. A true termination simultaneous
        with a timeout still reports ``terminated=True``.
        All waiting goes through ``clock`` (SystemClock by default).
        """
        for name, value in (("post_action_delay_ms", post_action_delay_ms),
                            ("startup_delay_ms", startup_delay_ms),
                            ("reset_delay_ms", reset_delay_ms)):
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value!r}")
        if max_episode_steps is not None and max_episode_steps <= 0:
            raise ValueError(f"max_episode_steps must be positive, got {max_episode_steps!r}")
        if max_episode_seconds is not None and max_episode_seconds <= 0:
            raise ValueError(f"max_episode_seconds must be positive, got {max_episode_seconds!r}")
        super().__init__()
        self.interface = interface
        self.reward_provider = reward_provider
        self.termination_provider = termination_provider
        self.lifecycle = lifecycle
        self.clock = clock or SystemClock()
        self.post_action_delay_ms = float(post_action_delay_ms)
        self.startup_delay_ms = float(startup_delay_ms)
        self.reset_delay_ms = float(reset_delay_ms)
        self.max_episode_steps = max_episode_steps
        self.max_episode_seconds = max_episode_seconds
        self.stack = FrameStack(num_stack=num_stack, size=size)
        self.action_space = self._discrete(interface.num_actions)
        self.observation_space = self._box((num_stack, size, size))
        self._steps = 0
        self._episode_start = 0.0
        self._started = False
        self._last_raw: np.ndarray | None = None

    @staticmethod
    def _discrete(n: int):
        if gym is not None:
            from gymnasium import spaces

            return spaces.Discrete(n)

        class _Discrete:
            def __init__(self, n):
                self.n = n

            def contains(self, x):
                return isinstance(x, int) and 0 <= x < n

        return _Discrete(n)

    @staticmethod
    def _box(shape: tuple[int, ...]):
        if gym is not None:
            from gymnasium import spaces

            return spaces.Box(0.0, 1.0, shape, np.float32)

        class _Box:
            def __init__(self, shape):
                self.shape = shape

        return _Box(shape)

    # -- session ---------------------------------------------------------
    def _ensure_session(self) -> bool:
        """Ensure a game is present. Returns True if attach() ran and worked."""
        if self.lifecycle is None:
            return False
        if not self.lifecycle.is_available():
            if not self.lifecycle.attach():
                raise RuntimeError("external game session unavailable and attach() failed")
            self.lifecycle.focus()  # best-effort; False is tolerated
            return True
        self.lifecycle.focus()  # best-effort; False is tolerated
        return False

    def _sleep_ms(self, milliseconds: float) -> None:
        if milliseconds:
            self.clock.sleep(milliseconds / 1000.0)

    # -- Gymnasium API ----------------------------------------------------
    def reset(self, *, seed=None, options=None):
        attached_now = self._ensure_session()
        if attached_now and not self._started:
            self._sleep_ms(self.startup_delay_ms)
            self._started = True
        self._sleep_ms(self.reset_delay_ms)
        raw = self.interface.capture()
        self._validate_raw(raw)
        self._steps = 0
        self._episode_start = self.clock.now()
        self._last_raw = raw
        return self.stack.reset(raw), {}

    def step(self, action):
        if self._last_raw is None:
            raise RuntimeError("step() before reset()")
        self.interface.execute(action)  # validates + performs OS input
        self._sleep_ms(self.post_action_delay_ms)
        raw = self.interface.capture()
        self._validate_raw(raw)
        self._steps += 1
        reward = float(self.reward_provider.reward(self._last_raw, raw, int(action)))
        terminated, truncated = (bool(v) for v in
                                 self.termination_provider.done(self._last_raw, raw, int(action), self._steps))
        if not terminated and self._timed_out():
            truncated = True  # timeouts are truncations, never terminations
        self._last_raw = raw
        return self.stack.push(raw), reward, terminated, truncated, {}

    def _timed_out(self) -> bool:
        if self.max_episode_steps is not None and self._steps >= self.max_episode_steps:
            return True
        if self.max_episode_seconds is not None:
            return (self.clock.now() - self._episode_start) >= self.max_episode_seconds
        return False

    def render(self):
        if self._last_raw is None:
            raise RuntimeError("render() before reset()")
        return self._last_raw.copy()

    def close(self):
        if self.lifecycle is not None:
            self.lifecycle.close()

    @staticmethod
    def _validate_raw(raw: np.ndarray) -> None:
        if not isinstance(raw, np.ndarray) or raw.ndim != 3 or raw.shape[2] != 3:
            raise ValueError(f"capture must return HxWx3 RGB, got {type(raw)} {getattr(raw, 'shape', None)}")
        if raw.dtype != np.uint8:
            raise ValueError(f"capture must return uint8, got {raw.dtype}")


def _build_action_table(table_cfg) -> list:
    """Action table from config: 'default' or a list of ActionDef dicts."""
    from interface.controller import ActionDef, pc_action_table

    if table_cfg is None or table_cfg == "default":
        return pc_action_table()
    if not isinstance(table_cfg, list) or not table_cfg:
        raise ValueError("actions.table must be 'default' or a non-empty list")
    table = []
    for entry in table_cfg:
        if not isinstance(entry, dict):
            raise ValueError(f"action entries must be mappings, got {entry!r}")
        table.append(ActionDef(
            name=str(entry["name"]),
            kind=str(entry.get("kind", "key")),
            vk=int(entry.get("vk", 0)),
            button=str(entry.get("button", "left")),
            dx=int(entry.get("dx", 0)),
            dy=int(entry.get("dy", 0)),
            hold_ms=int(entry.get("hold_ms", 0)),
        ))
    return table


def make_external_env_from_config(env_cfg: dict, clock=None):
    """Build an ExternalGameEnv factory from config (no game hardcoded).

    ``type: external`` selects this path; every component below maps to a
    real constructor argument -- nothing here names a specific game.
    Live capture (region/window) additionally requires
    ``allow_live_capture: true`` as an explicit safeguard; without it only
    the display-free ``synthetic`` source is built.
    Returns a zero-arg factory like ``make_env_from_config``.
    """
    from interface.adapter import GameInterface
    from interface.capture import MSSBackend, ScreenCapture, SyntheticBackend, WindowCapture
    from interface.controller import ActionMapper, RecordingBackend

    cfg = dict(env_cfg or {})
    cap_cfg = cfg.get("capture", {}) or {}
    mode = str(cap_cfg.get("mode", "synthetic"))
    out_w, out_h = int(cap_cfg.get("out_width", 64)), int(cap_cfg.get("out_height", 64))
    if out_w <= 0 or out_h <= 0:
        raise ValueError(f"capture out size must be positive, got {(out_w, out_h)!r}")
    timing = cfg.get("timing", {}) or {}
    life_cfg = cfg.get("lifecycle", {}) or {}
    actions_cfg = cfg.get("actions", {}) or {}
    backend_name = str(actions_cfg.get("backend", "recording"))

    live = mode in ("region", "window")
    if live and not cfg.get("allow_live_capture", False):
        raise RuntimeError(
            "refusing live screen capture without explicit 'allow_live_capture: true' "
            "in the env config (no game launches or input happens otherwise)"
        )
    if mode not in ("synthetic", "region", "window"):
        raise ValueError(f"unknown capture.mode {mode!r}: expected synthetic|region|window")
    reward_name = str((cfg.get("reward", {}) or {}).get("provider", "null"))
    if reward_name != "null":
        raise ValueError(f"unknown reward.provider {reward_name!r}: expected null")
    term_cfg = cfg.get("termination", {}) or {}
    term_name = str(term_cfg.get("provider", "never"))
    if term_name not in ("never", "step_limit"):
        raise ValueError(f"unknown termination.provider {term_name!r}: expected never|step_limit")

    def _timeout(value, cast):
        return None if value is None else cast(value)

    def make():
        manager, lifecycle = None, None
        if mode == "synthetic":
            frames = [np.full((out_h, out_w, 3), int(cap_cfg.get("frame_value", 0)), dtype=np.uint8)]
            capture = ScreenCapture(SyntheticBackend(frames), 0, 0, out_w, out_h, out_w, out_h)
        else:
            from interface.window import WindowManager

            backend = MSSBackend()
            if mode == "region":
                region = cap_cfg.get("region", {}) or {}
                capture = ScreenCapture(backend, int(region.get("x", 0)), int(region.get("y", 0)),
                                        int(region.get("width", out_w)), int(region.get("height", out_h)),
                                        out_w, out_h)
            else:
                title = str(cap_cfg.get("title", "") or life_cfg.get("title", ""))
                if not title:
                    raise ValueError("capture.mode 'window' needs capture.title (or lifecycle.title)")
                manager = WindowManager(title)
                capture = WindowCapture(manager, backend, out_w, out_h)
            if life_cfg.get("mode", "none") == "window" or mode == "window":
                if manager is None:
                    raise ValueError("lifecycle.mode 'window' needs a window target")
                lifecycle = WindowLifecycle(manager, life_cfg.get("launch_command"))
        if backend_name == "recording":
            input_backend = RecordingBackend()
        elif backend_name == "sendinput":
            from interface.controller import SendInputBackend

            input_backend = SendInputBackend()
        else:
            raise ValueError(f"unknown actions.backend {backend_name!r}: expected recording|sendinput")
        controller = ActionMapper(input_backend, _build_action_table(actions_cfg.get("table", "default")))
        game = GameInterface(capture, controller, manager)
        term_provider = (StepLimitTermination(int(term_cfg.get("max_steps", 500)))
                         if term_name == "step_limit" else NeverTerminateProvider())
        return ExternalGameEnv(
            game, NullRewardProvider(), term_provider, lifecycle,
            num_stack=int(cfg.get("num_stack", 4)), size=int(cfg.get("obs_size", 84)),
            post_action_delay_ms=float(timing.get("post_action_delay_ms", 0.0)),
            startup_delay_ms=float(timing.get("startup_delay_ms", 0.0)),
            reset_delay_ms=float(timing.get("reset_delay_ms", 0.0)),
            max_episode_steps=_timeout(timing.get("max_episode_steps"), int),
            max_episode_seconds=_timeout(timing.get("max_episode_seconds"), float),
            clock=clock,
        )

    return make
