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
               mode: str = "random", seed: int = 0) -> dict:
    """Run the sense-infer-act loop over any ExternalGameEnv. No OS calls here."""
    if steps <= 0:
        raise ValueError(f"steps must be positive, got {steps!r}")
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
        pixel_delta = 0.0
        prev = obs.copy()
        start = time.perf_counter()
        with torch.no_grad():
            for t in range(steps):
                frame = torch.from_numpy(np.ascontiguousarray(obs, dtype=np.float32))
                logits, _, hidden = model(frame.unsqueeze(0).to(device), hidden)
                action = rng.randrange(int(logits.shape[1])) if mode == "random" else fixed[t % len(fixed)]
                obs, _, term, trunc, _ = env.step(int(action))
                counts[int(action)] = counts.get(int(action), 0) + 1
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
        "steps": steps,
        "decisions": decisions,
        "frames_captured": decisions + 1,
        "actions_sent": counts,
        "episodes": episodes,
        "terminated": terminated,
        "truncated": truncated,
        "logits_shape": tuple(logits.shape),
        "mean_pixel_delta": pixel_delta / max(decisions, 1),
        "elapsed_s": elapsed,
        "fps": decisions / max(elapsed, 1e-6),
    }


def run_external_smoke(title: str = "ExternPongSmoke", steps: int = 50, seed: int = 0,
                       game_fps: int = 60, mode: str = "random",
                       post_action_delay_ms: float = 80.0,
                       capture_size: int = 96, launch: bool = True) -> dict:
    """Launch (optional), attach, drive the loop, clean up. Bounded by steps."""
    proc = launch_game(title, seed, game_fps) if launch else None
    backend = MSSBackend()
    try:
        manager = WindowManager(title)
        deadline = time.monotonic() + 15.0
        while True:
            try:
                manager.attach()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"could not attach to window {title!r} within 15 s")
                time.sleep(0.2)
        lifecycle = WindowLifecycle(manager)
        capture = WindowCapture(manager, backend, capture_size, capture_size)
        controller = ActionMapper(SendInputBackend(), build_action_table())
        game = GameInterface(capture, controller, manager)
        stack_size = 84
        env = ExternalGameEnv(game, NullRewardProvider(),
                              StepLimitTermination(max_steps=steps),
                              lifecycle, num_stack=4, size=stack_size,
                              post_action_delay_ms=post_action_delay_ms)
        model = ActorCritic(num_actions=int(env.action_space.n))
        report = drive_loop(env, model, steps, mode=mode, seed=seed)
        report["title"] = title
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
    parser.add_argument("--mode", choices=("random", "fixed"), default="random")
    parser.add_argument("--post-action-delay-ms", type=float, default=80.0)
    parser.add_argument("--capture-size", type=int, default=96)
    parser.add_argument("--no-launch", action="store_true", help="attach to an existing window instead")
    args = parser.parse_args(argv)
    if args.steps <= 0:
        print("error: --steps must be positive", file=sys.stderr)
        return 2
    report = run_external_smoke(title=args.title, steps=args.steps, seed=args.seed,
                                game_fps=args.game_fps, mode=args.mode,
                                post_action_delay_ms=args.post_action_delay_ms,
                                capture_size=args.capture_size, launch=not args.no_launch)
    print(f"external smoke: steps={report['steps']} frames={report['frames_captured']} "
          f"actions={report['actions_sent']} episodes={report['episodes']} "
          f"(term={report['terminated']} trunc={report['truncated']}) "
          f"pixel_delta={report['mean_pixel_delta']:.4f} "
          f"elapsed={report['elapsed_s']:.1f}s fps={report['fps']:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
