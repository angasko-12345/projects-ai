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
from environment.external_game import SessionUnavailableError, make_external_env_from_config
from interface.capture import CaptureError
from interface.window import WindowLostError, WindowManager, WindowNotFoundError
from training.evaluate import evaluate
from training.ppo import PPOConfig, PPOTrainer, summarize_history

#: A dead game session, however it surfaced: the capture path raises a window
#: or capture error, ``reset()`` raises ``SessionUnavailableError``. All three
#: are recoverable by relaunching the game and reloading the checkpoint.
_SESSION_ERRORS = (WindowLostError, WindowNotFoundError, CaptureError,
                   SessionUnavailableError)
MAX_WINDOW_RELAUNCHES = 3


def _resume_trainer(trainer, make_env) -> PPOTrainer:
    """Persist in-memory training state and reload it on a fresh env."""
    path = str(Path(trainer.config.checkpoint_dir) / "ppo_interrupted.pt")
    trainer.save_checkpoint(path)
    env = make_env()  # if this raises, no env exists yet: nothing to close
    try:
        return PPOTrainer.load_checkpoint(path, env)
    except BaseException:
        # The checkpoint object never took ownership (it only owns env on
        # success), so close here or the fresh env leaks.
        try:
            env.close()
        except Exception:
            pass  # original load error is what the caller must see
        raise


def _concat_histories(histories: list[dict]) -> dict:
    """Concatenate per-attempt PPO histories (all values are lists)."""
    merged: dict = {}
    for history in histories:
        for key, values in history.items():
            merged.setdefault(key, []).extend(values)
    return merged


def _close_quietly(env) -> None:
    """Release an env; a dead session must not block the caller's cleanup."""
    try:
        env.close()
    except Exception:
        pass


def train_with_window_relaunch(new_trainer, make_env, launch_session,
                               max_relaunches: int = MAX_WINDOW_RELAUNCHES,
                               resume=_resume_trainer):
    """Train to the configured budget across game-window deaths.

    `new_trainer()` builds the fresh trainer; `make_env()` builds a new env
    for each attempt; `launch_session()` starts the game and returns a
    zero-arg cleanup. On session loss the in-memory state is checkpointed
    and reloaded on a fresh env (partial rollout steps are recounted, not
    replayed). Returns (trainer, merged_history, relaunch_count).
    """
    relaunches = 0
    trainer = new_trainer()
    histories = []
    while True:
        cleanup = launch_session()
        try:
            histories.append(trainer.train())
        except _SESSION_ERRORS as exc:
            _close_quietly(trainer.env)
            cleanup()
            if relaunches >= max_relaunches:
                raise RuntimeError(
                    f"game window lost {relaunches + 1} times; giving up: {exc}")
            relaunches += 1
            print(f"window lost ({exc}); relaunching "
                  f"({relaunches}/{max_relaunches})...", flush=True)
            trainer = resume(trainer, make_env)
        except BaseException:
            # Not a session loss: a real failure or a Ctrl-C. Both the branch
            # above and the success branch below are skipped, so the env and
            # the launched game process have to be released here or they
            # outlive the run -- a real window left up, still taking input.
            _close_quietly(trainer.env)
            cleanup()
            raise
        else:
            _close_quietly(trainer.env)
            cleanup()
            return trainer, _concat_histories(histories), relaunches
APP = Path(__file__).resolve().parent.parent / "games" / "extern_pong.py"

METRIC_DEFINITIONS = {
    "episode_reward": "sum of external rewards in one episode (greedy eval) or PPO rollout accounting (train)",
    "mean_episode_reward": "mean over evaluated episodes, or rolling mean over last <=100 training episodes",
    "mean_episode_length": "mean decisions per episode",
    "reward_semantics": "the evaluated env's declared reward semantics: 'sign' means reward>0 is a paddle hit and reward<0 a paddle miss; anything else is 'generic' and no hit/miss metric is reported",
    "mean_hits": "mean paddle hits per episode; only present when reward_semantics is 'sign'",
    "mean_misses": "mean paddle misses per episode; only present when reward_semantics is 'sign'",
    "terminated": "episodes ended by the game's own terminal signal (red MISS banner)",
    "truncated": "episodes ended by a step/time budget, not by the game",
    "external_reward": "reward from screen pixels via the configured provider (no game internals)",
    "intrinsic_reward": "curiosity bonus (0.0 here: curiosity disabled for this run)",
    "total_training_reward": "external + intrinsic per training episode",
    "decision_fps": "agent decisions per wall-clock second during training",
    "upd_action_share": "per-update fraction of each action index in the rollout (dict index -> share); exposes action imbalance or collapse",
    "decision_period_ms_measured": "measured wall-clock milliseconds between agent decisions (1000 / decision_fps)",
    "history_span_ms": "wall-clock span covered by the frame stack: num_stack x decision_period_ms_measured",
}

