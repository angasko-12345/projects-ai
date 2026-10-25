"""Non-learning baselines for the arcade games: random, constant, sticky, oracle.

Random / constant / sticky-random policies never look at the observation, so
they are trivially pixel-only. The per-game **oracles are PRIVILEGED**: they
read the env's underscore-private state (positions, velocities) and exist only
as labelled upper bounds -- the same role as ``training/toy_oracle.py``. They
are never wired into PPO or any learning path.

Usage (numpy only, no torch, no window):
    python -m training.arcade_baselines --out <json> [--episodes 20] [--seeds 0 1 2]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from environment.arcade import GAMES, LEFT, NOOP, RIGHT, make_game

EVAL_SEED_BASE = 10_000  # eval episode i of seed s uses 10_000 + 1000*s + i


def eval_seeds(seed: int, episodes: int) -> list[int]:
    return [EVAL_SEED_BASE + 1000 * seed + i for i in range(episodes)]


def _toward(target_x: float, paddle_x: float, paddle_w: float, tol: float = 1.0) -> int:
    centre = paddle_x + paddle_w / 2.0
    if target_x < centre - tol:
        return LEFT
    if target_x > centre + tol:
        return RIGHT
    return NOOP


# -- PRIVILEGED oracles (read env internals; upper bounds only) --------------
# ``skip`` is the decision period; the dead band widens with it so a repeated
# action does not overshoot the target back and forth.
def _tol(speed: float, skip: int) -> float:
    return max(1.0, speed * skip / 2.0)


def _oracle_pong(env, skip: int = 1) -> int:
    if skip == 1:  # the toy oracle's rule (training/toy_oracle.py)
        return _toward(env._bx + env._ball_size / 2.0, env._paddle_x, env._paddle_w)
    # Coarser decisions: aim at where the ball will cross the paddle row.
    x, y, vx, vy = env._bx, env._by, env._vx, env._vy
    for _ in range(200):
        if vy > 0 and y + env._ball_size >= env._paddle_y:
            break
        x, y = x + vx, y + vy
        if x <= 0:
            x, vx = 0, abs(vx)
        elif x >= env._w - env._ball_size:
            x, vx = env._w - env._ball_size, -abs(vx)
        if y <= 0:
            y, vy = 0, abs(vy)
    return _toward(x + env._ball_size / 2.0, env._paddle_x, env._paddle_w,
                   _tol(env._paddle_speed, skip))


def _oracle_catch(env, skip: int = 1) -> int:
    return _toward(env._fx + env._fs / 2.0, env._px, env._pw, _tol(env._pspeed, skip))


def _oracle_breakout(env, skip: int = 1) -> int:
    return _toward(env._bx + env._bs / 2.0, env._px, env._pw, _tol(env._pspeed, skip))


def _oracle_volley(env, skip: int = 1) -> int:
    # Simulate the ball forward (no collisions but walls) to the paddle row.
    x, y, vx, vy = env._bx, env._by, env._vx, env._vy
    for _ in range(400):
        if vy > 0 and y + 3 >= env._py:
            break
        vy += env._g
        vx = max(-1.5, min(1.5, vx + env._wind))
        x += vx
        y += vy
        if x <= 0 or x >= env._w - 3:
            x = min(max(x, 0.0), float(env._w - 3))
            vx = -vx
        if y <= 0:
            y, vy = 0.0, abs(vy)
    return _toward(x + 1.5, env._px, env._pw, _tol(env._pspeed, skip))


def _oracle_dodge(env, skip: int = 1) -> int:
    centre = env._px + env._pw / 2.0
    threats = [b for b in env._blocks if b[1] < env._py + 3]
    if not threats:
        return _toward(env._w / 2.0, env._px, env._pw, tol=4.0)
    x, _, bw, _ = max(threats, key=lambda b: b[1])  # lowest block still above
    margin = 2.0
    if x - margin < env._px + env._pw and env._px < x + bw + margin:
        left_room, right_room = x, env._w - (x + bw)
        go_left = left_room >= env._pw + margin if centre < x + bw / 2 else right_room < env._pw + margin
        return LEFT if go_left else RIGHT
    return NOOP


ORACLES = {
    "pong": _oracle_pong,
    "catch": _oracle_catch,
    "breakout": _oracle_breakout,
    "volley": _oracle_volley,
    "dodge": _oracle_dodge,
}


def summarize_episodes(rewards, lengths, terminated, negatives, game: str, max_steps: int) -> dict:
    """Game-agnostic episode metrics plus breakout's wall-clear rate.

    A breakout episode cleared the wall iff it terminated without a negative
    reward (the only negative reward is the terminal miss), so the rate is
    derived from the reward stream, not from env internals.
    """
    n = len(rewards)
    out = {
        "episodes": n,
        "mean_reward": float(np.mean(rewards)),
        "std_reward": float(np.std(rewards)),
        "mean_length": float(np.mean(lengths)),
        # Every episode ends terminated or truncated (the time cap), so "not
        # terminated" is survival to the cap -- independent of frame skip.
        "survival_rate": float(sum(1 for t in terminated if not t) / n),
        "max_steps_frames": int(max_steps),
        "episode_rewards": [float(r) for r in rewards],
        "episode_lengths": [int(x) for x in lengths],
    }
    if game == "breakout":
        out["clear_rate"] = float(sum(1 for t, neg in zip(terminated, negatives, strict=True)
                                      if t and neg == 0) / n)
    return out


def run_policy(game: str, policy: str, seeds, max_steps: int = 500, rng_seed: int = 0,
               skip: int = 1) -> dict:
    """Play a non-learning policy on fixed episode seeds.

    ``skip`` is the decision period in frames (the agent's ``env.skip``): one
    decision is repeated for ``skip`` frames, so baselines act at the same
    rate as the PPO agent they are compared with. Lengths count decisions.
    """
    rng = np.random.default_rng(rng_seed)
    rewards, lengths, terms, negs = [], [], [], []
    for s in seeds:
        env = make_game(game, max_steps)
        env.reset(seed=int(s))
        total, steps, neg, prev = 0.0, 0, 0, int(rng.integers(0, 3))
        while True:
            if policy == "random":
                a = int(rng.integers(0, 3))
            elif policy.startswith("const_"):
                a = {"const_noop": NOOP, "const_left": LEFT, "const_right": RIGHT}[policy]
            elif policy == "sticky_random":
                a = prev if rng.random() < 0.8 else int(rng.integers(0, 3))
            elif policy == "oracle":
                a = ORACLES[game](env, skip)
            else:
                raise ValueError(f"unknown policy {policy!r}")
            prev = a
            r = 0.0
            for _ in range(skip):
                _, fr, term, trunc, _ = env.step(a)
                r += fr
                if term or trunc:
                    break
            total += r
            steps += 1
            neg += int(r < 0)
            if term or trunc:
                break
        env.close()
        rewards.append(total)
        lengths.append(steps)
        terms.append(bool(term))
        negs.append(neg)
    rep = summarize_episodes(rewards, lengths, terms, negs, game, max_steps)
    rep["seeds"] = [int(s) for s in seeds]
    return rep


POLICIES = ("random", "const_noop", "const_left", "const_right", "sticky_random", "oracle")


def run_all(episodes: int = 20, seeds=(0, 1, 2), max_steps: int = 500, skip: int = 1) -> dict:
    out: dict = {"note": "oracle = PRIVILEGED upper bound (reads env internals), never learnable",
                 "episodes": episodes, "seeds": list(seeds), "max_steps": max_steps,
                 "skip": skip, "results": {}}
    policies = POLICIES + (("oracle_skip1",) if skip > 1 else ())
    t0 = time.perf_counter()
    for game in GAMES:
        out["results"][game] = {}
        for pol in policies:
            # oracle_skip1: the privileged oracle acting every frame (a looser bound).
            name, k = ("oracle", 1) if pol == "oracle_skip1" else (pol, skip)
            per_seed = [run_policy(game, name, eval_seeds(s, episodes), max_steps, rng_seed=s, skip=k)
                        for s in seeds]
            means = [p["mean_reward"] for p in per_seed]
            out["results"][game][pol] = {
                "mean_over_seeds": float(np.mean(means)),
                "std_over_seeds": float(np.std(means)),
                "mean_length": float(np.mean([p["mean_length"] for p in per_seed])),
                "survival_rate": float(np.mean([p["survival_rate"] for p in per_seed])),
                **({"clear_rate": float(np.mean([p["clear_rate"] for p in per_seed]))}
                   if game == "breakout" else {}),
                "per_seed": per_seed,
            }
    out["seconds"] = time.perf_counter() - t0
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Arcade non-learning baselines (oracle = privileged bound)")
    p.add_argument("--episodes", type=int, default=20)
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--max-steps", type=int, default=500)
    p.add_argument("--skip", type=int, default=1, help="decision period in frames")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    res = run_all(a.episodes, a.seeds, a.max_steps, a.skip)
    for game, pols in res["results"].items():
        print(game, " ".join(f"{k}={v['mean_over_seeds']:+.2f}" for k, v in pols.items()))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
