"""PPO trainer for the recurrent actor-critic. Correctness first.

Operates strictly through the environment interface (``reset`` / ``step``)
on preprocessed pixel observations; never touches game internals.
Single-environment loop (no vectorization): rollout -> GAE -> PPO clipped
updates in sequential chunks (hidden state carried forward, detached
between chunks, so recurrence stays consistent) -> checkpoints + metrics.
"""
from __future__ import annotations

import argparse
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Categorical

from agent.model import ActorCritic
from environment.vec import SyncVectorEnv
from training.telemetry import FIELDNAMES, PPOUpdateCSVWriter


@dataclass
class PPOConfig:
    learning_rate: float = 2.5e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_range: float = 0.2
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    rollout_length: int = 128
    minibatch_size: int = 32
    update_epochs: int = 4
    total_timesteps: int = 10000
    max_grad_norm: float = 0.5
    seed: int = 0
    checkpoint_dir: str = "checkpoints"
    checkpoint_every_updates: int = 10
    # None (or "") disables per-update CSV telemetry; otherwise the path of
    # the CSV file that gets one row per completed PPO update
    # (training/telemetry.py). A relative path resolves against the process
    # working directory, so configs should normally use an absolute path.
    telemetry_path: str | None = None

    def __post_init__(self):
        for name in (
            "learning_rate", "gamma", "gae_lambda", "clip_range",
            "entropy_coef", "value_coef", "max_grad_norm",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)!r}")
        for name in ("rollout_length", "minibatch_size", "update_epochs", "total_timesteps"):
            if not isinstance(getattr(self, name), int) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive int, got {getattr(self, name)!r}")
        if (not isinstance(self.checkpoint_every_updates, int)
                or isinstance(self.checkpoint_every_updates, bool)
                or self.checkpoint_every_updates < 0):
            raise ValueError(
                "checkpoint_every_updates must be a non-negative int "
                f"(0 disables periodic checkpoints), got {self.checkpoint_every_updates!r}")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            raise ValueError(f"seed must be a non-negative int, got {self.seed!r}")
        if not 0 <= self.gamma <= 1 or not 0 <= self.gae_lambda <= 1:
            raise ValueError("gamma and gae_lambda must be in [0, 1]")
        if self.telemetry_path is not None and not isinstance(self.telemetry_path, (str, os.PathLike)):
            raise ValueError(
                "telemetry_path must be a path string or None "
                f"(None disables telemetry), got {self.telemetry_path!r}")

    @classmethod
    def from_dict(cls, data: dict) -> "PPOConfig":
        import warnings

        known = {f.name for f in fields(cls)}
        for key in data:
            if key not in known:
                warnings.warn(f"ppo: ignoring unknown config key {key!r}", UserWarning, stacklevel=3)
        return cls(**{k: v for k, v in data.items() if k in known})


def compute_gae(rewards, values, terminated, next_value, gamma, gae_lambda):
    """Discounted GAE. 1D float tensors length T; values length T; next_value scalar.

    Timeouts (truncations) are NOT terminals: pass terminated (not done) so
    bootstrapping continues across them. Returns (advantages, returns).
    """
    advantages = torch.zeros_like(rewards)
    last_gae = 0.0
    for t in reversed(range(len(rewards))):
        nonterminal = 1.0 - terminated[t]
        next_v = values[t + 1] if t + 1 < len(values) else next_value
        delta = rewards[t] + gamma * next_v * nonterminal - values[t]
        last_gae = delta + gamma * gae_lambda * nonterminal * last_gae
        advantages[t] = last_gae
    return advantages, advantages + values


def summarize_history(history: dict[str, list]) -> dict:
    """Aggregate a training history that may legitimately be empty.

    A trainer restored from a checkpoint that already reached
    ``total_timesteps`` runs no update, so ``train()`` returns an empty
    history. Readers must not index it blindly: report the aggregates below
    and check ``updates_run`` to tell "nothing was trained" apart from
    "trained, reward happened to be zero".
    """
    def values(key):
        return history.get(key) or []

    def last(key):
        seq = values(key)
        return seq[-1] if seq else 0.0

    def total(key):
        return int(sum(values(key)))

    def mean(key):
        seq = values(key)
        return float(np.mean(seq)) if seq else 0.0

    components = values("components")
    return {
        "updates_run": len(values("timesteps")),
        "episodes": int(last("episodes")),
        "mean_reward": float(last("mean_reward")),
        "mean_ext_reward": float(last("mean_ext_reward")),
        "mean_int_reward": float(last("mean_int_reward")),
        "predictor_loss": float(last("predictor_loss")),
        "pixel_change": mean("pixel_change"),
        "mean_episode_length": mean("upd_mean_length"),
        "terminated_episodes": total("upd_terminated"),
        "truncated_episodes": total("upd_truncated"),
        "component_means": dict(components[-1]) if components else {},
    }


