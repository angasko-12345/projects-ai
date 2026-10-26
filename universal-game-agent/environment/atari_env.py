"""Atari 2600 games (ALE, via ``ale-py``) behind the UGA game contract.

Optional extra: ``pip install -r requirements-atari.txt`` (``ale-py>=0.9``,
which bundles the ROMs; licence GPL-2.0-only). Nothing imports ``ale_py`` at
module scope, so this module -- and every non-Atari path -- works without it.

Contract (same shape as :mod:`environment.arcade`):

* Observation = the raw ``(210, 160, 3)`` uint8 RGB screen, nothing else.
  ``info`` is **always** ``{}``: lives, frame counters, seeds and RAM never
  leave :class:`AtariGame`, so they cannot reach the model.
* ``terminated`` = the game ended (ALE game over); ``truncated`` = the
  per-episode frame cap cut a live game. Never both.
* Reward = the emulator's score delta (raw game score), allowed as feedback.
  :class:`RewardTransform` (``reward_transform: sign``) clips it for training
  only; evaluation always reports raw score.
* **Explicit reset, never autoreset.** UGA's trainer resets finished episodes
  itself (``environment/vec.py``). A ``step()`` after ``terminated`` or
  ``truncated`` raises instead of silently continuing a finished game.

Preprocessing (documented choices, all ALE v5 / CleanRL-consistent):

* the emulator runs with ``frameskip=1``; UGA's ``PreprocessingWrapper(skip=4)``
  repeats each decision for 4 frames and max-pools the last two (CleanRL
  ``MaxAndSkipEnv``), then gray (BT.601) -> bilinear resize -> frame stack;
* sticky actions ``repeat_action_probability`` default 0.25 (ALE v5 default,
  applied per emulator frame inside ALE exactly like v5); the Pong anchor sets 0
  to match ``PongNoFrameskip-v4``;
* minimal action set, then the UGA pad (``environment/universal_actions.py``);
* ``max_episode_frames`` default 108000 (v5's 30 min cap);
* ``noop_max`` default 30: each reset plays 1..30 NOOPs drawn from the reset
  seed (CleanRL ``NoopResetEnv``); 0 disables;
* by default NOT included: ``EpisodicLifeEnv`` and ``FireResetEnv``. Stage 2
  adds both as opt-in **training-only** flags (``episodic_life``, ``fire_reset``)
  for CleanRL parity. They read the ALE's own lives counter and game-over flag
  *inside this adapter*; the only thing that changes outside is the
  ``terminated`` flag the trainer sees (a lost life ends a training episode
  while the game continues). Lives never reach the observation or ``info``.
  Evaluation (``train=False``) always plays full games with both off.

Training telemetry: :meth:`AtariGame.drain_game_scores` hands the trainer the
raw score of every *full game* finished since the last call (so curves report
real game score under sign clipping and episodic life). It is not an
observation and the model never sees it.
"""
from __future__ import annotations

import numpy as np

from environment.reward import GENERIC_SEMANTICS

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:  # pragma: no cover - minimal installs
    gym = None  # type: ignore[assignment]
    spaces = None  # type: ignore[assignment]

SCREEN_SHAPE = (210, 160, 3)
REWARD_TRANSFORMS = ("none", "sign")
OBS_DTYPES = ("float32", "uint8")
ACTION_SPACES = ("universal", "native")
VECTOR_BACKENDS = ("sync", "ale")


def ale_available() -> bool:
    try:
        import ale_py  # noqa: F401
    except ImportError:
        return False
    return True


def _require_ale():
    try:
        import ale_py
        from ale_py.env import AtariEnv
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError("env.type 'atari' needs ale-py: "
                           "pip install -r requirements-atari.txt") from exc
    return ale_py, AtariEnv


_Base = gym.Env if gym is not None else object


