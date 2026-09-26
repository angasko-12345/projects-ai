"""First real external-game RL experiment (no training-state reuse between phases).

Phase 1: launch game -> greedy baseline eval on a fresh model.
Phase 2: fresh game launch -> PPO training through screen + keyboard only.
Phase 3: fresh game launch -> separate eval env loads the trained checkpoint.

Usage: python -m training.external_experiment --config experiments/exp_external_pong_01.yaml
Writes <stem>_results.json next to the config. Learning improvement is
reported honestly, never assumed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
import yaml

from agent.model import ActorCritic
from environment.external_game import make_external_env_from_config
from interface.window import WindowManager
from training.evaluate import evaluate
from training.ppo import PPOConfig, PPOTrainer

APP = Path(__file__).resolve().parent.parent / "games" / "extern_pong.py"

METRIC_DEFINITIONS = {
    "episode_reward": "sum of external rewards in one episode (greedy eval) or PPO rollout accounting (train)",
    "mean_episode_reward": "mean over evaluated episodes, or rolling mean over last <=100 training episodes",
    "mean_episode_length": "mean decisions per episode",
    "terminated": "episodes ended by the game's own terminal signal (red MISS banner)",
    "truncated": "episodes ended by a step/time budget, not by the game",
    "external_reward": "reward from screen pixels via the configured provider (no game internals)",
    "intrinsic_reward": "curiosity bonus (0.0 here: curiosity disabled for this run)",
    "total_training_reward": "external + intrinsic per training episode",
    "decision_fps": "agent decisions per wall-clock second during training",
}

def launch_game(title: str, seed: int, game_fps: int, phase: str, geometry=None) -> subprocess.Popen:
    """Start the game with stdout/stderr captured for crash diagnosis."""
    log_path = Path("logs") / f"extern_pong_{title}_{phase}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")  # noqa: P201 -- closed with the process
    cmd = [sys.executable, str(APP), "--title", title, "--seed", str(seed),
           "--fps", str(game_fps)]
    if geometry:
        cmd += ["--geometry", str(geometry)]
    proc = subprocess.Popen(
        cmd,
        stdout=log_file, stderr=subprocess.STDOUT, close_fds=True,
    )
    proc._log_file = log_file  # noqa: SLF001 -- released in stop()
    return proc


def wait_attach(title: str, timeout_s: float = 20.0) -> None:
    manager = WindowManager(title)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            manager.attach()
            return
        except Exception:
            if time.monotonic() > deadline:
                raise RuntimeError(f"game window {title!r} did not appear within {timeout_s:g} s")
            time.sleep(0.3)


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    finally:
        log_file = getattr(proc, "_log_file", None)
        if log_file is not None:
            log_file.close()


def check_alive(proc: subprocess.Popen, phase: str) -> None:
    if proc.poll() is not None:
        raise RuntimeError(f"game process exited (code {proc.returncode}) during {phase}; "
                           f"see logs/extern_pong_*.log")


def dependency_versions() -> dict:
    versions = {"python": sys.version.split()[0]}
    for mod in ("torch", "gymnasium", "numpy", "mss", "yaml"):
        try:
            versions[mod] = __import__(mod).__version__
        except (ImportError, AttributeError):
            versions[mod] = "missing"
    return versions


def _unique_title(base: str) -> str:
    """Per-run window title so concurrent runs never share a game window."""
    return f"{base}-{os.getpid()}"


def _run_checkpoint_dir(ppo_cfg_raw: dict) -> str:
    """Per-run checkpoint dir so concurrent runs never share checkpoints."""
    return str(Path(ppo_cfg_raw.get("checkpoint_dir", "checkpoints")) / f"run-{os.getpid()}")


def _results_path(config_path) -> Path:
    """Per-run results file next to the config."""
    config_path = Path(config_path)
    return config_path.with_name(config_path.stem + f"-{os.getpid()}_results.json")


def _apply_run_title(env_cfg: dict, title: str) -> dict:
    """Copy of env_cfg with window capture/lifecycle retargeted at this run's title."""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (env_cfg or {}).items()}
    cap = dict(cfg.get("capture", {}) or {})
    if cap.get("mode") == "window":
        cap["title"] = title
        cfg["capture"] = cap
    life = dict(cfg.get("lifecycle", {}) or {})
    if life.get("mode") == "window":
        life["title"] = title
        cfg["lifecycle"] = life
    return cfg

