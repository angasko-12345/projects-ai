"""Non-learning baselines for ALE games: uniform-random pad and constant NOOP.

Neither policy looks at the observation, so both are trivially pixel-only.
They act through the exact evaluation pipeline the agent uses (same frame
skip, sticky actions, no-op reset, UGA pad, raw game score), so their numbers
are directly comparable with ``training/generalization.py`` evaluations.
Lengths count decisions (``env.skip`` frames each).

Usage (needs ``ale-py``; numpy only otherwise, no torch, no window):
    python -m training.atari_baselines --config experiments/stage1/atari_pilot.yaml --out <json>
        [--episodes 10] [--seeds 0] [--max-decisions N]
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np

POLICIES = ("random", "const_noop")


def run_policy(make_env, policy: str, seeds, rng_seed: int = 0, max_decisions: int | None = None) -> dict:
    """Play one baseline policy on fixed episode seeds (raw game score)."""
    rng = np.random.default_rng(rng_seed)
    rewards, lengths, terms, truncs = [], [], [], []
    t0 = time.perf_counter()
    for s in seeds:
        env = make_env()
        try:
            env.reset(seed=int(s))
            n = int(env.action_space.n)
            total, steps, term, trunc = 0.0, 0, False, False
            while True:
                a = int(rng.integers(0, n)) if policy == "random" else 0
                if policy not in POLICIES:
                    raise ValueError(f"unknown policy {policy!r}")
                _, r, term, trunc, _ = env.step(a)
                total, steps = total + float(r), steps + 1
                if term or trunc:
                    break
                if max_decisions is not None and steps >= max_decisions:
                    trunc = True  # step cap (reported as truncation)
                    break
        finally:
            env.close()
        rewards.append(total)
        lengths.append(steps)
        terms.append(bool(term))
        truncs.append(bool(trunc))
    secs = time.perf_counter() - t0
    return {
        "episodes": len(rewards), "seeds": [int(s) for s in seeds],
        "mean_reward": float(np.mean(rewards)), "std_reward": float(np.std(rewards)),
        "min_reward": float(np.min(rewards)), "max_reward": float(np.max(rewards)),
        "mean_length": float(np.mean(lengths)),
        "survival_rate": float(sum(1 for t in terms if not t) / len(terms)),
        "episode_rewards": [float(r) for r in rewards], "episode_lengths": [int(x) for x in lengths],
        "episode_terminated": terms, "episode_truncated": truncs,
        "seconds": secs, "decisions_per_second": float(sum(lengths) / max(secs, 1e-9)),
    }


def run_all_atari(cfg: dict, episodes: int = 10, seeds=(0,), max_decisions: int | None = None,
                  games=None) -> dict:
    from environment.atari_env import atari_kwargs, make_atari_factory
    from training.arcade_baselines import eval_seeds
    from training.generalization import suite_of

    suite = suite_of(cfg)
    kwargs = atari_kwargs(cfg["env"], train=False)
    out: dict = {"episodes": episodes, "seeds": list(seeds), "max_decisions": max_decisions,
                 "env": kwargs, "policies": list(POLICIES),
                 "note": "raw game score; random = uniform over the env's action space "
                         f"({kwargs['action_space']}); episode seeds 10000 + 1000*seed + i",
                 "platform": platform.platform(), "results": {}}
    t0 = time.perf_counter()
    for game in games or suite.games:
        make = make_atari_factory(game, **kwargs)
        out["results"][game] = {}
        for pol in POLICIES:
            per_seed = [run_policy(make, pol, eval_seeds(s, episodes), rng_seed=s,
                                   max_decisions=max_decisions) for s in seeds]
            means = [p["mean_reward"] for p in per_seed]
            out["results"][game][pol] = {
                "mean_over_seeds": float(np.mean(means)), "std_over_seeds": float(np.std(means)),
                "mean_length": float(np.mean([p["mean_length"] for p in per_seed])),
                "survival_rate": float(np.mean([p["survival_rate"] for p in per_seed])),
                "per_seed": per_seed,
            }
            print(f"{game:14s} {pol:10s} mean={np.mean(means):8.2f} "
                  f"len={out['results'][game][pol]['mean_length']:7.1f} "
                  f"({sum(p['seconds'] for p in per_seed):.0f}s)", flush=True)
    out["seconds"] = time.perf_counter() - t0
    return out


def main(argv=None) -> int:
    from training.generalization import load_config

    p = argparse.ArgumentParser(description="ALE random / constant baselines (raw game score)")
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--episodes", type=int, default=10)
    p.add_argument("--seeds", type=int, nargs="+", default=[0])
    p.add_argument("--max-decisions", type=int, default=None)
    p.add_argument("--games", nargs="+", default=None)
    a = p.parse_args(argv)
    res = run_all_atari(load_config(a.config), a.episodes, a.seeds, a.max_decisions, a.games)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(f"baselines -> {a.out} ({res['seconds']:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
