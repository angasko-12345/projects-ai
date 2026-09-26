"""Screen-only reward/termination for the extern-Pong test game.

Reads rendered frames only -- never imports the game, never touches
coordinates, scores, or flags. Visual protocol (see games/extern_pong.py):
the ball latches red on a paddle hit (cleared on serve) and a large red
MISS banner appears on terminal states. Detection is strictly edge-based:

* normal -> MISS banner = -1 (exactly once), terminated
* MISS -> MISS          =  0 (no repeated penalty)
* MISS -> normal        =  0 (reset produces nothing)
* normal -> small red   = +1 (exactly one hit reward)
* small red -> small red =  0 (latched state pays nothing)
* small red -> normal   =  0 (serve clearing the latch is not an event)
* otherwise             =  0, not terminated

Detection runs on native-resolution capture frames: the 6px ball and the
banner are distinguished by red-pixel-count bands (hit_min..hit_max vs
miss_min) that only hold when the frame is NOT aggressively downscaled.
Sizes in red-pixel counts; thresholds are constructor parameters.
"""
from __future__ import annotations

import numpy as np

from environment.reward import RewardProvider
from environment.termination import NaturalTerminationProvider


def red_mask(frame: np.ndarray) -> np.ndarray:
    """Boolean mask of clearly-red pixels (excludes white ball, gray text)."""
    f = frame.astype(np.int16)
    return (f[:, :, 0] > 150) & (f[:, :, 1] < 100) & (f[:, :, 2] < 100)


class ExternPongReward(RewardProvider):
    def __init__(self, hit_reward: float = 1.0, miss_reward: float = -1.0,
                 hit_min: int = 8, hit_max: int = 200, miss_min: int = 300):
        if not 0 <= hit_min <= hit_max < miss_min:
            raise ValueError(
                f"need 0 <= hit_min <= hit_max < miss_min, got {(hit_min, hit_max, miss_min)!r}"
            )
        self.hit_reward, self.miss_reward = float(hit_reward), float(miss_reward)
        self.hit_min, self.hit_max, self.miss_min = hit_min, hit_max, miss_min

    def _red_count(self, frame: np.ndarray) -> int:
        return int(red_mask(frame).sum())

    def _is_banner(self, count: int) -> bool:
        return count >= self.miss_min

    def _is_ball_flash(self, count: int) -> bool:
        return self.hit_min <= count <= self.hit_max

    def reward(self, previous_frame: np.ndarray, current_frame: np.ndarray, context: int) -> float:
        prev_n = self._red_count(previous_frame)
        cur_n = self._red_count(current_frame)
        prev_banner, cur_banner = self._is_banner(prev_n), self._is_banner(cur_n)
        if cur_banner and not prev_banner:
            return self.miss_reward
        if cur_banner or prev_banner:
            return 0.0
        prev_small = self._is_ball_flash(prev_n)
        cur_small = self._is_ball_flash(cur_n)
        if cur_small and not prev_small:
            return self.hit_reward
        return 0.0


class ExternPongTermination(NaturalTerminationProvider):
    """Terminated iff the red MISS banner is visible in this frame."""

    def __init__(self, miss_min: int = 300):
        if miss_min <= 0:
            raise ValueError(f"miss_min must be positive, got {miss_min!r}")
        self.miss_min = miss_min

    def terminated(self, frame: np.ndarray, context: int) -> bool:
        return int(red_mask(frame).sum()) >= self.miss_min
