"""Throughput benchmark: single-env PPO vs SyncVectorEnv (2, 4 envs).

Measures environment frame throughput and PPO update throughput across 1, 2,
and 4 environments using a lightweight deterministic scripted env.  No
window, no MSS, no SendInput, no rendering overhead.  CPU only.

Usage::

    cd universal-game-agent
    python -m training.benchmark --total-timesteps 512 --rollout-length 16
"""
from __future__ import annotations

import argparse
import contextlib
import io
import tempfile
import time
from pathlib import Path

import numpy as np
import torch

from agent.model import ActorCritic
from environment.vec import SyncVectorEnv
from training.ppo import PPOConfig, PPOTrainer

OBS_SHAPE = (4, 84, 84)


class BenchEnv:
    """Lightweight deterministic scripted env for throughput benchmarking.

    Observations are a flat float32 array filled with a deterministic value,
    so env overhead is minimal and model forward/backward dominates -- that is
    exactly what vectorizing the model forward pass targets.
    """

    def __init__(self, script: list[tuple[float, bool, bool]], seed: int = 0):
        self.script = list(script)
        self.seed = seed
        self.t = 0
        self.resets = 0
        self.action_space = type("Space", (), {"n": 2})()
        self.observation_space = type("Space", (), {"shape": OBS_SHAPE})()

    def reset(self, *, seed=None, options=None):
        self.resets += 1
        self.t = 0
        return np.full(OBS_SHAPE, float(self.seed), dtype=np.float32), {}

    def step(self, action):
        if self.t < len(self.script):
            reward, terminated, truncated = self.script[self.t]
        else:
            reward, terminated, truncated = 0.0, False, False
        self.t += 1
        obs = np.full(OBS_SHAPE, float(self.t), dtype=np.float32)
        return obs, float(reward), bool(terminated), bool(truncated), {}

    def close(self):
        pass


def _make_envs(num_envs: int, script: list) -> BenchEnv | SyncVectorEnv:
    """Create envs for a benchmark variant."""
    if num_envs == 1:
        return BenchEnv(script, seed=0)
    factories = [lambda s=script, i=i: BenchEnv(s, seed=i) for i in range(num_envs)]
    return SyncVectorEnv(factories)


def _num_actions(env) -> int:
    """Get action-space size from either a raw or vector env."""
    if isinstance(env, SyncVectorEnv):
        return env.single_action_space.n
    return env.action_space.n


def _count_resets(env) -> int:
    """Count resets across all env slots."""
    if isinstance(env, SyncVectorEnv):
        return sum(e.resets for e in env._envs)
    return env.resets


def _warmup(num_envs: int, rollout_length: int, num_actions: int, seed: int):
    """One short training run to initialise torch/CNN caches before timing."""
    script = [(0.0, False, False)] * (rollout_length + 1)
    env = _make_envs(num_envs, script)
    torch.manual_seed(seed)
    model = ActorCritic(num_actions=num_actions)
    with tempfile.TemporaryDirectory() as tmp:
        config = PPOConfig(
            rollout_length=rollout_length,
            minibatch_size=rollout_length,
            update_epochs=1,
            total_timesteps=rollout_length * num_envs,
            learning_rate=1e-3,
            checkpoint_dir=str(Path(tmp) / "ckpt"),
            checkpoint_every_updates=10 ** 9,
        )
        trainer = PPOTrainer(env, model, config)
        with contextlib.redirect_stdout(io.StringIO()):
            trainer.train()
    env.close()