def run_external_experiment(config_path) -> dict:
    config_path = Path(config_path)
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    game, env_cfg = cfg["game"], cfg["env"]
    model_cfg, ppo_cfg_raw, eval_cfg = cfg["model"], cfg["ppo"], cfg["eval"]
    seed = int(ppo_cfg_raw.get("seed", 0))
    title = _unique_title(str(game.get("title", "ExternPongExp")))
    env_cfg = _apply_run_title(env_cfg, title)
    eval_episodes = int(eval_cfg.get("episodes", 8))
    eval_seeds = [seed * 1000 + i for i in range(eval_episodes)]

    torch.manual_seed(seed)
    np.random.seed(seed)
    make_env = make_external_env_from_config(env_cfg)
    probe = make_env()
    num_actions = int(probe.action_space.n)
    probe.close()
    try:
        WindowManager(title).attach()
    except Exception:
        pass  # WindowNotFoundError expected: no stale game running
    else:
        raise RuntimeError(f"a window titled {title!r} already exists; "
                           f"kill the stale game before starting a new run")

    def fresh_model():
        torch.manual_seed(seed)
        return ActorCritic(num_actions=num_actions, **model_cfg)
    proc = launch_game(title, seed, int(game.get("fps", 60)), "phase1")
    try:
        wait_attach(title)
        baseline = evaluate(fresh_model(), make_env, episodes=eval_episodes, seeds=eval_seeds)
        check_alive(proc, "phase 1 baseline eval")
    finally:
        stop(proc)
    print(f"baseline mean={baseline['mean_reward']:.2f} len={baseline['mean_length']:.1f}")

    # Phase 2: fresh process, fresh model, PPO training.
    print("=== phase 2: PPO training ===")
    model = fresh_model()
    ppo_config = PPOConfig.from_dict(ppo_cfg_raw)
    ppo_config.checkpoint_dir = _run_checkpoint_dir(ppo_cfg_raw)
    proc = launch_game(title, seed, int(game.get("fps", 60)), "phase2")
    t0 = time.perf_counter()
    trainer = None
    try:
        wait_attach(title)
        trainer = PPOTrainer(make_env(), model, ppo_config, env_config=env_cfg)
        history = trainer.train()
    finally:
        if trainer is not None:
            trainer.env.close()
        stop(proc)
    train_seconds = time.perf_counter() - t0
    ckpt = str(Path(ppo_config.checkpoint_dir) / "ppo_final.pt")

    # Phase 3: fresh process, separate env, trained checkpoint.
    print("=== phase 3: final eval ===")
    proc = launch_game(title, seed, int(game.get("fps", 60)), "phase3")
    try:
        wait_attach(title)
        trained = PPOTrainer.load_checkpoint(ckpt, make_env()).model
        final = evaluate(trained, make_env, episodes=eval_episodes, seeds=eval_seeds)
        check_alive(proc, "phase 3 final eval")
    finally:
        stop(proc)
    print(f"final mean={final['mean_reward']:.2f} len={final['mean_length']:.1f}")
    out_path = _results_path(config_path)

    report = {
        "config_file": str(config_path),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "model_config": {"num_actions": num_actions, **model_cfg},
        "ppo_config": ppo_cfg_raw,
        "env_config": env_cfg,
        "game": {"title": title, "fps": int(game.get("fps", 60))},
        "metric_definitions": METRIC_DEFINITIONS,
        "baseline_eval": baseline,
        "final_eval": final,
        "initial_mean_episode_reward": baseline["mean_reward"],
        "final_eval_mean_reward": final["mean_reward"],
        "final_train_rolling_mean_reward": history["mean_reward"][-1],
        "final_train_rolling_mean_ext_reward": history["mean_ext_reward"][-1],
        "train_mean_episode_length": float(np.mean(history["upd_mean_length"])),
        "train_terminated_episodes": int(sum(history["upd_terminated"])),
        "train_truncated_episodes": int(sum(history["upd_truncated"])),
        "train_component_means": history["components"][-1] if history["components"] else {},
        "training_steps": trainer.num_timesteps,
        "training_seconds": train_seconds,
        "decision_fps": trainer.num_timesteps / max(train_seconds, 1e-6),
        "checkpoint": ckpt,
        "dependencies": dependency_versions(),
        "history_tail": {k: v[-5:] for k, v in history.items()},
    }
    out_path = _results_path(config_path)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"baseline={baseline['mean_reward']:.2f} final={final['mean_reward']:.2f} "
          f"steps={trainer.num_timesteps} time={train_seconds:.0f}s -> {out_path}")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Real external-game RL experiment")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    if not Path(args.config).is_file():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        return 2
    try:
        run_external_experiment(args.config)
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
