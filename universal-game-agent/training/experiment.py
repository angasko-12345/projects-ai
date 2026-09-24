"""Reproducible experiment: baseline eval -> fresh-model PPO train -> eval.

Usage: python -m training.experiment --config experiments/exp_toy_ppo_01.yaml
Writes <config-stem>_results.json next to the config and prints the report:
initial/final mean reward, episode length, eval performance, time, FPS.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
import yaml

from agent.model import ActorCritic
from environment.preprocessing import PreprocessingWrapper
from environment.toy_pong import ToyPongEnv
from training.evaluate import evaluate
from training.ppo import PPOConfig, PPOTrainer


def load_experiment(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ValueError(f"experiment config {path} must be a mapping")
    for section in ("env", "model", "ppo", "eval"):
        if section not in cfg:
            raise ValueError(f"experiment config {path} missing section: {section}")
    return cfg


_ENV_KEYS = {"type", "width", "height", "max_steps", "obs_size", "num_stack", "skip",
             "capture", "window", "lifecycle", "actions", "timing", "reward",
             "termination", "allow_live_capture"}
def make_env_from_config(env_cfg: dict):
    """Toy Pong by default; ``type: external`` delegates to the external factory."""
    import warnings

    if (env_cfg or {}).get("type", "toy") == "external":
        from environment.external_game import make_external_env_from_config

        return make_external_env_from_config(env_cfg)
    for key in (env_cfg or {}):
        if key not in _ENV_KEYS:
            warnings.warn(f"env: ignoring unknown config key {key!r}", UserWarning, stacklevel=3)

    def make():
        return PreprocessingWrapper(
            ToyPongEnv(
                width=int(env_cfg.get("width", 64)),
                height=int(env_cfg.get("height", 48)),
                max_steps=int(env_cfg.get("max_steps", 500)),
            ),
            size=int(env_cfg.get("obs_size", 84)),
            num_stack=int(env_cfg.get("num_stack", 4)),
            skip=int(env_cfg.get("skip", 1)),
        )
    return make


def run_experiment(config_path: str | Path) -> dict:
    config_path = Path(config_path)
    cfg = load_experiment(config_path)
    seed = int(cfg["ppo"].get("seed", 0))
    torch.manual_seed(seed)
    np.random.seed(seed)

    make_env = make_env_from_config(cfg["env"])
    probe = make_env()
    model = ActorCritic(num_actions=int(probe.action_space.n), **cfg["model"])
    probe.close()
    if int(cfg["ppo"].get("total_timesteps", 0)) <= 0:
        raise ValueError("ppo.total_timesteps must be positive")

    eval_cfg = cfg["eval"]
    eval_episodes = int(eval_cfg.get("episodes", 20))
    eval_seeds = [seed * 1000 + i for i in range(eval_episodes)]

    print("=== baseline eval (fresh random network) ===")
    baseline = evaluate(model, make_env, episodes=eval_episodes, seeds=eval_seeds)

    print("=== PPO training (fresh checkpoint) ===")
    ppo_config = PPOConfig.from_dict(cfg["ppo"])
    curiosity = None
    cur_cfg = cfg.get("curiosity") or {}
    if cur_cfg.get("enabled", False):
        from training.curiosity import CuriosityConfig, CuriosityModule

        curiosity = CuriosityModule(
            num_actions=model._config["num_actions"], config=CuriosityConfig.from_dict(cur_cfg)
        )
        print(f"curiosity enabled: scale={curiosity.config.scale}")
    trainer = PPOTrainer(make_env(), model, ppo_config, curiosity=curiosity)
    t0 = time.perf_counter()
    history = trainer.train()
    train_seconds = time.perf_counter() - t0

    print("=== final eval (trained network, separate env) ===")
    final = evaluate(model, make_env, episodes=eval_episodes, seeds=eval_seeds)

    report = {
        "config_file": str(config_path),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "curiosity_enabled": curiosity is not None,
        "initial_mean_episode_reward": baseline["mean_reward"],
        "final_train_rolling_mean_reward": history["mean_reward"][-1],
        "final_train_rolling_mean_ext_reward": history["mean_ext_reward"][-1],
        "final_train_rolling_mean_int_reward": history["mean_int_reward"][-1],
        "final_predictor_loss": history["predictor_loss"][-1],
        "mean_pixel_change": float(np.mean(history["pixel_change"])),
        "train_mean_episode_length": float(np.mean(history["upd_mean_length"])),
        "train_terminated_episodes": int(sum(history["upd_terminated"])),
        "train_truncated_episodes": int(sum(history["upd_truncated"])),
        "train_component_means": history["components"][-1] if history["components"] else {},
        "final_eval_mean_reward": final["mean_reward"],
        "final_eval_std_reward": final["std_reward"],
        "final_eval_mean_episode_length": final["mean_length"],
        "baseline_eval": baseline,
        "final_eval": final,
        "training_seconds": train_seconds,
        "training_steps": trainer.num_timesteps,
        "fps": trainer.num_timesteps / max(train_seconds, 1e-6),
        "history_tail": {k: v[-5:] for k, v in history.items()},
    }
    out_path = config_path.with_name(config_path.stem + "_results.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(
        f"baseline_mean={baseline['mean_reward']:.2f} "
        f"final_train_rolling={report['final_train_rolling_mean_reward']:.2f} "
        f"(ext={report['final_train_rolling_mean_ext_reward']:.2f} "
        f"int={report['final_train_rolling_mean_int_reward']:.3f}) "
        f"final_eval_mean={final['mean_reward']:.2f} "
        f"eval_len={final['mean_length']:.1f} "
        f"time={train_seconds:.0f}s fps={report['fps']:.0f} "
        f"-> {out_path}"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Baseline -> train -> eval experiment")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    run_experiment(args.config)
