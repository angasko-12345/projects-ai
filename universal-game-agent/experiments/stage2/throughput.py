"""Before/after PPO training throughput; writes experiments/stage2/throughput.json.

    python experiments/stage2/throughput.py [--rounds 2] [--updates 2] [--threads 2] [--out PATH]

Arms (ALE Pong, 8 envs x 128 steps, 4 epochs; one warm-up update excluded):

* ``legacy``      configs/atari_pong.yaml as Stage 1 ran it (legacy engine, GRU, float32 obs)
* ``fast_gru``    same model + hyperparameters on ``ppo.engine: fast`` with ``env.obs_dtype: uint8``
* ``fast_cleanrl`` configs/stage2_pong_cleanrl.yaml (CleanRL preset, frame-stack model, native
  actions, EpisodicLife + FireReset)

Arms are interleaved round by round so a co-tenant on a shared machine depresses every arm
alike; per-round load averages are recorded. Env-only rows compare the legacy float32 and
the fast uint8 preprocessing. Needs ale-py + torch. No window, no SendInput.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "stage1"))


def load(path):
    from configs import load_config

    return load_config(path)


def arm_configs():
    anchor = load(ROOT / "configs" / "atari_pong.yaml")
    fast_gru = json.loads(json.dumps(anchor))
    fast_gru["env"]["obs_dtype"] = "uint8"
    fast_gru["ppo"]["engine"] = "fast"
    cleanrl = load(ROOT / "configs" / "stage2_pong_cleanrl.yaml")
    return {"legacy": anchor, "fast_gru": fast_gru, "fast_cleanrl": cleanrl}


def build(cfg, tmp):
    from agent.model import ActorCritic
    from training.experiment import make_env_for_training, make_env_from_config
    from training.fast_ppo import build_trainer, wants_fast_engine
    from training.ppo import PPOConfig, PPOTrainer

    make = make_env_from_config(cfg["env"])
    probe = make()
    n_actions = int(probe.action_space.n)
    probe.close()
    ppo = {**cfg["ppo"], "checkpoint_dir": tmp, "checkpoint_every_updates": 0, "telemetry_path": None,
           "torch_threads": 0}
    env = make_env_for_training(make, int(ppo["num_envs"]))
    if wants_fast_engine(ppo):
        return build_trainer(env, n_actions, cfg["model"], ppo, env_config=cfg["env"]), env
    ppo.pop("torch_threads")
    return PPOTrainer(env, ActorCritic(num_actions=n_actions, **cfg["model"]), PPOConfig.from_dict(ppo)), env


def ppo_rate(cfg, updates):
    with tempfile.TemporaryDirectory() as tmp:
        trainer, env = build(cfg, tmp)
        unit = int(cfg["ppo"]["num_envs"]) * int(cfg["ppo"]["rollout_length"])
        trainer.config.total_timesteps = unit  # warm-up
        trainer.update(trainer.collect_rollout(respect_budget=True)[0])
        t0 = time.perf_counter()
        for _ in range(updates):
            trainer.config.total_timesteps = trainer.num_timesteps + unit
            trainer.update(trainer.collect_rollout(respect_budget=True)[0])
        dt = time.perf_counter() - t0
        env.close()
    return {"steps": unit * updates, "seconds": round(dt, 2), "steps_per_second": round(unit * updates / dt, 1)}


def env_rate(obs_dtype, n, seconds):
    from environment.atari_env import make_atari_factory
    from environment.vec import SyncVectorEnv

    vec = SyncVectorEnv([make_atari_factory("pong", sticky=0.0, obs_dtype=obs_dtype)] * n)
    rng = np.random.default_rng(0)
    vec.reset(seeds=list(range(n)))
    steps, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        _, _, te, tr, _ = vec.step([int(a) for a in rng.integers(0, 18, size=n)])
        steps += n
        for i in range(n):
            if te[i] or tr[i]:
                vec.reset_env(i)
    dt = time.perf_counter() - t0
    vec.close()
    return {"obs_dtype": obs_dtype, "num_envs": n, "decisions_per_second": round(steps / dt, 1)}


def main(argv=None) -> int:
    import torch
    from throughput import hardware

    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument("--updates", type=int, default=2)
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--env-seconds", type=float, default=10.0)
    p.add_argument("--arms", nargs="*", default=["legacy", "fast_gru", "fast_cleanrl"])
    p.add_argument("--out", default=str(ROOT / "experiments" / "stage2" / "throughput.json"))
    a = p.parse_args(argv)
    torch.set_num_threads(a.threads)
    torch.manual_seed(0)
    configs = arm_configs()
    rows = []
    for rnd in range(a.rounds):
        for arm in a.arms:
            load_before = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
            row = {"round": rnd, "arm": arm, **ppo_rate(configs[arm], a.updates),
                   "load_1min_before": load_before,
                   "load_1min_after": os.getloadavg()[0] if hasattr(os, "getloadavg") else None}
            print(row, flush=True)
            rows.append(row)
    summary = {}
    for arm in a.arms:
        vals = [r["steps_per_second"] for r in rows if r["arm"] == arm]
        summary[arm] = {"median_sps": float(np.median(vals)), "runs": vals}
    if "legacy" in summary:
        for arm in summary:
            summary[arm]["speedup_vs_legacy"] = round(summary[arm]["median_sps"] / summary["legacy"]["median_sps"], 2)
    env_rows = [env_rate(d, 8, a.env_seconds) for d in ("float32", "uint8")]
    for r in env_rows:
        print(r, flush=True)
    doc = {"note": ("ALE Pong PPO training steps/s (agent decisions incl. update), 8 envs x 128 steps, "
                    "interleaved rounds; shared 2-vCPU sandbox -- see load columns."),
           "torch_threads": a.threads, "updates_per_measurement": a.updates, "ppo": rows,
           "summary": summary, "env_only_8_envs": env_rows, "hardware": hardware(),
           "measured_local": time.strftime("%Y-%m-%d %H:%M:%S %z")}
    Path(a.out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