def run_benchmark_variant(
    num_envs: int,
    total_timesteps: int,
    rollout_length: int,
    seed: int = 0,
    warmup: bool = True,
) -> dict:
    """Run one benchmark variant and return a metrics dict.

    The script never terminates or truncates, so every env is reset exactly
    once at the start.  This isolates the throughput of the training loop
    itself, not episode-reset overhead.
    """
    script = [(0.0, False, False)] * (total_timesteps + rollout_length)
    env = _make_envs(num_envs, script)
    torch.manual_seed(seed)
    model = ActorCritic(num_actions=_num_actions(env))

    with tempfile.TemporaryDirectory() as tmp:
        config = PPOConfig(
            rollout_length=rollout_length,
            minibatch_size=rollout_length,
            update_epochs=1,
            total_timesteps=total_timesteps,
            learning_rate=1e-3,
            checkpoint_dir=str(Path(tmp) / "ckpt"),
            checkpoint_every_updates=10 ** 9,
        )
        trainer = PPOTrainer(env, model, config)

        if warmup:
            _warmup(num_envs, rollout_length, _num_actions(env), seed)

        start = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            trainer.train()
        elapsed = time.perf_counter() - start

    env.close()

    actual_timesteps = trainer.num_timesteps
    updates = trainer.num_updates
    resets = _count_resets(env)

    return {
        "num_envs": num_envs,
        "elapsed": elapsed,
        "total_timesteps": actual_timesteps,
        "env_steps_per_sec": actual_timesteps / elapsed if elapsed > 0 else 0.0,
        "updates": updates,
        "updates_per_sec": updates / elapsed if elapsed > 0 else 0.0,
        "resets": resets,
    }


def run_benchmark(
    total_timesteps: int = 512,
    rollout_length: int = 16,
    seed: int = 0,
) -> list[dict]:
    """Run all three variants (1, 2, 4 envs) and return list of metrics dicts."""
    return [
        run_benchmark_variant(n, total_timesteps, rollout_length, seed)
        for n in (1, 2, 4)
    ]


def _format_table(results: list[dict]) -> str:
    """Format results as a compact text table."""
    header = (
        f"{'variant':<14} {'time(s)':>8} {'env_steps/sec':>14} "
        f"{'updates':>8} {'updates/sec':>12} {'resets':>7}"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        if r["num_envs"] == 1:
            name = "1-env"
        else:
            name = f"{r['num_envs']}-env vec"
        lines.append(
            f"{name:<14} {r['elapsed']:>8.3f} {r['env_steps_per_sec']:>14.1f} "
            f"{r['updates']:>8} {r['updates_per_sec']:>12.2f} {r['resets']:>7}"
        )

    base = results[0]["env_steps_per_sec"]
    lines.append("")
    lines.append("Speedup (env_steps/sec vs 1-env baseline):")
    for r in results[1:]:
        if base > 0:
            ratio = r["env_steps_per_sec"] / base
            lines.append(f"  {r['num_envs']}-env: {ratio:.2f}x")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="PPO vectorization throughput benchmark (1 vs 2 vs 4 envs)"
    )
    parser.add_argument(
        "--total-timesteps", type=int, default=512,
        help="total environment steps per variant (default: 512)",
    )
    parser.add_argument(
        "--rollout-length", type=int, default=16,
        help="rollout length in steps (default: 16)",
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="torch seed (default: 0)"
    )
    args = parser.parse_args()

    # Warn if sample counts won't be exact across variants.
    lcm = 4 * args.rollout_length  # worst case: 4-env, steps-per-update = rollout*4
    if args.total_timesteps % lcm != 0:
        print(
            f"WARNING: total_timesteps={args.total_timesteps} is not divisible "
            f"by rollout_length*4={lcm}; variant step counts may differ"
        )

    print(f"Benchmark: single-env vs SyncVectorEnv (seed={args.seed})")
    print(f"config: total_timesteps={args.total_timesteps}, rollout_length={args.rollout_length}")
    print()

    results = run_benchmark(args.total_timesteps, args.rollout_length, args.seed)
    print(_format_table(results))

    # Verify sample-count consistency.
    counts = {r["total_timesteps"] for r in results}
    if len(counts) == 1:
        total = counts.pop()
        print(f"\nSample count check: all variants processed {total} timesteps OK")
    else:
        print(f"\nSample count check: MISMATCH ({sorted(counts)})")


if __name__ == "__main__":
    main()
