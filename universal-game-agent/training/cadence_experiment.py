"""Synthetic cadence control experiment: external-Pong timing without a window.

Reproduces the measured external decision timing in virtual time over the real
``games/pong_logic.PongLogic`` dynamics and the real observation/reward
pipeline (rendered frames -> ``ExternPongReward`` / ``ExternPongTermination``
-> ``ExternalGameEnv`` -> ``FrameStack``). No window, no SendInput, no MSS:
every ``Clock.sleep`` advances a virtual clock and the game ticks at the
configured frame rate, so hours of wall-clock play cost seconds.

Measured references this reproduces (from experiments/*_results.json):
  * decision period  147.6 ms (exp02: 6.775 decisions/s) .. 149.7 ms (exp01)
  * key hold         60 ms per press (env.actions.table hold_ms)
  * post-action delay 80 ms (env.timing.post_action_delay_ms)
  * game tick        60 fps (game.fps)

Timing model per decision (interface owns the whole period; the env's
post_action delay is 0 in synthetic runs):
  [hold_ms key-down | decision_period_ms - hold_ms key-up], game ticks
  continuously at 1/fps across both phases and across decisions.
A press therefore moves the paddle (held game ticks) x 5 px; with a 60 ms
hold at 60 fps that is 3-4 ticks (15-20 px) depending on tick phase.

Usage: python -m training.cadence_experiment --config <yaml>
Writes <stem>_results.json next to the config.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from environment.external_game import Clock, ExternalGameEnv
from environment.extern_pong_rewards import ExternPongReward, ExternPongTermination
from games.pong_logic import (
    BALL_SIZE,
    HEIGHT,
    LEFT,
    NOOP,
    PADDLE_H,
    PADDLE_W,
    PADDLE_Y,
    RIGHT,
    WIDTH,
    PongLogic,
)

#: Same three-action table as experiments/exp_external_pong_0*.yaml:
#: index -> (NOOP, PRESS_LEFT, PRESS_RIGHT).
_ACTION_TO_LOGIC = (NOOP, LEFT, RIGHT)
#: MISS banner visual duration; mirrors PongApp's ``banner_until = now + 1.0``.
BANNER_S = 1.0
#: Red rectangle used for the MISS banner; ~607 red px in the real Tk text,
#: so any count here stays inside the miss band (>= 300) and outside the
#: hit band (8..200).
BANNER_W, BANNER_H = 102, 6
_EPS = 1e-9


class SyntheticPongSession:
    """One virtual game window: PongLogic + visual-protocol state + virtual time.

    Mirrors ``games/extern_pong.PongApp`` tick semantics exactly (hit latch,
    MISS banner for 1.0 s, auto re-serve after the banner) without a Tk loop.
    """

    def __init__(self, seed: int = 0, fps: int = 60):
        if fps <= 0:
            raise ValueError(f"fps must be positive, got {fps!r}")
        self.logic = PongLogic(seed=seed)
        self.fps = int(fps)
        self.tick_s = 1.0 / self.fps
        self.t = 0.0
        self.next_tick = 0.0
        self.held = NOOP
        self.red_on = False
        self.banner_until = 0.0

    # -- time ------------------------------------------------------------
    def advance_to(self, target: float) -> None:
        """Run game ticks in [current, ``target``) -- end-exclusive.

        A tick exactly at a window boundary belongs to the NEXT window: a
        zero-length key hold must not move the paddle, and a tick at release
        time must see the key released.
        """
        while self.next_tick < target - _EPS:
            self.t = self.next_tick
            self._tick()
            self.next_tick += self.tick_s
        if target > self.t:
            self.t = target

    def advance_by(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError(f"cannot advance a negative duration: {seconds!r}")
        self.advance_to(self.t + seconds)

    # -- game loop (mirror of PongApp._tick, minus canvas drawing) --------
    def _tick(self) -> None:
        event = self.logic.step(self.held)
        if event == "hit":
            self.red_on = True  # latched until serve, as in the real app
        elif event == "miss":
            self.banner_until = self.t + BANNER_S
        if self.logic.over and self.t >= self.banner_until:
            self.logic.re_serve()
            self.red_on = False

    # -- visual protocol (mirror of PongApp drawing) ----------------------
    def render(self) -> np.ndarray:
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        g = self.logic
        px = g.paddle_x
        frame[PADDLE_Y:PADDLE_Y + PADDLE_H, px:px + PADDLE_W] = 255  # white
        bx, by = g.ball_x, g.ball_y
        # Off-screen slices (by >= HEIGHT) are empty, matching Tk clipping.
        if 0 <= by < HEIGHT and 0 <= bx < WIDTH:
            color = (255, 0, 0) if self.red_on else (255, 255, 255)
            frame[by:by + BALL_SIZE, bx:bx + BALL_SIZE] = color
        if self.t < self.banner_until:
            y0, x0 = HEIGHT // 2 - BANNER_H // 2, WIDTH // 2 - BANNER_W // 2
            frame[y0:y0 + BANNER_H, x0:x0 + BANNER_W] = (255, 0, 0)
        return frame


class VirtualClock(Clock):
    """Clock whose sleeps advance the shared session instead of wall time."""

    def __init__(self, session: SyntheticPongSession):
        self.session = session

    def now(self) -> float:
        return self.session.t

    def sleep(self, seconds: float) -> None:
        self.session.advance_by(seconds)


class SyntheticGameInterface:
    """Duck-typed ``GameInterface``: capture/execute/num_actions, no OS.

    ``execute`` holds the pressed key for ``hold_ms`` of virtual time, then
    releases it for the rest of ``decision_period_ms`` -- the full decision
    period lives here so the env's own post-action delay stays 0 and cadence
    has exactly one owner.
    """

    def __init__(self, session: SyntheticPongSession,
                 decision_period_ms: float, hold_ms: float):
        if decision_period_ms <= 0:
            raise ValueError(f"decision_period_ms must be positive, got {decision_period_ms!r}")
        if hold_ms < 0:
            raise ValueError(f"hold_ms must be >= 0, got {hold_ms!r}")
        if hold_ms > decision_period_ms:
            raise ValueError(
                f"hold_ms ({hold_ms}) cannot exceed decision_period_ms ({decision_period_ms})")
        self.session = session
        self.decision_period_ms = float(decision_period_ms)
        self.hold_ms = float(hold_ms)

    @property
    def num_actions(self) -> int:
        return len(_ACTION_TO_LOGIC)

    def execute(self, action: int) -> str:
        if isinstance(action, bool) or not isinstance(action, int) or not 0 <= action < self.num_actions:
            raise ValueError(f"invalid action {action!r}")
        self.session.held = _ACTION_TO_LOGIC[action]
        self.session.advance_by(self.hold_ms / 1000.0)
        self.session.held = NOOP
        released_ms = self.decision_period_ms - self.hold_ms
        self.session.advance_by(released_ms / 1000.0)
        return ("NOOP", "PRESS_LEFT", "PRESS_RIGHT")[action]

    def capture(self) -> np.ndarray:
        return self.session.render()


def make_synthetic_env_factory(session: SyntheticPongSession, decision_period_ms: float,
                               hold_ms: float, max_episode_steps: int | None = 200,
                               max_episode_seconds: float | None = 120.0,
                               num_stack: int = 4, obs_size: int = 84):
    """Zero-arg env factory sharing one session (one virtual game window)."""
    clock = VirtualClock(session)
    interface = SyntheticGameInterface(session, decision_period_ms, hold_ms)

    def make():
        return ExternalGameEnv(
            interface, ExternPongReward(), ExternPongTermination(), lifecycle=None,
            num_stack=num_stack, size=obs_size,
            post_action_delay_ms=0.0,  # cadence is owned by the interface
            max_episode_steps=max_episode_steps,
            max_episode_seconds=max_episode_seconds,
            clock=clock,
        )

    return make


# -- fixed-policy discrimination probe ------------------------------------
def _oracle_reactive(session: SyntheticPongSession) -> int:
    """Privileged but purely reactive: keep the paddle centred under the ball."""
    g = session.logic
    target = g.ball_x + BALL_SIZE / 2.0
    centre = g.paddle_x + PADDLE_W / 2.0
    if centre < target - 2.0:
        return 2  # PRESS_RIGHT
    if centre > target + 2.0:
        return 1  # PRESS_LEFT
    return 0  # NOOP


def _oracle_lookahead(session: SyntheticPongSession) -> int:
    """Privileged perfect-foresight policy: aim at the ball's intercept point.

    Deep-copies the game state and steps it forward with NOOP until contact
    or miss, then presses toward where the ball will be. This is the
    best-achievable control signal, not a learnable policy -- it bounds what
    the reward can award at this cadence.
    """
    import copy

    g = session.logic
    if g.over:
        return 0
    sim = copy.deepcopy(g)
    for _ in range(400):
        e = sim.step(NOOP)
        if e in ("hit", "miss") or sim.over:
            break
    target = sim.ball_x + BALL_SIZE / 2.0
    centre = g.paddle_x + PADDLE_W / 2.0
    if centre < target - 2.0:
        return 2
    if centre > target + 2.0:
        return 1
    return 0


def _constant(action: int):
    return lambda _session: action


def _random(rng: np.random.Generator):
    return lambda _session: int(rng.integers(0, 3))


def run_policy(env, policy, episodes: int) -> dict:
    """Greedy fixed-policy rollouts through the real env/reward pipeline."""
    rewards, lengths = [], []
    for _ in range(episodes):
        env.reset()
        total, steps = 0.0, 0
        while True:
            action = policy(env.interface.session)
            _obs, reward, terminated, truncated, _ = env.step(action)
            total, steps = total + float(reward), steps + 1
            if terminated or truncated:
                break
        rewards.append(total)
        lengths.append(steps)
    rewards_arr = np.asarray(rewards, dtype=np.float64)
    return {
        "episodes": episodes,
        "mean_reward": float(rewards_arr.mean()),
        "std_reward": float(rewards_arr.std()),
        "mean_length": float(np.mean(lengths)),
        "episode_rewards": [float(r) for r in rewards],
        "episode_lengths": [int(s) for s in lengths],
    }


def run_probe(make_env, episodes: int, seed: int) -> dict:
    """Reward discrimination: known-skill policies through the current config."""
    env = make_env()
    try:
        rng = np.random.default_rng(seed)
        builders = {
            "oracle_lookahead": lambda: _oracle_lookahead,
            "oracle_reactive": lambda: _oracle_reactive,
            "random": lambda: _random(np.random.default_rng(seed)),
            "constant_left": lambda: _constant(1),
            "constant_right": lambda: _constant(2),
            "no_op": lambda: _constant(0),
        }
        return {name: run_policy(env, build(), episodes)
                for name, build in builders.items()}
    finally:
        env.close()


# -- PPO matrix ------------------------------------------------------------
def run_cell(cell: dict, cfg: dict, base_seed: int) -> dict:
    """Probe + PPO train + before/after eval for one cadence cell."""
    import torch

    from agent.model import ActorCritic
    from training.evaluate import evaluate
    from training.external_experiment import summarize_difference, summarize_eval
    from training.ppo import PPOConfig, PPOTrainer, summarize_history

    game_cfg = cfg.get("game", {}) or {}
    model_cfg = dict(cfg.get("model", {}) or {})
    eval_episodes = int((cfg.get("eval", {}) or {}).get("episodes", 10))
    probe_episodes = int((cfg.get("probe", {}) or {}).get("episodes", 6))
    max_steps = (cfg.get("env", {}) or {}).get("max_episode_steps", 200)
    max_seconds = (cfg.get("env", {}) or {}).get("max_episode_seconds", 120.0)

    session = SyntheticPongSession(seed=int(game_cfg.get("seed", 0)),
                                   fps=int(game_cfg.get("fps", 60)))
    make_env = make_synthetic_env_factory(
        session, float(cell["decision_period_ms"]), float(cell["hold_ms"]),
        max_episode_steps=max_steps, max_episode_seconds=max_seconds)

    probe = run_probe(make_env, probe_episodes, base_seed)

    torch.manual_seed(base_seed)
    np.random.seed(base_seed)
    probe_env = make_env()
    num_actions = int(probe_env.action_space.n)
    probe_env.close()

    def fresh_model():
        torch.manual_seed(base_seed)
        return ActorCritic(num_actions=num_actions, **model_cfg)

    baseline = evaluate(fresh_model(), make_env, episodes=eval_episodes)
    ppo_raw = dict(cfg.get("ppo", {}) or {})
    ppo_raw["checkpoint_dir"] = str(
        Path(ppo_raw.get("checkpoint_dir", "checkpoints")) / _safe_name(cell["name"]))
    config = PPOConfig.from_dict(ppo_raw)
    trainer = PPOTrainer(make_env(), fresh_model(), config)
    t0 = time.perf_counter()
    history = trainer.train()
    train_seconds = time.perf_counter() - t0
    final = evaluate(trainer.model, make_env, episodes=eval_episodes)

    untrained_summary = summarize_eval(baseline)
    trained_summary = summarize_eval(final)
    summary = summarize_history(history)
    return {
        "name": cell["name"],
        "decision_period_ms": float(cell["decision_period_ms"]),
        "hold_ms": float(cell["hold_ms"]),
        "released_ms": float(cell["decision_period_ms"]) - float(cell["hold_ms"]),
        "probe": probe,
        "baseline_eval": baseline,
        "final_eval": final,
        "comparison": {
            "untrained": untrained_summary,
            "trained": trained_summary,
            "difference_trained_minus_untrained": summarize_difference(
                untrained_summary, trained_summary),
        },
        "training_updates": summary["updates_run"],
        "training_steps": trainer.num_timesteps,
        "training_seconds": train_seconds,
        "final_train_rolling_mean_reward": summary["mean_reward"],
        "train_terminated_episodes": summary["terminated_episodes"],
        "train_truncated_episodes": summary["truncated_episodes"],
        "history_tail": {k: v[-5:] for k, v in history.items()},
    }


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name))


METRIC_DEFINITIONS = {
    "probe.mean_reward": "mean episode reward of a fixed policy (privileged oracle, seeded random, constants) through the real reward/termination pipeline; discrimination = the spread between policies",
    "episode_reward": "sum of external rewards in one episode: +1 first hit after serve, -1 miss (terminates), 0 otherwise; per-episode ceiling is +1 because the hit latch only clears on serve",
    "decision_period_ms": "virtual milliseconds between agent decisions (hold + released); the measured real default is 147.6 ms",
    "hold_ms": "virtual milliseconds the pressed key is held each decision; the real default is 60 ms",
    "decision_fps": "agent decisions per wall-clock second during the synthetic training run (simulation speed, not game cadence)",
}


def run_matrix(config_path) -> dict:
    config_path = Path(config_path)
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cells = cfg.get("cells") or []
    if not cells:
        raise ValueError("config needs a non-empty 'cells' list")
    seed = int((cfg.get("ppo", {}) or {}).get("seed", 0))
    results = []
    for cell in cells:
        for key in ("name", "decision_period_ms", "hold_ms"):
            if key not in cell:
                raise ValueError(f"cell missing {key!r}: {cell!r}")
        print(f"=== cell {cell['name']}: period={cell['decision_period_ms']} ms "
              f"hold={cell['hold_ms']} ms ===", flush=True)
        results.append(run_cell(cell, cfg, seed))
    report = {
        "config_file": str(config_path),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "kind": "synthetic-cadence-matrix",
        "note": ("Virtual-time simulation of the real external path: real "
                 "PongLogic dynamics, rendered frames through the real "
                 "ExternPongReward/ExternPongTermination/ExternalGameEnv/"
                 "FrameStack pipeline, no window and no SendInput. The "
                 "'current' cell reproduces the measured external cadence "
                 "(147.6 ms decision, 60 ms hold, 60 fps game)."),
        "game": cfg.get("game", {}),
        "model_config": cfg.get("model", {}),
        "ppo_config": cfg.get("ppo", {}),
        "eval_config": cfg.get("eval", {}),
        "metric_definitions": METRIC_DEFINITIONS,
        "cells": results,
    }
    out_path = config_path.with_name(config_path.stem + "_results.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"wrote {out_path}", flush=True)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthetic external-Pong cadence/action-hold control experiment")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    if not Path(args.config).is_file():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        return 2
    try:
        run_matrix(args.config)
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
