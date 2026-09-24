"""External reward providers: pixels in, scalar out. No game internals.

Operates only on permitted external observations (rendered frames) plus the
abstract action index as context. Curiosity/intrinsic reward lives in
``training/curiosity.py`` and is combined with this external reward inside
the PPO trainer -- never inside a provider or the environment.
"""
from __future__ import annotations

import dataclasses
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


@dataclasses.dataclass
class RewardResult:
    """Inspectable breakdown: scalar total plus per-component contributions."""

    total: float
    components: dict


class EventReward(RewardProvider):
    """Fixed reward when a detector sees an event in the frame pair."""

    def __init__(self, detector, reward: float):
        """detector(previous_frame, current_frame) -> bool; pure pixels."""
        self.detector = detector
        self.reward_value = float(reward)

    def reward(self, previous_frame, current_frame, context: int) -> float:
        return self.reward_value if self.detector(previous_frame, current_frame) else 0.0


class TerminalPenalty(RewardProvider):
    """Fixed (negative) reward when the current frame shows a terminal event."""

    def __init__(self, detector, penalty: float):
        """detector(previous_frame, current_frame) -> bool; pure pixels."""
        self.detector = detector
        self.penalty = float(penalty)

    def reward(self, previous_frame, current_frame, context: int) -> float:
        return self.penalty if self.detector(previous_frame, current_frame) else 0.0


class SurvivalReward(RewardProvider):
    """Fixed reward per non-terminal decision step."""

    def __init__(self, reward: float, terminal_detector=None):
        """terminal_detector(previous_frame, current_frame) -> bool, or None to always pay."""
        self.reward_value = float(reward)
        self.terminal_detector = terminal_detector
    def reward(self, previous_frame, current_frame, context: int) -> float:
        if self.terminal_detector is not None and self.terminal_detector(previous_frame, current_frame):
            return 0.0
        return self.reward_value


class ProgressReward(RewardProvider):
    """Scaled reward for measured visual advance between the two frames."""

    def __init__(self, meter, scale: float = 1.0):
        """meter(frame) -> float (explicitly configured visual detector output)."""
        self.meter = meter
        self.scale = float(scale)

    def reward(self, previous_frame, current_frame, context: int) -> float:
        return max(0.0, self.scale * (self.meter(current_frame) - self.meter(previous_frame)))


class CompositeReward(RewardProvider):
    """Named components summed to one scalar; breakdown stays inspectable.

    Components are pure functions of (previous_frame, current_frame,
    context) and hold no episode state. Anything stateful must implement
    ``reset()``; this composite propagates it, and ``ExternalGameEnv``
    calls it on every env reset so state can never leak across episodes.
    """

    def __init__(self, components: dict):
        if not components:
            raise ValueError("CompositeReward needs at least one named component")
        self.components = dict(components)
        for name, component in self.components.items():
            if not isinstance(component, RewardProvider):
                raise ValueError(f"component {name!r} must be a RewardProvider")

    def breakdown(self, previous_frame, current_frame, context: int) -> RewardResult:
        parts = {name: float(component.reward(previous_frame, current_frame, context))
                 for name, component in self.components.items()}
        return RewardResult(total=float(sum(parts.values())), components=parts)

    def reward(self, previous_frame, current_frame, context: int) -> float:
        return self.breakdown(previous_frame, current_frame, context).total

    def reset(self) -> None:
        for component in self.components.values():
            reset = getattr(component, "reset", None)
            if callable(reset):
                reset()


def _warn_unknown(section: str, spec: dict, known: set) -> None:
    import warnings

    for key in spec:
        if key not in known:
            warnings.warn(f"reward/{section}: ignoring unknown config key {key!r}",
                          UserWarning, stacklevel=4)


def _red_count(frame) -> int:
    from environment.extern_pong_rewards import red_mask

    return int(red_mask(frame).sum())