class AtariGame(_Base):
    """One ALE game as a raw-pixel UGA game (see the module docstring)."""

    metadata = {"render_modes": ["rgb_array"]}
    reward_semantics = GENERIC_SEMANTICS

    def __init__(self, game: str, *, sticky: float = 0.25, max_episode_frames: int = 108_000,
                 noop_max: int = 30, mode: int | None = None, difficulty: int | None = None,
                 episodic_life: bool = False, fire_reset: bool = False, reset_action_frames: int = 4):
        _, AtariEnv = _require_ale()
        if not isinstance(game, str) or not game:
            raise ValueError(f"game must be an ALE rom id like 'pong', got {game!r}")
        if not 0.0 <= float(sticky) <= 1.0:
            raise ValueError(f"sticky must be in [0, 1], got {sticky!r}")
        if not isinstance(max_episode_frames, int) or max_episode_frames <= 0:
            raise ValueError(f"max_episode_frames must be a positive int, got {max_episode_frames!r}")
        if not isinstance(noop_max, int) or noop_max < 0:
            raise ValueError(f"noop_max must be a non-negative int, got {noop_max!r}")
        self.game = game
        self._ale_env = AtariEnv(game, mode=mode, difficulty=difficulty, obs_type="rgb",
                                 frameskip=1, repeat_action_probability=float(sticky),
                                 full_action_space=False,
                                 max_num_frames_per_episode=int(max_episode_frames))
        self.action_meanings = tuple(self._ale_env.get_action_meanings())
        if self.action_meanings[0] != "NOOP":  # ALE minimal sets start with NOOP
            raise RuntimeError(f"{game}: minimal action set does not start with NOOP")
        self.action_space = spaces.Discrete(len(self.action_meanings))
        self.observation_space = spaces.Box(0, 255, SCREEN_SHAPE, np.uint8)
        self._noop_max = noop_max
        self._noop_rng = np.random.default_rng()
        self._done = True  # must reset() before the first step()
        self._screen = None
        self.episodic_life = bool(episodic_life)
        # CleanRL applies FireResetEnv only when the minimal set has FIRE (at index 1).
        self.fire_reset = bool(fire_reset) and "FIRE" in self.action_meanings
        if self.fire_reset and (self.action_meanings[1] != "FIRE" or len(self.action_meanings) < 3):
            raise RuntimeError(f"{game}: fire_reset needs FIRE at index 1 and >= 3 actions")
        if not isinstance(reset_action_frames, int) or reset_action_frames <= 0:
            raise ValueError(f"reset_action_frames must be a positive int, got {reset_action_frames!r}")
        self._reset_frames = reset_action_frames
        self._real_done = True
        self._lives = 0
        self._game_score = 0.0
        self._finished_scores: list[float] = []

    def _lives_now(self) -> int:
        return int(self._ale_env.ale.lives())

    def _macro(self, action: int, obs):
        """FireReset step: hold ``action`` for one decision (``reset_action_frames``)."""
        for _ in range(self._reset_frames):
            obs, _, term, trunc, _ = self._ale_env.step(action)
            if term or trunc:
                return obs, True
        return obs, False

    def _full_reset(self, seed):
        obs, _ = self._ale_env.reset(seed=None if seed is None else int(seed))
        for _ in range(int(self._noop_rng.integers(1, self._noop_max + 1)) if self._noop_max else 0):
            obs, _, term, trunc, _ = self._ale_env.step(0)
            if term or trunc:
                obs, _ = self._ale_env.reset()
        self._game_score = 0.0
        return obs

    def reset(self, *, seed: int | None = None, options=None):
        if seed is not None:
            self._noop_rng = np.random.default_rng([int(seed), 0x5EED])
        if self.episodic_life and not self._real_done and seed is None:
            # Life lost, game still running: CleanRL EpisodicLifeEnv steps a no-op
            # instead of resetting the game.
            obs, _, term, trunc, _ = self._ale_env.step(0)
            if term or trunc:
                self._record_game_end()
                obs = self._full_reset(None)
        else:
            obs = self._full_reset(seed)
        if self.fire_reset:  # CleanRL FireResetEnv: FIRE (1) then action 2
            for action in (1, 2):
                obs, ended = self._macro(action, obs)
                if ended:
                    self._record_game_end()
                    obs = self._full_reset(None)
        self._lives = self._lives_now()
        self._real_done = False
        self._done = False
        self._screen = obs
        return obs, {}

    def _record_game_end(self):
        self._finished_scores.append(self._game_score)
        self._game_score = 0.0

    def drain_game_scores(self) -> list[float]:
        """Raw scores of full games finished since the last call (training telemetry)."""
        out, self._finished_scores = self._finished_scores, []
        return out

    def step(self, action):
        if self._done:
            raise RuntimeError(f"{self.game}: step() after the episode ended; call reset() "
                               "(UGA environments never autoreset)")
        if (isinstance(action, bool) or not isinstance(action, (int, np.integer))
                or not 0 <= int(action) < self.action_space.n):
            raise ValueError(f"invalid action {action!r} for {self.game} "
                             f"(native actions {self.action_meanings})")
        obs, reward, term, trunc, _ = self._ale_env.step(int(action))
        terminated = bool(term)
        truncated = bool(trunc) and not terminated
        self._game_score += float(reward)
        self._real_done = terminated or truncated
        if self._real_done:
            self._record_game_end()
        elif self.episodic_life:
            lives = self._lives_now()
            if 0 < lives < self._lives:  # CleanRL EpisodicLifeEnv condition
                terminated = True
            self._lives = lives
        self._done = terminated or truncated
        self._screen = obs
        return obs, float(reward), terminated, truncated, {}

    def render(self):
        return self._screen

    def close(self):
        self._ale_env.close()


