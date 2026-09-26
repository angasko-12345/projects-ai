# universal-game-agent — Architecture Memory

## Pipeline (toy path)
```text
toy pixels -> grayscale/84x84/normalize -> 4-stack (4,84,84) float32
 -> CNN(32/64/64) -> Linear-256 -> GRU(128) -> actor logits + scalar value
 -> discrete action -> toy game -> reward -> PPO update
```

## Pipeline (external path)
```text
game window -> MSS capture (native) ->+- raw_frame -> reward/termination providers
                                      +- resize -> (4,84,84) -> CNN -> GRU -> action
-> ActionMapper -> SendInput -> game -> pixels -> PPO update
```

## Module map (file -> responsibility -> key API)
- `environment/toy_pong.py` — pixel-only Pong toy (Gymnasium). `reset(seed)`, `step(a)`.
- `environment/preprocessing.py` — gray/resize/normalize/stack/skip. `preprocess_frame`, `FrameStack`, `PreprocessingWrapper`.
- `agent/model.py` — `ActorCritic`: `forward` (1 step), `forward_sequence` (BPTT chunks), `initial_state`, `save`/`load` (`weights_only`).
- `training/ppo.py` — single-env recurrent PPO. Segmented replay (hidden reset at dones), timeout-aware GAE, clipped objectives + entropy, grad clip. Checkpoints: model+optim+counters+curiosity+env_config.
- `training/curiosity.py` — ICM forward-dynamics, RMS norm, scale 0.1. Disabled = legacy behavior.
- `training/evaluate.py` — separate env/episode, greedy/sampled, fixed seeds, mode save/restore, no-grad. Reports `action_counts` per action index.
- `training/experiment.py` — toy experiments; `make_env_from_config` (toy default, `type: external` delegates).
- `training/external_experiment.py` — real-game 3-phase runs. Unique PID titles/outputs, stale-title preflight, per-phase logs + liveness asserts.
- `training/external_smoke.py` — OS-loop smoke: `drive_loop` (random/fixed/policy, budgets, rewards), `run_bounded` failed/timeout reports.
- `environment/external_game.py` — `ExternalGameEnv` + `make_external_env_from_config` (`allow_live_capture` safeguard). Carries `raw_frame` (native, providers) + resized `frame` (network). `last_breakdown` diagnostics.
- `environment/reward.py` — `RewardProvider`, `NullRewardProvider`, composable Event/TerminalPenalty/Survival/Progress + `CompositeReward` (breakdown + reset), config factories (`red_present`/`red_edge`/`brightness` detectors).
- `environment/termination.py` — `TerminationProvider`, `Natural`/`Never`/`StepLimit`.
- `environment/extern_pong_rewards.py` — screen-only hit/miss detector on NATIVE frames: latched signature transitions, each event pays once (hit->hit/miss->miss/->anything = 0); banner >= miss_min terminal.
- `interface/` — `WindowManager` (ctypes), `Screen/WindowCapture` (mss, full-frame; out-size optional, unset = native passthrough for detection), `ActionMapper` (NOOP/key/**chord**/buttons/move, hold-once + finally release, per-action cooldown), `GameInterface.capture/execute`.
- `games/` — standalone tkinter Pong (pure `pong_logic.py` + app). Red-toggle hit signature, MISS banner, `--title/--seed/--fps/--auto-quit/--geometry`. No state channel. `extern_pong.py` builds native capture + `ResizeObservation(96,96)` on the obs path only.
- `main.py` — thin CLI: smoke-test/train/evaluate/experiment. Overrides, `--resume` (LR + curiosity-scale sync), dual-format checkpoint eval, exit-2 errors.
- `configs/default.yaml` — truthful baseline (env/model/ppo/curiosity/eval/logging).

## Key invariants
- Recurrence: rollout hidden reset on every done; replay segments restart from zeros; detached carry across chunks; truncation bootstraps pre-reset obs+hidden.
- Termination (game ended) vs truncation (budget) never conflated, everywhere.
- Checkpoints backward compatible (`ckpt.get` for newer keys).
- Lazy `__getattr__` package re-exports (avoids `runpy` submodule warning).