def make_detector(spec: dict):
    """Build a pure pixel predicate/meter from a config mapping.

    Kinds: ``red_present`` {min}, ``red_edge`` {min, max} (small-red
    presence transitions between frames), ``brightness`` (mean-intensity
    meter for progress components). Unknown kinds raise; unknown keys warn.
    """
    if not isinstance(spec, dict):
        raise ValueError(f"detector spec must be a mapping, got {spec!r}")
    kind = str(spec.get("kind", ""))
    if kind == "red_present":
        _warn_unknown("detector", spec, {"kind", "min"})
        minimum = int(spec.get("min", 1))
        return lambda prev, cur: _red_count(cur) >= minimum
    if kind == "red_edge":
        _warn_unknown("detector", spec, {"kind", "min", "max"})
        minimum, maximum = int(spec.get("min", 8)), int(spec.get("max", 200))
        if not 0 <= minimum <= maximum:
            raise ValueError(f"red_edge needs 0 <= min <= max, got {(minimum, maximum)!r}")

        def edge(prev, cur):
            before = minimum <= _red_count(prev) <= maximum
            after = minimum <= _red_count(cur) <= maximum
            return before != after

        return edge
    if kind == "brightness":
        _warn_unknown("detector", spec, {"kind"})
        import numpy as np

        return lambda frame: float(np.ascontiguousarray(frame).mean())
    raise ValueError(f"unknown detector kind {kind!r}: expected red_present|red_edge|brightness")


def _build_component(spec: dict) -> tuple:
    """(name, provider) from one component mapping."""
    if not isinstance(spec, dict):
        raise ValueError(f"component spec must be a mapping, got {spec!r}")
    kind = str(spec.get("type", ""))
    name = str(spec.get("name", kind))
    if kind == "event":
        _warn_unknown(f"component {name}", spec, {"type", "name", "value", "detector"})
        if "detector" not in spec:
            raise ValueError(f"event component {name!r} needs a detector mapping")
        return name, EventReward(make_detector(spec["detector"]), float(spec.get("value", 0.0)))
    if kind in ("terminal", "terminal_penalty"):
        _warn_unknown(f"component {name}", spec, {"type", "name", "value", "detector"})
        if "detector" not in spec:
            raise ValueError(f"terminal component {name!r} needs a detector mapping")
        return name, TerminalPenalty(make_detector(spec["detector"]), float(spec.get("value", 0.0)))
    if kind == "survival":
        _warn_unknown(f"component {name}", spec,
                      {"type", "name", "value", "skip_on"})
        skip = make_detector(spec["skip_on"]) if "skip_on" in spec else None
        return name, SurvivalReward(float(spec.get("value", 0.0)),
                                    terminal_detector=skip)
    if kind == "progress":
        _warn_unknown(f"component {name}", spec, {"type", "name", "scale", "meter"})
        if "meter" not in spec:
            raise ValueError(f"progress component {name!r} needs a meter mapping")
        return name, ProgressReward(make_detector(spec["meter"]), float(spec.get("scale", 1.0)))
    raise ValueError(
        f"unknown component type {kind!r}: expected event|terminal|terminal_penalty|survival|progress"
    )


def make_reward_from_config(reward_cfg) -> RewardProvider:
    """Build a RewardProvider from config. ``None``/``null`` -> NullRewardProvider.

    A ``components`` list builds a CompositeReward; every entry is validated
    eagerly so bad configs fail at construction, not mid-episode.
    """
    cfg = dict(reward_cfg or {})
    provider = str(cfg.get("provider", "null" if "components" not in cfg else "composite"))
    if provider == "null":
        _warn_unknown("reward", cfg, {"provider", "components"})
        if "components" in cfg:
            import warnings

            warnings.warn("reward: 'components' ignored with provider 'null'",
                          UserWarning, stacklevel=2)
        return NullRewardProvider()
    if provider == "composite":
        _warn_unknown("reward", cfg, {"provider", "components"})
        parts = cfg.get("components", [])
        if not parts:
            raise ValueError("composite reward needs a non-empty 'components' list")
        return CompositeReward(dict(_build_component(spec) for spec in parts))
    raise ValueError(f"unknown reward.provider {provider!r}: expected null|composite")
