"""Throughput benchmark: single-env PPO vs SyncVectorEnv (2, 4 envs).

Measures environment frame throughput and PPO update throughput across 1, 2,
and 4 environments using a lightweight deterministic scripted env.  No
window, no MSS, no SendInput, no rendering overhead.  CPU only.

Usage::

    cd universal-game-agent
    python -m training.benchmark --total-timesteps 512 --rollout-length 16

``--env toy`` instead trains real PPO on toy Pong (``configs/default.yaml``
unless ``--config`` names another file) for each ``--num-envs`` value and
also times raw env stepping with random actions (no model), so the env-bound
ceiling is visible next to the training number. ``--ppo key=value`` and
``--env-opt key=value`` override config keys (YAML-parsed values), and
``--json PATH`` writes every number plus the host description (CPU, core
count, torch version and threads) to a results file::

    python -m training.benchmark --env toy --num-envs 1 8 --total-timesteps 4096 \
        --json experiments/throughput_baseline.json
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import platform
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


# -- toy Pong: real env, real PPO config ------------------------------------
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "configs" / "default.yaml"


def host_info() -> dict:
    """What the numbers were measured on. Read, never guessed."""
    cpu = platform.processor() or ""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.lower().startswith(("model name", "cpu model")):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    if not cpu:
        try:  # Linux on ARM has no "model name" in /proc/cpuinfo
            import subprocess

            out = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                if line.strip().lower().startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
        except Exception:  # noqa: BLE001 - description only
            pass
    affinity = None
    if hasattr(os, "sched_getaffinity"):
        affinity = len(os.sched_getaffinity(0))
    return {
        "cpu": cpu or "unknown",
        "machine": platform.machine(),
        "platform": platform.platform(),
        "os_cpu_count": os.cpu_count(),
        "usable_cpus": affinity,
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torch_threads": torch.get_num_threads(),
        "numpy": np.__version__,
        # Other processes compete for the same cores on a shared box; the
        # 1/5/15-minute load average says how contended the run was.
        "load_average_at_report": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    }


def _parse_overrides(items) -> dict:
    import yaml

    out = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"override must be key=value, got {item!r}")
        key, value = item.split("=", 1)
        out[key.strip()] = yaml.safe_load(value)
    return out


def _load_toy_config(config_path, ppo_overrides: dict, env_overrides: dict) -> dict:
    from configs import load_config

    cfg = load_config(config_path)
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in cfg.items()}
    cfg.setdefault("env", {}).update(env_overrides)
    cfg.setdefault("ppo", {}).update(ppo_overrides)
    return cfg


def run_toy_env_only(num_envs: int, steps: int, cfg: dict, seed: int = 0) -> dict:
    """Raw env stepping (random actions, no model): the env-bound ceiling."""
    from training.experiment import make_env_for_training, make_env_from_config

    make_env = make_env_from_config(cfg["env"])
    env = make_env_for_training(make_env, num_envs)
    rng = np.random.default_rng(seed)
    try:
        if num_envs == 1:
            n_actions = int(env.action_space.n)
            env.reset(seed=seed)
        else:
            n_actions = int(env.single_action_space.n)
            env.reset(seeds=[seed + i for i in range(num_envs)])
        per_slot = max(steps // num_envs, 1)
        start = time.perf_counter()
        for _ in range(per_slot):
            if num_envs == 1:
                _, _, term, trunc, _ = env.step(int(rng.integers(n_actions)))
                if term or trunc:
                    env.reset()
            else:
                _, _, terms, truncs, _ = env.step(
                    [int(a) for a in rng.integers(n_actions, size=num_envs)])
                for i in range(num_envs):
                    if terms[i] or truncs[i]:
                        env.reset_env(i)
        elapsed = time.perf_counter() - start
    finally:
        env.close()
    total = per_slot * num_envs
    return {"num_envs": num_envs, "steps": total, "elapsed": elapsed,
            "env_steps_per_sec": total / elapsed if elapsed > 0 else 0.0}


def run_toy_variant(num_envs: int, total_timesteps: int, cfg: dict, seed: int = 0) -> dict:
    """Real PPO on toy Pong (fresh model, checkpoints in a temp dir)."""
    from training.experiment import make_env_for_training, make_env_from_config

    ppo_cfg = dict(cfg["ppo"])
    ppo_cfg.update(num_envs=num_envs, total_timesteps=total_timesteps, seed=seed,
                   checkpoint_every_updates=0, telemetry_path=None)
    torch.manual_seed(seed)
    np.random.seed(seed)
    make_env = make_env_from_config(cfg["env"])
    probe = make_env()
    n_actions = int(probe.action_space.n)
    probe.close()
    model = ActorCritic(num_actions=n_actions, **cfg.get("model", {}))
    env = make_env_for_training(make_env, num_envs)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ppo_cfg["checkpoint_dir"] = str(Path(tmp) / "ckpt")
            config = PPOConfig.from_dict(ppo_cfg)
            trainer = PPOTrainer(env, model, config)
            start = time.perf_counter()
            with contextlib.redirect_stdout(io.StringIO()):
                trainer.train()
            elapsed = time.perf_counter() - start
    finally:
        env.close()
    return {
        "num_envs": num_envs,
        "total_timesteps": trainer.num_timesteps,
        "updates": trainer.num_updates,
        "elapsed": elapsed,
        "env_steps_per_sec": trainer.num_timesteps / elapsed if elapsed > 0 else 0.0,
        "torch_threads_during_run": torch.get_num_threads(),
        "ppo": {k: v for k, v in sorted(ppo_cfg.items()) if k != "checkpoint_dir"},
        "env": dict(sorted(cfg["env"].items())),
    }


def run_toy_benchmark(num_envs_list, total_timesteps: int, cfg: dict, seed: int = 0,
                      env_only_steps: int = 4096, warmup_steps: int = 0) -> dict:
    if warmup_steps:
        run_toy_variant(1, warmup_steps, cfg, seed)
    env_only = [run_toy_env_only(n, env_only_steps, cfg, seed) for n in num_envs_list]
    train = [run_toy_variant(n, total_timesteps, cfg, seed) for n in num_envs_list]
    base = train[0]["env_steps_per_sec"] if train else 0.0
    for row in train:
        row["speedup_vs_first"] = row["env_steps_per_sec"] / base if base else None
    return {"env_only": env_only, "train": train}


def _toy_table(result: dict) -> str:
    lines = [f"{'envs':>4} {'env-only sps':>13} {'train sps':>10} {'time(s)':>8} "
             f"{'updates':>8} {'x vs first':>10}"]
    by_n = {r["num_envs"]: r for r in result["env_only"]}
    for r in result["train"]:
        eo = by_n.get(r["num_envs"], {}).get("env_steps_per_sec", float("nan"))
        lines.append(f"{r['num_envs']:>4} {eo:>13.1f} {r['env_steps_per_sec']:>10.1f} "
                     f"{r['elapsed']:>8.2f} {r['updates']:>8} {r['speedup_vs_first']:>10.2f}")
    return "\n".join(lines)


def main_toy(args) -> dict:
    ppo_over = _parse_overrides(args.ppo)
    env_over = _parse_overrides(args.env_opt)
    cfg = _load_toy_config(args.config or DEFAULT_CONFIG, ppo_over, env_over)
    num_envs_list = args.num_envs or [1, 8]
    for n in num_envs_list:
        if args.total_timesteps % n:
            raise SystemExit(f"--total-timesteps {args.total_timesteps} must be divisible by {n}")
    print(f"toy PPO benchmark: config={args.config or DEFAULT_CONFIG} "
          f"steps={args.total_timesteps} num_envs={num_envs_list} "
          f"ppo overrides={ppo_over} env overrides={env_over}")
    result = run_toy_benchmark(num_envs_list, args.total_timesteps, cfg, args.seed,
                               env_only_steps=args.env_only_steps,
                               warmup_steps=args.warmup_steps)
    print(_toy_table(result))
    report = {
        "benchmark": "training.benchmark --env toy",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": args.label,
        "host": host_info(),
        "config_file": str(args.config or "configs/default.yaml"),
        "ppo_overrides": ppo_over,
        "env_overrides": env_over,
        "total_timesteps_per_variant": args.total_timesteps,
        "seed": args.seed,
        **result,
    }
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        if args.append and out.exists():
            existing = json.loads(out.read_text(encoding="utf-8"))
            runs = existing.get("runs", [existing]) if isinstance(existing, dict) else list(existing)
            payload = {"runs": [*runs, report]}
        else:
            payload = {"runs": [report]}
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return report


def main():
    parser = argparse.ArgumentParser(
        description="PPO vectorization throughput benchmark (1 vs 2 vs 4 envs)"
    )
    parser.add_argument("--env", choices=("bench", "toy"), default="bench",
                        help="bench: scripted no-op env (default, legacy output); "
                             "toy: real toy Pong + real PPO config")
    parser.add_argument("--num-envs", type=int, nargs="+", default=None,
                        help="toy only: env counts to measure (default: 1 8)")
    parser.add_argument("--config", default=None, help="toy only: config YAML (default configs/default.yaml)")
    parser.add_argument("--ppo", action="append", default=[], metavar="KEY=VALUE",
                        help="toy only: override a ppo: key (repeatable)")
    parser.add_argument("--env-opt", action="append", default=[], metavar="KEY=VALUE",
                        help="toy only: override an env: key (repeatable)")
    parser.add_argument("--env-only-steps", type=int, default=4096,
                        help="toy only: random-action env steps for the env-only ceiling")
    parser.add_argument("--warmup-steps", type=int, default=0,
                        help="toy only: untimed 1-env warmup run first")
    parser.add_argument("--json", default=None, help="toy only: write results JSON here")
    parser.add_argument("--append", action="store_true",
                        help="toy only: append this run to an existing --json file")
    parser.add_argument("--label", default=None, help="toy only: free-text label stored in the JSON")
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
    if args.env == "toy":
        main_toy(args)
        return

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
