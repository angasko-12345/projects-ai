"""Profile where a PPO training step's wall time goes; writes experiments/stage2/profile.json.

    python experiments/stage2/profile_ppo.py [--engine legacy|fast] [--updates 2] [--threads 2]
        [--preset cleanrl] [--out PATH]

Wraps the hot calls of one trainer (env.step / reset_env, model forward,
forward_sequence, backward, optimizer step) with wall-clock timers and runs a
few real updates on ALE Pong with the configs/atari_pong.yaml model/ppo blocks
(8 envs x 128 steps). "other" = rollout or update time not inside a timed call
(numpy<->tensor copies, stacking, GAE, Python bookkeeping). Needs ale-py + torch.
No window, no SendInput.
"""
from __future__ import annotations

import argparse
import functools
import json
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "stage1"))

TIMES: dict = defaultdict(float)
CALLS: dict = defaultdict(int)


def timed(name, fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            TIMES[name] += time.perf_counter() - t0
            CALLS[name] += 1
    return wrapper


def main(argv=None) -> int:
    import torch

    from configs import load_config
    from throughput import hardware  # stage1 helper
    from training.experiment import make_env_for_training, make_env_from_config

    p = argparse.ArgumentParser()
    p.add_argument("--engine", default="legacy", choices=("legacy", "fast"))
    p.add_argument("--preset", default=None)
    p.add_argument("--recurrent", default=None, choices=(None, "true", "false"))
    p.add_argument("--updates", type=int, default=2)
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--config", default=str(ROOT / "configs" / "atari_pong.yaml"))
    p.add_argument("--out", default=str(ROOT / "experiments" / "stage2" / "profile.json"))
    p.add_argument("--label", default=None)
    a = p.parse_args(argv)
    torch.set_num_threads(a.threads)
    torch.manual_seed(0)
    cfg = load_config(a.config)
    model_cfg = dict(cfg["model"])
    if a.recurrent is not None:
        model_cfg["recurrent"] = a.recurrent == "true"
    make = make_env_from_config(cfg["env"])
    probe = make()
    n_actions = int(probe.action_space.n)
    probe.close()
    env = make_env_for_training(make, int(cfg["ppo"]["num_envs"]))
    # env timers: per-slot step (includes ALE + UGA preprocessing)
    for slot in getattr(env, "_envs", []):
        slot.step = timed("env.step (ALE + preprocessing)", slot.step)
        slot.reset = timed("env.reset", slot.reset)
    with tempfile.TemporaryDirectory() as tmp:
        ppo = {**cfg["ppo"], "checkpoint_dir": tmp, "checkpoint_every_updates": 0,
               "telemetry_path": None}
        if a.preset:
            ppo["preset"] = a.preset
        if a.engine == "fast":
            ppo["engine"] = "fast"
        unit = int(ppo["num_envs"]) * int(ppo["rollout_length"])
        ppo["total_timesteps"] = unit * (a.updates + 1)  # +1 warm-up update, excluded below
        if a.engine == "legacy" and not a.preset:
            from agent.model import ActorCritic
            from training.ppo import PPOConfig, PPOTrainer

            trainer = PPOTrainer(env, ActorCritic(num_actions=n_actions, **model_cfg),
                                 PPOConfig.from_dict(ppo))
        else:
            from training.fast_ppo import build_trainer

            trainer = build_trainer(env, n_actions, model_cfg, ppo, env_config=cfg["env"])
        model = trainer.model
        model.forward = timed("model.forward (rollout/bootstrap)", model.forward)
        if hasattr(model, "forward_sequence"):
            model.forward_sequence = timed("model.forward_sequence (update replay)", model.forward_sequence)
        if hasattr(model, "evaluate_actions"):
            model.evaluate_actions = timed("model.evaluate_actions (update)", model.evaluate_actions)
        opt = trainer.optimizer
        opt.step = timed("optimizer.step", opt.step)
        orig_backward = torch.Tensor.backward

        def backward(self, *x, **k):
            t0 = time.perf_counter()
            try:
                return orig_backward(self, *x, **k)
            finally:
                TIMES["loss.backward"] += time.perf_counter() - t0
                CALLS["loss.backward"] += 1
        torch.Tensor.backward = backward
        trainer.config.total_timesteps = unit  # warm-up update
        buf = trainer.collect_rollout(respect_budget=True)[0]
        trainer.update(buf)
        TIMES.clear()
        CALLS.clear()
        roll_s = upd_s = 0.0
        for _ in range(a.updates):
            trainer.config.total_timesteps = trainer.num_timesteps + unit
            t0 = time.perf_counter()
            buf = trainer.collect_rollout(respect_budget=True)[0]
            t1 = time.perf_counter()
            trainer.update(buf)
            t2 = time.perf_counter()
            roll_s += t1 - t0
            upd_s += t2 - t1
        torch.Tensor.backward = orig_backward
    env.close()
    steps = unit * a.updates
    total = roll_s + upd_s
    rollout_parts = {k: TIMES[k] for k in TIMES if k.startswith(("env.", "model.forward ("))}
    update_parts = {k: TIMES[k] for k in TIMES if k not in rollout_parts}
    rows = {
        "rollout_total_s": roll_s,
        **{f"rollout/{k}": v for k, v in rollout_parts.items()},
        "rollout/other (tensor conversion, stacking, bookkeeping)": roll_s - sum(rollout_parts.values()),
        "update_total_s": upd_s,
        **{f"update/{k}": v for k, v in update_parts.items()},
        "update/other (GAE, segment split, indexing, loss math)": upd_s - sum(update_parts.values()),
    }
    label = a.label or f"{a.engine}{'-' + a.preset if a.preset else ''}"
    result = {
        "label": label, "engine": a.engine, "preset": a.preset, "model": model_cfg,
        "num_envs": int(cfg["ppo"]["num_envs"]), "rollout_length": int(cfg["ppo"]["rollout_length"]),
        "updates_timed": a.updates, "steps_timed": steps, "torch_threads": a.threads,
        "steps_per_second": round(steps / total, 1),
        "seconds": {k: round(v, 3) for k, v in rows.items()},
        "share_of_total": {k: round(v / total, 3) for k, v in rows.items() if not k.endswith("_total_s")},
        "calls": dict(CALLS),
        "generated_utc": datetime.now(timezone.utc).isoformat(), "hardware": hardware(),
    }
    print(json.dumps(result, indent=2))
    out = Path(a.out)
    doc = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"runs": []}
    doc["runs"] = [r for r in doc["runs"] if r.get("label") != label] + [result]
    doc["note"] = ("Wall-clock split of PPO training on ALE Pong (configs/atari_pong.yaml blocks, "
                   "8 envs x 128 steps). One warm-up update excluded. Shared 2-vCPU sandbox.")
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