def launch_game(title: str, seed: int, game_fps: int, phase: str, geometry=None) -> subprocess.Popen:
    """Start the game with stdout/stderr captured for crash diagnosis."""
    log_path = Path("logs") / f"extern_pong_{title}_{phase}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")  # noqa: P201 -- owned below
    cmd = [sys.executable, str(APP), "--title", title, "--seed", str(seed),
           "--fps", str(game_fps)]
    if geometry:
        cmd += ["--geometry", str(geometry)]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file, stderr=subprocess.STDOUT, close_fds=True,
        )
    except BaseException:
        # Popen() never returned, so no handle exists for stop() to clean
        # up: release the log here or it leaks.
        log_file.close()
        raise
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
    # Safe to call more than once (failure-path cleanup may race the
    # session cleanup): terminate/wait on a reaped proc are no-ops and
    # closing an already-closed log file is harmless.
    if getattr(proc, "_stopped", False):
        return
    proc._stopped = True  # noqa: SLF001 -- flag lives with the handle
    try:
        proc.terminate()
    except Exception:
        pass  # already exited or unstartable: still reap + release the log
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        proc.wait(timeout=10)
    finally:
        log_file = getattr(proc, "_log_file", None)
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass


def check_alive(proc: subprocess.Popen, phase: str) -> None:
    if proc.poll() is not None:
        raise RuntimeError(f"game process exited (code {proc.returncode}) during {phase}; "
                           f"see logs/extern_pong_*.log")


def launch_phase2_process(title: str, seed: int, game_fps: int) -> subprocess.Popen:
    """Launch the phase-2 game; ownership transfers to the caller only on success.

    If attach or the startup liveness check fails, the process is stopped
    here (the caller never receives the handle, so nobody else could).
    """
    proc = launch_game(title, seed, game_fps, "phase2")
    try:
        wait_attach(title)
        check_alive(proc, "phase 2 startup")
    except BaseException:
        stop(proc)
        raise
    return proc


def load_eval_model(ckpt, make_env):
    """Load a trained model on a throwaway env that is always closed."""
    env = make_env()
    try:
        return PPOTrainer.load_checkpoint(ckpt, env).model
    finally:
        env.close()


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


#: Metrics compared between the untrained and trained runs. Hit/miss metrics
#: are added only when the reward semantics license them, so a generic-reward
#: run is never compared on counts that would not mean hits.
COMPARISON_METRICS = ("mean_reward", "std_reward", "mean_length",
                      "terminated_episodes", "truncated_episodes")
_SIGN_METRICS = ("mean_hits", "mean_misses")


def summarize_eval(rep: dict) -> dict:
    """Flat comparable summary of one evaluate() report (no verdict attached)."""
    total_actions = sum(rep["action_counts"].values()) or 1
    summary = {
        "episodes": int(rep["episodes"]),
        "reward_semantics": str(rep.get("reward_semantics", "generic")),
        "mean_reward": float(rep["mean_reward"]),
        "std_reward": float(rep["std_reward"]),
        "mean_length": float(rep["mean_length"]),
        "terminated_episodes": int(rep["terminated_episodes"]),
        "truncated_episodes": int(rep["truncated_episodes"]),
        "action_counts": {str(a): int(c) for a, c in rep["action_counts"].items()},
        "action_share": {str(a): float(c) / float(total_actions)
                         for a, c in rep["action_counts"].items()},
    }
    if summary["reward_semantics"] == "sign":
        summary["mean_hits"] = float(rep["mean_hits"])
        summary["mean_misses"] = float(rep["mean_misses"])
    return summary


