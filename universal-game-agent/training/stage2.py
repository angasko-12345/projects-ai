"""Stage 2 multi-game Atari benchmark runner (fast PPO engine, pixels only).

Config: ``experiments/stage2/benchmark.yaml``. Arms:

* ``random``     -- uniform-random UGA-pad policy on every game (eval only);
* ``specialist`` -- one train game in every slot;
* ``multi``      -- every train game at once, slots follow ``suite.mix``;
* ``zeroshot``   -- the ``multi`` checkpoint evaluated on the held-out games
  (no training; read from the multi run's final evaluation);
* ``scratch``    -- fresh network on one held-out game;
* ``finetune``   -- held-out game starting from the ``multi`` weights of the
  same seed (fresh optimizer), same budget as ``scratch``.

Every run evaluates on every game at its end (``eval.policy``: sampled or
greedy, fixed seeds) and reports raw score and human-normalised score
(``training/hns.py``). Runs are chunked (``budgets.chunk_every``): each chunk
ends in a checkpoint + ``progress.json``, so a run killed by a session limit
resumes from its last chunk; ``--max-hours`` stops cleanly between chunks
(exit code 3 = paused, rerun the same command to continue). The model never
sees a game id: per-game ids only pick the reward normaliser's statistics.

Usage (no window, no SendInput):
    python -m training.stage2 plan      --config experiments/stage2/benchmark.yaml
    python -m training.stage2 random    --config ... --artifacts DIR
    python -m training.stage2 run       --config ... --artifacts DIR --arm multi --seed 0
    python -m training.stage2 run       --config ... --artifacts DIR --arm specialist --game pong --seed 0
    python -m training.stage2 all       --config ... --artifacts DIR --seeds 0 [--max-hours 11]
    python -m training.stage2 aggregate --config ... --artifacts DIR --out-dir DIR
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from training import hns

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = _ROOT / "experiments" / "stage2" / "benchmark.yaml"
ARMS = ("specialist", "multi", "scratch", "finetune")
PAUSED = 3
EVAL_SEED_BASE = 20_000


class Paused(Exception):
    """Raised between chunks when the wall-clock guard fires."""


def load_config(path=DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for key in ("suite", "env", "model", "ppo", "budgets", "eval"):
        if key not in cfg:
            raise ValueError(f"{path}: missing section {key!r}")
    s = cfg["suite"]
    train, held = list(s.get("train") or []), list(s.get("heldout") or [])
    if not train or not held or set(train) & set(held):
        raise ValueError("suite needs disjoint non-empty train and heldout lists")
    mix = s.get("mix") or {g: 1 for g in train}
    if set(mix) != set(train) or any(int(v) <= 0 for v in mix.values()):
        raise ValueError("suite.mix must give a positive slot count for every train game")
    if sum(int(v) for v in mix.values()) != int(cfg["ppo"]["num_envs"]):
        raise ValueError(f"sum(suite.mix) must equal ppo.num_envs ({cfg['ppo']['num_envs']})")
    return cfg


def games(cfg) -> list[str]:
    return [*cfg["suite"]["train"], *cfg["suite"]["heldout"]]


def slot_games(cfg, arm: str, game: str | None) -> list[str]:
    n = int(cfg["ppo"]["num_envs"])
    if arm == "multi":
        return [g for g in cfg["suite"]["train"] for _ in range(int(cfg["suite"]["mix"][g]))]
    if game is None:
        raise ValueError(f"arm {arm!r} needs --game")
    allowed = cfg["suite"]["train"] if arm == "specialist" else cfg["suite"]["heldout"]
    if game not in allowed:
        raise ValueError(f"arm {arm!r} plays one of {allowed}, got {game!r}")
    return [game] * n


def run_id(arm: str, seed: int, game: str | None) -> str:
    return f"{arm}_{'multi' if arm == 'multi' else game}_s{seed}"


def factory(cfg, game: str, train: bool = True):
    from environment.atari_env import atari_kwargs, make_atari_factory

    env_cfg = dict(cfg["env"])
    if not train and cfg["eval"].get("max_episode_frames"):
        env_cfg["max_episode_frames"] = int(cfg["eval"]["max_episode_frames"])
    return make_atari_factory(game, **atari_kwargs(env_cfg, train=train))


def eval_seeds(seed: int, n: int) -> list[int]:
    return [EVAL_SEED_BASE + 1000 * seed + i for i in range(n)]


def evaluate_games(model, cfg, seed: int, game_list) -> dict:
    import torch

    from training.evaluate import evaluate

    n = int(cfg["eval"]["episodes"])
    greedy = cfg["eval"].get("policy", "sample") == "greedy"
    out = {}
    for g in game_list:
        torch.manual_seed(EVAL_SEED_BASE + seed)  # sampled eval is reproducible
        rep = evaluate(model, factory(cfg, g, train=False), episodes=n, seeds=eval_seeds(seed, n),
                       greedy=greedy)
        out[g] = {"mean_score": rep["mean_reward"], "std_score": rep["std_reward"],
                  "scores": rep["episode_rewards"], "mean_length": rep["mean_length"],
                  "truncated_episodes": rep["truncated_episodes"],
                  "hns": hns.human_normalized(g, rep["mean_reward"]),
                  "policy": "greedy" if greedy else "sample"}
    return out


def random_baseline(cfg, seed: int, game_list) -> dict:
    from environment.universal_actions import NUM_PAD_ACTIONS

    n = int(cfg["eval"]["episodes"])
    rng = np.random.default_rng(EVAL_SEED_BASE + seed)
    out = {}
    for g in game_list:
        scores, lengths = [], []
        for s in eval_seeds(seed, n):
            env = factory(cfg, g, train=False)()
            try:
                env.reset(seed=s)
                total, steps = 0.0, 0
                while True:
                    _, r, te, tr, _ = env.step(int(rng.integers(0, NUM_PAD_ACTIONS)))
                    total, steps = total + float(r), steps + 1
                    if te or tr:
                        break
            finally:
                env.close()
            scores.append(total)
            lengths.append(steps)
        mean = float(np.mean(scores))
        out[g] = {"mean_score": mean, "std_score": float(np.std(scores)), "scores": scores,
                  "mean_length": float(np.mean(lengths)), "hns": hns.human_normalized(g, mean),
                  "policy": "uniform-random-pad"}
        print(f"[random] {g} s{seed}: {mean:+.1f}", flush=True)
    return out


def hardware() -> dict:
    import os
    import platform

    import torch

    info = {"platform": platform.platform(), "cpu_count": os.cpu_count(), "python": platform.python_version(),
            "torch": torch.__version__, "torch_threads": torch.get_num_threads(),
            "cuda": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
    return info


def _trim_telemetry(path: Path, max_steps: int) -> None:
    """Drop CSV rows past the checkpoint a resumed run restarts from (no double counting)."""
    if not path.exists():
        return
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 2:
        return
    keep = [rows[0]] + [r for r in rows[1:] if r and int(float(r[0])) <= max_steps]
    if len(keep) != len(rows):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(keep)


def train_run(cfg: dict, arm: str, seed: int, artifacts: Path, game: str | None = None,
              scale: float = 1.0, max_hours: float | None = None, device: str | None = None,
              max_chunks: int | None = None) -> dict:
    import torch

    from environment.vec import SyncVectorEnv
    from training.fast_ppo import FastPPOConfig, FastPPOTrainer, build_trainer

    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}")
    t_start = time.perf_counter()
    rid = run_id(arm, seed, game)
    run_dir = artifacts / "runs" / rid
    result_path = run_dir / "result.json"
    if result_path.exists():
        print(f"[skip] {rid}: done")
        return json.loads(result_path.read_text(encoding="utf-8"))
    run_dir.mkdir(parents=True, exist_ok=True)
    ppo = dict(cfg["ppo"])
    if device:
        ppo["device"] = device
    num_envs = int(ppo["num_envs"])
    unit = num_envs * int(ppo["rollout_length"])
    total = max(unit, int(round(int(cfg["budgets"][arm]) * scale / unit)) * unit)
    step = max(unit, int(round(int(cfg["budgets"].get("chunk_every", total)) * scale / unit)) * unit)
    points = sorted(set(range(step, total, step)) | {total})
    curve_every = (cfg["budgets"].get("curve_every") or {}).get(arm)
    curve = set()
    if curve_every:
        c = max(unit, int(round(int(curve_every) * scale / unit)) * unit)
        curve = set(range(c, total, c))
        points = sorted(set(points) | curve)
    slots = slot_games(cfg, arm, game)
    env = SyncVectorEnv([factory(cfg, g) for g in slots], check_spaces=True)
    ckpt_dir, progress_path = run_dir / "ckpt", run_dir / "progress.json"
    ppo.update({"total_timesteps": points[0], "anneal_total_timesteps": total,
                "seed": 1000 * seed + 17, "checkpoint_dir": str(ckpt_dir),
                "checkpoint_every_updates": 0, "telemetry_path": str(run_dir / "telemetry.csv")})
    progress = {"chunks": [], "curve": [], "train_seconds": 0.0, "resumes": 0}
    final_ckpt = ckpt_dir / "ppo_final.pt"
    init_from = None
    if progress_path.exists() and final_ckpt.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        trainer = FastPPOTrainer.load_checkpoint(final_ckpt, env,
                                                 config_overrides=asdict(FastPPOConfig.from_dict(ppo)))
        progress["resumes"] += 1
        _trim_telemetry(run_dir / "telemetry.csv", trainer.num_timesteps)
        print(f"[resume] {rid} at {trainer.num_timesteps} steps", flush=True)
    else:
        trainer = build_trainer(env, 18 if cfg["env"].get("action_space", "universal") == "universal"
                                else _native_actions(cfg, slots[0]),
                                cfg["model"], ppo, env_config=cfg["env"], slot_games=slots)
        if arm == "finetune":
            init_from = artifacts / "runs" / run_id("multi", seed, None) / "ckpt" / "ppo_final.pt"
            if not (artifacts / "runs" / run_id("multi", seed, None) / "result.json").exists():
                raise FileNotFoundError(f"finetune needs the finished multi run of seed {seed} first")
            state = torch.load(init_from, map_location="cpu", weights_only=True)["model"]
            trainer.model.load_state_dict(state)
            progress["init_from"] = str(init_from)
    done_curve = {c["steps"] for c in progress["curve"]}
    chunks_this_call = 0
    for target in points:
        if target <= trainer.num_timesteps and target not in curve - done_curve:
            continue
        if target > trainer.num_timesteps:
            if ((max_hours is not None and (time.perf_counter() - t_start) / 3600 > max_hours)
                    or (max_chunks is not None and chunks_this_call >= max_chunks)):
                env.close()
                raise Paused(f"{rid}: stop guard (max_hours={max_hours}, max_chunks={max_chunks}) "
                             f"at {trainer.num_timesteps} steps")
            chunks_this_call += 1
            trainer.config.total_timesteps = target
            t0, s0 = time.perf_counter(), trainer.num_timesteps
            hist = trainer.train()
            dt = time.perf_counter() - t0
            progress["train_seconds"] += dt
            progress["chunks"].append({"steps": trainer.num_timesteps, "seconds": round(dt, 1),
                                       "sps": round((trainer.num_timesteps - s0) / max(dt, 1e-9), 1),
                                       "game_score_100": hist["game_scores"][-1] if hist["game_scores"] else {}})
        if target in curve and target not in done_curve:
            progress["curve"].append({"steps": target,
                                      "eval": evaluate_games(trainer.model, cfg, seed, [game])})
            done_curve.add(target)
        progress_path.write_text(json.dumps(progress, indent=1) + "\n", encoding="utf-8")
    final = evaluate_games(trainer.model, cfg, seed, games(cfg))
    result = {
        "run_id": rid, "arm": arm, "seed": seed, "game": game, "slot_games": slots,
        "total_timesteps": trainer.num_timesteps, "budget_scale": scale,
        "train_seconds": progress["train_seconds"],
        "steps_per_second": trainer.num_timesteps / max(progress["train_seconds"], 1e-9),
        "resumes": progress["resumes"], "init_from": progress.get("init_from"),
        "final_eval": final, "curve": progress["curve"], "chunks": progress["chunks"],
        "config": {k: cfg[k] for k in ("suite", "env", "model", "ppo", "eval")},
        "hns_source": hns.SOURCE_URL, "hardware": hardware(),
        "device": str(trainer.device), "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "checkpoint": str(final_ckpt),
    }
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    env.close()
    print(f"[done] {rid}: {trainer.num_timesteps} steps in {progress['train_seconds']:.0f}s; "
          + " ".join(f"{g}={final[g]['mean_score']:+.1f}" for g in final), flush=True)
    return result


def _native_actions(cfg, game) -> int:
    probe = factory(cfg, game)()
    try:
        return int(probe.action_space.n)
    finally:
        probe.close()


def plan_runs(cfg, seeds) -> list[tuple[str, int, str | None]]:
    runs: list[tuple[str, int, str | None]] = []
    for s in seeds:
        runs.append(("multi", s, None))
        runs += [("specialist", s, g) for g in cfg["suite"]["train"]]
        for g in cfg["suite"]["heldout"]:
            runs += [("scratch", s, g), ("finetune", s, g)]
    return runs


def _stat(vals):
    vals = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return {"mean": None, "std": None, "n": 0}
    return {"mean": float(np.mean(vals)), "std": float(np.std(vals)), "n": len(vals)}


def aggregate(cfg, artifacts: Path, out_dir: Path) -> dict:
    """Raw score + HNS per (arm, game), mean +- std over seeds; transfer = finetune - scratch."""
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((artifacts / "runs").glob("*/result.json"))]
    base = artifacts / "random.json"
    rnd = json.loads(base.read_text(encoding="utf-8")) if base.exists() else {}
    cells: dict = {}

    def add(arm, game, seed, ev):
        cells.setdefault((arm, game), []).append((seed, ev["mean_score"], ev["hns"]))

    for seed, per_game in rnd.items():
        for g, ev in per_game.items():
            add("random", g, int(seed), ev)
    for r in runs:
        ev = r["final_eval"]
        if r["arm"] == "multi":
            for g in cfg["suite"]["train"]:
                add("multi", g, r["seed"], ev[g])
            for g in cfg["suite"]["heldout"]:
                add("zeroshot", g, r["seed"], ev[g])
        elif r["arm"] == "specialist":
            add("specialist", r["game"], r["seed"], ev[r["game"]])
        else:
            add(r["arm"], r["game"], r["seed"], ev[r["game"]])
    table = []
    for (arm, g), vals in sorted(cells.items()):
        table.append({"arm": arm, "game": g, "split": "train" if g in cfg["suite"]["train"] else "heldout",
                      "score": _stat([v[1] for v in vals]), "hns": _stat([v[2] for v in vals]),
                      "seeds": sorted(v[0] for v in vals)})
    summary = {}
    for arm in ("random", "specialist", "multi", "zeroshot", "scratch", "finetune"):
        for split in ("train", "heldout"):
            rows = [t for t in table if t["arm"] == arm and t["split"] == split and t["hns"]["mean"] is not None]
            if rows:
                vals = [t["hns"]["mean"] for t in rows]
                summary[f"{arm}/{split}"] = {"mean_hns": float(np.mean(vals)), "median_hns": float(np.median(vals)),
                                             "games": len(rows)}
    transfer = {}
    for g in cfg["suite"]["heldout"]:
        ft = {v[0]: v[1] for v in cells.get(("finetune", g), [])}
        sc = {v[0]: v[1] for v in cells.get(("scratch", g), [])}
        paired = [ft[s] - sc[s] for s in ft if s in sc]
        if paired:
            transfer[g] = {"finetune_minus_scratch": _stat(paired), "seeds": sorted(s for s in ft if s in sc)}
    out = {"table": table, "summary_hns": summary, "transfer": transfer, "runs": len(runs),
           "hns_source": hns.SOURCE_URL,
           "hns_note": "UGA protocol (sticky actions, eval frame cap, pad) differs from the reference "
                       "protocol; HNS is comparable across UGA arms, indicative vs published numbers."}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    with open(out_dir / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["arm", "game", "split", "score_mean", "score_std", "hns_mean", "hns_std", "n_seeds"])
        for t in table:
            w.writerow([t["arm"], t["game"], t["split"], t["score"]["mean"], t["score"]["std"],
                        t["hns"]["mean"], t["hns"]["std"], t["score"]["n"]])
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("command", choices=("plan", "random", "run", "all", "aggregate"))
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--artifacts", default=None)
    p.add_argument("--arm", choices=ARMS)
    p.add_argument("--game")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--seeds", type=int, nargs="*")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--max-hours", type=float, default=None,
                   help="stop cleanly between chunks after this many hours (exit 3 = paused)")
    p.add_argument("--max-chunks", type=int, default=None, help="stop after N chunks (exit 3)")
    p.add_argument("--device", choices=("cpu", "cuda", "auto"))
    p.add_argument("--threads", type=int, default=0)
    p.add_argument("--eval-episodes", type=int, default=None, help="override eval.episodes (smoke tests)")
    p.add_argument("--eval-max-frames", type=int, default=None, help="override eval.max_episode_frames")
    p.add_argument("--out-dir", default=None)
    a = p.parse_args(argv)
    cfg = load_config(a.config)
    if a.eval_episodes:
        cfg["eval"]["episodes"] = a.eval_episodes
    if a.eval_max_frames:
        cfg["eval"]["max_episode_frames"] = a.eval_max_frames
    seeds = a.seeds if a.seeds is not None else list(cfg.get("seeds", [0, 1, 2]))
    if a.threads:
        import torch

        torch.set_num_threads(a.threads)
        cfg["ppo"]["torch_threads"] = a.threads
    if a.command == "plan":
        for arm, s, g in plan_runs(cfg, seeds):
            print(f"{arm} {g or '-'} {s}")
        return 0
    if a.artifacts is None:
        p.error("--artifacts is required")
    art = Path(a.artifacts)
    art.mkdir(parents=True, exist_ok=True)
    try:
        if a.command == "random":
            path = art / "random.json"
            doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            for s in seeds:
                if str(s) not in doc:
                    doc[str(s)] = random_baseline(cfg, s, games(cfg))
                    path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        elif a.command == "run":
            if a.arm is None:
                p.error("run needs --arm")
            train_run(cfg, a.arm, a.seed, art, a.game, a.scale, a.max_hours, a.device, a.max_chunks)
        elif a.command == "all":
            t0 = time.perf_counter()
            for arm, s, g in plan_runs(cfg, seeds):
                left = None if a.max_hours is None else a.max_hours - (time.perf_counter() - t0) / 3600
                if left is not None and left <= 0:
                    raise Paused("wall-clock guard reached between runs")
                train_run(cfg, arm, s, art, g, a.scale, left, a.device)
        elif a.command == "aggregate":
            out = aggregate(cfg, art, Path(a.out_dir or art / "summary"))
            print(json.dumps(out["summary_hns"], indent=2))
    except Paused as exc:
        print(f"[paused] {exc}; rerun the same command to resume", flush=True)
        return PAUSED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
