"""Privileged scripted oracle for toy Pong, and toy-eval rally metrics.

The oracle reads the toy env's hidden state (``_bx``/``_paddle_x``) and
steers the paddle centre toward the ball centre. That is exactly the
information the learning path is forbidden to see, so the oracle is an
UPPER BOUND for comparison only: it is never used for training, never
wired into PPO, and must not be read as a learnable policy (same role as the
lookahead/reactive probes in ``training/cadence_experiment.py``).

``summarize_toy_eval`` derives rally metrics from any toy eval report that
carries ``episode_rewards``/``episode_lengths`` (including the older
``experiments/exp_toy_ppo_0*_results.json`` files, which predate the
``episode_hits``/``truncated_episodes`` fields). The toy reward is +1 per hit
and -1 on the single miss that terminates, so per episode
``hits = reward + 1`` when it ended in a miss and ``hits = reward`` when it
was truncated at ``max_steps``.

Usage (no window, no SendInput, no torch):
    python -m training.toy_oracle [--episodes 20] [--out <json>]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from environment.toy_pong import LEFT, NOOP, RIGHT, ToyPongEnv

_ROOT = Path(__file__).resolve().parent.parent
_TOY_RESULTS = (
    "experiments/exp_toy_ppo_01_results.json",
    "experiments/exp_toy_ppo_02_nocur_results.json",
    "experiments/exp_toy_ppo_03_cur_results.json",
)


def oracle_action(env: ToyPongEnv) -> int:
    """PRIVILEGED: move the paddle centre toward the ball centre (env internals)."""
    ball_c = env._bx + env._ball_size / 2.0
    paddle_c = env._paddle_x + env._paddle_w / 2.0
    if ball_c < paddle_c - 1:
        return LEFT
    if ball_c > paddle_c + 1:
        return RIGHT
    return NOOP


def summarize_toy_eval(rewards, lengths, max_steps: int = 500) -> dict:
    """Rally metrics for toy-Pong episodes from per-episode rewards and lengths."""
    rewards = [float(r) for r in rewards]
    lengths = [int(n) for n in lengths]
    if len(rewards) != len(lengths) or not rewards:
        raise ValueError("need equal-length, non-empty rewards and lengths")
    truncated = [n >= max_steps for n in lengths]
    hits = [int(round(r)) + (0 if t else 1) for r, t in zip(rewards, truncated, strict=True)]
    if any(h < 0 for h in hits):
        raise ValueError("rewards inconsistent with toy +1 hit / -1 terminal miss")
    n = len(rewards)
    return {
        "episodes": n,
        "max_steps": max_steps,
        "episode_hits": hits,
        "mean_hits": float(np.mean(hits)),
        "first_ball_hit_rate": sum(1 for h in hits if h >= 1) / n,
        "second_ball_hit_rate": sum(1 for h in hits if h >= 2) / n,
        "miss_rate": sum(1 for t in truncated if not t) / n,
        "survived_to_truncation": sum(truncated),
        "mean_length": float(np.mean(lengths)),
    }


def run_oracle(episodes: int = 20, seeds=None, max_steps: int = 500) -> dict:
    """Play the privileged oracle on fixed seeds; same seed list as toy eval (0..N-1)."""
    seeds = list(range(episodes)) if seeds is None else list(seeds)
    rewards, lengths = [], []
    for seed in seeds:
        env = ToyPongEnv(max_steps=max_steps)
        env.reset(seed=seed)
        total, steps = 0.0, 0
        while True:
            _, reward, terminated, truncated, _ = env.step(oracle_action(env))
            total, steps = total + reward, steps + 1
            if terminated or truncated:
                break
        env.close()
        rewards.append(total)
        lengths.append(steps)
    report = summarize_toy_eval(rewards, lengths, max_steps=max_steps)
    report.update(seeds=seeds, episode_rewards=rewards, episode_lengths=lengths)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Toy Pong privileged oracle bound + rally metrics of saved toy runs")
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--out", default=None, help="optional JSON output path")
    args = parser.parse_args(argv)
    out = {
        "note": "oracle reads env internals: an upper bound, never a learnable policy",
        "oracle": run_oracle(args.episodes),
        "saved_toy_runs": {},
    }
    for rel in _TOY_RESULTS:
        path = _ROOT / rel
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        out["saved_toy_runs"][rel] = {
            key: summarize_toy_eval(data[key]["episode_rewards"], data[key]["episode_lengths"])
            for key in ("baseline_eval", "final_eval") if key in data
        }
    text = json.dumps(out, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
