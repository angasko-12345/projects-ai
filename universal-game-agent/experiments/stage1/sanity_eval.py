"""Evaluate a Pong sanity/anchor checkpoint against random and NOOP on identical seeds.

    python experiments/stage1/sanity_eval.py --config configs/atari_pong.yaml \
        --checkpoint <ckpt> --telemetry <csv> --out experiments/stage1/pong_sanity.json [--gif-dir DIR]

Greedy and sampled PPO, uniform-random UGA pad and constant NOOP all play the same
10 episode seeds (10000 + i) through the evaluation pipeline (raw game score, no
reward clipping). The training curve is read from the run's telemetry CSV. GIFs
are written only when Pillow is installed. No window, no SendInput.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def curve(telemetry: Path, every: int = 10240) -> list[dict]:
    rows = list(csv.DictReader(open(telemetry, encoding="utf-8")))
    out, seen = [], set()
    for r in rows:
        t = int(r["timesteps"])
        if t in seen:
            continue
        seen.add(t)
        if t % every == 0 or r is rows[-1]:
            out.append({"steps": t, "rolling100_train_return": float(r["mean_reward"]),
                        "episodes_seen": int(r["episodes"]), "entropy": float(r["entropy"]),
                        "upd_mean_return": float(r["upd_mean_ext"]), "upd_episodes": int(r["upd_episodes"])})
    return out


def main(argv=None) -> int:
    import torch

    from configs import load_config
    from environment.atari_env import make_atari_env_from_config
    from training.atari_baselines import run_policy
    from training.evaluate import evaluate
    from training.ppo import PPOTrainer

    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--telemetry", default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--episodes", type=int, default=10)
    p.add_argument("--gif-dir", default=None)
    p.add_argument("--note", default="")
    a = p.parse_args(argv)
    torch.set_num_threads(2)
    cfg = load_config(a.config)
    make = make_atari_env_from_config(cfg["env"], train=False)
    seeds = [10_000 + i for i in range(a.episodes)]
    model = PPOTrainer.load_checkpoint(a.checkpoint, make()).model
    ckpt = torch.load(a.checkpoint, map_location="cpu", weights_only=True)
    res = {"generated_utc": datetime.now(timezone.utc).isoformat(), "config": a.config,
           "checkpoint_steps": int(ckpt["num_timesteps"]), "checkpoint_updates": int(ckpt["num_updates"]),
           "action_space": ckpt.get("action_space"), "env": cfg["env"], "seeds": seeds, "note": a.note,
           "eval": {}}
    torch.manual_seed(0)
    for name, greedy in (("ppo_greedy", True), ("ppo_sampled", False)):
        rep = evaluate(model, make, episodes=a.episodes, seeds=seeds, greedy=greedy)
        res["eval"][name] = {k: rep[k] for k in ("mean_reward", "std_reward", "min_reward", "max_reward",
                                                  "mean_length", "episode_rewards", "action_counts")}
        print(name, rep["mean_reward"], rep["episode_rewards"], flush=True)
    for pol in ("random", "const_noop"):
        rep = run_policy(make, pol, seeds, rng_seed=0)
        res["eval"][pol] = {k: rep[k] for k in ("mean_reward", "std_reward", "min_reward", "max_reward",
                                                 "mean_length", "episode_rewards")}
        print(pol, rep["mean_reward"], flush=True)
    if a.telemetry:
        res["training_curve"] = curve(Path(a.telemetry))
    if a.gif_dir:
        try:
            import PIL  # noqa: F401
        except ImportError:
            print("Pillow absent: no GIFs")
        else:
            from training.generalization import record_gif

            gcfg = {"suite": {"kind": "atari", "train": ["pong"], "heldout": "breakout",
                              "far_heldout": "freeway"},
                    "env": {k: v for k, v in cfg["env"].items() if k not in ("type", "vector_backend")}}
            model.eval()
            gifs = []
            for tag, m in (("random", None), ("ppo", model)):
                gifs.append(record_gif(Path(a.gif_dir) / f"pong_{tag}.gif", "pong", 0, gcfg, model=m,
                                       max_frames=480, scale=1))
            res["gifs"] = gifs
            print(gifs)
    Path(a.out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
