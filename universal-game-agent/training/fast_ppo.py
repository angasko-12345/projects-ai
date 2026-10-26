"""Fast, batched PPO engine (``ppo.engine: fast``) with CleanRL-faithful options.

Stage 2 adds this engine **next to** the legacy ``training/ppo.py:PPOTrainer``,
which is not edited: every existing config keeps the legacy engine and its
byte-identical behaviour. Opt in with ``ppo.engine: fast`` or
``ppo.preset: cleanrl``.

What is different from the legacy engine (and why):

* **Throughput**: one batched forward over every env slot per step under
  ``torch.inference_mode``; rollout buffers preallocated as ``[T, N, ...]``
  tensors on the training device; observations stored as uint8 (0..255) and
  scaled by the model (``agent/model.py:ActorCritic.encode``); one stacked
  numpy -> tensor copy per step instead of per-slot copies; recurrent replay
  batches every slot of a minibatch through one masked GRU loop instead of
  per-slot, per-segment ``forward_sequence`` calls. ``ppo.device`` (``cpu`` |
  ``cuda`` | ``auto``) and ``ppo.torch_threads`` are honoured.
* **CleanRL ``ppo_atari.py`` knobs** (https://docs.cleanrl.dev/rl-algorithms/ppo/,
  ``cleanrl/ppo_atari.py`` ``Args``): Adam ``eps``, linear LR annealing,
  ``num_minibatches`` with per-epoch shuffling (flat samples for a
  frame-stack model; whole slots for a recurrent one, as ``ppo_atari_lstm.py``),
  per-minibatch advantage normalisation, optional value clipping, and
  ``approx_kl`` / ``old_approx_kl`` / ``clipfrac`` / ``explained_variance`` /
  ``learning_rate`` telemetry (appended after the legacy CSV columns).
  ``ppo.preset: cleanrl`` sets all of them to the cited values.
* **Per-game return normalisation** (``ppo.reward_norm: per_game``): gym
  ``NormalizeReward`` logic with one running return-variance estimate per game
  id, so large-score games do not dominate a multi-game update. Game ids come
  from the env factories, never from pixels; the model never sees them.
* **Boundaries**: same contract as legacy -- terminated -> value 0 and hidden
  zeroed; truncated -> bootstrap from V(final pre-reset obs, its hidden); the
  env never autoresets. One deliberate difference: the GAE lambda-accumulation
  is cut at every done (terminated *or* truncated), so a truncated episode
  never receives advantage from the next episode in the same slot.
* **Checkpoints** carry ``schema_version: 2`` (legacy files have none = v1).

No curiosity support (legacy-only feature); asking for it raises.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Categorical

from agent.model import ActorCritic
from training.ppo import PPOConfig, declared_action_space
from training.telemetry import FAST_FIELDNAMES, PPOUpdateCSVWriter

CHECKPOINT_SCHEMA_VERSION = 2
ENGINES = ("legacy", "fast")
PRESETS = ("cleanrl",)
ADV_NORMS = ("global", "minibatch")
REWARD_NORMS = ("none", "per_game")
DEVICES = ("cpu", "cuda", "auto")

# CleanRL ppo_atari.py Args (https://github.com/vwxyzjn/cleanrl/blob/master/cleanrl/ppo_atari.py),
# fetched 2026-10-26. num_envs/num_steps/total_timesteps stay whatever the config says.
CLEANRL_PRESET = {
    "engine": "fast", "learning_rate": 2.5e-4, "anneal_lr": True, "gamma": 0.99,
    "gae_lambda": 0.95, "num_minibatches": 4, "update_epochs": 4, "adv_norm": "minibatch",
    "clip_range": 0.1, "clip_vloss": True, "entropy_coef": 0.01, "value_coef": 0.5,
    "max_grad_norm": 0.5, "adam_eps": 1e-5, "shuffle_minibatches": True,
}


@dataclass
class FastPPOConfig(PPOConfig):
    engine: str = "fast"
    preset: str | None = None
    adam_eps: float = 1e-8
    anneal_lr: bool = False
    # Annealing horizon; None = total_timesteps. Chunked runners set the full
    # budget here so a run stopped and resumed per chunk anneals once, not per chunk.
    anneal_total_timesteps: int | None = None
    # 0 = legacy meaning: ceil(rollout_length / minibatch_size) minibatches.
    num_minibatches: int = 0
    shuffle_minibatches: bool = False
    adv_norm: str = "global"
    clip_vloss: bool = True
    reward_norm: str = "none"
    torch_threads: int = 0
    device: str = "cpu"

    def __post_init__(self):
        super().__post_init__()
        if self.engine != "fast":
            raise ValueError(f"FastPPOConfig needs engine 'fast', got {self.engine!r}")
        if self.preset is not None and self.preset not in PRESETS:
            raise ValueError(f"ppo.preset must be one of {PRESETS} or null, got {self.preset!r}")
        if not self.adam_eps > 0:
            raise ValueError(f"adam_eps must be > 0, got {self.adam_eps!r}")
        for name in ("anneal_lr", "shuffle_minibatches", "clip_vloss"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a bool, got {getattr(self, name)!r}")
        if self.anneal_total_timesteps is not None and (
                not isinstance(self.anneal_total_timesteps, int) or self.anneal_total_timesteps <= 0):
            raise ValueError("anneal_total_timesteps must be a positive int or null")
        if not isinstance(self.num_minibatches, int) or self.num_minibatches < 0:
            raise ValueError(f"num_minibatches must be an int >= 0, got {self.num_minibatches!r}")
        if self.adv_norm not in ADV_NORMS:
            raise ValueError(f"adv_norm must be one of {ADV_NORMS}, got {self.adv_norm!r}")
        if self.reward_norm not in REWARD_NORMS:
            raise ValueError(f"reward_norm must be one of {REWARD_NORMS}, got {self.reward_norm!r}")
        if not isinstance(self.torch_threads, int) or self.torch_threads < 0:
            raise ValueError(f"torch_threads must be an int >= 0, got {self.torch_threads!r}")
        if self.device not in DEVICES:
            raise ValueError(f"device must be one of {DEVICES}, got {self.device!r}")

    @classmethod
    def from_dict(cls, data: dict) -> FastPPOConfig:
        """Preset first, then explicit keys (explicit always wins)."""
        import warnings

        data = dict(data)
        preset = data.get("preset")
        merged = dict(CLEANRL_PRESET) if preset == "cleanrl" else {}
        merged.update(data)
        merged["engine"] = "fast"
        known = {f.name for f in fields(cls)}
        for key in merged:
            if key not in known:
                warnings.warn(f"ppo: ignoring unknown config key {key!r}", UserWarning, stacklevel=3)
        return cls(**{k: v for k, v in merged.items() if k in known})


def wants_fast_engine(ppo_cfg: dict | None) -> bool:
    """True when a ``ppo:`` mapping selects this engine (``engine: fast`` or a preset)."""
    ppo_cfg = ppo_cfg or {}
    engine = ppo_cfg.get("engine", "legacy")
    if engine not in ENGINES:
        raise ValueError(f"ppo.engine must be one of {ENGINES}, got {engine!r}")
    return engine == "fast" or ppo_cfg.get("preset") is not None


def resolve_device(name: str) -> torch.device:
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("ppo.device is 'cuda' but torch.cuda.is_available() is False")
    return torch.device(name)


class RunningMeanStd:
    """Parallel-variance running moments (gym ``RunningMeanStd``)."""

    def __init__(self, epsilon: float = 1e-4):
        self.mean, self.var, self.count = 0.0, 1.0, float(epsilon)

    def update(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=np.float64).reshape(-1)
        if x.size == 0:
            return
        b_mean, b_var, b_count = float(x.mean()), float(x.var()), float(x.size)
        delta = b_mean - self.mean
        tot = self.count + b_count
        self.mean = self.mean + delta * b_count / tot
        m2 = self.var * self.count + b_var * b_count + delta ** 2 * self.count * b_count / tot
        self.var, self.count = m2 / tot, tot

    def state_dict(self) -> dict:
        return {"mean": self.mean, "var": self.var, "count": self.count}

    def load_state_dict(self, state: dict) -> None:
        self.mean, self.var, self.count = float(state["mean"]), float(state["var"]), float(state["count"])


class PerGameReturnNormalizer:
    """gym ``NormalizeReward`` with one return-variance estimate per game id.

    ``r_norm = r / sqrt(var_game(discounted return) + eps)``. Each slot keeps
    its discounted return (reset at every done); the slot's game id picks the
    estimate it updates and is scaled by. Group ids never reach the model.
    """

    def __init__(self, groups, gamma: float, epsilon: float = 1e-8):
        self.groups = [str(g) for g in groups]
        self.gamma, self.epsilon = float(gamma), float(epsilon)
        self.rms = {g: RunningMeanStd() for g in sorted(set(self.groups))}
        self.returns = np.zeros(len(self.groups), dtype=np.float64)
        self._idx = {g: np.array([i for i, x in enumerate(self.groups) if x == g]) for g in self.rms}

    def __call__(self, rewards: np.ndarray, dones: np.ndarray) -> np.ndarray:
        rewards = np.asarray(rewards, dtype=np.float64)
        self.returns = self.returns * self.gamma + rewards
        out = np.empty_like(rewards)
        for g, idx in self._idx.items():
            self.rms[g].update(self.returns[idx])
            out[idx] = rewards[idx] / math.sqrt(self.rms[g].var + self.epsilon)
        self.returns[np.asarray(dones, dtype=bool)] = 0.0
        return out

    def scales(self) -> dict:
        return {g: math.sqrt(r.var + self.epsilon) for g, r in self.rms.items()}

    def state_dict(self) -> dict:
        return {"groups": self.groups, "returns": self.returns.tolist(),
                "rms": {g: r.state_dict() for g, r in self.rms.items()}}

    def load_state_dict(self, state: dict) -> None:
        for g, s in state.get("rms", {}).items():
            if g in self.rms:
                self.rms[g].load_state_dict(s)
        if len(state.get("returns", [])) == len(self.returns):
            self.returns = np.asarray(state["returns"], dtype=np.float64)


class _SingleAsVector:
    """Present one env through the vector API (the fast engine is vector-only)."""

    def __init__(self, env):
        self._envs = [env]

    num_envs = 1

    def reset(self, *, seeds=None):
        obs, info = self._envs[0].reset(seed=None if seeds is None else seeds[0])
        return [obs], [info]

    def step(self, actions):
        obs, r, te, tr, info = self._envs[0].step(actions[0])
        return [obs], [r], [te], [tr], [info]

    def reset_env(self, index, *, seed=None):
        if index != 0:
            raise IndexError(index)
        return self._envs[0].reset(seed=seed)

    def close(self):
        self._envs[0].close()


def slot_game_ids(env) -> list[str]:
    """Per-slot game id from the slot env (``unwrapped.game``), else ``default``."""
    slots = getattr(env, "_envs", None) or []
    out = []
    for slot in slots:
        base = getattr(slot, "unwrapped", slot)
        out.append(str(getattr(base, "game", "default")))
    if not out:  # native vector backends (no python slots): one game
        out = [str(getattr(env, "game", "default"))] * int(env.num_envs)
    return out


def _to_uint8(batch: np.ndarray) -> np.ndarray:
    if batch.dtype == np.uint8:
        return batch
    return np.clip(np.rint(batch.astype(np.float32) * 255.0), 0, 255).astype(np.uint8)


class FastPPOTrainer:
    """Batched PPO over a vector env (see the module docstring)."""

    def __init__(self, env, model: ActorCritic, config: FastPPOConfig, env_config=None,
                 slot_games=None):
        if not isinstance(config, FastPPOConfig):
            raise TypeError("FastPPOTrainer needs a FastPPOConfig")
        if not hasattr(env, "reset_env"):
            env = _SingleAsVector(env)
        self.env, self.model, self.config = env, model, config
        self._num_slots = int(env.num_envs)
        if config.torch_threads:
            torch.set_num_threads(config.torch_threads)
        self.device = resolve_device(config.device)
        self.model.to(self.device)
        self.optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate,
                                          eps=config.adam_eps)
        self.curiosity = None
        self.env_config = dict(env_config) if env_config is not None else None
        self.slot_games = list(slot_games) if slot_games is not None else slot_game_ids(env)
        if len(self.slot_games) != self._num_slots:
            raise ValueError(f"slot_games has {len(self.slot_games)} entries for {self._num_slots} slots")
        self.reward_normalizer = (PerGameReturnNormalizer(self.slot_games, config.gamma)
                                  if config.reward_norm == "per_game" else None)
        self.num_timesteps = 0
        self.num_updates = 0
        self._reward_window: deque[float] = deque(maxlen=100)
        self._game_window: deque[float] = deque(maxlen=100)
        self._game_windows: dict[str, deque] = {}
        self._game_episodes = 0
        self.history: dict[str, list] = {k: [] for k in (
            *FAST_FIELDNAMES, "components", "upd_action_share", "game_scores")}
        self._check_minibatching()

    # -- helpers -----------------------------------------------------------
    def _check_minibatching(self):
        cfg = self.config
        if self.model.recurrent and cfg.num_minibatches:
            if self._num_slots % cfg.num_minibatches:
                raise ValueError(
                    f"recurrent fast PPO splits minibatches by env slot: num_envs "
                    f"({self._num_slots}) must be divisible by num_minibatches ({cfg.num_minibatches})")

    def _num_minibatches(self, steps: int) -> int:
        cfg = self.config
        if cfg.num_minibatches:
            return cfg.num_minibatches
        if self.model.recurrent:
            return 1  # legacy minibatch_size has no slot meaning; one sequence batch
        return max(1, math.ceil(steps / cfg.minibatch_size))

    def current_lr(self, steps_done: int | None = None) -> float:
        """CleanRL ``frac = 1 - (iteration - 1) / num_iterations``: steps done *before* the rollout."""
        cfg = self.config
        if not cfg.anneal_lr:
            return cfg.learning_rate
        horizon = cfg.anneal_total_timesteps or cfg.total_timesteps
        done = self.num_timesteps if steps_done is None else steps_done
        frac = max(0.0, 1.0 - done / horizon)
        return frac * cfg.learning_rate

    def _obs_tensor(self, obs_list) -> torch.Tensor:
        arr = _to_uint8(np.stack([np.asarray(o) for o in obs_list]))
        return torch.from_numpy(arr).to(self.device, non_blocking=True)

    def _drain_game_scores(self) -> list[tuple[str, float]]:
        out = []
        for game, slot in zip(self.slot_games, getattr(self.env, "_envs", None) or [], strict=False):
            base = getattr(slot, "unwrapped", slot)
            drain = getattr(base, "drain_game_scores", None)
            if callable(drain):
                out.extend((game, float(s)) for s in drain())
        return out

    # -- rollout ---------------------------------------------------------
    def collect_rollout(self, respect_budget: bool = False):
        cfg, n, dev = self.config, self._num_slots, self.device
        steps = cfg.rollout_length
        if respect_budget:
            remaining = cfg.total_timesteps - self.num_timesteps
            if remaining % n:
                raise ValueError(f"total_timesteps must be divisible by num_envs ({n})")
            steps = min(steps, max(remaining // n, 1))
        if self.num_timesteps == 0 or not hasattr(self, "_carry_obs"):
            obs_list, _ = self.env.reset(seeds=[cfg.seed + self.num_updates + e for e in range(n)])
            obs = self._obs_tensor(obs_list)
            hidden = self.model.initial_state(n, dev)
        else:
            obs, hidden = self._carry_obs, self._carry_hidden
        shape = tuple(obs.shape[1:])
        buf = {
            "obs": torch.empty((steps, n, *shape), dtype=torch.uint8, device=dev),
            "actions": torch.empty((steps, n), dtype=torch.long, device=dev),
            "logprobs": torch.empty((steps, n), device=dev),
            "values": torch.empty((steps, n), device=dev),
            "trunc_values": torch.zeros((steps, n), device=dev),
        }
        h0 = hidden.clone()
        raw_rewards = np.zeros((steps, n), dtype=np.float64)
        train_rewards = np.zeros((steps, n), dtype=np.float64)
        terms = np.zeros((steps, n), dtype=bool)
        truncs = np.zeros((steps, n), dtype=bool)
        acc_r = getattr(self, "_acc_r", np.zeros(n))
        acc_l = getattr(self, "_acc_l", np.zeros(n, dtype=np.int64))
        ep_rewards, ep_lengths, ep_term = [], [], []
        for t in range(steps):
            buf["obs"][t].copy_(obs)
            with torch.inference_mode():
                logits, value, new_hidden = self.model(obs, hidden)
                dist = Categorical(logits=logits)
                action = dist.sample()
                logp = dist.log_prob(action)
            buf["actions"][t].copy_(action)
            buf["logprobs"][t].copy_(logp)
            buf["values"][t].copy_(value)
            step_obs, rew, te, tr, _ = self.env.step(action.tolist())
            te = np.asarray(te, dtype=bool)
            tr = np.asarray(tr, dtype=bool) & ~te
            raw = np.asarray(rew, dtype=np.float64)
            raw_rewards[t], terms[t], truncs[t] = raw, te, tr
            done = te | tr
            train_rewards[t] = (self.reward_normalizer(raw, done) if self.reward_normalizer is not None
                                else raw)
            self.num_timesteps += n
            acc_r += raw
            acc_l += 1
            next_obs = self._obs_tensor(step_obs)
            if done.any():
                idx = np.nonzero(done)[0]
                tidx = np.nonzero(tr)[0]
                with torch.inference_mode():
                    if len(tidx):  # bootstrap from the final pre-reset obs + its hidden
                        ti = torch.as_tensor(tidx, device=dev)
                        _, tv, _ = self.model(next_obs[ti], new_hidden[:, ti].contiguous())
                    new_hidden = new_hidden.clone()
                    new_hidden[:, torch.as_tensor(idx, device=dev)] = 0
                if len(tidx):
                    buf["trunc_values"][t, ti] = tv
                fresh = [self.env.reset_env(int(e))[0] for e in idx]
                next_obs[torch.as_tensor(idx, device=dev)] = self._obs_tensor(fresh)
                for e in idx:
                    ep_rewards.append(float(acc_r[e]))
                    ep_lengths.append(int(acc_l[e]))
                    ep_term.append(bool(te[e]))
                    acc_r[e], acc_l[e] = 0.0, 0
            obs, hidden = next_obs, new_hidden
        self._acc_r, self._acc_l = acc_r, acc_l
        self._carry_obs, self._carry_hidden = obs, hidden
        with torch.inference_mode():
            _, next_value, _ = self.model(obs, hidden)
        buf["next_value"] = next_value.clone()
        buf["h0"] = h0
        buf["rewards"] = torch.as_tensor(train_rewards, dtype=torch.float32, device=dev)
        buf["ext"] = torch.as_tensor(raw_rewards, dtype=torch.float32)
        buf["terminated"] = torch.as_tensor(terms, device=dev)
        buf["truncated"] = torch.as_tensor(truncs, device=dev)
        buf["dones"] = buf["terminated"] | buf["truncated"]
        return buf, ep_rewards, ep_lengths, ep_term

    def compute_advantages(self, buf):
        """GAE per slot; bootstrap cut by terminated, accumulation cut by any done."""
        cfg = self.config
        values, rewards = buf["values"], buf["rewards"]
        steps = values.shape[0]
        next_values = torch.empty_like(values)
        next_values[:-1] = values[1:]
        next_values[-1] = buf["next_value"]
        trunc = buf["truncated"]
        next_values = torch.where(trunc, buf["trunc_values"], next_values)
        nonterm = 1.0 - buf["terminated"].float()
        notdone = 1.0 - buf["dones"].float()
        adv = torch.zeros_like(values)
        last = torch.zeros_like(values[0])
        for t in reversed(range(steps)):
            delta = rewards[t] + cfg.gamma * next_values[t] * nonterm[t] - values[t]
            last = delta + cfg.gamma * cfg.gae_lambda * notdone[t] * last
            adv[t] = last
        return adv, adv + values

    # -- update ----------------------------------------------------------
    def _replay_recurrent(self, obs, h0, dones):
        """obs (T,n,...) uint8, h0 (L,n,H), dones (T,n) -> logits (T,n,A), values (T,n)."""
        steps, n = obs.shape[:2]
        feat = self.model.encode(obs.reshape(steps * n, *obs.shape[2:])).reshape(steps, n, -1)
        # One nn.GRU call per stretch between done steps of *any* slot in the
        # minibatch (dones are rare, so stretches are long); after a stretch that
        # ends on a done, the finished slots' hidden is zeroed -- exactly the
        # rollout's per-step reset, without a Python loop over every step.
        done_any = dones.any(dim=1).nonzero().flatten().tolist()
        h, outs, start = h0, [], 0
        for end in [*(d + 1 for d in done_any), steps]:
            if end <= start:
                continue
            out, h = self.model.gru(feat[start:end].transpose(0, 1), h)  # (n, L, H)
            outs.append(out.transpose(0, 1))
            if end - 1 in done_any:
                h = h * (~dones[end - 1]).float().view(1, n, 1)
            start = end
        core = torch.cat(outs)  # (T, n, H)
        logits, values = self.model.heads(core.reshape(steps * n, -1))
        return logits.reshape(steps, n, -1), values.reshape(steps, n)

    def update(self, buf) -> dict:
        cfg = self.config
        steps, n = buf["values"].shape
        lr = self.current_lr(self.num_timesteps - steps * n)  # this rollout's start
        for group in self.optimizer.param_groups:
            group["lr"] = lr
        advantages, returns = self.compute_advantages(buf)
        if cfg.adv_norm == "global":
            std = advantages.std()
            if not torch.isfinite(std):
                std = torch.ones(())
            advantages = (advantages - advantages.mean()) / (std + 1e-8)
        num_mb = self._num_minibatches(steps)
        metrics: dict[str, list[float]] = {k: [] for k in (
            "policy_loss", "value_loss", "entropy", "approx_kl", "old_approx_kl", "clipfrac")}
        t0 = self.num_timesteps
        recurrent = self.model.recurrent
        total = steps * n
        units = n if recurrent else total
        if recurrent and units % num_mb:
            raise ValueError(f"num_envs ({n}) must be divisible by num_minibatches ({num_mb})")
        flat = {k: buf[k].reshape(total, *buf[k].shape[2:]) for k in ("obs", "actions", "logprobs", "values")}
        flat_adv, flat_ret = advantages.reshape(-1), returns.reshape(-1)
        for _ in range(cfg.update_epochs):
            order = (torch.randperm(units, device=self.device) if cfg.shuffle_minibatches
                     else torch.arange(units, device=self.device))
            for mb in torch.tensor_split(order, num_mb):
                if mb.numel() == 0:
                    continue
                if recurrent:
                    logits, new_v = self._replay_recurrent(buf["obs"][:, mb], buf["h0"][:, mb].contiguous(),
                                                           buf["dones"][:, mb])
                    logits = logits.reshape(-1, logits.shape[-1])
                    new_v = new_v.reshape(-1)
                    actions, old_lp, old_v, adv, ret = (
                        x[:, mb].reshape(-1)
                        for x in (buf["actions"], buf["logprobs"], buf["values"], advantages, returns))
                else:
                    feat = self.model.encode(flat["obs"][mb])
                    logits, new_v = self.model.heads(feat)
                    actions, old_lp, old_v = flat["actions"][mb], flat["logprobs"][mb], flat["values"][mb]
                    adv, ret = flat_adv[mb], flat_ret[mb]
                dist = Categorical(logits=logits)
                new_lp = dist.log_prob(actions)
                entropy = dist.entropy().mean()
                logratio = new_lp - old_lp
                ratio = logratio.exp()
                with torch.no_grad():
                    metrics["old_approx_kl"].append(float((-logratio).mean()))
                    metrics["approx_kl"].append(float(((ratio - 1) - logratio).mean()))
                    metrics["clipfrac"].append(float(((ratio - 1.0).abs() > cfg.clip_range).float().mean()))
                if cfg.adv_norm == "minibatch" and adv.numel() > 1:
                    adv = (adv - adv.mean()) / (adv.std() + 1e-8)
                pg_loss = torch.max(-adv * ratio,
                                    -adv * torch.clamp(ratio, 1 - cfg.clip_range, 1 + cfg.clip_range)).mean()
                if cfg.clip_vloss:
                    v_clipped = old_v + torch.clamp(new_v - old_v, -cfg.clip_range, cfg.clip_range)
                    v_loss = 0.5 * torch.max((new_v - ret) ** 2, (v_clipped - ret) ** 2).mean()
                else:
                    v_loss = 0.5 * ((new_v - ret) ** 2).mean()
                loss = pg_loss - cfg.entropy_coef * entropy + cfg.value_coef * v_loss
                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), cfg.max_grad_norm)
                self.optimizer.step()
                metrics["policy_loss"].append(pg_loss.item())
                metrics["value_loss"].append(v_loss.item())
                metrics["entropy"].append(entropy.item())
        y_pred = buf["values"].reshape(-1).double().cpu().numpy()
        y_true = returns.reshape(-1).double().cpu().numpy()
        var_y = float(np.var(y_true))
        self.num_updates += 1
        out = {k: float(np.mean(v)) if v else 0.0 for k, v in metrics.items()}
        out["explained_variance"] = float("nan") if var_y == 0 else float(1 - np.var(y_true - y_pred) / var_y)
        out["learning_rate"] = lr
        out["timesteps"] = t0
        return out

    # -- checkpoints -----------------------------------------------------
    def save_checkpoint(self, path) -> str:
        import os
        import tempfile

        path = str(path)
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION, "engine": "fast",
            "ppo_config": asdict(self.config), "model_config": self.model._config,
            "model": self.model.state_dict(), "optimizer": self.optimizer.state_dict(),
            "num_timesteps": self.num_timesteps, "num_updates": self.num_updates,
            "curiosity": None, "env_config": self.env_config, "slot_games": self.slot_games,
            "reward_norm": (self.reward_normalizer.state_dict()
                            if self.reward_normalizer is not None else None),
        }
        action_space = declared_action_space(self.env)
        if action_space is not None:
            payload["action_space"] = action_space
        fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".tmp-ckpt-", suffix=".pt")
        try:
            os.close(fd)
            torch.save(payload, tmp)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path

    @classmethod
    def load_checkpoint(cls, path, env, device=None, config_overrides: dict | None = None) -> FastPPOTrainer:
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
        check_schema(ckpt, path)
        saved_space = ckpt.get("action_space") or "native"
        env_space = declared_action_space(env) or "native"
        if saved_space != env_space:
            raise ValueError(f"checkpoint {path} was trained with action space {saved_space!r} but "
                             f"the env declares {env_space!r}")
        ppo = dict(ckpt["ppo_config"])
        ppo.update(config_overrides or {})
        if device is not None:
            ppo["device"] = device
        config = FastPPOConfig(**ppo)
        trainer = cls(env, ActorCritic(**ckpt["model_config"]), config, env_config=ckpt.get("env_config"),
                      slot_games=ckpt.get("slot_games") if ckpt.get("slot_games") and len(
                          ckpt["slot_games"]) == int(getattr(env, "num_envs", 1)) else None)
        trainer.model.load_state_dict(ckpt["model"])
        trainer.optimizer.load_state_dict(ckpt["optimizer"])
        trainer.num_timesteps, trainer.num_updates = ckpt["num_timesteps"], ckpt["num_updates"]
        if trainer.reward_normalizer is not None and ckpt.get("reward_norm"):
            trainer.reward_normalizer.load_state_dict(ckpt["reward_norm"])
        return trainer

    # -- main loop -------------------------------------------------------
    def is_complete(self) -> bool:
        return self.num_timesteps >= self.config.total_timesteps

    def train(self) -> dict[str, list]:
        cfg = self.config
        if cfg.total_timesteps % self._num_slots:
            raise ValueError(f"total_timesteps ({cfg.total_timesteps}) must be divisible by "
                             f"num_envs ({self._num_slots})")
        ckpt_dir = Path(cfg.checkpoint_dir)
        tel = PPOUpdateCSVWriter(cfg.telemetry_path, fieldnames=FAST_FIELDNAMES) if cfg.telemetry_path else None
        if self.is_complete():
            print(f"training already complete: checkpoint is at {self.num_timesteps} timesteps, "
                  f"budget is {cfg.total_timesteps}; no updates will run.")
            if (ckpt_dir / "ppo_final.pt").exists():
                return self.history
        start = time.perf_counter()
        start_steps = self.num_timesteps
        episodes_seen = 0
        while self.num_timesteps < cfg.total_timesteps:
            buf, ep_rewards, ep_lengths, ep_term = self.collect_rollout(respect_budget=True)
            stats = self.update(buf)
            for r in ep_rewards:
                self._reward_window.append(r)
            games = self._drain_game_scores()
            for game, score in games:
                self._game_window.append(score)
                self._game_windows.setdefault(game, deque(maxlen=100)).append(score)
            self._game_episodes += len(games)
            episodes_seen += len(ep_rewards)
            fps = (self.num_timesteps - start_steps) / max(time.perf_counter() - start, 1e-6)
            n_ep = len(ep_rewards)
            n_term = sum(ep_term)
            mean_rew = float(np.mean(self._reward_window)) if self._reward_window else 0.0
            acts = buf["actions"].reshape(-1).cpu().numpy()
            uniq, counts = np.unique(acts, return_counts=True)
            row = {
                "timesteps": self.num_timesteps, "fps": fps, "policy_loss": stats["policy_loss"],
                "value_loss": stats["value_loss"], "entropy": stats["entropy"],
                "mean_reward": mean_rew, "mean_ext_reward": mean_rew, "mean_int_reward": 0.0,
                "predictor_loss": 0.0, "pixel_change": 0.0, "episodes": episodes_seen,
                "upd_episodes": n_ep,
                "upd_mean_length": float(np.mean(ep_lengths)) if ep_lengths else 0.0,
                "upd_terminated": n_term, "upd_truncated": n_ep - n_term,
                "upd_mean_ext": float(np.mean(ep_rewards)) if ep_rewards else 0.0,
                "upd_mean_int": 0.0,
                "upd_mean_total": float(np.mean(ep_rewards)) if ep_rewards else 0.0,
                "approx_kl": stats["approx_kl"], "old_approx_kl": stats["old_approx_kl"],
                "clipfrac": stats["clipfrac"], "explained_variance": stats["explained_variance"],
                "learning_rate": stats["learning_rate"],
                "mean_game_score": float(np.mean(self._game_window)) if self._game_window else float("nan"),
                "game_episodes": self._game_episodes,
                "upd_mean_game_score": float(np.mean([s for _, s in games])) if games else float("nan"),
            }
            for key, value in row.items():
                self.history[key].append(value)
            self.history["components"].append({})
            self.history["upd_action_share"].append(
                {str(int(a)): float(c) / max(acts.size, 1) for a, c in zip(uniq, counts, strict=False)})
            self.history["game_scores"].append(
                {g: float(np.mean(w)) for g, w in self._game_windows.items() if w})
            if tel is not None:
                tel.append(row)
            print(f"update {self.num_updates}: steps={self.num_timesteps} episodes={episodes_seen} "
                  f"train_return_100={mean_rew:.2f} game_score_100={row['mean_game_score']:.2f} "
                  f"pg={stats['policy_loss']:.4f} vf={stats['value_loss']:.4f} ent={stats['entropy']:.4f} "
                  f"kl={stats['approx_kl']:.4f} clip={stats['clipfrac']:.3f} "
                  f"ev={stats['explained_variance']:.3f} lr={stats['learning_rate']:.2e} fps={fps:.0f}",
                  flush=True)
            if cfg.checkpoint_every_updates and self.num_updates % cfg.checkpoint_every_updates == 0:
                self.save_checkpoint(ckpt_dir / f"ppo_{self.num_timesteps}.pt")
        final = self.save_checkpoint(ckpt_dir / "ppo_final.pt")
        print(f"training done: {self.num_timesteps} steps, checkpoint {final}")
        return self.history


def check_schema(ckpt: dict, path) -> int:
    version = int(ckpt.get("schema_version", 1))
    if version > CHECKPOINT_SCHEMA_VERSION:
        raise ValueError(f"checkpoint {path} has schema_version {version}; this code reads <= "
                         f"{CHECKPOINT_SCHEMA_VERSION}. Update the code.")
    return version


def build_trainer(env, num_actions: int, model_cfg: dict, ppo_cfg: dict, env_config=None,
                  slot_games=None, seed: int | None = None) -> FastPPOTrainer:
    """Fresh fast trainer: seeds torch/numpy (``ppo.seed`` unless given), builds model + config."""
    config = FastPPOConfig.from_dict(ppo_cfg)
    s = config.seed if seed is None else seed
    torch.manual_seed(s)
    np.random.seed(s)
    model = ActorCritic(num_actions=num_actions, **(model_cfg or {}))
    return FastPPOTrainer(env, model, config, env_config=env_config, slot_games=slot_games)
