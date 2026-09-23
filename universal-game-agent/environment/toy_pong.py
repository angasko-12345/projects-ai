"""Tiny Pong-like toy environment for pixel-pipeline experiments.

Learning boundary: the agent receives ONLY the rendered RGB frame.
Ball/paddle coordinates, velocities, scores, and collision flags live in
underscore-prefixed attributes and never appear in observations or info.

Gymnasium API: ``reset(seed) -> (obs, info)``,
``step(action) -> (obs, reward, terminated, truncated, info)``.
Actions: 0 = NOOP, 1 = LEFT, 2 = RIGHT. Rewards: +1.0 paddle hit,
-1.0 miss (terminates), 0.0 otherwise. Truncation at ``max_steps``.
"""
from __future__ import annotations

import argparse
import random

import numpy as np

try:  # Prefer real Gymnasium; fall back to API-compatible stubs.
    import gymnasium as gym
    from gymnasium import spaces

    _Base = gym.Env
except ImportError:  # pragma: no cover - only on minimal installs
    gym = None  # type: ignore[assignment]

    class _Discrete:
        def __init__(self, n: int):
            self.n = n

        def contains(self, x) -> bool:
            return isinstance(x, (int, np.integer)) and 0 <= int(x) < self.n

    class _Box:
        def __init__(self, low, high, shape, dtype):
            self.low, self.high, self.shape, self.dtype = low, high, shape, dtype

        def contains(self, x) -> bool:
            return (
                isinstance(x, np.ndarray)
                and x.shape == self.shape
                and x.dtype == np.dtype(self.dtype)
            )

    class _Spaces:
        Discrete = _Discrete
        Box = _Box

    spaces = _Spaces()  # type: ignore[assignment]
    _Base = object

NOOP, LEFT, RIGHT = 0, 1, 2
HIT_REWARD, MISS_REWARD = 1.0, -1.0


class ToyPongEnv(_Base):
    """Paddle (bottom) vs. falling ball, rendered as a small RGB frame."""

    metadata = {"render_modes": ["rgb_array"]}

    def __init__(self, width: int = 64, height: int = 48, max_steps: int = 500):
        super().__init__()
        self._w, self._h, self._max_steps = width, height, max_steps
        self._paddle_w, self._paddle_h, self._paddle_speed = 12, 3, 3
        self._paddle_y = height - 5
        self._ball_size, self._ball_speed = 2, 2
        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(0, 255, (height, width, 3), np.uint8)
        self._rng = random.Random()
        self._reset_state()

    # -- Gymnasium API ----------------------------------------------------
    def reset(self, *, seed: int | None = None, options=None):
        if seed is not None:
            self._rng.seed(seed)
        self._reset_state()
        self._serve()
        return self._render_frame(), {}

    def step(self, action):
        if (
            isinstance(action, bool)
            or not isinstance(action, (int, np.integer))
            or int(action) not in (NOOP, LEFT, RIGHT)
        ):
            raise ValueError(f"Invalid action {action!r}: expected 0 (NOOP), 1 (LEFT), or 2 (RIGHT)")
        action = int(action)
        if action == LEFT:
            self._paddle_x = max(0, self._paddle_x - self._paddle_speed)
        elif action == RIGHT:
            self._paddle_x = min(self._w - self._paddle_w, self._paddle_x + self._paddle_speed)

        self._bx += self._vx
        self._by += self._vy
        if self._bx <= 0:
            self._bx, self._vx = 0, self._ball_speed
        elif self._bx >= self._w - self._ball_size:
            self._bx, self._vx = self._w - self._ball_size, -self._ball_speed
        if self._by <= 0:
            self._by, self._vy = 0, self._ball_speed

        reward = 0.0
        terminated = False
        if self._vy > 0 and self._by + self._ball_size >= self._paddle_y:
            overlap = self._bx + self._ball_size > self._paddle_x and self._bx < self._paddle_x + self._paddle_w
            if overlap and self._by < self._h:
                self._by, self._vy = self._paddle_y - self._ball_size, -self._ball_speed
                reward = HIT_REWARD
        if self._by >= self._h:
            terminated, reward = True, MISS_REWARD

        self._steps += 1
        truncated = not terminated and self._steps >= self._max_steps
        return self._render_frame(), float(reward), terminated, truncated, {}

    def render(self):
        return self._render_frame()

    def close(self):
        pass

    # -- internals (never exposed to the agent) ---------------------------
    def _reset_state(self):
        self._paddle_x = (self._w - self._paddle_w) // 2
        self._bx = self._by = self._vx = self._vy = 0
        self._steps = 0

    def _serve(self):
        self._bx = self._w // 2 + self._rng.randint(-8, 8)
        self._by = self._h // 3
        self._vx = self._rng.choice((-self._ball_speed, self._ball_speed))
        self._vy = self._ball_speed  # always served downward, toward the paddle

    def _render_frame(self) -> np.ndarray:
        frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        frame[self._paddle_y : self._paddle_y + self._paddle_h, self._paddle_x : self._paddle_x + self._paddle_w] = 255
        frame[self._by : self._by + self._ball_size, self._bx : self._bx + self._ball_size] = 255
        return frame


def _pixel_policy_chase(obs: np.ndarray) -> int:
    """Demo policy using pixels only: move the paddle toward the ball."""
    h = obs.shape[0]
    paddle_y = h - 5
    lower = np.where(obs[paddle_y : paddle_y + 3, :, 0] > 0)[1]
    upper = np.where(obs[:paddle_y, :, 0] > 0)[1]
    if len(lower) == 0 or len(upper) == 0:
        return NOOP
    paddle_x, ball_x = lower.mean(), upper.mean()
    if ball_x < paddle_x - 2:
        return LEFT
    if ball_x > paddle_x + 2:
        return RIGHT
    return NOOP


def run_episodes(episodes: int = 3, seed: int = 0) -> None:
    env = ToyPongEnv()
    for ep in range(episodes):
        obs, _ = env.reset(seed=seed + ep)
        total, steps = 0.0, 0
        while True:
            obs, reward, terminated, truncated, _ = env.step(_pixel_policy_chase(obs))
            total, steps = total + reward, steps + 1
            if terminated or truncated:
                print(f"episode {ep}: steps={steps} total_reward={total:.1f} "
                      f"{'terminated(miss)' if terminated else 'truncated'}")
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Toy Pong pixel env demo (pixels-only policy)")
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    run_episodes(episodes=args.episodes, seed=args.seed)
