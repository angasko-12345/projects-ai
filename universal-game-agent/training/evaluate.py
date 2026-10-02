"""Separate-environment evaluation: greedy (or sampled) policy, fixed seeds.

Builds its own env instance -- never the training one -- and touches only
pixel observations. Returns per-episode rewards/lengths plus aggregates.

Measurement contract (callers must not assume more):

* ``seeds`` are forwarded to ``env.reset(seed=...)`` per episode. Envs that
  implement seeding (e.g. the toy env) reproduce episodes; envs that do not
  (e.g. :class:`ExternalGameEnv`, whose game is seeded once at process
  launch) ignore them and episodes are session continuations.
* ``reward_semantics`` is copied from the env's own declaration
  (:func:`environment.reward.reward_semantics_of`). ``sign`` means reward > 0
  iff a paddle hit and reward < 0 iff a paddle miss that decision step;
  ``generic`` means the sign carries no event meaning.
* ``episode_hits``/``episode_misses``/``mean_hits``/``mean_misses`` are
  reported ONLY when ``reward_semantics`` is ``sign``. Key absence -- not a
  zero -- is the encoding: ``0.0`` reads as "hit nothing", which under a
  survival, progress, or composite provider it does not mean.
* ``episode_positive_reward_steps``/``episode_negative_reward_steps`` (and
  their means) are the sign counts under a name that does not claim an event,
  and are always reported.
* Envs that do not declare their semantics are treated as ``generic``, so a
  missing or unrecognised declaration fails closed to no hit/miss reporting.
* If the per-episode envs do not agree on their declared semantics, the whole
  report is ``generic``: a mixed run cannot make the stronger claim.
* Each per-episode env is closed even when a step raises.
"""
from __future__ import annotations

import numpy as np
import torch

from agent.model import ActorCritic
from environment.reward import GENERIC_SEMANTICS, SIGN_SEMANTICS, reward_semantics_of


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
    positive_steps, negative_steps = [], []
    episode_terminated, episode_truncated = [], []
    declared: set[str] = set()
    try:
        for ep in range(episodes):
            env = make_env()
            try:
                declared.add(reward_semantics_of(env))
                obs, _ = env.reset(seed=seeds[ep])
                hidden = model.initial_state(1, device)
                total, steps = 0.0, 0
                counts: dict = {}
                positive = negative = 0
                while True:
                    t = torch.from_numpy(np.ascontiguousarray(obs, dtype=np.float32)).unsqueeze(0).to(device)
                    logits, _, hidden = model(t, hidden)
                    action = int(logits.argmax(-1).item()) if greedy else int(torch.distributions.Categorical(logits=logits).sample().item())
                    counts[action] = counts.get(action, 0) + 1
                    obs, reward, terminated, truncated, _ = env.step(action)
                    total, steps = total + float(reward), steps + 1
                    if float(reward) > 0:
                        positive += 1
                    elif float(reward) < 0:
                        negative += 1
                    if terminated or truncated:
                        break
            finally:
                env.close()
            rewards.append(total)
            lengths.append(steps)
            action_counts.append(counts)
            positive_steps.append(positive)
            negative_steps.append(negative)
            episode_terminated.append(bool(terminated))
            episode_truncated.append(bool(truncated))
    finally:
        if was_training:
            model.train()
    rewards = np.array(rewards, dtype=np.float64)
    total_counts: dict = {}
    for counts in action_counts:
        for action, count in counts.items():
            total_counts[int(action)] = total_counts.get(int(action), 0) + int(count)
    semantics = (declared.pop() if len(declared) == 1 else GENERIC_SEMANTICS)
    report = {
        "episodes": episodes,
        "greedy": greedy,
        "seeds": seeds,
        "reward_semantics": semantics,
        "mean_reward": float(rewards.mean()),
        "std_reward": float(rewards.std()),
        "min_reward": float(rewards.min()),
        "max_reward": float(rewards.max()),
        "mean_length": float(np.mean(lengths)),
        "episode_rewards": [float(r) for r in rewards],
        "episode_lengths": [int(s) for s in lengths],
        "action_counts": total_counts,
        "episode_positive_reward_steps": [int(p) for p in positive_steps],
        "episode_negative_reward_steps": [int(n) for n in negative_steps],
        "mean_positive_reward_steps": float(np.mean(positive_steps)),
        "mean_negative_reward_steps": float(np.mean(negative_steps)),
        "episode_terminated": [bool(t) for t in episode_terminated],
        "episode_truncated": [bool(t) for t in episode_truncated],
        "terminated_episodes": int(sum(1 for t in episode_terminated if t)),
        "truncated_episodes": int(sum(1 for t in episode_truncated if t)),
    }
    if semantics == SIGN_SEMANTICS:
        # Only now is "hit"/"miss" a statement about the game rather than
        # about the reward's sign.
        report["episode_hits"] = list(report["episode_positive_reward_steps"])
        report["episode_misses"] = list(report["episode_negative_reward_steps"])
        report["mean_hits"] = report["mean_positive_reward_steps"]
        report["mean_misses"] = report["mean_negative_reward_steps"]
    return report