def summarize_difference(untrained: dict, trained: dict) -> dict:
    """Trained-minus-untrained deltas over the metrics both summaries carry."""
    # Derived from the summaries, not from a second hand-maintained list: a
    # metric is compared only when both sides actually reported it.
    metrics = [m for m in (*COMPARISON_METRICS, *_SIGN_METRICS)
               if m in untrained and m in trained]
    diff = {m: float(trained[m]) - float(untrained[m]) for m in metrics}
    untrained_semantics, trained_semantics = (untrained.get("reward_semantics"),
                                              trained.get("reward_semantics"))
    diff["reward_semantics"] = (untrained_semantics
                                if untrained_semantics == trained_semantics
                                else "mixed")
    actions = sorted(set(untrained["action_counts"]) | set(trained["action_counts"]))
    diff["action_counts"] = {a: int(trained["action_counts"].get(a, 0))
                             - int(untrained["action_counts"].get(a, 0)) for a in actions}
    return diff


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
    """Run the experiment; a failure still leaves a results file behind.

    ROOT-027: record why the run died where operators look for results,
    then re-raise so cleanup and the CLI error path stay unchanged.
    """
    config_path = Path(config_path)
    try:
        return _run_external_experiment(config_path)
    except Exception as error:
        report = {
            "config_file": str(config_path),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error": str(error),
            "error_type": type(error).__name__,
        }
        try:
            with open(_results_path(config_path), "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2)
        except Exception as write_error:
            # Never mask the original run failure with the recorder's own error.
            print(f"warning: could not write failure results: {write_error}",
                  file=sys.stderr)
        raise


def _run_external_experiment(config_path: Path) -> dict:
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

    # Phase 2: fresh process, fresh model, PPO training (window-loss tolerant).
    print("=== phase 2: PPO training ===")
    ppo_config = PPOConfig.from_dict(ppo_cfg_raw)
    ppo_config.checkpoint_dir = _run_checkpoint_dir(ppo_cfg_raw)

    def new_trainer():
        return PPOTrainer(make_env(), fresh_model(), ppo_config, env_config=env_cfg)

    def launch_phase2():
        return launch_phase2_process(title, seed, int(game.get("fps", 60)))

    t0 = time.perf_counter()
    launch_state: dict = {}

    def launch_session():
        launch_state["proc"] = launch_phase2()
        return lambda: stop(launch_state["proc"])

    trainer, history, relaunches = train_with_window_relaunch(
        new_trainer, make_env, launch_session)
    train_seconds = time.perf_counter() - t0
    print(f"phase 2 done: relaunches={relaunches}")
    ckpt = str(Path(ppo_config.checkpoint_dir) / "ppo_final.pt")

    # Phase 3: fresh process, separate env, trained checkpoint.
    print("=== phase 3: final eval ===")
    proc = launch_game(title, seed, int(game.get("fps", 60)), "phase3")
    try:
        wait_attach(title)
        trained = load_eval_model(ckpt, make_env)
        final = evaluate(trained, make_env, episodes=eval_episodes, seeds=eval_seeds)
        check_alive(proc, "phase 3 final eval")
    finally:
        stop(proc)
    print(f"final mean={final['mean_reward']:.2f} len={final['mean_length']:.1f}")
    out_path = _results_path(config_path)
    untrained_summary = summarize_eval(baseline)
    trained_summary = summarize_eval(final)
    difference = summarize_difference(untrained_summary, trained_summary)
    # summarize_history, not direct indexing: a resumed checkpoint that already
    # met the budget runs no update, so the history can legitimately be empty
    # (ROOT-014) and every aggregate below has to survive that.
    summary = summarize_history(history)
    print(f"untrained: {untrained_summary}")
    print(f"trained:   {trained_summary}")
    print(f"delta (trained - untrained): {difference}")

    # Effective observation cadence: what the policy actually sees per decision
    # and how far back the frame stack reaches, measured from this run.
    decision_fps = trainer.num_timesteps / max(train_seconds, 1e-6)
    decision_period_ms = 1000.0 / decision_fps if decision_fps > 0 else None
    num_stack = int(env_cfg.get("num_stack", 4))
    timing_cfg = env_cfg.get("timing", {}) or {}
    action_table = (env_cfg.get("actions", {}) or {}).get("table", []) or []
    observation_timing = {
        "num_stack": num_stack,
        "obs_size": int(env_cfg.get("obs_size", 84)),
        "frame_skip": "none (one capture per decision on the external path)",
        "hold_ms_by_action": {str(a.get("name", i)): float(a.get("hold_ms", 0))
                              for i, a in enumerate(action_table)},
        "post_action_delay_ms": float(timing_cfg.get("post_action_delay_ms", 0.0)),
        "decision_period_ms_measured": decision_period_ms,
        "history_span_ms": (num_stack * decision_period_ms
                            if decision_period_ms is not None else None),
    }

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
        "comparison": {
            "untrained": untrained_summary,
            "trained": trained_summary,
            "difference_trained_minus_untrained": difference,
        },
        "initial_mean_episode_reward": baseline["mean_reward"],
        # updates_run makes "nothing was trained" visible in the artifact
        # instead of letting the aggregates below read as measurements.
        "training_updates": summary["updates_run"],
        "final_train_rolling_mean_reward": summary["mean_reward"],
        "final_train_rolling_mean_ext_reward": summary["mean_ext_reward"],
        "train_mean_episode_length": summary["mean_episode_length"],
        "train_terminated_episodes": summary["terminated_episodes"],
        "train_truncated_episodes": summary["truncated_episodes"],
        "train_component_means": summary["component_means"],
        "training_steps": trainer.num_timesteps,
        "training_seconds": train_seconds,
        "decision_fps": decision_fps,
        "observation_timing": observation_timing,
        "window_relaunches": relaunches,
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
