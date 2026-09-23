"""Game-agnostic visual observation pipeline: RGB frame -> network-ready obs.

Only ever touches pixel arrays: no game memory, no object handles, no
semantic state (positions, scores) is read or produced. Output is a
``float32`` channel-first stack ``(num_stack, size, size)`` in ``[0, 1]``,
ready for a PyTorch conv net.

Pieces: :func:`preprocess_frame` (grayscale + resize + normalize),
:class:`FrameStack` (temporal stacking), :class:`PreprocessingWrapper`
(Gymnasium wrapper adding configurable frame skipping on top).
"""
from __future__ import annotations

import argparse
from collections import deque

import numpy as np

try:
    import gymnasium as gym
except ImportError:  # pragma: no cover - only on minimal installs
    gym = None  # type: ignore[assignment]

TARGET_SIZE = 84
# ITU-R BT.601 luma weights.
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float64)


def _validate_frame(frame: np.ndarray) -> tuple[int, int]:
    if not isinstance(frame, np.ndarray):
        raise TypeError(f"frame must be np.ndarray, got {type(frame).__name__}")
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"frame must be HxWx3 RGB, got shape {frame.shape}")
    if frame.dtype != np.uint8:
        raise ValueError(f"frame must be uint8, got {frame.dtype}")
    h, w, _ = frame.shape
    if h == 0 or w == 0:
        raise ValueError(f"frame must be non-empty, got shape {frame.shape}")
    return h, w


def _to_grayscale(frame: np.ndarray) -> np.ndarray:
    return (frame.astype(np.float64) * _LUMA).sum(axis=2)


def _resize_bilinear(gray: np.ndarray, size: int) -> np.ndarray:
    h, w = gray.shape
    if (h, w) == (size, size):
        return gray.astype(np.float32)
    rows = np.clip((np.arange(size) + 0.5) * h / size - 0.5, 0, h - 1)
    cols = np.clip((np.arange(size) + 0.5) * w / size - 0.5, 0, w - 1)
    r0 = np.floor(rows).astype(int)
    c0 = np.floor(cols).astype(int)
    r1 = np.minimum(r0 + 1, h - 1)
    c1 = np.minimum(c0 + 1, w - 1)
    dr, dc = (rows - r0)[:, None], (cols - c0)[None, :]
    return (
        gray[r0[:, None], c0[None, :]] * (1 - dr) * (1 - dc)
        + gray[r0[:, None], c1[None, :]] * (1 - dr) * dc
        + gray[r1[:, None], c0[None, :]] * dr * (1 - dc)
        + gray[r1[:, None], c1[None, :]] * dr * dc
    ).astype(np.float32)


def preprocess_frame(frame: np.ndarray, size: int = TARGET_SIZE) -> np.ndarray:
    """RGB uint8 frame -> ``(size, size)`` float32 grayscale in ``[0, 1]``."""
    if not isinstance(size, (int, np.integer)) or int(size) <= 0:
        raise ValueError(f"size must be a positive int, got {size!r}")
    _validate_frame(frame)
    gray = _to_grayscale(frame)
    return np.clip(_resize_bilinear(gray, int(size)) / 255.0, 0.0, 1.0).astype(np.float32)


def to_torch(obs: np.ndarray):
    """Share a stacked obs with PyTorch (no copy); needs torch installed."""
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("torch is required for to_torch (pip install torch)") from exc
    arr = np.ascontiguousarray(obs, dtype=np.float32)
    return torch.from_numpy(arr)


