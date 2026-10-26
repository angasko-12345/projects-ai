"""Evaluate a Stage 2 Pong checkpoint on Stage 1's episode seeds; read its training curve.

    python experiments/stage2/pong_eval.py --config CFG --checkpoint CKPT --telemetry CSV \
        --out experiments/stage2/pong_<name>.json [--episodes 10] [--every 10240]

Greedy and sampled policy, uniform-random native actions and constant NOOP play the same
episode seeds 10000+i as experiments/stage1/sanity_eval.py, through the evaluation pipeline
(raw score, full games: no reward clipping, no EpisodicLife, no FireReset). The curve is the
telemetry's ``mean_game_score`` (rolling 100 full-game raw Pong scores) next to the training
return and diagnostics. No window, no SendInput.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def curve(path: Path, every: int) -> list[dict]:
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    out = []
    for i, r in enumerate(rows):
        t = int(r["timesteps"])
        if t % every and i != len(rows) - 1:
            continue
        g = float(r["mean_game_score"])
        out.append({"steps": t, "game_score_100": None if math.isnan(g) else round(g, 2),
                    "games_seen": int(r["game_episodes"]), "train_return_100": round(float(r["mean_reward"]), 3),
                    "entropy": round(float(r["entropy"]), 3), "approx_kl": round(float(r["approx_kl"]), 5),
                    "clipfrac": round(float(r["clipfrac"]), 4),
                    "explained_variance": round(float(r["explained_variance"]), 3),
                    "learning_rate": float(r["learning_rate"]), "fps": round(float(r["fps"]), 1)})
    return out


def scripted(make, seeds, policy, n_actions):
    rng = np.random.default_rng(0)
    scores, lengths = [], []
    for s in seeds:
        env = make()
        env.reset(seed=s)
        total, steps = 0.0, 0
        while True:
            a = int(rng.integers(0, n_actions)) if policy == "random" else 0
            _, r, te, tr, _ = env.step(a)
            total, steps = total + r, steps + 1
            if te or tr:
                break
        env.close()
        scores.append(total)
        lengths.append(steps)
    return {"mean": float(np.mean(scores)), "std": float(np.std(scores)), "scores": scores,
            "mean_length": float(np.mean(lengths))}


def main(argv=None) -> int:
    import torch

    from configs import load_config
    from environment.atari_env import make_atari_env_from_config
    from training.evaluate import evaluate
    from training.ppo import PPOTrainer

    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--telemetry", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--episodes", type=int, default=10)
    p.add_argument("--every", type=int, default=10240)
    p.add_argument("--note", default="")
    a = p.parse_args(argv)
    torch.set_num_threads(2)
    cfg = load_config(a.config)
    make = make_atari_env_from_config(cfg["env"], train=False)
    seeds = [10_000 + i for i in range(a.episodes)]
    trainer = PPOTrainer.load_checkpoint(a.checkpoint, make(), device="cpu")
    model = trainer.model
    probe = make()
    n_actions = int(probe.action_space.n)
    probe.close()
    ev = {}
    for name, greedy in (("ppo_greedy", True), ("ppo_sampled", False)):
        torch.manual_seed(0)
        rep = evaluate(model, make, episodes=a.episodes, seeds=seeds, greedy=greedy)
        ev[name] = {"mean": rep["mean_reward"], "std": rep["std_reward"], "scores": rep["episode_rewards"],
                    "mean_length": rep["mean_length"], "action_counts": rep["action_counts"]}
    ev["random_native"] = scripted(make, seeds, "random", n_actions)
    ev["const_noop"] = scripted(make, seeds, "noop", n_actions)
    out = {"config": a.config, "checkpoint_steps": trainer.num_timesteps, "seeds": seeds,
           "action_meanings": list(getattr(probe.unwrapped, "action_meanings", ()) or ()),
           "env": cfg["env"], "model": cfg["model"], "ppo": cfg["ppo"], "eval": ev,
           "training_curve": curve(Path(a.telemetry), a.every), "note": a.note}
    Path(a.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: (v["mean"], v["std"]) for k, v in ev.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
