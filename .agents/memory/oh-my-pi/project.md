# universal-game-agent — Project Memory

> Canonical facts for Oh-My-Pi sessions working on `universal-game-agent/`
> (repo root of `projects-ai`). Populate only verifiable facts.

## Identity
- Pixel-based RL testbed at `universal-game-agent/`, repo `projects-ai`, branch `main`.
- Live: https://github.com/angasko-12345/projects-ai/tree/main/universal-game-agent
- NOTE: `small-projects/universal-game-agent/` is an unrelated EMPTY dir — leave alone.
## Current state (2026-09-26)
- Full suite: **268/268 passing** under the project venv.
- Commits through `d15c02b` (exp02: term/trunc reporting, comparison block,
  exp_02 yaml + results JSON; verdict no-learning, 3-step deaths unexplained).
- Toy Pong: fully validated (see results below). External Pong: reward/termination
  correct at the real RL config; full 3-phase comparison pipeline proven (exp02).

## Environment (non-negotiable)
- Interpreter for ALL runs: `D:\Users\admin\Python\Python314\.venv\Scripts\python.exe`
  (torch 2.14.0+cpu, numpy 2.5.3, gymnasium 1.3.0, pyyaml 6.0.3, mss 10.2.0).
- System `C:\Python314\python.exe` lacks torch — model/training work fails there.
- requirements.txt: torch, gymnasium, numpy, pyyaml, mss. No stable-baselines3 (removed; custom PPO).

## Information boundary (project law)
- Agent input: rendered pixels only. Agent output: abstract discrete action indices.
- `info` dicts are `{}` on learning paths. No RAM/APIs/coordinates/scores in learning code.

## Validated results (with caveats)
- Toy seed-0, 30k steps: eval mean −0.40 → −0.10; miss rate 40% → 10% (20 fixed-seed greedy eps).
- Toy A/B seed-1, 30k: plain PPO −0.45 → −0.35 (9/20 miss); +curiosity −0.45 → −0.05 (1/20). Single run/arm — suggestive only.
- Live calibration: latched ball 19–36 red px; MISS banner 619 red px (thresholds 8/200/300).
- 2026-09-26 reward fix (b77bf4d): banner is 607 native px but only 61 px after
  96x96 downscale (inside hit band) — hence native-frame detection. Latch pays each
  event once; held banner frames +0.0 with termination true. Diag320: 11/11 misses
  terminal, single -1. Diag96: 6/6 misses terminal, single -1 (acceptance met).
  Note: no live ball-hit caught in 600 captures (flashes brief); +1 covered by
  synthetic 320x240 test only. One normal frame hit 207 red px — outside band, no
  event either way, but the loud-normal vs hit-band margin is thin.
- exp02 validation run DONE (4096 steps, 100+100 fixed-seed evals, exit 0, 605 s):
  untrained -0.15 vs trained -0.11 (d+0.04); length 2.47 -> 3.81; policy collapsed
  to always-PRESS_LEFT; all 200 eval episodes terminated, 0 truncated. Verdict:
  no learning — episodes die in ~3 steps with ~0.17 misses/ep, so PPO sees almost
  no signal. Results: experiments/exp_external_pong_02-15516_results.json.
- CAUSE FOUND (Step-1 trace, 6 NOOP episodes): `reset()` captures whatever is on
  screen with no game sync; the MISS banner persists 1.0s, so post-miss resets land
  mid-banner -> 1-step phantom episodes (prev=607 cur=607, reward 0, terminated).
  Live rally (ep 0) missed honestly at step 9 with -1. So ~5/6 traced episodes were
  phantoms: mean length 2.47 and 0.17 misses/ep explained.
- FIX APPLIED (Step 2, uncommitted): `ExternalGameEnv.reset_settle_timeout_s`
  (default 5.0s, None/<=0 disables); reset captures until the termination
  provider's per-frame `terminated` is false. Providers without that signal
  (step limits) settle at once. Suite 273 OK (5 new settle tests).
  Live re-trace: PHANTOM RESETS 0/6 (was ~5/6); all episodes start live,
  misses pay -1 honestly. CAVEAT: verify window showed a constant 59-red
  baseline (likely ClearType title-bar fringing) — inside the 8-200 hit band,
  so ball flashes may not create a pay edge there. MUST check baseline reds
  in the real exp window before the next training run.
- STEP 4 DONE (uncommitted): phase-2 window-loss tolerance — `train_with_window_relaunch`
  (max 3 relaunches): on WindowLost/WindowNotFound/CaptureError the game is
  relaunched and training resumes from in-memory state (`ppo_interrupted.pt`) on a
  fresh env; histories concatenated; `window_relaunches` in report. Periodic ckpts
  1000 -> 25 updates in exp02 yaml. 5 new tests; suite 278 OK.

## Open threads
- NEXT (Step 3 pre-flight): check live-play baseline reds in the exp window
  (59-red chrome caveat above); then re-run exp02. Uncommitted: Step-2 code
  (external_game settle + factory + exp02 yaml + 5 tests) + these memory files.
- `checkpoints/extern_pong_01/ppo_final.pt` (12 MB) deliberately uncommitted; nested path dodges `checkpoints/*.pt` ignore.
