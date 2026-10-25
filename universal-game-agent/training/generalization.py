"""Cross-game generalization experiment runner (arcade games, pixels only).

Arms (all share one model/PPO config; the model never sees a game id):

* ``specialist``: PPO on one training game (all ``num_envs`` slots that game).
* ``multi``: PPO on every training game at once -- a ``SyncVectorEnv`` whose
  slots round-robin over the games (slot ``i`` plays ``train[i % 3]``).
* ``scratch``: PPO from a fresh network on the held-out game.
* ``finetune``: PPO on the held-out game starting from the ``multi`` run of
  the same seed (weights only; fresh optimizer), same budget as ``scratch``.

Every run evaluates greedily on every game (20 fixed eval seeds per policy
seed, disjoint from training resets) and writes ``result.json`` under
``<artifacts>/runs/<run_id>/``. Checkpoints stay under ``<artifacts>``, which
must be outside the git tree (or in the gitignored ``checkpoints/``).

Usage (no window, no SendInput):
    python -m training.generalization run --arm multi --seed 0 --artifacts <dir>
    python -m training.generalization all --artifacts <dir> [--seeds 0 1 2] [--scale 1.0]
    python -m training.generalization baselines --artifacts <dir>
    python -m training.generalization gif --artifacts <dir> --out-dir <dir>
    python -m training.generalization aggregate --artifacts <dir> --out-dir <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from environment.arcade import (
    FAR_HELDOUT_GAME,
    GAMES,
    HELDOUT_GAME,
    TRAIN_GAMES,
    make_game,
    make_game_factory,
)
from training.arcade_baselines import eval_seeds, run_all, summarize_episodes

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = _ROOT / "experiments" / "generalization" / "config.yaml"


def load_config(path=DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for key in ("env", "model", "ppo", "budgets", "eval"):
        if key not in cfg:
            raise ValueError(f"{path}: missing section {key!r}")
    return cfg


def _factory(cfg: dict, game: str):
    e = cfg["env"]
    return make_game_factory(game, obs_size=int(e["obs_size"]), num_stack=int(e["num_stack"]),
                             skip=int(e.get("skip", 1)), max_steps=int(e["max_steps"]))


def slot_games(arm: str, game: str | None, num_envs: int) -> list[str]:
    """Game played by each vector slot: round-robin for ``multi``."""
    if arm == "multi":
        return [TRAIN_GAMES[i % len(TRAIN_GAMES)] for i in range(num_envs)]
    if game is None:
        raise ValueError(f"arm {arm!r} needs a game")
    return [game] * num_envs


def run_id(arm: str, seed: int, game: str | None) -> str:
    return f"{arm}_{'multi' if arm == 'multi' else game}_s{seed}"


def hardware() -> dict:
    import torch

    mem = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            mem = fh.readline().split()[1] + " kB"
    except OSError:
        pass
    return {"platform": platform.platform(), "processor": platform.processor() or platform.machine(),
            "cpu_count": os.cpu_count(), "mem_total": mem, "python": platform.python_version(),
            "torch": torch.__version__, "torch_threads": torch.get_num_threads(), "numpy": np.__version__}


def eval_model(model, cfg: dict, seed: int, games=None) -> dict:
    """Greedy eval of a model on each game; game-agnostic metrics."""
    from training.evaluate import evaluate

    n = int(cfg["eval"]["episodes"])
    out = {}
    for g in games or list(GAMES):
        rep = evaluate(model, _factory(cfg, g), episodes=n, seeds=eval_seeds(seed, n), greedy=True)
        out[g] = summarize_episodes(rep["episode_rewards"], rep["episode_lengths"],
                                    rep["episode_terminated"], rep["episode_negative_reward_steps"],
                                    g, int(cfg["env"]["max_steps"]))
        out[g]["action_counts"] = rep["action_counts"]
    return out


def train_run(cfg: dict, arm: str, seed: int, artifacts: Path, game: str | None = None,
              scale: float = 1.0, threads: int = 1) -> dict:
    import torch

    from agent.model import ActorCritic
    from environment.vec import SyncVectorEnv
    from training.ppo import PPOConfig, PPOTrainer, summarize_history

    torch.set_num_threads(threads)
    rid = run_id(arm, seed, game)
    run_dir = artifacts / "runs" / rid
    result_path = run_dir / "result.json"
    if result_path.exists():
        print(f"[skip] {rid}: {result_path} exists")
        return json.loads(result_path.read_text(encoding="utf-8"))
    run_dir.mkdir(parents=True, exist_ok=True)
    ppo = dict(cfg["ppo"])
    num_envs = int(ppo["num_envs"])
    unit = num_envs * int(ppo["rollout_length"])
    budget = int(cfg["budgets"][arm])
    total = max(unit, int(round(budget * scale / unit)) * unit)
    curve_every = cfg["budgets"].get("curve_every", {}).get(arm)
    points = list(range(0, total + 1, max(unit, int(round(curve_every * scale / unit)) * unit))) \
        if curve_every else [0, total]
    if points[-1] != total:
        points.append(total)
    games = slot_games(arm, game, num_envs)
    env = SyncVectorEnv([_factory(cfg, g) for g in games], check_spaces=True)
    ckpt_dir = run_dir / "ckpt"
    progress_path = run_dir / "progress.json"
    torch.manual_seed(seed)
    np.random.seed(seed)
    model = ActorCritic(num_actions=3, **cfg["model"])
    init_from = None
    if arm == "finetune":
        init_from = artifacts / "runs" / run_id("multi", seed, None) / "ckpt" / "ppo_final.pt"
        if not init_from.exists():
            raise FileNotFoundError(f"finetune needs the multi run first: {init_from}")
        model.load_state_dict(torch.load(init_from, map_location="cpu", weights_only=True)["model"])
    ppo_cfg = PPOConfig.from_dict({**ppo, "total_timesteps": points[1] if len(points) > 1 else total,
                                   # Reset seeds are distinct per policy seed (slot e of seed s
                                   # resets with 1000*s + 17 + e on its first episode).
                                   "seed": 1000 * seed + 17, "checkpoint_dir": str(ckpt_dir),
                                   "checkpoint_every_updates": 0})
    progress = {"curve": [], "train_seconds": 0.0, "resumed_from": None}
    if progress_path.exists() and (ckpt_dir / "ppo_final.pt").exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        trainer = PPOTrainer.load_checkpoint(ckpt_dir / "ppo_final.pt", env)
        progress["resumed_from"] = trainer.num_timesteps
        print(f"[resume] {rid} at {trainer.num_timesteps} steps")
    else:
        trainer = PPOTrainer(env, model, ppo_cfg)
    model = trainer.model
    histories = []
    done_points = {c["steps"] for c in progress["curve"]}
    for target in points:
        if target in done_points:
            continue
        if target > trainer.num_timesteps:
            trainer.config.total_timesteps = target
            t0 = time.perf_counter()
            histories.append(trainer.train())
            progress["train_seconds"] += time.perf_counter() - t0
        if curve_every or target == total:
            ev_games = [game] if (curve_every and target != total) else list(GAMES)
            progress["curve"].append({"steps": target, "eval": eval_model(model, cfg, seed, ev_games)})
        progress_path.write_text(json.dumps(progress) + "\n", encoding="utf-8")
    final = progress["curve"][-1]["eval"]
    summary = summarize_history(histories[-1]) if histories else {}
    result = {
        "run_id": rid, "arm": arm, "seed": seed, "train_games": sorted(set(games)), "slot_games": games,
        "init_from": str(init_from) if init_from else None, "total_timesteps": trainer.num_timesteps,
        "budget_scale": scale, "train_seconds": progress["train_seconds"],
        "steps_per_second": trainer.num_timesteps / max(progress["train_seconds"], 1e-9)
        if not progress.get("resumed_from") else None,
        "resumed_from": progress.get("resumed_from"),
        "final_eval": final, "curve": progress["curve"],
        "last_chunk_train_summary": {k: v for k, v in summary.items() if not isinstance(v, dict)},
        "config": {"env": cfg["env"], "model": cfg["model"], "ppo": {**ppo, "seed": 1000 * seed + 17}},
        "eval_seeds": eval_seeds(seed, int(cfg["eval"]["episodes"])),
        "hardware": hardware(), "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "checkpoint": str(ckpt_dir / "ppo_final.pt"),
    }
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    env.close()
    print(f"[done] {rid}: {trainer.num_timesteps} steps in {progress['train_seconds']:.0f}s; "
          + " ".join(f"{g}={final[g]['mean_reward']:+.2f}" for g in final))
    return result


def plan_runs(seeds) -> list[tuple[str, int, str | None]]:
    runs: list[tuple[str, int, str | None]] = []
    for s in seeds:
        runs.append(("multi", s, None))
    for s in seeds:
        for g in TRAIN_GAMES:
            runs.append(("specialist", s, g))
    for s in seeds:
        runs.append(("scratch", s, HELDOUT_GAME))
        runs.append(("finetune", s, HELDOUT_GAME))
    return runs


# -- GIFs --------------------------------------------------------------------
def record_gif(path: Path, game: str, seed: int, cfg: dict, model=None, policy: str = "random",
               max_frames: int = 240, scale: int = 3) -> dict:
    """Play one episode (model greedy, or a uniform-random policy) and save an upscaled GIF.

    The policy acts through the same ``PreprocessingWrapper`` (frame skip,
    stack, resize) as in training; every raw frame is recorded.
    """
    import gymnasium as gym
    import torch
    from PIL import Image

    from environment.preprocessing import PreprocessingWrapper

    frames: list = []

    class _Record(gym.Wrapper):
        def reset(self, **kw):
            obs, info = self.env.reset(**kw)
            frames.append(obs)
            return obs, info

        def step(self, action):
            out = self.env.step(action)
            frames.append(out[0])
            return out

    e = cfg["env"]
    env = PreprocessingWrapper(_Record(make_game(game, int(e["max_steps"]))), size=int(e["obs_size"]),
                               num_stack=int(e["num_stack"]), skip=int(e.get("skip", 1)))
    rng = np.random.default_rng(seed)
    obs, _ = env.reset(seed=eval_seeds(seed, 1)[0])
    hidden = model.initial_state(1) if model is not None else None
    total = 0.0
    while len(frames) < max_frames:
        if model is not None:
            with torch.no_grad():
                logits, _, hidden = model(torch.from_numpy(obs).unsqueeze(0), hidden)
            a = int(logits.argmax(-1).item())
        else:
            a = int(rng.integers(0, 3))
        obs, r, term, trunc, _ = env.step(a)
        total += r
        if term or trunc:
            break
    frames = frames[:max_frames]
    imgs = [Image.fromarray(f).resize((f.shape[1] * scale, f.shape[0] * scale), Image.NEAREST)
            for f in frames]
    path.parent.mkdir(parents=True, exist_ok=True)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=40, loop=0, optimize=True)
    return {"path": str(path), "frames": len(frames), "reward": total, "bytes": path.stat().st_size}


# -- aggregation -------------------------------------------------------------
def aggregate(artifacts: Path, out_dir: Path) -> dict:
    runs = {}
    for p in sorted((artifacts / "runs").glob("*/result.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        runs[r["run_id"]] = r
    base_path = artifacts / "baselines.json"
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else None

    def stat(vals):
        return {"mean": float(np.mean(vals)), "std": float(np.std(vals)), "n": len(vals),
                "per_seed": [float(v) for v in vals]}

    table: dict = {}
    for g in GAMES:
        row: dict = {}
        if base:
            for pol, v in base["results"][g].items():
                row[pol] = {"mean": v["mean_over_seeds"], "std": v["std_over_seeds"],
                            "n": len(v["per_seed"]), "survival_rate": v["survival_rate"],
                            **({"clear_rate": v["clear_rate"]} if "clear_rate" in v else {})}
        groups = {
            "multi": [r for r in runs.values() if r["arm"] == "multi"],
            "specialist_own": [r for r in runs.values() if r["arm"] == "specialist"
                               and r["train_games"] == [g]],
            "scratch": [r for r in runs.values() if r["arm"] == "scratch"],
            "finetune": [r for r in runs.values() if r["arm"] == "finetune"],
        }
        for name, rs in groups.items():
            if not rs:
                continue
            ev = [r["final_eval"][g] for r in rs if g in r["final_eval"]]
            if not ev:
                continue
            row[name] = {**stat([e["mean_reward"] for e in ev]),
                         "survival_rate": float(np.mean([e["survival_rate"] for e in ev])),
                         "mean_length": float(np.mean([e["mean_length"] for e in ev])),
                         **({"clear_rate": float(np.mean([e["clear_rate"] for e in ev]))}
                            if g == "breakout" else {})}
        for tg in TRAIN_GAMES:  # zero-shot of each specialist on this game
            rs = [r for r in runs.values() if r["arm"] == "specialist" and r["train_games"] == [tg]]
            if rs and tg != g:
                row[f"specialist_{tg}_zero_shot"] = stat([r["final_eval"][g]["mean_reward"] for r in rs])
        table[g] = row
    curves: dict = {}
    for arm in ("scratch", "finetune"):
        rs = sorted((r for r in runs.values() if r["arm"] == arm), key=lambda r: r["seed"])
        if not rs:
            continue
        steps = [c["steps"] for c in rs[0]["curve"]]
        per = [[c["eval"][HELDOUT_GAME]["mean_reward"] for c in r["curve"]] for r in rs]
        curves[arm] = {"steps": steps, "seeds": [r["seed"] for r in rs], "per_seed": per,
                       "mean": [float(np.mean(col)) for col in zip(*per, strict=True)],
                       "std": [float(np.std(col)) for col in zip(*per, strict=True)],
                       "auc_mean_per_seed": [float(np.mean(p)) for p in per]}
    timing = {rid: {"steps": r["total_timesteps"], "train_seconds": r["train_seconds"],
                    "steps_per_second": r["steps_per_second"]} for rid, r in runs.items()}
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(), "train_games": list(TRAIN_GAMES),
           "heldout_game": HELDOUT_GAME, "far_heldout_game": FAR_HELDOUT_GAME,
           "per_game": table, "heldout_curves": curves, "timing": timing,
           "hardware": next(iter(runs.values()))["hardware"] if runs else None,
           "config": next(iter(runs.values()))["config"] if runs else None}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    with open(out_dir / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["game", "policy", "mean_reward", "std_over_seeds", "n_seeds", "survival_rate", "clear_rate"])
        for g, row in table.items():
            for pol, v in row.items():
                w.writerow([g, pol, f"{v['mean']:.4f}", f"{v['std']:.4f}", v.get("n", ""),
                            v.get("survival_rate", ""), v.get("clear_rate", "")])
    with open(out_dir / "runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["run_id", "arm", "seed", "steps", "train_seconds", "steps_per_second",
                    *[f"eval_{g}" for g in GAMES]])
        for rid, r in sorted(runs.items()):
            w.writerow([rid, r["arm"], r["seed"], r["total_timesteps"], f"{r['train_seconds']:.1f}",
                        f"{r['steps_per_second']:.1f}" if r["steps_per_second"] else "",
                        *[f"{r['final_eval'][g]['mean_reward']:.3f}" for g in GAMES]])
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Cross-game generalization experiments (pixels only)")
    p.add_argument("command", choices=("run", "all", "baselines", "gif", "aggregate"))
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--artifacts", required=True, help="output dir for checkpoints/metrics (outside git)")
    p.add_argument("--arm", choices=("multi", "specialist", "scratch", "finetune"))
    p.add_argument("--game", default=None)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--seeds", type=int, nargs="+", default=None)
    p.add_argument("--scale", type=float, default=1.0, help="multiply every step budget")
    p.add_argument("--threads", type=int, default=1, help="torch.set_num_threads")
    p.add_argument("--out-dir", default=None, help="aggregate/gif output dir")
    a = p.parse_args(argv)
    cfg = load_config(a.config)
    art = Path(a.artifacts)
    art.mkdir(parents=True, exist_ok=True)
    seeds = a.seeds if a.seeds is not None else list(cfg.get("seeds", [0, 1, 2]))
    if a.command == "run":
        if a.arm is None:
            p.error("run needs --arm")
        game = HELDOUT_GAME if a.arm in ("scratch", "finetune") and a.game is None else a.game
        train_run(cfg, a.arm, a.seed, art, game=game, scale=a.scale, threads=a.threads)
    elif a.command == "all":
        res = run_all(int(cfg["eval"]["episodes"]), seeds, int(cfg["env"]["max_steps"]),
                      int(cfg["env"].get("skip", 1)))
        (art / "baselines.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
        for arm, s, g in plan_runs(seeds):
            train_run(cfg, arm, s, art, game=g, scale=a.scale, threads=a.threads)
        aggregate(art, Path(a.out_dir) if a.out_dir else art)
    elif a.command == "baselines":
        res = run_all(int(cfg["eval"]["episodes"]), seeds, int(cfg["env"]["max_steps"]),
                      int(cfg["env"].get("skip", 1)))
        (art / "baselines.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
        print(f"baselines -> {art / 'baselines.json'} ({res['seconds']:.1f}s)")
    elif a.command == "aggregate":
        aggregate(art, Path(a.out_dir) if a.out_dir else art)
    elif a.command == "gif":
        import torch

        from agent.model import ActorCritic

        out = Path(a.out_dir) if a.out_dir else art / "gifs"
        made = []
        ck = art / "runs" / run_id("multi", a.seed, None) / "ckpt" / "ppo_final.pt"
        multi = ActorCritic(num_actions=3, **cfg["model"])
        multi.load_state_dict(torch.load(ck, map_location="cpu", weights_only=True)["model"])
        multi.eval()
        for g in GAMES:
            made.append(record_gif(out / f"multi_s{a.seed}_{g}.gif", g, a.seed, cfg, model=multi))
        made.append(record_gif(out / f"random_{HELDOUT_GAME}.gif", HELDOUT_GAME, a.seed, cfg))
        ft = art / "runs" / run_id("finetune", a.seed, HELDOUT_GAME) / "ckpt" / "ppo_final.pt"
        if ft.exists():
            m = ActorCritic(num_actions=3, **cfg["model"])
            m.load_state_dict(torch.load(ft, map_location="cpu", weights_only=True)["model"])
            m.eval()
            made.append(record_gif(out / f"finetune_s{a.seed}_{HELDOUT_GAME}.gif", HELDOUT_GAME, a.seed,
                                   cfg, model=m))
        print(json.dumps(made, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
