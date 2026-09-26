# Universal-Game-Agent — Engineering Handoff (2026-09-24 ~21:45 UTC+8)

## 1. Status snapshot
- Location: `universal-game-agent/` at repo root of `projects-ai`
  (`https://github.com/angasko-12345/projects-ai/tree/main/universal-game-agent`).
  `small-projects/universal-game-agent/` is an unrelated EMPTY dir — do not touch.
- HEAD: `3adda60`. Suite: **246/246 green** under the project venv
  (`D:\Users\admin\Python\Python314\.venv\Scripts\python.exe`
  → torch 2.14.0+cpu, numpy 2.5.3, gymnasium 1.3.0, pyyaml 6.0.3, mss 10.2.0).
  System `C:\Python314\python.exe` lacks torch; never use it for model work.
- requirements.txt: torch, gymnasium, numpy, pyyaml, mss (stable-baselines3 removed — never imported).
- 19 test files. No test touches a real display, real input, or the network.
- **Uncommitted work (intended next commit):** `training/external_experiment.py` (new),
  `experiments/exp_external_pong_01.yaml` (new), `training/external_smoke.py`
  (policy/timeout/status), `interface/controller.py` (try/finally holds),
  `tests/test_external_smoke.py`, `tests/test_interface.py` (stuck-key regression),
  `environment/external_game.py` (backend close on env close).

## 2. Architecture map (agent sees pixels only; `info` is `{}` on learning paths)
| File | Responsibility | Key API |
|---|---|---|
| `environment/toy_pong.py` | Pixel-only Pong toy (Gymnasium). 64×48 RGB, NOOP/LEFT/RIGHT, +1/−1/0, 500-step truncation, seeded RNG | `reset(seed)`, `step(a)` |
| `environment/preprocessing.py` | BT.601 gray → bilinear 84×84 → float32[0,1] → 4-stack; frame skip w/ max-pool | `preprocess_frame`, `FrameStack`, `PreprocessingWrapper` |
| `agent/model.py` | CNN(32/64/64)→256→GRU(128)→actor logits + scalar value. `weights_only` checkpoints | `forward`, `forward_sequence`, `initial_state`, `save`/`load` |
| `training/ppo.py` | Single-env recurrent PPO. Segmented replay (hidden reset at dones), timeout-aware GAE, clipped objectives + entropy, grad clip. Checkpoints: model+optim+counters+curiosity+env_config | `PPOTrainer.train/collect_rollout/update/save|load_checkpoint` |
| `training/curiosity.py` | ICM forward-dynamics, RMS norm, scale 0.1, clip 5.0, boundary-masked. Disabled = legacy behavior | `CuriosityModule.intrinsic/update` |
| `training/evaluate.py` | Separate env/episode, greedy/sampled, fixed seeds, mode save/restore, no-grad | `evaluate(model, make_env, episodes, seeds)` |
| `training/experiment.py` | Toy baseline→train→final + JSON. `make_env_from_config` (toy default, `type: external` delegates) | `run_external_experiment` analog `run_experiment` |
| `training/external_experiment.py` | Real-game 3-phase run (NEW, uncommitted). Unique PID titles, PID-isolated outputs, stale-title preflight, per-phase game logs + liveness asserts | `run_external_experiment(config)` |
| `environment/reward.py` etc. | `RewardProvider`/`NullRewardProvider`; composable Event/TerminalPenalty/Survival/Progress + `CompositeReward` (breakdown + reset); config `make_reward_from_config` (red_present/red_edge/brightness); `Natural/Never/StepLimit` termination; screen-only `ExternPongReward/Termination` | `reward()`, `breakdown()`, factories |
| `interface/` | `WindowManager` (ctypes), `Screen/WindowCapture` (mss, full-frame), `ActionMapper` (data tables, hold-once + finally release), `GameInterface.capture/execute` | see files |
| `games/` | Standalone tkinter Pong (`pong_logic.py` pure sim + `extern_pong.py` app). Red-toggle hit signature, MISS banner, arrows/AD+P/R/Q, --title/--seed/--fps/--auto-quit. No state channel | `python games/extern_pong.py` |
| `main.py` | Thin CLI: smoke-test/train/evaluate/experiment. Overrides, --resume (LR+curiosity-scale sync), dual-format checkpoint eval, exit-2 errors | `python main.py <cmd> --help` |
| `configs/default.yaml` | Truthful baseline (env/model/ppo/curiosity/eval/logging); loaders warn on unknown keys; seeds validated ≥0 | `load_config`, `from_dict` |

## 3. Validated results (with caveats)
- Toy seed-0 30k steps: eval mean −0.40 → −0.10; miss rate 40% → 10% (20 fixed-seed greedy eps).
- Toy A/B seed-1 30k: no-curiosity −0.45 → −0.35 (9/20 miss); curiosity −0.45 → −0.05 (1/20). One run/arm — suggestive only.
- Calibration (live): latched ball 19–36 red px; MISS banner 619 red px vs thresholds 8/200/300. Real 30-step OS smoke OK (~7 FPS); 300-step input soak OK; idle game stable 90 s+.
- **External experiment NEVER completed.** bg_1 died at 218 s ("window gone"); two relaunch attempts killed deliberately (duplicate chaos, then user needed the desktop). No external results JSON exists. Next: single guarded run (~6 min of free desktop).

## 4. Incident log + fixes (all in tree or noted)
- **Stuck-key (user-reported, resolved NOT-a-bug):** keys verified toggling live via GetAsyncKeyState sampling (True/False/False/True…); constant-RIGHT was untrained-greedy argmax + focus effects during duplicate runs. Hold path still hardened with try/finally + test as insurance.
- **Duplicate background execution (confirmed systematic):** pairs share identical creation timestamps again and again. Mitigated via PID-unique titles/outputs + stale-title preflight; still, verify singularity by enumerating titled windows mapped to owner PIDs (process count alone misled once). Full experiment still uncompleted; compare-run salvaged the baseline-vs-trained question instead.
- **Kill hygiene:** `taskkill /PID x /F` works; PowerShell `kill` without `-Force` silently fails; `wmic` absent (use Get-CimInstance). Always follow kills with a SendInput key_up sweep (arrows/WASD/space/modifiers/mouse).
- Uncommitted files listed in §1 are the complete pending set; commit `universal-game-agent/` only.

## 5. Conventions that must survive
- Lazy `__getattr__` package re-exports (avoids `runpy` warning); re-ground edit anchors every call — stale anchors scrambled `ppo.py`, `experiment.py`, `main.py`, tests repeatedly this session (always caught by syntax check + targeted tests).
- Tests: fakes only (Recording/Synthetic/FakeClock), stdlib unittest, torch imports guarded, `TemporaryDirectory` for artifacts (suite leaves only `.gitkeep`), close `FileHandler`s on Windows.
- Checkpoints: `weights_only=True`; no schema version (accepted gap).
- Config philosophy: unknown keys warn (never silently drop), invalid values raise, CLI maps to exit 2.
- Do not touch: `small-projects/`, `.agents/` peers' files, `tasks/`, root strays; torch/CPU budgets stay modest; no SB3, no LSTM swap, no CV beyond color-edge detectors.
