# universal-game-agent — Project Memory

> Canonical facts for Oh-My-Pi sessions working on `universal-game-agent/`
> (repo root of `projects-ai`). Populate only verifiable facts.

## Identity
- Pixel-based RL testbed at `universal-game-agent/`, repo `projects-ai`, branch `main`.
- Live: https://github.com/angasko-12345/projects-ai/tree/main/universal-game-agent
- NOTE: `small-projects/universal-game-agent/` is an unrelated EMPTY dir — leave alone.
## Current state (2026-09-26)
- Full suite: **266/266 passing** under the project venv.
- Commits through `b77bf4d` (extern-Pong reward/termination fix: native-frame
  detection + edge-triggered once-only rewards; live-verified at 96 and 320).
- Toy Pong: fully validated (see results below). External Pong: reward/termination
  now correct at the real RL config; no completed full experiment yet.

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
  no signal. Suspect: serve x = center +/-40 vs 48px paddle (~half serves DOA) or
  reset/banner timing; NOT yet proven. 30k attempt died at step 6144 on window loss
  (flaky env, empty game log). Results: experiments/exp_external_pong_02-15516_results.json.

## Open threads
- NEXT: diagnose why external episodes terminate in ~3 steps with ~0 events (see roadmap).
- Uncommitted: exp02 code (eval term/trunc, comparison report, exp_02 yaml) + these
  memory files; commit `universal-game-agent/` + `.agents/memory/oh-my-pi/` only.
- `checkpoints/extern_pong_01/ppo_final.pt` (12 MB) deliberately uncommitted; nested path dodges `checkpoints/*.pt` ignore.