_WrapperBase = gym.Wrapper if gym is not None else object


class RewardTransform(_WrapperBase):
    """Training-only reward transform: ``none`` (identity) or ``sign`` clipping."""

    def __init__(self, env, mode: str = "none"):
        if mode not in REWARD_TRANSFORMS:
            raise ValueError(f"reward_transform must be one of {REWARD_TRANSFORMS}, got {mode!r}")
        super().__init__(env)
        self.mode = mode
        from environment.reward import reward_semantics_of

        self.reward_semantics = reward_semantics_of(env) if mode == "none" else GENERIC_SEMANTICS

    def step(self, action):
        obs, reward, terminated, truncated, info = self.env.step(action)
        if self.mode == "sign":
            reward = float(np.sign(reward))
        return obs, reward, terminated, truncated, info


def make_atari_factory(game: str, *, obs_size: int = 84, num_stack: int = 4, skip: int = 4,
                       sticky: float = 0.25, max_episode_frames: int = 108_000, noop_max: int = 30,
                       reward_transform: str = "none", action_space: str = "universal",
                       obs_dtype: str = "float32", episodic_life: bool = False,
                       fire_reset: bool = False):
    """Zero-argument factory: ALE game -> UGA pixel pipeline (-> UGA pad)."""
    from environment.preprocessing import PreprocessingWrapper

    if obs_dtype not in OBS_DTYPES:
        raise ValueError(f"obs_dtype must be one of {OBS_DTYPES}, got {obs_dtype!r}")
    if reward_transform not in REWARD_TRANSFORMS:
        raise ValueError(f"reward_transform must be one of {REWARD_TRANSFORMS}, got {reward_transform!r}")
    if action_space not in ACTION_SPACES:
        raise ValueError(f"action_space must be one of {ACTION_SPACES}, got {action_space!r}")
    _require_ale()

    def make():
        extra = {}
        if episodic_life or fire_reset:  # legacy construction stays byte-identical
            extra = {"episodic_life": episodic_life, "fire_reset": fire_reset,
                     "reset_action_frames": skip}
        game_env = AtariGame(game, sticky=sticky, max_episode_frames=max_episode_frames,
                             noop_max=noop_max, **extra)
        if obs_dtype == "float32":
            env = PreprocessingWrapper(game_env, size=obs_size, num_stack=num_stack, skip=skip)
        else:
            env = PreprocessingWrapper(game_env, size=obs_size, num_stack=num_stack, skip=skip,
                                       obs_dtype=obs_dtype)
        if reward_transform != "none":
            env = RewardTransform(env, reward_transform)
        if action_space == "universal":
            from environment.universal_actions import UniversalActionWrapper

            env = UniversalActionWrapper(env)
        return env

    make.atari_game = game
    return make


_ATARI_KEYS = {"type", "game", "obs_size", "num_stack", "skip", "sticky", "max_episode_frames",
               "noop_max", "reward_transform", "action_space", "vector_backend", "obs_dtype",
               "episodic_life", "fire_reset"}


