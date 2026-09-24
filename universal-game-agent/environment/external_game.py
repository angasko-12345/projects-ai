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


class RewardProvider(ABC):
    """Pixels + action -> scalar reward. No game internals allowed in."""

    @abstractmethod
    def reward(self, previous_pixels: np.ndarray, pixels: np.ndarray, action: int) -> float:
        """Reward for the transition. Both frames are uint8 HxWx3 RGB."""


class NullReward(RewardProvider):
    """Zero reward baseline (e.g. pure exploration / smoke runs)."""

    def reward(self, previous_pixels, pixels, action: int) -> float:
        return 0.0


class TerminationProvider(ABC):
    """Pixels + bookkeeping -> episode end. Returns (terminated, truncated)."""

    @abstractmethod
    def done(self, previous_pixels: np.ndarray, pixels: np.ndarray, action: int, steps: int):
        """Decide the boundary. Must not consult game internals."""


class StepLimitTermination(TerminationProvider):
    """Truncate after max_steps decisions (never a true termination)."""

    def __init__(self, max_steps: int):
        if max_steps <= 0:
            raise ValueError(f"max_steps must be positive, got {max_steps!r}")
        self.max_steps = max_steps

    def done(self, previous_pixels, pixels, action: int, steps: int):
        over = steps >= self.max_steps
        return False, bool(over)


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
