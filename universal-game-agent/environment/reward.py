"""External reward providers: pixels in, scalar out. No game internals.

Operates only on permitted external observations (rendered frames) plus the
abstract action index as context. Curiosity/intrinsic reward lives in
``training/curiosity.py`` and is combined with this external reward inside
the PPO trainer -- never inside a provider or the environment.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class RewardProvider(ABC):
    """Pixels + action context -> scalar extrinsic reward."""

    @abstractmethod
    def reward(self, previous_frame: np.ndarray, current_frame: np.ndarray, context: int) -> float:
        """Reward for one transition. Frames are uint8 HxWx3 RGB; context is the action index."""


class NullRewardProvider(RewardProvider):
    """Zero reward baseline (pure exploration / smoke runs)."""

    def reward(self, previous_frame, current_frame, context: int) -> float:
        return 0.0


#: Backwards-compatible alias (same zero behavior).
NullReward = NullRewardProvider
