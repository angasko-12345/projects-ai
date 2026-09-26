"""Live diagnostic for the extern-Pong reward/termination detectors.

Attaches to a real game window through the SAME pipeline the experiment
uses (WindowCapture + configured resize + ExternPongReward/Termination)
and prints per-frame classification. Never sends input: launch the game
yourself (or --launch it) and play manually while it watches.

Usage:
    python -m training.reward_diagnostic --title DiagGame --captures 200
    python -m training.reward_diagnostic --launch --title DiagGame --seed 3
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from environment.extern_pong_rewards import (
    ExternPongReward,
    ExternPongTermination,
    red_mask,
)
from interface.capture import MSSBackend, WindowCapture
from interface.window import WindowManager

APP = Path(__file__).resolve().parent.parent / "games" / "extern_pong.py"


def classify(red_count: int, detector: ExternPongReward) -> str:
    """Frame class label from the detector's own thresholds."""
    if red_count >= detector.miss_min:
        return "terminal"
    if detector.hit_min <= red_count <= detector.hit_max:
        return "hit"
    return "normal"


def diagnose(title: str, captures: int, capture_size: int = 96,
             interval_s: float = 0.05, launch: bool = False,
             seed: int = 0) -> dict:
    """Capture bounded frames, print classifications, return observed ranges."""
    proc = None
    if launch:
        proc = subprocess.Popen(
            [sys.executable, str(APP), "--title", title, "--seed", str(seed)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    backend = MSSBackend()
    try:
        manager = WindowManager(title)
        deadline = time.monotonic() + 20.0
        while True:
            try:
                manager.attach()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"could not attach to window {title!r}")
                time.sleep(0.3)
        capture = WindowCapture(manager, backend, capture_size, capture_size)
        reward_fn = ExternPongReward()
        term_fn = ExternPongTermination()
        ranges: dict[str, list] = {"normal": [], "hit": [], "terminal": []}
        prev = None
        prev_label = "normal"
        hits = misses = 0
        for i in range(captures):
            frame = capture.capture()
            reds = int(red_mask(frame).sum())
            label = classify(reds, reward_fn)
            ranges[label].append(reds)
            if prev is None:
                reward, terminated = 0.0, term_fn.terminated(frame, 0)
            else:
                reward = reward_fn.reward(prev, frame, 0)
                terminated = term_fn.terminated(frame, 0)
            if reward > 0:
                hits += 1
            if reward < 0:
                misses += 1
            if label != prev_label:
                print(f"[{i}] {prev_label} -> {label} "
                      f"(reward={reward:+.1f} terminated={terminated})", flush=True)
                prev_label = label
            else:
                print(f"[{i}] {frame.shape} reds={reds:5d} {label:8s} "
                      f"reward={reward:+.1f} terminated={terminated}", flush=True)
            prev = frame
            time.sleep(interval_s)
        summary = {k: (min(v), max(v), len(v)) if v else None for k, v in ranges.items()}
        print(f"ranges(min,max,n): {summary} hits={hits} misses={misses}", flush=True)
        return {"ranges": ranges, "hits": hits, "misses": misses,
                "summary": summary}
    finally:
        backend.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Live reward-detector diagnostic (no input sent)")
    parser.add_argument("--title", default="ExternPongDiag")
    parser.add_argument("--captures", type=int, default=200)
    parser.add_argument("--capture-size", type=int, default=96)
    parser.add_argument("--interval-s", type=float, default=0.05)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--seed", type=int, default=3)
    args = parser.parse_args(argv)
    if args.captures <= 0:
        print("error: --captures must be positive", file=sys.stderr)
        return 2
    try:
        diagnose(title=args.title, captures=args.captures,
                 capture_size=args.capture_size, interval_s=args.interval_s,
                 launch=args.launch, seed=args.seed)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
