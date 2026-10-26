"""Measure ALE adapter and PPO throughput; writes experiments/stage1/throughput.json.

    python experiments/stage1/throughput.py [--out PATH] [--seconds 20] [--ppo-updates 3]

Env-only rows step uniform-random UGA pad actions; "decisions" = agent steps
(4 emulator frames each). PPO rows run the real trainer (configs/atari_pong.yaml
model + ppo blocks) for a few updates and report steps / wall second including
the update. Needs ale-py and torch. No window, no SendInput.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def hardware() -> dict:
    import torch

    info = {"platform": platform.platform(), "machine": platform.machine(),
            "processor": platform.processor() or platform.machine(), "cpu_count": os.cpu_count(),
            "python": platform.python_version(), "torch": torch.__version__,
            "torch_threads": torch.get_num_threads(), "numpy": np.__version__}
    try:
        import ale_py

        info["ale_py"] = ale_py.__version__
    except ImportError:
        info["ale_py"] = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            info["mem_total"] = fh.readline().split()[1] + " kB"
    except OSError:
        pass
    if hasattr(os, "getloadavg"):  # shared machines: other jobs depress every number here
        info["load_average_1_5_15"] = [round(x, 2) for x in os.getloadavg()]
    return info


def env_rate(make_vec, n: int, seconds: float) -> dict:
    vec = make_vec(n)
    rng = np.random.default_rng(0)
    vec.reset(seeds=list(range(n)))
    steps, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        _, _, term, trunc, _ = vec.step([int(a) for a in rng.integers(0, 18, size=n)])
        steps += n
        for i in range(n):
            if term[i] or trunc[i]:
                vec.reset_env(i)
    dt = time.perf_counter() - t0
    vec.close()
    return {"num_envs": n, "decisions": steps, "seconds": round(dt, 2),
            "decisions_per_second": round(steps / dt, 1), "frames_per_second": round(4 * steps / dt, 1)}


def ppo_rate(cfg: dict, num_envs: int, updates: int, threads: int, backend: str) -> dict:
    import torch

    from agent.model import ActorCritic
    from training.experiment import make_env_for_training, make_env_from_config
    from training.ppo import PPOConfig, PPOTrainer

    torch.set_num_threads(threads)
    torch.manual_seed(0)
    env_cfg = {**cfg["env"], "vector_backend": backend}
    make = make_env_from_config(env_cfg)
    env = make_env_for_training(make, num_envs)
    with tempfile.TemporaryDirectory() as tmp:
        ppo = {**cfg["ppo"], "num_envs": num_envs, "checkpoint_dir": tmp,
               "checkpoint_every_updates": 0, "telemetry_path": None,
               "total_timesteps": num_envs * cfg["ppo"]["rollout_length"] * updates}
        trainer = PPOTrainer(env, ActorCritic(num_actions=18, **cfg["model"]), PPOConfig.from_dict(ppo))
        t0 = time.perf_counter()
        trainer.train()
        dt = time.perf_counter() - t0
    env.close()
    return {"num_envs": num_envs, "backend": backend, "torch_threads": threads, "updates": updates,
            "steps": trainer.num_timesteps, "seconds": round(dt, 1),
            "steps_per_second": round(trainer.num_timesteps / dt, 1)}


def main(argv=None) -> int:
    from configs import load_config
    from environment.atari_env import AtariVectorEnvAdapter, make_atari_factory
    from environment.vec import SyncVectorEnv

    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "experiments" / "stage1" / "throughput.json"))
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--ppo-updates", type=int, default=3)
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--skip-ppo", action="store_true")
    a = p.parse_args(argv)
    cfg = load_config(ROOT / "configs" / "atari_pong.yaml")
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(), "hardware": hardware(),
           "game": "pong", "note": "decisions = agent steps (4 emulator frames); random UGA-pad actions; "
           "sync = UGA SyncVectorEnv of python AtariGame + PreprocessingWrapper (sequential); "
           "ale = ale_py AtariVectorEnv (C++ threads, its own preprocessing)", "env_only": [], "ppo": []}
    sync = lambda n: SyncVectorEnv([make_atari_factory("pong", sticky=0.25)] * n)  # noqa: E731
    ale = lambda n: AtariVectorEnvAdapter("pong", n, sticky=0.25, num_threads=a.threads)  # noqa: E731
    for name, mk in (("sync", sync), ("ale", ale)):
        for n in (1, 4, 8):
            row = {"backend": name, **env_rate(mk, n, a.seconds)}
            print(row, flush=True)
            out["env_only"].append(row)
    if not a.skip_ppo:
        for n, backend in ((1, "sync"), (8, "sync"), (8, "ale")):
            row = ppo_rate(cfg, n, a.ppo_updates, a.threads, backend)
            print(row, flush=True)
            out["ppo"].append(row)
    Path(a.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
