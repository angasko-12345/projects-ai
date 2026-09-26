"""Separate-environment evaluation: greedy (or sampled) policy, fixed seeds.

Builds its own env instance -- never the training one -- and touches only
pixel observations. Returns per-episode rewards/lengths plus aggregates.
"""
from __future__ import annotations

import numpy as np
import torch

from agent.model import ActorCritic


@torch.no_grad()
def evaluate(model: ActorCritic, make_env, episodes: int = 20, seeds=None, greedy: bool = True) -> dict:
    if episodes <= 0:
        raise ValueError(f"episodes must be positive, got {episodes!r}")
    seeds = list(range(episodes)) if seeds is None else list(seeds)
    if len(seeds) != episodes:
        raise ValueError(f"need {episodes} seeds, got {len(seeds)}")
    was_training = model.training
    model.eval()
    device = next(model.parameters()).device
    rewards, lengths, action_counts = [], [], []
    episode_hits, episode_misses = [], []
    episode_terminated, episode_truncated = [], []
    try:
        for ep in range(episodes):
            env = make_env()
            obs, _ = env.reset(seed=seeds[ep])
            hidden = model.initial_state(1, device)
            total, steps = 0.0, 0
            counts: dict = {}
            hits = misses = 0
            while True:
                t = torch.from_numpy(np.ascontiguousarray(obs, dtype=np.float32)).unsqueeze(0).to(device)
                logits, _, hidden = model(t, hidden)
                action = int(logits.argmax(-1).item()) if greedy else int(torch.distributions.Categorical(logits=logits).sample().item())
                counts[action] = counts.get(action, 0) + 1
                obs, reward, terminated, truncated, _ = env.step(action)
                total, steps = total + float(reward), steps + 1
                if float(reward) > 0:
                    hits += 1
                elif float(reward) < 0:
                    misses += 1
                if terminated or truncated:
                    break
            rewards.append(total)
            lengths.append(steps)
            action_counts.append(counts)
            episode_hits.append(hits)
            episode_misses.append(misses)
            episode_terminated.append(bool(terminated))
            episode_truncated.append(bool(truncated))
            env.close()
    finally:
        if was_training:
            model.train()
    rewards = np.array(rewards, dtype=np.float64)
    total_counts: dict = {}
    for counts in action_counts:
        for action, count in counts.items():
            total_counts[int(action)] = total_counts.get(int(action), 0) + int(count)
    return {
        "episodes": episodes,
        "greedy": greedy,
        "seeds": seeds,
        "mean_reward": float(rewards.mean()),
        "std_reward": float(rewards.std()),
        "min_reward": float(rewards.min()),
        "max_reward": float(rewards.max()),
        "mean_length": float(np.mean(lengths)),
        "episode_rewards": [float(r) for r in rewards],
        "episode_lengths": [int(s) for s in lengths],
        "action_counts": total_counts,
        "episode_hits": [int(h) for h in episode_hits],
        "episode_misses": [int(m) for m in episode_misses],
        "mean_hits": float(np.mean(episode_hits)),
        "mean_misses": float(np.mean(episode_misses)),
        "episode_terminated": [bool(t) for t in episode_terminated],
        "episode_truncated": [bool(t) for t in episode_truncated],
        "terminated_episodes": int(sum(1 for t in episode_terminated if t)),
        "truncated_episodes": int(sum(1 for t in episode_truncated if t)),
    }
