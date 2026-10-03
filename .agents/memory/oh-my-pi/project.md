# universal-game-agent — Project Memory

> Canonical facts for Oh-My-Pi sessions working on `universal-game-agent/`
> (repo root of `projects-ai`). Populate only verifiable facts.

## Identity
- Pixel-based RL testbed at `universal-game-agent/`, repo `projects-ai`, branch `main`.
- Live: https://github.com/angasko-12345/projects-ai/tree/main/universal-game-agent
- NOTE: `small-projects/universal-game-agent/` is an unrelated EMPTY dir — leave alone.
## Current state (2026-09-26)
- Full suite: **278/278 passing** under the project venv.
- Commits through `f751d73` (Step 4: window-loss-tolerant phase 2 + ckpts every
  25 updates). All Step-2/Step-4 code committed; only memory files uncommitted.
- Toy Pong: fully validated (see results below). External Pong: reward/termination
  correct, phantom resets fixed, comparison + relaunch pipeline proven. No re-run
  since the fixes — exp02's no-learning verdict is SUSPENDED (was confounded).

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
  to always-PRESS_LEFT. VERDICT SUSPENDED 2026-09-26: ~5/6 episodes were phantom
  resets (see CAUSE below), so this run measured mostly reset timing, not skill.
  Results: experiments/exp_external_pong_02-15516_results.json (pre-fix artifact).
- CAUSE FOUND (Step-1 trace, 6 NOOP episodes): `reset()` captures whatever is on
  screen with no game sync; the MISS banner persists 1.0s, so post-miss resets land
  mid-banner -> 1-step phantom episodes (prev=607 cur=607, reward 0, terminated).
  Live rally (ep 0) missed honestly at step 9 with -1. So ~5/6 traced episodes were
  phantoms: mean length 2.47 and 0.17 misses/ep explained.
- FIX APPLIED + COMMITTED (Step 2, `6890c42`): `ExternalGameEnv.reset_settle_timeout_s`
  (default 5.0s, None/<=0 disables); reset captures until the termination
  provider's per-frame `terminated` is false. Providers without that signal
  (step limits) settle at once. 5 settle tests. Live re-trace: PHANTOM RESETS
  0/6 (was ~5/6); all episodes start live, misses pay -1 honestly.
  CAVEAT: verify window showed a constant 59-red baseline (likely ClearType
  title-bar fringing) — inside the 8–200 hit band, so ball flashes may not
  create a pay edge there. MUST check baseline reds in the real exp window
  before the next training run (Step-3 pre-flight).
- STEP 4 DONE + COMMITTED (`f751d73`): phase-2 window-loss tolerance —
  `train_with_window_relaunch` (max 3 relaunches): on WindowLost/WindowNotFound/
  CaptureError the game is relaunched and training resumes from in-memory state
  (`ppo_interrupted.pt`) on a fresh env; histories concatenated;
  `window_relaunches` in report. Periodic ckpts 1000 -> 25 updates in exp02 yaml.
  5 relaunch tests; suite 278 OK.

## Open threads
- NEXT (Step 3 pre-flight): check live-play baseline reds in the exp window,
  then re-run exp02. Uncommitted: memory files only.
- `checkpoints/extern_pong_01/ppo_final.pt` (12 MB) deliberately uncommitted; nested path dodges `checkpoints/*.pt` ignore.

## Current state (2026-10-02, superseding the 2026-10-01 entry)

- Full suite: **317 tests, 1 skip, OK** (`cd universal-game-agent`, `python -m unittest
  discover -s tests`). The 261 → 288 → 295 → 317 progression is history; 288 was the 2cb2413
  count, 295 the pre-fix 2026-10-02 baseline. No PPO-math changes; the old entropy/GAE/GRU/curiosity audit claims were
  re-verified 2026-10-02 and are **DISPROVEN** — do not revive them
  (`master-bug-synthesis.md` §0).
- **Open UGA bugs (2026-10-02, after root fixes):** only the UGA share of ROOT-034
  (test-quality cluster). ROOT-014, ROOT-027, and ROOT-036 are FIXED (commits
  `b46a3e0`, `2a7b25a`). ROOT-011 and ROOT-026 are CONTRACT GAP / ACCEPTED DEBT.
- The rest of the 2026-10-01 P5/P6/P7 record below remains accurate and stays as history.

### Historical entry: state as of 2026-10-01, commit 2cb2413, omp session P5–P10