def atari_kwargs(env_cfg: dict, *, train: bool = True) -> dict:
    """Factory kwargs from an ``env:`` mapping.

    ``train=False`` (evaluation) -> raw reward and full games: the training-only
    ``reward_transform``, ``episodic_life`` and ``fire_reset`` are dropped.
    Stage 2 keys appear only when set, so legacy factories are unchanged.
    """
    import warnings

    for key in env_cfg:
        if key not in _ATARI_KEYS:
            warnings.warn(f"env: ignoring unknown atari config key {key!r}", UserWarning, stacklevel=3)
    extra = {}
    if "obs_dtype" in env_cfg:
        extra["obs_dtype"] = str(env_cfg["obs_dtype"])
    for key in ("episodic_life", "fire_reset"):
        if train and env_cfg.get(key, False):
            if not isinstance(env_cfg[key], bool):
                raise ValueError(f"env.{key} must be a bool, got {env_cfg[key]!r}")
            extra[key] = True
    return {
        **extra,
        "obs_size": int(env_cfg.get("obs_size", 84)),
        "num_stack": int(env_cfg.get("num_stack", 4)),
        "skip": int(env_cfg.get("skip", 4)),
        "sticky": float(env_cfg.get("sticky", 0.25)),
        "max_episode_frames": int(env_cfg.get("max_episode_frames", 108_000)),
        "noop_max": int(env_cfg.get("noop_max", 30)),
        "reward_transform": str(env_cfg.get("reward_transform", "none")) if train else "none",
        "action_space": str(env_cfg.get("action_space", "universal")),
    }


def make_atari_env_from_config(env_cfg: dict, *, train: bool = True):
    """``env.type: atari`` -> zero-argument factory (see :func:`make_atari_factory`)."""
    if "game" not in env_cfg:
        raise ValueError("env.type 'atari' needs env.game (an ALE rom id, e.g. 'pong')")
    backend = str(env_cfg.get("vector_backend", "sync"))
    if backend not in VECTOR_BACKENDS:
        raise ValueError(f"env.vector_backend must be one of {VECTOR_BACKENDS}, got {backend!r}")
    kwargs = atari_kwargs(env_cfg, train=train)
    make = make_atari_factory(str(env_cfg["game"]), **kwargs)
    if backend == "ale":
        def make_vector(num_envs: int):
            return AtariVectorEnvAdapter(str(env_cfg["game"]), num_envs, **kwargs)
        make.make_vector = make_vector
    return make


def check_atari_env(env, seed: int = 0, steps: int = 200) -> None:
    """Assert the raw-game contract on an :class:`AtariGame`; raises ``AssertionError``."""
    obs, info = env.reset(seed=seed)
    assert info == {}, f"reset info must be empty, got {info!r}"
    assert obs.dtype == np.uint8 and obs.shape == SCREEN_SHAPE
    rng = np.random.default_rng(seed)
    for _ in range(steps):
        obs, reward, terminated, truncated, info = env.step(int(rng.integers(0, env.action_space.n)))
        assert info == {}, f"step info must be empty, got {info!r}"
        assert obs.dtype == np.uint8 and obs.shape == SCREEN_SHAPE
        assert isinstance(reward, float)
        assert isinstance(terminated, bool) and isinstance(truncated, bool)
        assert not (terminated and truncated)
        assert np.array_equal(obs, env.render())
        if terminated or truncated:
            obs, info = env.reset()
            assert info == {}


# -- optional C++ vector backend ----------------------------------------------
from environment.vec import SyncVectorEnv  # noqa: E402 - stdlib-only module


