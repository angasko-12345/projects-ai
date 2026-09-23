"""PPO trainer for the recurrent actor-critic. Correctness first.

Operates strictly through the environment interface (``reset`` / ``step``)
on preprocessed pixel observations; never touches game internals.
Single-environment loop (no vectorization): rollout -> GAE -> PPO clipped
updates in sequential chunks (hidden state carried forward, detached
between chunks, so recurrence stays consistent) -> checkpoints + metrics.
"""
from __future__ import annotations

import argparse
import time
from collections import deque
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Categorical

from agent.model import ActorCritic


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
        if not 0 <= self.gamma <= 1 or not 0 <= self.gae_lambda <= 1:
            raise ValueError("gamma and gae_lambda must be in [0, 1]")

    @classmethod
    def from_dict(cls, data: dict) -> "PPOConfig":
        known = {f.name for f in fields(cls)}
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


class PPOTrainer:
    def __init__(self, env, model: ActorCritic, config: PPOConfig, device="cpu", curiosity=None):
        self.env, self.model, self.config = env, model, config
        self.device = torch.device(device)
        self.model.to(self.device)
        self.optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
        self.curiosity = curiosity
        self.num_timesteps = 0
        self.num_updates = 0
        self._reward_window: deque[float] = deque(maxlen=100)
        self._ext_window: deque[float] = deque(maxlen=100)
        self._int_window: deque[float] = deque(maxlen=100)
        self.history: dict[str, list] = {
            "timesteps": [], "fps": [], "policy_loss": [], "value_loss": [],
            "entropy": [], "mean_reward": [], "mean_ext_reward": [],
            "mean_int_reward": [], "predictor_loss": [], "pixel_change": [],
            "episodes": [],
        }

    # -- rollout ---------------------------------------------------------
    @torch.no_grad()
    def collect_rollout(self):
        cfg = self.config
        # Fresh trainer (or one restored from checkpoint, which carries no
        # mid-episode state) starts a new episode; otherwise continue.
        if self.num_timesteps == 0 or not hasattr(self, "_carry_obs"):
            obs, _ = self.env.reset(seed=cfg.seed + self.num_updates)
            hidden = self.model.initial_state(1, self.device)
        else:
            obs, hidden = self._carry_obs, self._carry_hidden
        buf = {k: [] for k in ("obs", "actions", "logprobs", "values", "ext", "terminated", "dones")}
        buf["h0"] = hidden.clone()
        for _ in range(cfg.rollout_length):
            t = torch.from_numpy(np.ascontiguousarray(obs, dtype=np.float32)).unsqueeze(0).to(self.device)
            logits, value, hidden = self.model(t, hidden)
            dist = Categorical(logits=logits.squeeze(0))
            action = dist.sample()
            next_obs, reward, terminated, truncated, _ = self.env.step(int(action.item()))
            buf["obs"].append(t.squeeze(0).cpu())
            buf["actions"].append(action.cpu())
            buf["logprobs"].append(dist.log_prob(action).cpu())
            buf["values"].append(value.squeeze(0).cpu())
            buf["ext"].append(float(reward))
            buf["terminated"].append(bool(terminated))
            buf["dones"].append(bool(terminated or truncated))
            self.num_timesteps += 1
            if terminated or truncated:
                obs, _ = self.env.reset()
                hidden = self.model.initial_state(1, self.device)
            else:
                obs = next_obs
        self._carry_obs, self._carry_hidden = obs, hidden
        for name in ("actions", "logprobs", "values", "ext", "terminated", "dones"):
            buf[name] = torch.stack(buf[name]) if isinstance(buf[name][0], torch.Tensor) else torch.tensor(buf[name])
        buf["obs"] = torch.stack(buf["obs"])  # (T, C, H, W), cpu
        buf["ext"] = buf["ext"].float()
        # Next-obs per step (last = carry); invalid across episode boundaries.
        buf["next_obs"] = torch.cat([buf["obs"][1:], torch.from_numpy(
            np.ascontiguousarray(obs, dtype=np.float32)).unsqueeze(0)])
        valid = ~buf["dones"]
        if self.curiosity is not None:
            int_scaled, int_raw = self.curiosity.intrinsic(buf["obs"], buf["actions"], buf["next_obs"], valid)
        else:
            int_scaled = torch.zeros(cfg.rollout_length)
            int_raw = torch.zeros(cfg.rollout_length)
        buf["int_rewards"] = int_scaled.float()
        buf["rewards"] = buf["ext"] + buf["int_rewards"]  # total drives GAE
        buf["pixel_change"] = float(torch.abs(buf["obs"][1:] - buf["obs"][:-1]).mean())
        # Per-episode sums, spanning-aware via carry accumulators.
        acc_e = getattr(self, "_ep_ext", 0.0)
        acc_i = getattr(self, "_ep_int", 0.0)
        acc_l = getattr(self, "_ep_len", 0)
        ep_rewards, ep_lengths, ep_ext, ep_int = [], [], [], []
        for t in range(cfg.rollout_length):
            acc_e, acc_i, acc_l = acc_e + float(buf["ext"][t]), acc_i + float(int_scaled[t]), acc_l + 1
            if buf["dones"][t]:
                ep_rewards.append(acc_e + acc_i)
                ep_ext.append(acc_e)
                ep_int.append(acc_i)
                ep_lengths.append(acc_l)
                acc_e, acc_i, acc_l = 0.0, 0.0, 0
        self._ep_ext, self._ep_int, self._ep_len = acc_e, acc_i, acc_l
        last_terminated = bool(buf["terminated"][-1])
        buf["next_value"] = torch.zeros(()) if last_terminated else self.model(
            torch.from_numpy(np.ascontiguousarray(obs, dtype=np.float32)).unsqueeze(0).to(self.device),
            hidden,
        )[1].squeeze(0).cpu()
        buf["int_raw_mean"] = float(int_raw.mean())
        return buf, ep_rewards, ep_lengths, ep_ext, ep_int

    # -- update ----------------------------------------------------------
    def update(self, buf) -> dict[str, float]:
        cfg = self.config
        rewards = buf["rewards"].float()
        values = buf["values"].float().squeeze(-1)
        terminated = buf["terminated"].float()
        advantages, returns = compute_gae(rewards, values, terminated, buf["next_value"].float(), cfg.gamma, cfg.gae_lambda)
        advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
        old_logprobs = buf["logprobs"].float()
        metrics: dict[str, list[float]] = {"policy_loss": [], "value_loss": [], "entropy": [], "predictor_loss": []}
        t0 = self.num_timesteps
        for _ in range(cfg.update_epochs):
            hidden = buf["h0"].to(self.device)  # (L,1,H): rollout-start state
            for start in range(0, cfg.rollout_length, cfg.minibatch_size):
                end = min(start + cfg.minibatch_size, cfg.rollout_length)
                chunk_obs = buf["obs"][start:end].to(self.device)
                # Time-major chunk: recurrence flows within it; the end state
                # (detached) seeds the next chunk.
                logits, value, hidden = self.model.forward_sequence(chunk_obs, hidden)
                hidden = hidden.detach()
                dist = Categorical(logits=logits)
                logprobs = dist.log_prob(buf["actions"][start:end].to(self.device))
                entropy = dist.entropy().mean()
                ratio = torch.exp(logprobs - old_logprobs[start:end].to(self.device))
                adv = advantages[start:end].to(self.device)
                policy_loss = -torch.min(ratio * adv, torch.clamp(ratio, 1 - cfg.clip_range, 1 + cfg.clip_range) * adv).mean()
                ret = returns[start:end].to(self.device)
                v = value.squeeze(-1)
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

    # -- checkpoints -----------------------------------------------------
    def save_checkpoint(self, path) -> str:
        path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "ppo_config": asdict(self.config),
            "model_config": self.model._config,
            "model": self.model.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "num_timesteps": self.num_timesteps,
            "num_updates": self.num_updates,
            "curiosity": self.curiosity.state_dict() if self.curiosity is not None else None,
        }, path)
        return path

    @classmethod
    def load_checkpoint(cls, path, env, device="cpu") -> "PPOTrainer":
        from training.curiosity import CuriosityConfig, CuriosityModule

        ckpt = torch.load(path, map_location=device)
        trainer = cls(env, ActorCritic(**ckpt["model_config"]), PPOConfig(**ckpt["ppo_config"]), device)
        trainer.model.load_state_dict(ckpt["model"])
        trainer.optimizer.load_state_dict(ckpt["optimizer"])
        trainer.num_timesteps, trainer.num_updates = ckpt["num_timesteps"], ckpt["num_updates"]
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
    def train(self) -> dict[str, list]:
        cfg = self.config
        ckpt_dir = Path(cfg.checkpoint_dir)
        start = time.perf_counter()
        episodes_seen = 0
        while self.num_timesteps < cfg.total_timesteps:
            buf, ep_rewards, ep_lengths, ep_ext, ep_int = self.collect_rollout()
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
            print(
                f"update {self.num_updates}: steps={self.num_timesteps} "
                f"episodes={episodes_seen} mean_total_100={self.history['mean_reward'][-1]:.2f} "
                f"(ext={self.history['mean_ext_reward'][-1]:.2f} int={self.history['mean_int_reward'][-1]:.3f}) "
                f"pg={stats['policy_loss']:.4f} vf={stats['value_loss']:.4f} "
                f"ent={stats['entropy']:.4f} pred={stats.get('predictor_loss', 0.0):.4f} fps={fps:.0f}"
            )
            if self.num_updates % cfg.checkpoint_every_updates == 0:
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
