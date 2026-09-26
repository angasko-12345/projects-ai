"""Real external-window smoke test: OS loop without any learning.

launch/attach game -> capture -> preprocess -> model inference ->
random/fixed action -> keyboard input -> wait -> next frame -> repeat.

The agent uses screen pixels + keyboard only; it never imports the game.
Not training: actions are random or a fixed sequence, rewards come from a
null provider, episodes end by step limit. Bounded by --steps.
"""
from __future__ import annotations

import argparse
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch

from agent.model import ActorCritic
from environment.external_game import (
    ExternalGameEnv,
    NullRewardProvider,
    StepLimitTermination,
    WindowLifecycle,
)
from interface.adapter import GameInterface
from interface.capture import MSSBackend, WindowCapture
from interface.controller import ActionDef, ActionMapper, SendInputBackend
from interface.window import WindowManager

APP = Path(__file__).resolve().parent.parent / "games" / "extern_pong.py"

# Extern-Pong expects arrows. Virtual-key codes, no game code imported.
ARROW_LEFT, ARROW_RIGHT = 0x25, 0x27


def build_action_table(hold_ms: int = 60) -> list[ActionDef]:
    return [ActionDef("NOOP", hold_ms=0),
            ActionDef("PRESS_LEFT", kind="key", vk=ARROW_LEFT, hold_ms=hold_ms),
            ActionDef("PRESS_RIGHT", kind="key", vk=ARROW_RIGHT, hold_ms=hold_ms)]


def launch_game(title: str, seed: int, fps: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(APP), "--title", title, "--seed", str(seed),
         "--fps", str(fps)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def drive_loop(env: ExternalGameEnv, model: ActorCritic, steps: int,
               mode: str = "random", seed: int = 0, max_seconds=None) -> dict:
    """Run the sense-infer-act loop over any ExternalGameEnv. No OS calls here.

    Modes: random (valid uniform actions), fixed (canned sequence), policy
    (greedy argmax from the model). Bounded by steps and optional
    max_seconds wall time. Reports external reward only, never internals.
    """
    if steps <= 0:
        raise ValueError(f"steps must be positive, got {steps!r}")
    if mode not in ("random", "fixed", "policy"):
        raise ValueError(f"unknown mode {mode!r}: expected random|fixed|policy")
    rng = random.Random(seed)
    fixed = [0, 1, 1, 2, 0, 2, 1, 0]
    was_training = model.training
    model = model.eval()
    device = next(model.parameters()).device
    try:
        obs, _ = env.reset(seed=seed)
        hidden = model.initial_state(1, device)
        counts: dict[int, int] = {}
        episodes = terminated = truncated = 0
        total_reward = 0.0
        pixel_delta = 0.0
        prev = obs.copy()
        status = "completed"
        start = time.perf_counter()
        with torch.no_grad():
            for t in range(steps):
                if max_seconds is not None and time.perf_counter() - start > max_seconds:
                    status = "timeout"
                    break
                frame = torch.from_numpy(np.ascontiguousarray(obs, dtype=np.float32))
                logits, _, hidden = model(frame.unsqueeze(0).to(device), hidden)
                if mode == "random":
                    action = rng.randrange(int(logits.shape[1]))
                elif mode == "policy":
                    action = int(logits.argmax(-1).item())
                else:
                    action = fixed[t % len(fixed)]
                obs, reward, term, trunc, _ = env.step(int(action))
                counts[int(action)] = counts.get(int(action), 0) + 1
                total_reward += float(reward)
                pixel_delta += float(np.abs(obs.astype(np.float64) - prev.astype(np.float64)).mean())
                prev = obs.copy()
                if term or trunc:
                    episodes += 1
                    terminated += term
                    truncated += trunc
                    if t + 1 < steps:
                        obs, _ = env.reset()
                        hidden = model.initial_state(1, device)
                        prev = obs.copy()
        elapsed = time.perf_counter() - start
        decisions = sum(counts.values())
    finally:
        if was_training:
            model.train()
    return {
        "status": status,
        "steps": steps,
        "decisions": decisions,
        "frames_captured": decisions + 1,
        "actions_sent": counts,
        "episodes": episodes,
        "terminated": terminated,
        "truncated": truncated,
        "total_external_reward": total_reward,
        "logits_shape": tuple(logits.shape) if decisions else None,
        "mean_pixel_delta": pixel_delta / max(decisions, 1),
        "elapsed_s": elapsed,
        "fps": decisions / max(elapsed, 1e-6),
    }


def run_bounded(env, model, steps: int, mode: str = "random", seed: int = 0,
                max_seconds=None) -> dict:
    """Drive the loop, converting environment failures into a failed report.

    Never raises for capture/input/termination failures: the run stops and
    the report explains why via status/error. Programmer errors (bad shapes,
    bad config) still raise.
    """
    try:
        report = drive_loop(env, model, steps, mode=mode, seed=seed,
                            max_seconds=max_seconds)
        return report
    except (RuntimeError, OSError, ValueError) as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                "steps": steps, "decisions": 0, "frames_captured": 0,
                "actions_sent": {}, "episodes": 0, "terminated": 0,
                "truncated": 0, "total_external_reward": 0.0,
                "mean_pixel_delta": 0.0, "elapsed_s": 0.0, "fps": 0.0}


