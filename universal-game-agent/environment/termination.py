"""External termination providers: pixels in, boundary out.

A provider reports NATURAL termination only (the game itself ended).
Time-limit/session timeouts are the environment's job and are reported as
truncation -- never converted into termination. See
:class:`ExternalGameEnv` for the combination rule.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class TerminationProvider(ABC):
    """Decide episode end. Returns (terminated, truncated)."""

    @abstractmethod
    def done(self, previous_frame: np.ndarray, frame: np.ndarray, context: int, steps: int):
        """Boundary decision from pixels, action context, and step count."""


class NaturalTerminationProvider(TerminationProvider):
    """Termination-only base: truncation is always False here."""

    @abstractmethod
    def terminated(self, frame: np.ndarray, context: int) -> bool:
        """True when the game naturally ended (seen in this frame)."""

    def done(self, previous_frame, frame, context: int, steps: int):
        return bool(self.terminated(frame, context)), False


class NeverTerminateProvider(NaturalTerminationProvider):
    """Never reports natural termination (timeouts still truncate)."""

    def terminated(self, frame, context: int) -> bool:
        return False


class StepLimitTermination(TerminationProvider):
    """Truncate after max_steps decisions (never a natural termination)."""

    def __init__(self, max_steps: int):
        if max_steps <= 0:
            raise ValueError(f"max_steps must be positive, got {max_steps!r}")
        self.max_steps = max_steps

    def done(self, previous_frame, frame, context: int, steps: int):
        return False, bool(steps >= self.max_steps)
