"""The UGA pad: one fixed, game-agnostic controller for every adapter.

The pad is the Atari 2600 joystick's full 18-action set, in ALE enum order,
so one policy head (``Discrete(18)``) drives arcade games and Atari alike.
Each adapter declares its *native* legal actions by name (ALE's minimal action
set via ``get_action_meanings()``, the arcade games' ``ACTION_MEANINGS``) and
:func:`projection` maps every pad action onto the native action with the
fewest mismatched components -- horizontal, vertical, fire -- where pointing
the opposite way costs more than not moving. Ties go to the lowest native
index. Nothing here knows any game: the table is a pure function of the
declared names.

Pad actions without an exact native counterpart are *aliases* (e.g. ``UP`` in
Pong plays ``NOOP``), not errors; :attr:`UniversalActionWrapper.legal_mask`
marks the exact ones for diagnostics. PPO is unchanged (no masking).

Versioning: a wrapper declares ``uga_action_space == PAD_VERSION``; the PPO
checkpoint records it, and a checkpoint with a different (or no, i.e. native)
action space refuses to load into a pad env instead of silently misreading
logits.
"""
from __future__ import annotations

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:  # pragma: no cover - minimal installs
    gym = None  # type: ignore[assignment]
    spaces = None  # type: ignore[assignment]

PAD_VERSION = "uga-pad18-v1"
NATIVE_ACTION_SPACE = "native"

PAD_ACTIONS = (
    "NOOP", "FIRE", "UP", "RIGHT", "LEFT", "DOWN",
    "UPRIGHT", "UPLEFT", "DOWNRIGHT", "DOWNLEFT",
    "UPFIRE", "RIGHTFIRE", "LEFTFIRE", "DOWNFIRE",
    "UPRIGHTFIRE", "UPLEFTFIRE", "DOWNRIGHTFIRE", "DOWNLEFTFIRE",
)
NUM_PAD_ACTIONS = len(PAD_ACTIONS)


def components(name: str) -> tuple[int, int, int]:
    """``(dx, dy, fire)`` of an action name; dx right=+1, dy up=-1."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"action name must be a non-empty str, got {name!r}")
    rest = name.upper()
    if rest == "NOOP":
        return 0, 0, 0
    fire = 0
    if rest.endswith("FIRE"):
        fire, rest = 1, rest[: -len("FIRE")]
    dy = 0
    if rest.startswith("UP"):
        dy, rest = -1, rest[2:]
    elif rest.startswith("DOWN"):
        dy, rest = 1, rest[4:]
    dx = {"": 0, "RIGHT": 1, "LEFT": -1}.get(rest)
    if dx is None:
        raise ValueError(f"unknown action name {name!r}")
    return dx, dy, fire


def _cost(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    # Movement axes: |diff| in {0,1,2} doubled (opposite = 4, missing = 2);
    # fire mismatch = 1, so movement is preserved before fire is.
    return 2 * abs(a[0] - b[0]) + 2 * abs(a[1] - b[1]) + abs(a[2] - b[2])


def projection(native_meanings) -> tuple[tuple[int, ...], tuple[bool, ...]]:
    """``(pad -> native index table, exact-match mask)`` from native names."""
    native = [components(n) for n in native_meanings]
    if not native:
        raise ValueError("native action set is empty")
    table, exact = [], []
    for pad in PAD_ACTIONS:
        c = components(pad)
        costs = [_cost(c, n) for n in native]
        best = min(range(len(native)), key=lambda i: (costs[i], i))
        table.append(best)
        exact.append(costs[best] == 0)
    return tuple(table), tuple(exact)


def native_meanings_of(env) -> tuple[str, ...]:
    """The env's declared native action names (ALE or arcade convention)."""
    base = getattr(env, "unwrapped", env)
    for obj in (env, base):
        names = getattr(obj, "action_meanings", None)
        if callable(names):
            names = names()
        if names:
            return tuple(names)
        getter = getattr(obj, "get_action_meanings", None)
        if callable(getter):
            return tuple(getter())
    raise ValueError(f"{type(base).__name__} declares no action meanings; "
                     "cannot map it onto the UGA pad")


_WrapperBase = gym.Wrapper if gym is not None else object


class UniversalActionWrapper(_WrapperBase):
    """Expose the UGA pad (``Discrete(18)``) over an env's native actions.

    Put it outermost so ``uga_action_space`` is a direct attribute (Gymnasium
    1.x wrappers do not forward attribute lookups).
    """

    uga_action_space = PAD_VERSION

    def __init__(self, env):
        if gym is None:  # pragma: no cover
            raise RuntimeError("gymnasium is required for UniversalActionWrapper")
        super().__init__(env)
        self.native_action_meanings = native_meanings_of(env)
        n_native = getattr(env.action_space, "n", None)
        if n_native != len(self.native_action_meanings):
            raise ValueError(f"env declares {len(self.native_action_meanings)} action names "
                             f"but its action space has n={n_native}")
        self.to_native, mask = projection(self.native_action_meanings)
        self.legal_mask = np.array(mask, dtype=bool)
        self.action_space = spaces.Discrete(NUM_PAD_ACTIONS)
        from environment.reward import reward_semantics_of

        self.reward_semantics = reward_semantics_of(env)

    def step(self, action):
        if (isinstance(action, bool) or not isinstance(action, (int, np.integer))
                or not 0 <= int(action) < NUM_PAD_ACTIONS):
            raise ValueError(f"invalid pad action {action!r}: expected int in [0, {NUM_PAD_ACTIONS})")
        return self.env.step(self.to_native[int(action)])