- Full suite: **288/288 passing** (at that time; `cd universal-game-agent`, `python -m unittest
  discover -s tests`, ~15 s). Baseline moved 261 → 288 via this session's
  11 regression tests (4 PPO checkpoint, 6 launch/lifecycle, 1 external
  seed-no-op) plus parallel-session additions. No PPO-math changes; old
  entropy/GAE/GRU/curiosity audit claims left as-is, green.
- P5 checkpoints (`training/ppo.py`): `checkpoint_every_updates` validated —
  non-negative int, 0 disables periodic (only `ppo_final.pt` written).
  `save_checkpoint` atomic via same-dir temp + `os.replace`, temp cleaned on
  failure. Note: `__post_init__` now also rejects `bool` for the interval and
  seed (bool is an int subclass; `True` previously passed as 1).
- P6 lifecycle (`training/external_experiment.py`, `training/evaluate.py`):
  `launch_phase2_process()` — proc ownership reaches the caller only on
  success, stopped locally on attach/liveness failure. `load_eval_model()`
  closes the throwaway checkpoint-load env (phase 3 used to leak it).
  `stop()` idempotent via `_stopped` flag. `evaluate()` closes each
  per-episode env in `finally` (a mid-episode `step()` raise used to leak it).
- P7 contracts (doc-only + 1 test): external `reset(seed=)` accepts but
  ignores seed — game seeded once at `launch_game(..., seed, ...)`; episodes
  are session continuations. `hits`/`misses` = reward-sign counts, meaningful
  only for sign-based providers (toy ±1, extern_pong ±1); null → always 0,
  composite/survival inflates hits. Settle logic untouched (already correct,
  5+ tests). No results invalidated.
- exp02 verdict still SUSPENDED (Step-3 pre-flight + re-run still open).
- `small-projects/universal-game-agent/` confirmed still EMPTY 2026-10-01 —
  leave alone. Repo is now tracked as THREE products; see canonical memory.
- Interpreter: this box runs `D:/admin/code/projects` venv python 3.14.7
  (torch CPU). If `import torch` dies with OpenBLAS allocation errors + exit
  45 and zero test output: prefix
  `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1`. Environmental, always retry
  with it before suspecting code.

## Open threads (2026-10-01)

- STEP 3 pre-flight (live-play baseline reds) + exp02 re-run — unchanged, still next.
- mini-llm TinyStories baseline recorded (`small-projects/mini-llm/docs/EXPERIMENT-tinystories.md`):
  3.1M-token prep from `tinystories-small.txt`, vocab 8192, step-10000 ckpt
  (train 1.785/val 1.833), coherent samples. Wart: ckpt stores default
  `tokenizer_path` — always pass `--tokenizer` explicitly.
- `data/tokenizer.json` working-tree deletion predates 2026-10-01 and breaks
  3 `TestGenerationSeed` tests; deliberately NOT restored (user's change).

## Current state (2026-10-03, superseding the 2026-10-02 entry)

- Full suite: **318 tests, 1 skip, OK** (`cd universal-game-agent`, `python -m unittest
  discover -s tests`). Progression: 261 → 288 → 295 → 317 → 318; the +1 is the new
  `tests/test_cwd_isolation.py`. Direct execution of any `tests/test_*.py` from any
  working directory now runs the identical tests (verified per-module against
  discovery; scratch-CWD sweep left no files).
- Test-infra convention (from `59f5a1b`): every UGA test module starts with the
  `try: from . import _bootstrap / except ImportError: import _bootstrap` prelude and
  keeps `unittest.main()` at EOF. Do both when adding a test file.
- ROOT-034 UGA share: **DBG-06 and DBG-07 closed 2026-10-03 (`59f5a1b`)** — direct
  execution works from any cwd; the 4 checkpoint-writing tests use temp dirs
  (guard: `tests/test_cwd_isolation.py`). Remaining ROOT-034 items untouched; the
  ledger is reconciled separately. AGENTS.md baseline sentence updated to 318/2026-10-03.
- Pre-existing, reported not regenerated: gitignored working-tree
  `checkpoints/ppo_final.pt` (1.49 MB, mtime 2026-10-03 14:45) was clobbered by the
  pre-fix audit runs; `checkpoints/ppo_untrained.pt` untouched.
- Unchanged: exp02 verdict still SUSPENDED (Step-3 pre-flight + re-run still next).