def run_external_smoke(title: str = "ExternPongSmoke", steps: int = 50, seed: int = 0,
                       game_fps: int = 60, mode: str = "random",
                       post_action_delay_ms: float = 80.0,
                       capture_size: int = 96, launch: bool = True,
                       checkpoint=None, max_seconds=None,
                       attach_timeout_s: float = 15.0) -> dict:
    """Launch (optional), attach, drive the loop, clean up. Bounded by steps
    and optional max_seconds. Policy mode needs --checkpoint."""
    proc = launch_game(title, seed, game_fps) if launch else None
    backend = MSSBackend()
    try:
        manager = WindowManager(title)
        deadline = time.monotonic() + attach_timeout_s
        while True:
            try:
                manager.attach()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"could not attach to window {title!r} within {attach_timeout_s:g} s "
                        f"(is the game running? use --no-launch only for existing windows)")
                time.sleep(0.2)
        lifecycle = WindowLifecycle(manager)
        capture = WindowCapture(manager, backend, capture_size, capture_size)
        controller = ActionMapper(SendInputBackend(), build_action_table())
        game = GameInterface(capture, controller, manager)
        env = ExternalGameEnv(game, NullRewardProvider(),
                              StepLimitTermination(max_steps=steps),
                              lifecycle, num_stack=4, size=84,
                              post_action_delay_ms=post_action_delay_ms)
        if mode == "policy":
            if checkpoint is None:
                raise ValueError("policy mode needs --checkpoint PATH")
            import torch

            keys = torch.load(checkpoint, map_location="cpu", weights_only=True).keys()
            if "model_config" in keys:
                from training.ppo import PPOTrainer

                model = PPOTrainer.load_checkpoint(checkpoint, env).model
            else:
                model = ActorCritic.load(checkpoint)
        else:
            model = ActorCritic(num_actions=int(env.action_space.n))
        report = run_bounded(env, model, steps, mode=mode, seed=seed,
                             max_seconds=max_seconds)
        report["title"] = title
        report["window_alive"] = manager.is_alive()
        return report
    finally:
        backend.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="External-window OS-loop smoke test (no learning)")
    parser.add_argument("--title", default="ExternPongSmoke")
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--game-fps", type=int, default=60)
    parser.add_argument("--mode", choices=("random", "fixed", "policy"), default="random")
    parser.add_argument("--checkpoint", default=None, help="required for policy mode")
    parser.add_argument("--max-seconds", type=float, default=None, help="wall-time budget")
    parser.add_argument("--attach-timeout-s", type=float, default=15.0)
    parser.add_argument("--post-action-delay-ms", type=float, default=80.0)
    parser.add_argument("--capture-size", type=int, default=96)
    parser.add_argument("--no-launch", action="store_true", help="attach to an existing window instead")
    args = parser.parse_args(argv)
    if args.steps <= 0:
        print("error: --steps must be positive", file=sys.stderr)
        return 2
    try:
        report = run_external_smoke(title=args.title, steps=args.steps, seed=args.seed,
                                    game_fps=args.game_fps, mode=args.mode,
                                    post_action_delay_ms=args.post_action_delay_ms,
                                    capture_size=args.capture_size, launch=not args.no_launch,
                                    checkpoint=args.checkpoint, max_seconds=args.max_seconds,
                                    attach_timeout_s=args.attach_timeout_s)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if report["status"] != "completed" and report["status"] != "timeout":
        print(f"external smoke FAILED: {report.get('error')}", file=sys.stderr)
        return 1
    print(f"external smoke [{report['status']}]: steps={report['steps']} frames={report['frames_captured']} "
          f"actions={report['actions_sent']} episodes={report['episodes']} "
          f"(term={report['terminated']} trunc={report['truncated']}) "
          f"reward={report['total_external_reward']:.1f} "
          f"pixel_delta={report['mean_pixel_delta']:.4f} "
          f"elapsed={report['elapsed_s']:.1f}s fps={report['fps']:.1f} "
          f"window_alive={report.get('window_alive')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
