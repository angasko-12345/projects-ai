"""Procedural pixel minigames that share toy Pong's contract, for cross-game work.

Common game contract (an extension of the Gymnasium API ``ToyPongEnv`` already
follows, not a replacement):

* ``action_space`` is ``Discrete(3)`` with :data:`ACTION_MEANINGS`
  ``("NOOP", "LEFT", "RIGHT")`` in every game -- toy Pong's own action set,
  so one policy head drives every game.
* Observations are the rendered frame only: ``(48, 64, 3)`` uint8 RGB. Every
  object position, velocity, score, brick map and variation parameter lives in
  underscore-prefixed attributes and never reaches ``obs`` or ``info``;
  ``info`` is always ``{}``.
* ``terminated`` means the game itself ended (a miss, a collision, or a
  cleared wall); ``truncated`` means the ``max_steps`` time limit cut a live
  episode. They are never both true.
* Rewards are floats in ``[-1, 1]``: ``+1`` per scoring event, ``-1`` on the
  event that ends the game, ``0`` otherwise.
* ``reset(seed=s)`` reseeds the env's own RNG, so an episode is a pure
  function of its seed and the actions; ``reset()`` without a seed continues
  that RNG. Each reset draws a fresh *variation* (palette, sizes, speeds) from
  that RNG, so no two episodes look or move exactly alike and a policy cannot
  memorise one layout.

Games: ``pong`` (toy Pong dynamics with variation), ``catch``, ``breakout``
(training set); ``volley`` (held-out near-transfer game: gravity arcs, new
shapes) and ``dodge`` (held-out far game with the opposite objective).

:func:`make_game_factory` wraps a game in the standard
:class:`~environment.preprocessing.PreprocessingWrapper`, which is the only
path from a game to a model. :func:`check_game_env` validates the contract.
"""
from __future__ import annotations

import math

import numpy as np

from environment.preprocessing import PreprocessingWrapper
from environment.reward import GENERIC_SEMANTICS
from environment.toy_pong import _Base, spaces
from environment.toy_pong import ToyPongEnv

ACTION_MEANINGS = ("NOOP", "LEFT", "RIGHT")
NOOP, LEFT, RIGHT = 0, 1, 2
WIDTH, HEIGHT = 64, 48
_LUMA = np.array([0.299, 0.587, 0.114])


def _color(rng: np.random.Generator, lo: float, hi: float) -> np.ndarray:
    """Random RGB colour whose BT.601 luma lies in ``[lo, hi]`` (rejection)."""
    for _ in range(200):
        c = rng.integers(0, 256, size=3)
        if lo <= float(c @ _LUMA) <= hi:
            return c.astype(np.uint8)
    return np.full(3, int((lo + hi) / 2), dtype=np.uint8)


def _palette(rng: np.random.Generator, n: int) -> tuple[np.ndarray, list[np.ndarray]]:
    """Dark background plus ``n`` clearly brighter object colours."""
    bg = _color(rng, 0, 70)
    lo = float(bg @ _LUMA) + 90
    return bg, [_color(rng, lo, 255) for _ in range(n)]


def _validate_action(action) -> int:
    if (isinstance(action, bool) or not isinstance(action, (int, np.integer))
            or int(action) not in (NOOP, LEFT, RIGHT)):
        raise ValueError(f"Invalid action {action!r}: expected 0 (NOOP), 1 (LEFT), or 2 (RIGHT)")
    return int(action)