class PPOTrainer:
    """Single-env recurrent PPO with explicit boundary semantics.

    Termination (absorbing): value 0, GAE masks, next episode from zeros,
    replay restarts segments from zeros.
    Truncation (time limit): bootstrap V(final pre-reset obs, its hidden
    state); GAE continues; next episode still starts from zeros because the
    environment itself resets (no continuation exists); replay restarts
    segments from zeros exactly like rollout.
    Clean rollout boundary: (obs, hidden) carried verbatim; replay continues
    across chunks with detached carry and never resets mid-episode.
    """
    def __init__(self, env, model: ActorCritic, config: PPOConfig, device="cpu", curiosity=None,
                 env_config=None):
        self.env, self.model, self.config = env, model, config
        # Any SyncVectorEnv width is accepted; single envs keep the legacy
        # rollout below byte-for-byte. Only the vector path batches slots.
        self._vec = isinstance(env, SyncVectorEnv)
        self._num_slots = env.num_envs if self._vec else 1
        self.device = torch.device(device)
        self.model.to(self.device)
        self.optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
        self.curiosity = curiosity
        self.env_config = dict(env_config) if env_config is not None else None
        self.num_timesteps = 0
        self.num_updates = 0
        self._reward_window: deque[float] = deque(maxlen=100)
        self._ext_window: deque[float] = deque(maxlen=100)
        self._int_window: deque[float] = deque(maxlen=100)
        self.history: dict[str, list] = {
            "timesteps": [], "fps": [], "policy_loss": [], "value_loss": [],
            "entropy": [], "mean_reward": [], "mean_ext_reward": [],
            "mean_int_reward": [], "predictor_loss": [], "pixel_change": [],
            "episodes": [], "upd_episodes": [], "upd_mean_length": [],
            "upd_terminated": [], "upd_truncated": [], "upd_mean_ext": [],
            "upd_mean_int": [], "upd_mean_total": [], "components": [],
            "upd_action_share": [],
        }

    def _initial_obs(self, seed):
        """First observation: identical data through either env shape."""
        if self._vec:
            return self.env.reset(seeds=[seed])[0][0]
        return self.env.reset(seed=seed)[0]

    def _boundary_obs(self):
        """Post-episode fresh observation; the vec slot via ``reset_env(0)``."""
        if self._vec:
            return self.env.reset_env(0)[0]
        return self.env.reset()[0]

    def _step(self, action):
        """One env step as a 5-tuple through either env shape."""
        if self._vec:
            obs, rew, term, trunc, info = self.env.step([action])
            return obs[0], rew[0], term[0], trunc[0], info[0]
        return self.env.step(action)

    # -- rollout ---------------------------------------------------------
    @torch.no_grad()
    def collect_rollout(self):
        if self._vec:
            return self._collect_vector()
        cfg = self.config
        # Fresh trainer (or one restored from checkpoint, which carries no
        # mid-episode state) starts a new episode; otherwise continue.
        if self.num_timesteps == 0 or not hasattr(self, "_carry_obs"):
            obs = self._initial_obs(cfg.seed + self.num_updates)
            hidden = self.model.initial_state(1, self.device)
        else:
            obs, hidden = self._carry_obs, self._carry_hidden
        buf = {k: [] for k in ("obs", "actions", "logprobs", "values", "ext", "terminated", "truncated", "dones")}
        buf["h0"] = hidden.clone()
        hidden_traj = []  # post-step hidden per timestep (pre-reset), for timeout bootstrap
        next_list = []  # true post-step frame per timestep (pre-reset)
        comp_sums: dict = {}  # per-component external reward sums (diagnostics only)
        for _ in range(cfg.rollout_length):
            t = torch.from_numpy(np.ascontiguousarray(obs, dtype=np.float32)).unsqueeze(0).to(self.device)
            logits, value, hidden = self.model(t, hidden)
            dist = Categorical(logits=logits.squeeze(0))
            action = dist.sample()
            next_obs, reward, terminated, truncated, _ = self._step(int(action.item()))
            buf["obs"].append(t.squeeze(0).cpu())
            buf["actions"].append(action.cpu())
            buf["logprobs"].append(dist.log_prob(action).cpu())
            buf["values"].append(value.squeeze(0).cpu())
            hidden_traj.append(hidden.clone())
            next_list.append(np.ascontiguousarray(next_obs, dtype=np.float32))
            buf["ext"].append(float(reward))
            buf["terminated"].append(bool(terminated))
            buf["truncated"].append(bool(truncated))
            buf["dones"].append(bool(terminated or truncated))
            breakdown = getattr(self.env, "last_breakdown", None)
            if breakdown is not None:
                for key, value in breakdown.components.items():
                    comp_sums[key] = comp_sums.get(key, 0.0) + float(value)
            self.num_timesteps += 1
            if terminated or truncated:
                obs = self._boundary_obs()
                hidden = self.model.initial_state(1, self.device)
            else:
                obs = next_obs
        self._carry_obs, self._carry_hidden = obs, hidden
        for name in ("actions", "logprobs", "values", "ext", "terminated", "truncated", "dones"):
            buf[name] = torch.stack(buf[name]) if isinstance(buf[name][0], torch.Tensor) else torch.tensor(buf[name])
            if buf[name].dim() == 0:
                buf[name] = buf[name].unsqueeze(0)  # rollout_length=1 stays time-major
        buf["obs"] = torch.stack(buf["obs"])  # (T, C, H, W), cpu
        buf["ext"] = buf["ext"].float()
        # True next frame per step (pre-reset); boundary steps are masked by
        # callers via `dones`, so the reset frame never leaks in as a target.
        buf["next_obs"] = torch.from_numpy(np.stack(next_list))
        valid = ~buf["dones"]
        if self.curiosity is not None:
            int_scaled, int_raw = self.curiosity.intrinsic(buf["obs"], buf["actions"], buf["next_obs"], valid)
        else:
            int_scaled = torch.zeros(cfg.rollout_length)
            int_raw = torch.zeros(cfg.rollout_length)
        buf["int_rewards"] = int_scaled.float()
        buf["rewards"] = buf["ext"] + buf["int_rewards"]  # total drives GAE
        # No frame pair exists for a one-step rollout: report 0.0 (no observed
        # change) instead of NaN. Metric only; training is unaffected.
        buf["pixel_change"] = (
            float(torch.abs(buf["obs"][1:] - buf["obs"][:-1]).mean())
            if buf["obs"].shape[0] > 1
            else 0.0
        )
        # Per-episode sums, spanning-aware via carry accumulators.
        acc_e = getattr(self, "_ep_ext", 0.0)
        acc_i = getattr(self, "_ep_int", 0.0)
        acc_l = getattr(self, "_ep_len", 0)
        ep_rewards, ep_lengths, ep_ext, ep_int, ep_term = [], [], [], [], []
        for t in range(cfg.rollout_length):
            acc_e, acc_i, acc_l = acc_e + float(buf["ext"][t]), acc_i + float(int_scaled[t]), acc_l + 1
            if buf["dones"][t]:
                ep_rewards.append(acc_e + acc_i)
                ep_ext.append(acc_e)
                ep_int.append(acc_i)
                ep_lengths.append(acc_l)
                ep_term.append(bool(buf["terminated"][t]))
                acc_e, acc_i, acc_l = 0.0, 0.0, 0
        self._ep_ext, self._ep_int, self._ep_len = acc_e, acc_i, acc_l
        # Bootstrap semantics (see class docstring): terminated -> 0;
        # truncated -> V(final pre-reset obs, its hidden state); otherwise
        # the carried (obs, hidden), which equal next_obs[-1]/traj[-1].
        if bool(buf["terminated"][-1]):
            buf["next_value"] = torch.zeros(())
        elif bool(buf["truncated"][-1]):
            bv_obs = buf["next_obs"][-1].unsqueeze(0).to(self.device)
            bv_h = hidden_traj[-1].to(self.device)
            buf["next_value"] = self.model(bv_obs, bv_h)[1].squeeze(0).cpu()
        else:
            buf["next_value"] = self.model(
                torch.from_numpy(np.ascontiguousarray(obs, dtype=np.float32)).unsqueeze(0).to(self.device),
                hidden,
            )[1].squeeze(0).cpu()
        buf["int_raw_mean"] = float(int_raw.mean())
        buf["comp_sums"] = {k: float(v) for k, v in comp_sums.items()}
        buf["comp_steps"] = cfg.rollout_length
        return buf, ep_rewards, ep_lengths, ep_ext, ep_int, ep_term

    @torch.no_grad()
    def _collect_vector(self):
        """Rollout over ``[time, env]``: one hidden slice per slot.

        Same boundary contract as the single path, applied per slot: the
        terminal step's own data is stored untouched, only finished slots
        are reset (via ``reset_env``), only their hidden slice is zeroed,
        and only their episode accumulators restart. No autoreset.
        Terminated slots bootstrap to zero; truncated slots bootstrap from
        their own final pre-reset observation and hidden state.
        """
        cfg = self.config
        slots = self._num_slots
        if self.num_timesteps == 0 or not hasattr(self, "_carry_obs"):
            obs_list, _ = self.env.reset(
                seeds=[cfg.seed + self.num_updates + e for e in range(slots)])
            hidden = self.model.initial_state(slots, self.device)
        else:
            obs_list, hidden = self._carry_obs, self._carry_hidden
        buf = {k: [] for k in ("obs", "actions", "logprobs", "values", "ext", "terminated", "truncated", "dones")}
        buf["h0"] = hidden.clone()
        hidden_traj = []  # post-step hidden (L,N,H) per step, pre-reset
        next_list = []  # true post-step frames per step, pre-reset
        for _ in range(cfg.rollout_length):
            t = torch.stack([torch.from_numpy(
                np.ascontiguousarray(o, dtype=np.float32)) for o in obs_list]).to(self.device)
            logits, value, hidden = self.model(t, hidden)
            dist = Categorical(logits=logits)
            actions = dist.sample()
            logps = dist.log_prob(actions)
            step_obs, rewards, terms, truncs, _ = self.env.step(
                [int(a) for a in actions.cpu()])
            buf["obs"].append(t.cpu())
            buf["actions"].append(actions.cpu())
            buf["logprobs"].append(logps.cpu())
            buf["values"].append(value.cpu())
            hidden_traj.append(hidden.clone())
            next_list.append(torch.stack([torch.from_numpy(
                np.ascontiguousarray(o, dtype=np.float32)) for o in step_obs]))
            buf["ext"].append(torch.tensor([float(r) for r in rewards]))
            buf["terminated"].append(torch.tensor([bool(x) for x in terms]))
            buf["truncated"].append(torch.tensor([bool(x) for x in truncs]))
            buf["dones"].append(torch.tensor(
                [bool(a or b) for a, b in zip(terms, truncs)]))
            self.num_timesteps += slots
            for e in range(slots):
                if terms[e] or truncs[e]:
                    obs_list[e] = self.env.reset_env(e)[0]
                    hidden[:, e, :] = 0
                else:
                    obs_list[e] = step_obs[e]
        self._carry_obs, self._carry_hidden = obs_list, hidden
        for name in ("actions", "logprobs", "values", "ext", "terminated", "truncated", "dones"):
            buf[name] = torch.stack(buf[name])
        buf["obs"] = torch.stack(buf["obs"])  # (T, N, C, H, W), cpu
        buf["ext"] = buf["ext"].float()
        buf["next_obs"] = torch.stack(next_list)  # (T, N, C, H, W), cpu
        valid = ~buf["dones"]
        if self.curiosity is not None:
            flat = cfg.rollout_length * slots
            int_scaled, int_raw = self.curiosity.intrinsic(
                buf["obs"].reshape(flat, *buf["obs"].shape[2:]),
                buf["actions"].reshape(flat),
                buf["next_obs"].reshape(flat, *buf["next_obs"].shape[2:]),
                valid.reshape(flat))
            buf["int_rewards"] = int_scaled.float().reshape(cfg.rollout_length, slots)
        else:
            int_scaled = torch.zeros(cfg.rollout_length * slots)
            int_raw = torch.zeros(cfg.rollout_length * slots)
            buf["int_rewards"] = torch.zeros(cfg.rollout_length, slots)
        buf["rewards"] = buf["ext"] + buf["int_rewards"]
        buf["pixel_change"] = (
            float(torch.abs(buf["obs"][1:] - buf["obs"][:-1]).mean())
            if buf["obs"].shape[0] > 1
            else 0.0
        )
        acc_e = list(getattr(self, "_vacc_e", [0.0] * slots))
        acc_i = list(getattr(self, "_vacc_i", [0.0] * slots))
        acc_l = list(getattr(self, "_vacc_l", [0] * slots))
        ep_rewards, ep_lengths, ep_ext, ep_int, ep_term = [], [], [], [], []
        flat_scaled = int_scaled.reshape(cfg.rollout_length, slots)
        for t in range(cfg.rollout_length):
            for e in range(slots):
                acc_e[e] += float(buf["ext"][t, e])
                acc_i[e] += float(flat_scaled[t, e])
                acc_l[e] += 1
                if buf["dones"][t, e]:
                    ep_rewards.append(acc_e[e] + acc_i[e])
                    ep_ext.append(acc_e[e])
                    ep_int.append(acc_i[e])
                    ep_lengths.append(acc_l[e])
                    ep_term.append(bool(buf["terminated"][t, e]))
                    acc_e[e], acc_i[e], acc_l[e] = 0.0, 0.0, 0
        self._vacc_e, self._vacc_i, self._vacc_l = acc_e, acc_i, acc_l
        next_value = torch.zeros(slots)
        for e in range(slots):
            if buf["terminated"][-1, e]:
                continue  # bootstrap value stays zero
            if buf["truncated"][-1, e]:
                bv_obs = buf["next_obs"][-1, e].unsqueeze(0).to(self.device)
                bv_h = hidden_traj[-1][:, e, :].unsqueeze(1).to(self.device)
            else:
                bv_obs = torch.from_numpy(
                    np.ascontiguousarray(obs_list[e], dtype=np.float32)).unsqueeze(0).to(self.device)
                bv_h = hidden[:, e, :].unsqueeze(1).to(self.device)
            next_value[e] = self.model(bv_obs, bv_h)[1].squeeze(0).cpu()
        buf["next_value"] = next_value
        buf["int_raw_mean"] = float(int_raw.float().mean())
        # Component breakdowns are a single-env diagnostic (per-env
        # ``last_breakdown``); vector mode reports none rather than mixing.
        buf["comp_sums"] = {}
        buf["comp_steps"] = cfg.rollout_length * slots
        return buf, ep_rewards, ep_lengths, ep_ext, ep_int, ep_term

    # -- update ----------------------------------------------------------
    def update(self, buf) -> dict[str, float]:
        if self._vec:
            return self._update_vector(buf)
        cfg = self.config
        values = buf["values"].float().reshape(-1)
        rewards = buf["rewards"].float()
        terminated = buf["terminated"].float()
        advantages, returns = compute_gae(rewards, values, terminated, buf["next_value"].float(), cfg.gamma, cfg.gae_lambda)
        adv_std = advantages.std()
        if not torch.isfinite(adv_std):
            adv_std = torch.ones(())  # single-sample rollout: center only
        advantages = (advantages - advantages.mean()) / (adv_std + 1e-8)
        old_logprobs = buf["logprobs"].float()
        metrics: dict[str, list[float]] = {"policy_loss": [], "value_loss": [], "entropy": [], "predictor_loss": []}
        t0 = self.num_timesteps
        for _ in range(cfg.update_epochs):
            hidden = buf["h0"].to(self.device)  # (L,1,H): rollout-start state
            for start in range(0, cfg.rollout_length, cfg.minibatch_size):
                end = min(start + cfg.minibatch_size, cfg.rollout_length)
                # Replay in segments split at done (= terminated or truncated)
                # boundaries: the env resets in both cases and rollout ran
                # post-done steps from zeroed hidden, so replay must too.
                # Detached end state seeds the next chunk/segment.
                seg_logits, seg_values = [], []
                seg_start = start
                while seg_start < end:
                    done_idx = next(
                        (i for i in range(seg_start, end) if buf["dones"][i]), None
                    )
                    seg_end = done_idx + 1 if done_idx is not None else end
                    chunk_obs = buf["obs"][seg_start:seg_end].to(self.device)
                    seg_l, seg_v, hidden = self.model.forward_sequence(chunk_obs, hidden.detach())
                    seg_logits.append(seg_l)
                    seg_values.append(seg_v)
                    hidden = (
                        self.model.initial_state(1, self.device)
                        if done_idx is not None
                        else hidden.detach()
                    )
                    seg_start = seg_end
                logits = torch.cat(seg_logits)
                value = torch.cat(seg_values)
                dist = Categorical(logits=logits)
                logprobs = dist.log_prob(buf["actions"][start:end].to(self.device))
                entropy = dist.entropy().mean()
                ratio = torch.exp(logprobs - old_logprobs[start:end].to(self.device))
                adv = advantages[start:end].to(self.device)
                policy_loss = -torch.min(ratio * adv, torch.clamp(ratio, 1 - cfg.clip_range, 1 + cfg.clip_range) * adv).mean()
                ret = returns[start:end].to(self.device)
                v = value.reshape(-1)
                v_old = values[start:end].to(self.device)
                v_clipped = v_old + torch.clamp(v - v_old, -cfg.clip_range, cfg.clip_range)
                value_loss = 0.5 * torch.max((v - ret) ** 2, (v_clipped - ret) ** 2).mean()
                loss = policy_loss + cfg.value_coef * value_loss - cfg.entropy_coef * entropy
                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), cfg.max_grad_norm)
                self.optimizer.step()
                metrics["policy_loss"].append(policy_loss.item())
                metrics["value_loss"].append(value_loss.item())
                metrics["entropy"].append(entropy.item())
        pred_loss = 0.0
        if self.curiosity is not None:
            pred_loss = self.curiosity.update(buf["obs"], buf["actions"], buf["next_obs"], ~buf["dones"])
        metrics["predictor_loss"].append(pred_loss)
        self.num_updates += 1
        out = {k: float(np.mean(v)) for k, v in metrics.items()}
        out["timesteps"] = t0
        return out

    def _update_vector(self, buf) -> dict[str, float]:
        """PPO update over a ``[time, env]`` rollout, one pooled chunk mean.

        GAE runs independently per slot (masks never cross slots); the
        resulting advantages are normalized globally, exactly like the
        single path normalizes over its rollout. Replay runs each slot's
        segments through ``forward_sequence`` with that slot's own hidden
        carry (zeros after its dones), then pools every step of the time
        chunk into one loss. For one slot this reduces to the single path.
        """
        cfg = self.config
        slots = self._num_slots
        steps = cfg.rollout_length
        values = buf["values"].float()  # (T, N)
        flat = steps * slots
        advantages = torch.zeros(steps, slots)
        returns = torch.zeros(steps, slots)
        for e in range(slots):
            adv_e, ret_e = compute_gae(
                buf["rewards"][:, e].float(), values[:, e],
                buf["terminated"][:, e].float(),
                buf["next_value"][e].float(), cfg.gamma, cfg.gae_lambda)
            advantages[:, e], returns[:, e] = adv_e, ret_e
        adv_std = advantages.std()
        if not torch.isfinite(adv_std):
            adv_std = torch.ones(())  # single-sample rollout: center only
        advantages = (advantages - advantages.mean()) / (adv_std + 1e-8)
        flat_adv = advantages.reshape(-1).to(self.device)
        flat_ret = returns.reshape(-1).to(self.device)
        flat_old_lp = buf["logprobs"].float().reshape(-1).to(self.device)
        flat_values = values.reshape(-1).to(self.device)
        metrics: dict[str, list[float]] = {"policy_loss": [], "value_loss": [], "entropy": [], "predictor_loss": []}
        t0 = self.num_timesteps
        for _ in range(cfg.update_epochs):
            hidden = buf["h0"].to(self.device).clone()  # (L,N,H); slots mutated below
            for start in range(0, steps, cfg.minibatch_size):
                end = min(start + cfg.minibatch_size, steps)
                chunk_lp = torch.empty((end - start) * slots, device=self.device)
                chunk_v = torch.empty((end - start) * slots, device=self.device)
                chunk_ent = torch.empty((end - start) * slots, device=self.device)
                carries = []
                for e in range(slots):
                    h_e = hidden[:, e, :].unsqueeze(1)
                    slot_a = buf["actions"][start:end, e].to(self.device)
                    seg_lp, seg_v, seg_ent = [], [], []
                    seg_start = start
                    while seg_start < end:
                        done_idx = next(
                            (i for i in range(seg_start, end) if buf["dones"][i, e]), None
                        )
                        seg_end = done_idx + 1 if done_idx is not None else end
                        chunk_obs = buf["obs"][seg_start:seg_end, e].to(self.device)
                        out_l, out_v, h_e = self.model.forward_sequence(chunk_obs, h_e.detach())
                        seg_dist = Categorical(logits=out_l)
                        seg_lp.append(seg_dist.log_prob(slot_a[seg_start - start:seg_end - start]))
                        seg_v.append(out_v.reshape(-1))
                        seg_ent.append(seg_dist.entropy())
                        h_e = (
                            self.model.initial_state(1, self.device)
                            if done_idx is not None
                            else h_e.detach()
                        )
                        seg_start = seg_end
                    slot_lp = torch.cat(seg_lp)
                    slot_v = torch.cat(seg_v)
                    slot_ent = torch.cat(seg_ent)
                    for t in range(start, end):
                        chunk_lp[(t - start) * slots + e] = slot_lp[t - start]
                        chunk_v[(t - start) * slots + e] = slot_v[t - start]
                        chunk_ent[(t - start) * slots + e] = slot_ent[t - start]
                    carries.append(h_e.detach().squeeze(1))
                hidden = torch.stack(carries, dim=1)
                entropy = chunk_ent.mean()
                idx = torch.arange(start * slots, end * slots, device=self.device)
                ratio = torch.exp(chunk_lp - flat_old_lp[idx])
                adv = flat_adv[idx]
                policy_loss = -torch.min(ratio * adv, torch.clamp(ratio, 1 - cfg.clip_range, 1 + cfg.clip_range) * adv).mean()
                ret = flat_ret[idx]
                v = chunk_v
                v_old = flat_values[idx]
                v_clipped = v_old + torch.clamp(v - v_old, -cfg.clip_range, cfg.clip_range)
                value_loss = 0.5 * torch.max((v - ret) ** 2, (v_clipped - ret) ** 2).mean()
                loss = policy_loss + cfg.value_coef * value_loss - cfg.entropy_coef * entropy
                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), cfg.max_grad_norm)
                self.optimizer.step()
                metrics["policy_loss"].append(policy_loss.item())
                metrics["value_loss"].append(value_loss.item())
                metrics["entropy"].append(entropy.item())
        pred_loss = 0.0
        if self.curiosity is not None:
            pred_loss = self.curiosity.update(
                buf["obs"].reshape(flat, *buf["obs"].shape[2:]),
                buf["actions"].reshape(flat),
                buf["next_obs"].reshape(flat, *buf["next_obs"].shape[2:]),
                (~buf["dones"]).reshape(flat))
        metrics["predictor_loss"].append(pred_loss)
        self.num_updates += 1
        out = {k: float(np.mean(v)) for k, v in metrics.items()}
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
            "ppo_config": asdict(self.config),
            "model_config": self.model._config,
            "model": self.model.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "num_timesteps": self.num_timesteps,
            "num_updates": self.num_updates,
            "curiosity": self.curiosity.state_dict() if self.curiosity is not None else None,
            "env_config": self.env_config,
        }
        # Atomic replace: a kill during the temp write leaves only a stray
        # tmp file, never a truncated final checkpoint. Same-dir temp file
        # keeps os.replace atomic on all platforms.
        fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=".tmp-ckpt-", suffix=".pt")
        try:
            os.close(fd)
            torch.save(payload, tmp_name)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return path

    @classmethod
    def load_checkpoint(cls, path, env, device="cpu") -> "PPOTrainer":
        from training.curiosity import CuriosityConfig, CuriosityModule

        ckpt = torch.load(path, map_location=device, weights_only=True)
        trainer = cls(env, ActorCritic(**ckpt["model_config"]), PPOConfig(**ckpt["ppo_config"]), device)
        trainer.model.load_state_dict(ckpt["model"])
        trainer.optimizer.load_state_dict(ckpt["optimizer"])
        trainer.num_timesteps, trainer.num_updates = ckpt["num_timesteps"], ckpt["num_updates"]
        trainer.env_config = ckpt.get("env_config")
        if ckpt.get("curiosity") is not None:
            cur_state = ckpt["curiosity"]
            module = CuriosityModule(
                num_actions=ckpt["model_config"]["num_actions"],
                in_channels=ckpt["model_config"].get("in_channels", 4),
                frame_size=ckpt["model_config"].get("frame_size", 84),
                config=CuriosityConfig(**cur_state["config"]),
                device=device,
            )
            module.load_state_dict(cur_state)
            trainer.curiosity = module
        return trainer

    # -- main loop -------------------------------------------------------
    def is_complete(self) -> bool:
        """True once this trainer has reached its configured ``total_timesteps``.

        Includes the over-complete case: a checkpoint saved past the resumed
        run's (smaller) budget still has nothing left to train.
        """
        return self.num_timesteps >= self.config.total_timesteps

    def train(self) -> dict[str, list]:
        cfg = self.config
        ckpt_dir = Path(cfg.checkpoint_dir)
        # Created before the is_complete() early return so an already-finished
        # run still leaves a header-only CSV. None means disabled: no file,
        # no extra work, training output is byte-for-byte the legacy result.
        tel = PPOUpdateCSVWriter(cfg.telemetry_path) if cfg.telemetry_path else None
        start = time.perf_counter()
        episodes_seen = 0
        if self.is_complete():
            # Resumed checkpoint already satisfies the budget: report it and
            # train nothing. Restarting here would silently discard the
            # restored weights and step count.
            print(f"training already complete: checkpoint is at {self.num_timesteps} "
                  f"timesteps, budget is {cfg.total_timesteps}; no updates will run. "
                  f"Raise total_timesteps above {self.num_timesteps} to continue "
                  f"training from this checkpoint.")
            final = ckpt_dir / "ppo_final.pt"
            if final.exists():
                print(f"kept existing final checkpoint {final}")
                return self.history
        while self.num_timesteps < cfg.total_timesteps:
            buf, ep_rewards, ep_lengths, ep_ext, ep_int, ep_term = self.collect_rollout()
            stats = self.update(buf)
            for r in ep_rewards:
                self._reward_window.append(r)
            for e in ep_ext:
                self._ext_window.append(e)
            for i in ep_int:
                self._int_window.append(i)
            episodes_seen += len(ep_rewards)
            fps = self.num_timesteps / max(time.perf_counter() - start, 1e-6)
            mean = lambda w: float(np.mean(w)) if w else 0.0
            n_ep = len(ep_rewards)
            upd_terminated = sum(1 for t in ep_term if t)
            self.history["timesteps"].append(self.num_timesteps)
            self.history["fps"].append(fps)
            self.history["policy_loss"].append(stats["policy_loss"])
            self.history["value_loss"].append(stats["value_loss"])
            self.history["entropy"].append(stats["entropy"])
            self.history["mean_reward"].append(mean(self._reward_window))
            self.history["mean_ext_reward"].append(mean(self._ext_window))
            self.history["mean_int_reward"].append(mean(self._int_window))
            self.history["predictor_loss"].append(stats.get("predictor_loss", 0.0))
            self.history["pixel_change"].append(buf["pixel_change"])
            self.history["episodes"].append(episodes_seen)
            self.history["upd_episodes"].append(n_ep)
            self.history["upd_mean_length"].append(float(np.mean(ep_lengths)) if ep_lengths else 0.0)
            self.history["upd_terminated"].append(upd_terminated)
            self.history["upd_truncated"].append(n_ep - upd_terminated)
            # Action-distribution diagnostic: per-update share of each action
            # index over the rollout, e.g. {"0": 0.1, "1": 0.8, "2": 0.1}.
            rollout_actions = buf["actions"].detach().cpu().numpy().reshape(-1)
            uniq, counts = np.unique(rollout_actions, return_counts=True)
            total_actions = max(rollout_actions.size, 1)
            self.history["upd_action_share"].append(
                {str(int(a)): float(c) / float(total_actions)
                 for a, c in zip(uniq, counts)})
            self.history["upd_mean_ext"].append(float(np.mean(ep_ext)) if ep_ext else 0.0)
            self.history["upd_mean_int"].append(float(np.mean(ep_int)) if ep_int else 0.0)
            self.history["upd_mean_total"].append(float(np.mean(ep_rewards)) if ep_rewards else 0.0)
            steps = buf.get("comp_steps", cfg.rollout_length)
            self.history["components"].append(
                {k: float(v) / steps for k, v in buf.get("comp_sums", {}).items()})
            # One CSV row per completed update, straight from the history row
            # above: no duplicated metric math, nothing PPO reads back.
            if tel is not None:
                tel.append({key: self.history[key][-1] for key in FIELDNAMES})
            print(
                f"update {self.num_updates}: steps={self.num_timesteps} "
                f"episodes={episodes_seen} mean_total_100={self.history['mean_reward'][-1]:.2f} "
                f"(ext={self.history['mean_ext_reward'][-1]:.2f} int={self.history['mean_int_reward'][-1]:.3f}) "
                f"pg={stats['policy_loss']:.4f} vf={stats['value_loss']:.4f} "
                f"ent={stats['entropy']:.4f} pred={stats.get('predictor_loss', 0.0):.4f} fps={fps:.0f}"
            )
            if cfg.checkpoint_every_updates and self.num_updates % cfg.checkpoint_every_updates == 0:
                self.save_checkpoint(ckpt_dir / f"ppo_{self.num_timesteps}.pt")
        final = self.save_checkpoint(ckpt_dir / "ppo_final.pt")
        print(f"training done: {self.num_timesteps} steps, checkpoint {final}")
        return self.history


def run_experiment(total_timesteps=2048, rollout_length=128, seed=0, checkpoint_dir="checkpoints"):
    from environment.preprocessing import PreprocessingWrapper
    from environment.toy_pong import ToyPongEnv

    torch.manual_seed(seed)
    np.random.seed(seed)
    env = PreprocessingWrapper(ToyPongEnv())
    model = ActorCritic(num_actions=int(env.action_space.n))
    config = PPOConfig(total_timesteps=total_timesteps, rollout_length=rollout_length,
                       seed=seed, checkpoint_dir=checkpoint_dir)
    return PPOTrainer(env, model, config).train()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Short PPO run on the toy game")
    parser.add_argument("--total-timesteps", type=int, default=2048)
    parser.add_argument("--rollout-length", type=int, default=128)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--checkpoint-dir", default="checkpoints")
    args = parser.parse_args()
    run_experiment(total_timesteps=args.total_timesteps, rollout_length=args.rollout_length,
                   seed=args.seed, checkpoint_dir=args.checkpoint_dir)