class FrameStack:
    """Stack the last ``num_stack`` processed frames channel-first."""

    def __init__(self, num_stack: int = 4, size: int = TARGET_SIZE):
        if not isinstance(num_stack, (int, np.integer)) or int(num_stack) <= 0:
            raise ValueError(f"num_stack must be a positive int, got {num_stack!r}")
        self.num_stack, self.size = int(num_stack), int(size)
        self._frames: deque[np.ndarray] = deque(maxlen=self.num_stack)

    def reset(self, frame: np.ndarray) -> np.ndarray:
        first = preprocess_frame(frame, self.size)
        self._frames.clear()
        self._frames.extend([first] * self.num_stack)
        return self.get()

    def push(self, frame: np.ndarray) -> np.ndarray:
        self._frames.append(preprocess_frame(frame, self.size))
        if len(self._frames) < self.num_stack:
            pad = [self._frames[0]] * (self.num_stack - len(self._frames))
            return np.stack([*pad, *self._frames])
        return self.get()

    def get(self) -> np.ndarray:
        if len(self._frames) != self.num_stack:
            raise RuntimeError("FrameStack empty: call reset() first")
        return np.stack(list(self._frames))

    def __len__(self) -> int:
        return len(self._frames)


_WrapperBase = gym.Wrapper if gym is not None else object


class PreprocessingWrapper(_WrapperBase):
    """Gymnasium wrapper: frame skip + grayscale/resize/normalize + stacking.

    ``skip`` repeats the action and sums rewards (DQN-style), max-pooling the
    last two raw frames to tame flicker. Observation: ``(num_stack, size,
    size)`` float32 in ``[0, 1]``.
    """

    def __init__(self, env, size: int = TARGET_SIZE, num_stack: int = 4, skip: int = 1):
        if gym is not None:
            super().__init__(env)
        else:
            self.env = env
        if not isinstance(skip, (int, np.integer)) or int(skip) <= 0:
            raise ValueError(f"skip must be a positive int, got {skip!r}")
        self.size, self.skip = int(size), int(skip)
        self.stack = FrameStack(num_stack=num_stack, size=size)
        shape = (num_stack, size, size)
        if gym is not None:
            from gymnasium import spaces

            self.observation_space = spaces.Box(0.0, 1.0, shape, np.float32)
            self.action_space = env.action_space
        else:
            self.observation_space = shape
            self.action_space = env.action_space

    def reset(self, *, seed=None, options=None):
        raw, info = self.env.reset(seed=seed, options=options)
        return self.stack.reset(raw), info

    def step(self, action):
        total, prev, last = 0.0, None, None
        terminated = truncated = False
        info: dict = {}
        for _ in range(self.skip):
            raw, reward, terminated, truncated, info = self.env.step(action)
            total += float(reward)
            prev, last = last, raw
            if terminated or truncated:
                break
        pooled = np.maximum(prev, last) if prev is not None else last
        return self.stack.push(pooled), total, terminated, truncated, info


def _chase_last_frame(stacked: np.ndarray) -> int:
    """Pixels-only demo policy on the latest stacked channel."""
    frame = stacked[-1]
    paddle_band, ball_zone = frame[-8:, :], frame[:-8, :]
    px = np.where(paddle_band > 0.5)[1]
    bx = np.where(ball_zone > 0.5)[1]
    if len(px) == 0 or len(bx) == 0:
        return 0
    diff = bx.mean() - px.mean()
    return 1 if diff < -1.0 else (2 if diff > 1.0 else 0)


def run_demo(episodes: int = 2, seed: int = 0, skip: int = 2) -> None:
    from environment.toy_pong import ToyPongEnv

    env = PreprocessingWrapper(ToyPongEnv(), skip=skip)
    for ep in range(episodes):
        obs, _ = env.reset(seed=seed + ep)
        total, decisions = 0.0, 0
        while True:
            obs, reward, terminated, truncated, _ = env.step(_chase_last_frame(obs))
            total, decisions = total + reward, decisions + 1
            if terminated or truncated:
                print(
                    f"episode {ep}: obs={obs.shape} dtype={obs.dtype} "
                    f"range=[{obs.min():.2f},{obs.max():.2f}] "
                    f"decisions={decisions} total_reward={total:.1f} "
                    f"{'terminated' if terminated else 'truncated'}"
                )
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Frames -> network-ready obs demo")
    parser.add_argument("--episodes", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--skip", type=int, default=2)
    args = parser.parse_args()
    run_demo(episodes=args.episodes, seed=args.seed, skip=args.skip)