class AtariVectorEnvAdapter(SyncVectorEnv):
    """``ale_py.vector_env.AtariVectorEnv`` behind the UGA no-autoreset contract.

    ALE's vector env autoresets. It is run in ``SameStep`` mode: on a finished
    slot ALE returns the *new* episode's first obs and puts the terminal obs in
    ``info["final_obs"]``. This adapter returns the terminal obs untouched (as
    the trainer requires) and hands the already-reset obs back from
    :meth:`reset_env`. Consequences, documented rather than hidden: the reset
    happened eagerly inside ALE (its seed is ALE's, not ``reset_env``'s), and
    preprocessing is ALE's C++ pipeline (gray + area resize + stack + max-pool),
    not UGA's bilinear one, so its pixels differ slightly from the single-env
    path. ``info`` is dropped. Opt-in only (``env.vector_backend: ale``).
    """

    uga_action_space = None

    def __init__(self, game: str, num_envs: int, *, obs_size: int = 84, num_stack: int = 4,
                 skip: int = 4, sticky: float = 0.25, max_episode_frames: int = 108_000,
                 noop_max: int = 30, reward_transform: str = "none", action_space: str = "universal",
                 num_threads: int = 0, obs_dtype: str = "float32", episodic_life: bool = False,
                 fire_reset: bool = False):
        from ale_py.vector_env import AtariVectorEnv

        if obs_dtype not in OBS_DTYPES:
            raise ValueError(f"obs_dtype must be one of {OBS_DTYPES}, got {obs_dtype!r}")
        self._uint8 = obs_dtype == "uint8"

        self._vec = AtariVectorEnv(
            game, num_envs, num_threads=num_threads, max_num_frames_per_episode=max_episode_frames,
            repeat_action_probability=sticky, full_action_space=False, autoreset_mode="SameStep",
            img_height=obs_size, img_width=obs_size, grayscale=True, stack_num=num_stack,
            frameskip=skip, maxpool=True, noop_max=noop_max, episodic_life=bool(episodic_life),
            reward_clipping=False, use_fire_reset=bool(fire_reset))
        self._n = num_envs
        self._closed = False
        self._pending = [None] * num_envs
        self._reward_transform = reward_transform
        meanings = None
        try:
            probe = AtariGame(game, sticky=0.0, noop_max=0)
            meanings = probe.action_meanings
            probe.close()
        except Exception:  # pragma: no cover - same ROM, cannot really fail
            raise
        from environment.universal_actions import PAD_VERSION, projection

        self.native_action_meanings = meanings
        if action_space == "universal":
            self._to_native, _ = projection(meanings)
            self.uga_action_space = PAD_VERSION
            n_actions = 18
        else:
            self._to_native = tuple(range(len(meanings)))
            n_actions = len(meanings)
        self._action_space = spaces.Discrete(n_actions)
        shape = (num_stack, obs_size, obs_size)
        self._obs_space = (spaces.Box(0, 255, shape, np.uint8) if self._uint8
                           else spaces.Box(0.0, 1.0, shape, np.float32))
        self._envs = []  # no per-slot python envs

    @property
    def num_envs(self) -> int:
        return self._n

    @property
    def single_action_space(self):
        return self._action_space

    @property
    def single_observation_space(self):
        return self._obs_space

    def _f(self, obs) -> np.ndarray:
        if self._uint8:
            return np.array(obs, dtype=np.uint8, copy=True)
        return np.asarray(obs, dtype=np.float32) / 255.0

    def reset(self, *, seeds=None):
        if seeds is not None and len(seeds) != self._n:
            raise ValueError(f"need {self._n} seeds, got {len(seeds)}")
        seed = None if seeds is None else int(seeds[0])
        obs, _ = self._vec.reset(seed=seed)
        self._pending = [None] * self._n
        return [self._f(o) for o in obs], [{} for _ in range(self._n)]

    def step(self, actions):
        if len(actions) != self._n:
            raise ValueError(f"need {self._n} actions, got {len(actions)}")
        if any(p is not None for p in self._pending):
            raise RuntimeError("a finished slot was not reset_env()'d before step()")
        native = np.array([self._to_native[int(a)] for a in actions], dtype=np.int32)
        obs, rew, term, trunc, info = self._vec.step(native)
        out = []
        terms = [bool(t) for t in term]
        truncs = [bool(t) and not bool(x) for t, x in zip(trunc, term, strict=True)]
        for i in range(self._n):
            if terms[i] or truncs[i]:
                self._pending[i] = self._f(obs[i])
                out.append(self._f(info["final_obs"][i]))
            else:
                out.append(self._f(obs[i]))
        rewards = [float(np.sign(r)) if self._reward_transform == "sign" else float(r) for r in rew]
        return out, rewards, terms, truncs, [{} for _ in range(self._n)]

    def reset_env(self, index, *, seed=None):
        if not 0 <= index < self._n:
            raise IndexError(f"env index out of range: {index!r} with {self._n} envs")
        if self._pending[index] is None:
            raise RuntimeError("AtariVectorEnvAdapter can only reset a slot that just finished "
                               "(ALE resets it eagerly)")
        obs, self._pending[index] = self._pending[index], None
        return obs, {}

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._vec.close()