class ArcadeGame(_Base):
    """Base class: seeding, variation, step bookkeeping, contract-shaped API."""

    metadata = {"render_modes": ["rgb_array"]}
    reward_semantics = GENERIC_SEMANTICS
    action_meanings = ACTION_MEANINGS
    _GAME_ID = 0

    def __init__(self, max_steps: int = 500):
        super().__init__()
        if not isinstance(max_steps, int) or max_steps <= 0:
            raise ValueError(f"max_steps must be a positive int, got {max_steps!r}")
        self._w, self._h, self._max_steps = WIDTH, HEIGHT, max_steps
        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(0, 255, (HEIGHT, WIDTH, 3), np.uint8)
        self._rng = np.random.default_rng()
        self._steps = 0

    def reset(self, *, seed: int | None = None, options=None):
        if seed is not None:
            # Mixing in a per-game id keeps one seed from giving every game the
            # same palette/variation draw.
            self._rng = np.random.default_rng([int(seed), self._GAME_ID])
        self._steps = 0
        self._new_episode()
        return self._render_frame(), {}

    def step(self, action):
        action = _validate_action(action)
        reward, terminated = self._advance(action)
        self._steps += 1
        truncated = (not terminated) and self._steps >= self._max_steps
        return self._render_frame(), float(reward), bool(terminated), bool(truncated), {}

    def render(self):
        return self._render_frame()

    def close(self):
        pass

    # -- shared helpers ---------------------------------------------------
    def _move_paddle(self, action: int) -> None:
        if action == LEFT:
            self._px = max(0.0, self._px - self._pspeed)
        elif action == RIGHT:
            self._px = min(float(self._w - self._pw), self._px + self._pspeed)

    def _blank(self) -> np.ndarray:
        frame = np.empty((self._h, self._w, 3), dtype=np.uint8)
        frame[:] = self._bg
        return frame

    @staticmethod
    def _rect(frame, x, y, w, h, color) -> None:
        x0, y0 = int(round(x)), int(round(y))
        x0c, y0c = max(x0, 0), max(y0, 0)
        x1, y1 = min(x0 + int(w), frame.shape[1]), min(y0 + int(h), frame.shape[0])
        if x1 > x0c and y1 > y0c:
            frame[y0c:y1, x0c:x1] = color

    # subclasses implement these
    def _new_episode(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _advance(self, action: int) -> tuple[float, bool]:  # pragma: no cover
        raise NotImplementedError

    def _render_frame(self) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError


class VariedPongEnv(ToyPongEnv):
    """Toy Pong with per-episode palette, paddle width/speed and ball speed.

    Same rules, reward and hit/miss semantics as :class:`ToyPongEnv` (whose
    code is untouched); only the per-episode parameters and colours vary.
    """

    action_meanings = ACTION_MEANINGS

    def __init__(self, max_steps: int = 500):
        super().__init__(width=WIDTH, height=HEIGHT, max_steps=max_steps)
        self._bg = np.zeros(3, dtype=np.uint8)
        self._fg = [np.full(3, 255, dtype=np.uint8)] * 2

    def reset(self, *, seed: int | None = None, options=None):
        if seed is not None:
            self._rng.seed(seed)
        rng = np.random.default_rng(self._rng.getrandbits(63))
        self._paddle_w = int(rng.integers(8, 15))
        self._paddle_speed = int(rng.integers(2, 5))
        self._ball_speed = int(rng.integers(2, 4))
        self._bg, self._fg = _palette(rng, 2)
        return super().reset(seed=None, options=options)

    def _render_frame(self) -> np.ndarray:
        frame = np.empty((self._h, self._w, 3), dtype=np.uint8)
        frame[:] = self._bg
        frame[self._paddle_y:self._paddle_y + self._paddle_h,
              self._paddle_x:self._paddle_x + self._paddle_w] = self._fg[0]
        frame[self._by:self._by + self._ball_size, self._bx:self._bx + self._ball_size] = self._fg[1]
        return frame


class CatchEnv(ArcadeGame):
    """Catch falling fruit: +1 per catch, -1 on the first miss (terminates)."""

    _GAME_ID = 1

    def _new_episode(self) -> None:
        r = self._rng
        self._bg, (self._pc, self._fc) = _palette(r, 2)
        self._pw = int(r.integers(8, 15))
        self._pspeed = float(r.integers(2, 5))
        self._fs = int(r.integers(2, 4))
        self._vy_range = (0.6 + 0.4 * r.random(), 1.0 + 0.6 * r.random())
        self._py = self._h - 4
        self._px = float((self._w - self._pw) // 2)
        self._spawn()

    def _spawn(self) -> None:
        r = self._rng
        self._fx = float(r.integers(0, self._w - self._fs + 1))
        self._fy = 0.0
        self._fvy = float(r.uniform(*self._vy_range))
        self._fvx = float(r.uniform(-0.5, 0.5))

    def _advance(self, action: int) -> tuple[float, bool]:
        self._move_paddle(action)
        self._fx += self._fvx
        if self._fx <= 0 or self._fx >= self._w - self._fs:
            self._fx = min(max(self._fx, 0.0), float(self._w - self._fs))
            self._fvx = -self._fvx
        self._fy += self._fvy
        if self._fy + self._fs >= self._py:
            if self._fx + self._fs > self._px and self._fx < self._px + self._pw:
                self._spawn()
                return 1.0, False
            return -1.0, True
        return 0.0, False

    def _render_frame(self) -> np.ndarray:
        frame = self._blank()
        self._rect(frame, self._px, self._py, self._pw, 2, self._pc)
        self._rect(frame, self._fx, self._fy, self._fs, self._fs, self._fc)
        return frame


class BreakoutEnv(ArcadeGame):
    """Breakout-lite: +1 per brick, -1 on a miss; clearing the wall terminates."""

    _GAME_ID = 2

    _BRICK_W, _BRICK_H, _TOP = 8, 3, 4

    def _new_episode(self) -> None:
        r = self._rng
        self._bg, (self._pc, self._bc, *self._row_c) = _palette(r, 5)
        self._rows = int(r.integers(2, 4))
        self._bricks = np.ones((self._rows, self._w // self._BRICK_W), dtype=bool)
        self._pw = int(r.integers(10, 17))
        self._pspeed = float(r.integers(2, 5))
        self._speed = float(r.uniform(1.0, 1.6))
        self._py = self._h - 4
        self._px = float((self._w - self._pw) // 2)
        self._bs = 2
        self._bx = self._px + self._pw / 2 - 1 + float(r.uniform(-6, 6))
        self._by = float(self._py - 12)
        ang = float(r.uniform(-0.8, 0.8))
        self._vx, self._vy = self._speed * math.sin(ang), -self._speed * math.cos(ang)

    def _advance(self, action: int) -> tuple[float, bool]:
        self._move_paddle(action)
        self._bx += self._vx
        self._by += self._vy
        if self._bx <= 0 or self._bx >= self._w - self._bs:
            self._bx = min(max(self._bx, 0.0), float(self._w - self._bs))
            self._vx = -self._vx
        if self._by <= 0:
            self._by, self._vy = 0.0, abs(self._vy)
        reward = 0.0
        cx, cy = self._bx + self._bs / 2, self._by + self._bs / 2
        row = int((cy - self._TOP) // self._BRICK_H)
        col = int(cx // self._BRICK_W)
        if 0 <= row < self._rows and 0 <= col < self._bricks.shape[1] and cy >= self._TOP \
                and self._bricks[row, col]:
            self._bricks[row, col] = False
            self._vy = -self._vy
            reward = 1.0
            if not self._bricks.any():
                return reward, True
        if self._vy > 0 and self._by + self._bs >= self._py and self._by < self._py + 2:
            if self._bx + self._bs > self._px and self._bx < self._px + self._pw:
                off = (cx - (self._px + self._pw / 2)) / (self._pw / 2)
                ang = max(-1.0, min(1.0, off)) * 1.0
                self._vx, self._vy = self._speed * math.sin(ang), -self._speed * math.cos(ang)
                self._by = float(self._py - self._bs)
        if self._by >= self._h:
            return -1.0, True
        return reward, False

    def _render_frame(self) -> np.ndarray:
        frame = self._blank()
        for rr in range(self._rows):
            for cc in np.nonzero(self._bricks[rr])[0]:
                self._rect(frame, cc * self._BRICK_W + 1, self._TOP + rr * self._BRICK_H,
                           self._BRICK_W - 1, self._BRICK_H - 1, self._row_c[rr % 3])
        self._rect(frame, self._px, self._py, self._pw, 2, self._pc)
        self._rect(frame, self._bx, self._by, self._bs, self._bs, self._bc)
        return frame


class VolleyEnv(ArcadeGame):
    """HELD-OUT game: keep a gravity ball up. +1 per bounce, -1 when it lands.

    Differs from the training games in dynamics (parabolic arcs, sideways wind)
    and visuals (3x3 cross-shaped ball, paddle higher up, a ground strip and a
    decorative net post that do not collide).
    """

    _GAME_ID = 3

    def _new_episode(self) -> None:
        r = self._rng
        self._bg, (self._pc, self._bc, self._gc, self._nc) = _palette(r, 4)
        self._pw = int(r.integers(9, 15))
        self._pspeed = float(r.integers(2, 5))
        self._g = float(r.uniform(0.05, 0.10))
        self._wind = float(r.uniform(-0.012, 0.012))
        self._py = self._h - 7
        self._px = float((self._w - self._pw) // 2)
        self._net_x = int(r.integers(8, self._w - 8))
        self._serve()

    def _serve(self) -> None:
        r = self._rng
        self._bx = float(r.uniform(4, self._w - 7))
        self._by = 2.0
        self._vx = float(r.uniform(-0.7, 0.7))
        self._vy = 0.0

    def _advance(self, action: int) -> tuple[float, bool]:
        self._move_paddle(action)
        self._vy += self._g
        self._vx += self._wind
        self._vx = max(-1.5, min(1.5, self._vx))
        self._bx += self._vx
        self._by += self._vy
        if self._bx <= 0 or self._bx >= self._w - 3:
            self._bx = min(max(self._bx, 0.0), float(self._w - 3))
            self._vx = -self._vx
        if self._by <= 0:
            self._by, self._vy = 0.0, abs(self._vy)
        if self._vy > 0 and self._by + 3 >= self._py and self._by < self._py + 2:
            if self._bx + 3 > self._px and self._bx < self._px + self._pw:
                apex = float(self._rng.uniform(4, 16))  # bounce height varies
                self._vy = -math.sqrt(2 * self._g * max(self._py - 3 - apex, 1.0))
                off = (self._bx + 1.5 - (self._px + self._pw / 2)) / (self._pw / 2)
                self._vx = max(-1.5, min(1.5, self._vx + 0.6 * off))
                self._by = float(self._py - 3)
                return 1.0, False
        if self._by + 3 >= self._h - 2:
            return -1.0, True
        return 0.0, False

    def _render_frame(self) -> np.ndarray:
        frame = self._blank()
        frame[self._h - 2:, :] = self._gc
        self._rect(frame, self._net_x, self._h - 12, 1, 10, self._nc)
        self._rect(frame, self._px, self._py, self._pw, 2, self._pc)
        x, y = self._bx, self._by
        self._rect(frame, x + 1, y, 1, 3, self._bc)
        self._rect(frame, x, y + 1, 3, 1, self._bc)
        return frame


class DodgeEnv(ArcadeGame):
    """FAR HELD-OUT game: avoid falling blocks. +1 per dodged block, -1 on contact.

    Same reward shape as the training games but the opposite objective
    (moving *under* the object is what loses here).
    """

    _GAME_ID = 4

    def _new_episode(self) -> None:
        r = self._rng
        self._bg, (self._pc, self._bc) = _palette(r, 2)
        self._pw = int(r.integers(4, 7))
        self._pspeed = float(r.integers(2, 4))
        self._vy_range = (0.8, 0.8 + 0.7 * r.random())
        self._py = self._h - 5
        self._px = float((self._w - self._pw) // 2)
        self._blocks: list[list[float]] = []
        self._spawn_gap = int(r.integers(14, 24))
        self._timer = 0
        self._spawn()

    def _spawn(self) -> None:
        r = self._rng
        bw = int(r.integers(5, 10))
        if r.random() < 0.5:  # aimed at the player's current column
            x = min(max(self._px + self._pw / 2 - bw / 2, 0.0), float(self._w - bw))
        else:
            x = float(r.integers(0, self._w - bw + 1))
        self._blocks.append([float(x), 0.0, float(bw),
                             float(r.uniform(*self._vy_range))])

    def _advance(self, action: int) -> tuple[float, bool]:
        self._move_paddle(action)
        self._timer += 1
        if self._timer >= self._spawn_gap:
            self._timer = 0
            self._spawn()
        reward, keep = 0.0, []
        for blk in self._blocks:
            blk[1] += blk[3]
            x, y, bw = blk[0], blk[1], blk[2]
            if y + 3 >= self._py and y < self._py + 3 and x + bw > self._px and x < self._px + self._pw:
                return -1.0, True
            if y >= self._h:
                reward += 1.0
            else:
                keep.append(blk)
        self._blocks = keep
        return min(reward, 1.0), False

    def _render_frame(self) -> np.ndarray:
        frame = self._blank()
        for x, y, bw, _ in self._blocks:
            self._rect(frame, x, y, bw, 3, self._bc)
        self._rect(frame, self._px, self._py, self._pw, 3, self._pc)
        return frame


GAMES = {
    "pong": VariedPongEnv,
    "catch": CatchEnv,
    "breakout": BreakoutEnv,
    "volley": VolleyEnv,
    "dodge": DodgeEnv,
}
TRAIN_GAMES = ("pong", "catch", "breakout")
HELDOUT_GAME = "volley"
FAR_HELDOUT_GAME = "dodge"


def make_game(name: str, max_steps: int = 500):
    """Raw-frame game instance from the registry."""
    if name not in GAMES:
        raise ValueError(f"unknown game {name!r}; known: {sorted(GAMES)}")
    return GAMES[name](max_steps=max_steps)


def make_game_factory(name: str, obs_size: int = 84, num_stack: int = 4, skip: int = 1,
                      max_steps: int = 500):
    """Zero-argument factory: game -> standard pixel ``PreprocessingWrapper``."""
    make_game(name, max_steps).close()  # fail fast on a bad name

    def make():
        return PreprocessingWrapper(make_game(name, max_steps), size=obs_size,
                                    num_stack=num_stack, skip=skip)
    return make


def check_game_env(env, seed: int = 0, steps: int = 300) -> None:
    """Assert the common contract on a raw-frame game; raises ``AssertionError``."""
    assert getattr(env.action_space, "n", None) == len(ACTION_MEANINGS), "action space must be Discrete(3)"
    obs, info = env.reset(seed=seed)
    assert info == {}, f"reset info must be empty, got {info!r}"
    assert isinstance(obs, np.ndarray) and obs.dtype == np.uint8 and obs.shape == (HEIGHT, WIDTH, 3), \
        f"obs must be uint8 {(HEIGHT, WIDTH, 3)}, got {getattr(obs, 'shape', None)} {getattr(obs, 'dtype', None)}"
    rng = np.random.default_rng(seed)
    for _ in range(steps):
        obs, reward, terminated, truncated, info = env.step(int(rng.integers(0, 3)))
        assert info == {}, f"step info must be empty, got {info!r}"
        assert obs.dtype == np.uint8 and obs.shape == (HEIGHT, WIDTH, 3)
        assert isinstance(reward, float) and -1.0 <= reward <= 1.0, f"reward out of range: {reward!r}"
        assert isinstance(terminated, bool) and isinstance(truncated, bool)
        assert not (terminated and truncated), "terminated and truncated are exclusive"
        assert np.array_equal(obs, env.render()), "obs must equal the rendered frame"
        if terminated or truncated:
            obs, info = env.reset()
            assert info == {}
