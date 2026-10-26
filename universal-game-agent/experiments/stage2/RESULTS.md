# Stage 2 results: multi-game Atari benchmark readiness

Branch `upgrade/uga-stage2-benchmark` (draft PR #41, stacked: merge **#31 -> #34 -> #41**).
Plan locked before coding: `PLAN.md`. Everything below was measured in the Hark sandbox on
2026-10-26 (Asia/Manila): Linux aarch64 (Neoverse-V2), **2 vCPU, 8 GB**, Python 3.12.15,
torch 2.14.1+cpu, numpy 2.5.3, ale-py 0.12.1. The sandbox is **shared**: other tenants' jobs
pushed the load average to 2-7 for much of the session, and the sandbox rebooted twice during
the extended Pong run. Every speed here is therefore a noisy lower bound for this hardware;
before/after comparisons were interleaved in one process to cancel as much of that as
possible. No window, no SendInput, no GPU, no Kaggle sign-in.

## What ran

| Run | Command | Result |
|---|---|---|
| Profile, legacy, before any change (load ~0.4 at start) | `python experiments/stage2/profile_ppo.py --updates 2` | `profile.json` (`legacy-anchor-8env-before-idle`) |
| Profile, legacy vs fast (same session, after) | `profile_ppo.py` (legacy) and `profile_ppo.py --config configs/stage2_pong_cleanrl.yaml --engine fast` | `profile.json` |
| Throughput before/after, 3 interleaved rounds | `python experiments/stage2/throughput.py --rounds 3 --updates 2` | `throughput.json` |
| Same, 1 torch thread (fast CleanRL arm only) | `throughput.py --threads 1 --arms fast_cleanrl` | `throughput.json` -> `extra_fast_cleanrl_torch_threads_1` |
| (a) Pong 150,528 steps, CleanRL-faithful + fast, seed 1 | `main.py train` on a copy of `configs/stage2_pong_cleanrl.yaml` (total 150,528, LR annealed over it, 3 resumed chunks of <= 51,200 steps) | `pong_cleanrl_150k.json` |
| (b) Pong extended, seed 1 | same config, total 1,228,800 (annealed over it), chunks of 102,400 | `pong_cleanrl_ext.json`: reached 1,228,800, greedy +0.8, sampled -2.1 (section 5) |
| (c) Multi-game smoke | `python -m training.stage2 {random,run,aggregate}` with `--scale 0.0005 --eval-episodes 2 --eval-max-frames 4000` | `smoke/` |
| Kaggle wrapper, local dry run (CPU) | `python experiments/stage2/kaggle/uga_stage2_kaggle.py --workdir /tmp/kag --throughput --throughput-scale 0.0003` | cloned the pushed branch, installed, trained, wrote `throughput.json` (136 steps/s on CPU) |
| Regression suite, ale-py + Pillow installed | `python -m unittest discover -s tests` | 603 tests, OK, 3 skipped |
| Regression suite, ale_py and PIL unimportable (CI condition) | same, via an import blocker | 603 tests, OK, 27 skipped (3 old + 16 Stage 1 ALE + 8 Stage 2 ALE) |
| Evidence | `python tools/evidence/generate.py universal-game-agent`, `python tools/evidence/check.py` | pass (clean clone) |
| Lint | `ruff check .` (repo root) | All checks passed |

## 1. Profile: where the PPO step time goes (`profile.json`)

8 envs x 128 steps, configs/atari_pong.yaml model/ppo blocks, 2 torch threads, 2 updates after
one warm-up update.

| Share of wall time | legacy, before (idle) | legacy, after-session | fast CleanRL (ff, uint8) |
|---|---|---|---|
| steps/s | **132.6** | 137.2 | **163.8** |
| env.step (ALE + UGA preprocessing) | 2.47 s (16%) | 2.34 s | 1.52 s |
| rollout forward (batched over 8 slots) | 0.95 s (6%) | 1.28 s | 2.11 s* |
| rollout conversion / bookkeeping | 0.56 s (4%) | 0.43 s | 0.31 s |
| update: replay forward (`forward_sequence`) | 2.85 s (19%) | 2.59 s | (inside "other") |
| update: `loss.backward` | 7.86 s (51%) | 7.57 s | 6.05 s |
| update: other (GAE, indexing, loss math, ff encode) | 0.64 s | 0.59 s | 2.33 s |

\* An isolated microbenchmark of the same batch-8 forward runs in 2.4-2.9 ms (uint8 or float,
`no_grad` or `inference_mode`); the 8 ms per call inside the loop is OpenMP worker threads and
the single-threaded env stepping competing for 2 shared vCPUs, not the forward itself.

Findings:
* The legacy engine was **not** dominated by Python overhead. 70% of its time is the CNN
  forward+backward of the update (4 epochs x 1,024 samples), and the per-slot GRU segment loop
  adds comparatively little on this CPU.
* **Compute floor (measured):** Nature-CNN + 512 + head fwd+bwd+Adam on one 256-sample
  minibatch = 0.218 s with 2 threads, so CleanRL's 16 minibatches per 1,024-step update cost
  ~3.5 s, i.e. **<= ~293 steps/s even with a free environment**. Any CleanRL-faithful engine on
  this CPU is bounded there.
* UGA preprocessing (float64 gray + bilinear) cost ~0.97 ms per decision vs ~0.37 ms for the
  4 ALE frames. The opt-in float32/uint8 path (`env.obs_dtype: uint8`) costs ~0.21 ms.

## 2. Throughput before/after (`throughput.json`)

Interleaved rounds (legacy, fast_gru, fast_cleanrl) x 3, 2 timed updates each, 2 torch threads,
load average 1.8-2.8 during the measurement.

| Arm | What | steps/s (3 rounds) | median | vs legacy |
|---|---|---|---|---|
| `legacy` | Stage 1 anchor exactly as it ran (legacy engine, GRU, float32 obs, 18-pad) | 123.7 / 134.5 / 139.2 | **134.5** | 1.00x |
| `fast_gru` | same model + hyperparameters on the fast engine, uint8 obs | 146.6 / 208.5 / 106.4 | 146.6 | 1.09x (noisy) |
| `fast_cleanrl` | `configs/stage2_pong_cleanrl.yaml` (preset, ff model, native actions) | 200.3 / 208.5 / 210.3 | **208.5** | **1.55x** |
| `fast_cleanrl`, 1 thread | | 129.9 / 129.9 | 129.9 | (2 threads is better here) |

Env-only, 8 Pong slots, random actions: **899.9 decisions/s** (float32, legacy pixels) vs
**1,615.4** (uint8 fast path) -> 1.8x. Stage 1 recorded 68-74 PPO steps/s under load ~3; against
that figure the fast CleanRL arm is ~2.8-3.1x, but that comparison mixes machine load with
code, so it is not the claim. **The >= 3x target was not met on this CPU** (1.55x in the same
session): the fast CleanRL arm reaches ~71% of the measured compute floor, and the remaining
cost is the CNN math itself, which only a GPU (or a smaller network / fewer epochs, i.e. a
different algorithm) removes. bf16 autocast on this CPU was tried as a quick microbenchmark
and was far slower than fp32 (did not finish 5 iterations in 2 min); not pursued.

What the fast engine changes that matters on a **GPU** (not measured here): the update leaves
the CPU entirely, so a Kaggle process becomes env-bound; the uint8 path doubles env
throughput, rollout storage is uint8 on the device, and there is one host->device copy per step.

## 3. CleanRL-faithful mode

`ppo.preset: cleanrl` = cleanrl/ppo_atari.py `Args` (read from source,
https://github.com/vwxyzjn/cleanrl/blob/master/cleanrl/ppo_atari.py and
https://docs.cleanrl.dev/rl-algorithms/ppo/): lr 2.5e-4 linearly annealed (CleanRL's
`frac = 1 - (iteration-1)/num_iterations`, tested), Adam eps 1e-5, 4 minibatches (shuffled
flat samples for the frame-stack model; whole slots for a recurrent one, `ppo_atari_lstm`
scheme), 4 epochs, clip 0.1 + value clip, ent 0.01, vf 0.5, gamma 0.99, lambda 0.95,
max-grad 0.5, per-minibatch advantage normalisation, orthogonal init, Nature CNN -> 512 -> heads,
obs/255 in-model, EpisodicLife + FireReset, sign clip, minimal action set, sticky 0.
`configs/stage2_pong_cleanrl.yaml` lists the remaining deviations: bilinear-then-gray (CleanRL:
area resize, then gray), EpisodicLife checked per emulator frame, GAE cut and bootstrapped at
truncations (CleanRL treats truncation as a done), approx_kl/clipfrac averaged over minibatches.

EpisodicLife / FireReset use the ALE's own lives counter and game-over flag **inside
`AtariGame`**; only the `terminated` flag the trainer sees changes; lives never enter the
observation or `info` (tests: `tests/test_stage2_atari.py`). Evaluation always plays full games
with both off and raw score.

## 4. Pong (a): 150,528 steps, CleanRL-faithful + fast vs Stage 1 (`pong_cleanrl_150k.json`)

Seed 1, same budget as Stage 1. Training curve = rolling mean of the last 100 **full-game raw
Pong scores** (`mean_game_score` telemetry). The window restarts empty at each resume
(51,200 and 102,400 steps). Entropy max = ln 6 = 1.792.

| Steps | 10k | 20k | 41k | 51k | 61k | 82k | 102k | 123k | 143k | 150.5k |
|---|---|---|---|---|---|---|---|---|---|---|
| Game score (rolling) | -20.25 | -20.00 | -20.07 | -20.20 | -20.38 | -20.59 | -20.54 | -20.55 | -20.47 | -20.44 |
| Entropy | 1.760 | 1.737 | 1.754 | 1.757 | 1.717 | 1.719 | 1.689 | 1.720 | 1.716 | 1.727 |
| Explained variance | 0.00 | 0.01 | 0.11 | 0.24 | 0.50 | 0.43 | 0.68 | 0.78 | 0.66 | 0.86 |

Evaluation on Stage 1's episode seeds 10000..10009, full games, raw score:

| Policy | Stage 2 (CleanRL-faithful, 150k) | Stage 1 (legacy anchor, 150k) |
|---|---|---|
| PPO greedy | -21.0 +/- 0.0 (always LEFTFIRE) | -21.0 +/- 0.0 (always pad 17 -> LEFTFIRE) |
| PPO sampled | -20.2 +/- 0.87 | -20.7 +/- 0.64 |
| Uniform random (native 6 / pad 18) | -19.6 +/- 0.49 | -20.4 +/- 0.66 |
| Constant NOOP | -21.0 +/- 0.0 | -21.0 +/- 0.0 |

**Verdict: still flat at 150k, and identically so.** With every listed Stage 1 deviation
removed (no GRU, minimal action set, LR annealing, Adam eps, shuffled minibatches, EpisodicLife,
FireReset), the run looks the same: return at random level, entropy barely below maximum
(1.73 of 1.79), greedy argmax of a near-uniform policy picking LEFTFIRE. The value function
does learn (explained variance 0 -> 0.86), so gradients flow. This removes the Stage 1
deviations as the explanation for the flat run: 150k steps (1.5% of CleanRL's 10M) is simply
too early. (CleanRL's published curve is not re-plotted here; the comparison point that
matters is the 10M anchor below.) The greedy-LEFTFIRE collapse is a property of argmax over an
almost uniform policy, not of the 18-action pad.

## 5. Pong (b): extended run

Seed 1, same config as (a) but `total_timesteps` = `anneal_total_timesteps` = **1,228,800**
(8.2x Stage 1's budget, 12% of CleanRL's 10M). **The full budget was reached**, so the LR
schedule ran to its end (2.5e-4 -> 2.1e-7); nothing was truncated. 14 chunk processes of
<= 102,400 steps (two cut short by reboots; 12 distinct resume points), 1,200 updates, 1,200 telemetry rows (contiguous, no
duplicates after the two reboot trims). Wall clock **2 h 11 min** (10:47:43 -> 12:58:29 UTC),
including two sandbox reboots (re-ran 51,200 -> 61,440 and 563,200 -> 576,512) and per-chunk
start-up; chunk speeds 128-208 steps/s, ~157 steps/s averaged over the wall clock.
Evaluation of `ppo_1228800.pt` took ~2 min.

Training curve per 102,400-step bin. "Bin mean" = mean raw score of the full games that
**finished inside the bin** (weighted by games per update; unaffected by the window resets);
"rolling" = `mean_game_score` at the bin's last update, whose 100-game window restarts at each
resume, so late bins hold only ~20 games. Entropy / EV / KL = means over the bin's updates.

| Steps | 0-102k | -205k | -307k | -410k | -512k | -614k | -717k | -819k | -922k | -1024k | -1126k | -1229k |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Games finished | 106 | 103 | 108 | 105 | 77 | 56 | 48 | 47 | 45 | 48 | 47 | 52 |
| Bin mean game score | -20.42 | -20.33 | -20.14 | -20.11 | -18.43 | -16.30 | -12.40 | -11.40 | -9.76 | -9.81 | -8.49 | **-5.21** |
| Rolling (bin end) | -20.54 | -20.34 | -20.36 | -19.98 | -17.32 | -15.74 | -10.48 | -12.73 | -8.15 | -10.60 | -8.77 | -4.25 |
| Entropy | 1.728 | 1.761 | 1.742 | 1.725 | 1.563 | 1.414 | 1.314 | 1.229 | 1.211 | 1.159 | 1.136 | 1.141 |
| Explained variance | 0.31 | 0.62 | 0.69 | 0.69 | 0.56 | 0.52 | 0.63 | 0.74 | 0.80 | 0.85 | 0.87 | 0.90 |
| approx_kl | 0.0009 | 0.0008 | 0.0007 | 0.0012 | 0.0016 | 0.0017 | 0.0016 | 0.0016 | 0.0016 | 0.0013 | 0.0010 | 0.0003 |

KL and clipfrac (~0.08 at 500k-900k, 0.012 in the last bin) fall at the end because the LR is
annealing to zero. Fewer games per bin = longer games: the agent returns more balls.

Evaluation, same episode seeds 10000..10009 as (a), full games, raw score
(`pong_cleanrl_ext.json`):

| Policy | 1.23M (this run) | 150k (run a) |
|---|---|---|
| PPO greedy | **+0.8 +/- 17.8** (20, -21, -21, -20, 10, -20, 20, 10, 20, 10) | -21.0 +/- 0.0 |
| PPO sampled | **-2.1 +/- 15.9** (12, -20, -20, -21, 8, -20, 20, 7, 16, -3) | -20.2 +/- 0.87 |
| Uniform random (native 6) | -19.6 +/- 0.49 | -19.6 +/- 0.49 |
| Constant NOOP | -21.0 +/- 0.0 | -21.0 +/- 0.0 |

Greedy now uses five of the six actions (FIRE 9,237, LEFT 4,152, LEFTFIRE 2,435, RIGHTFIRE
1,707, NOOP 585; never RIGHT) instead of always LEFTFIRE; mean game length 1,812 decisions vs
1,014 for random.

**Verdict: the CleanRL-faithful run learns Pong; it is not solved.** The curve is flat at
random level for the first ~400k steps (entropy ~1.73 of 1.79), then rises steadily from
~450k: bin mean -20.1 -> -5.2, entropy 1.73 -> 1.14, explained variance 0.3 -> 0.9. Both
evaluated policies beat random and NOOP by ~18-22 points. The evaluation is **bimodal**, not a
uniform -2: greedy wins 3 games by 20 points and three more by 10, but 4 of 10 games are still
lost at -20/-21 (same pattern under sampling), so the +/- 16-18 std is real spread over 10
episodes, not noise around a level. This is one seed, 10 eval episodes, at 12% of CleanRL's
budget; it is evidence that the Stage 2 pipeline (fast engine + CleanRL preset + EpisodicLife /
FireReset) can learn Pong, and it is consistent with the 150k verdict that the earlier flat
runs were "too early" rather than broken. It says nothing about reaching CleanRL's
20.36 +/- 0.20 at 10M; that is still the 3-seed 10M anchor (commands below).

Resume check: across the 12 resume points, the mean score of the 25 updates after a resume vs the
25 before differs by -3.5 .. +3.4 with no consistent sign (after-resume games are biased
short because all 8 slots restart), so the chunking did not visibly disturb learning.

## 6. Multi-game smoke (`smoke/`)

`benchmark.yaml` with `--scale 0.0005` (every budget x 1/2000), eval 2 episodes per game capped
at 4,000 frames, seed 0, CPU. Purpose: prove the pipeline, not learning.

| Step | Result |
|---|---|
| `random` baseline, 8 games | written to `random.json`; e.g. space_invaders 265.0, demon_attack 170.0, freeway 0.0 |
| `multi` (10 slots = 2 x 5 train games), `--max-chunks 2` | paused after 2,560 steps with exit code 3, as designed |
| same `multi` command again | resumed at 2,560, finished 5,120 steps (1 resume), 164.8 steps/s, evaluated all 8 games |
| `finetune --game freeway` | loaded multi's weights (fresh optimizer), 1,280 steps, 178.4 steps/s |
| `scratch --game freeway` | 1,280 steps, 176.7 steps/s |
| `aggregate` | `summary.json` / `summary.csv`: per (arm, game) raw score + HNS, mean/median HNS per arm and split, finetune-minus-scratch transfer per held-out game |

HNS after a few thousand steps is ~0 everywhere (e.g. multi/train mean HNS 0.020, zeroshot/heldout
-0.016, random/heldout 0.001), which is the expected value; per-game return normalisation ran on
all 10 slots with 5 separate statistics.

## 7. Benchmark definition and hours (`benchmark.yaml`, `kaggle/`)

Train: pong, breakout, space_invaders (Stage 1), seaquest (full 18-action set), qbert (4-way
jumps). Held-out: demon_attack (near), freeway (vertical control, now trained via
seaquest/qbert), asterix (8-direction, no fire). Arms: random, specialist x5, multi,
zeroshot (multi checkpoint on held-out games), scratch x3, finetune x3; seeds 0, 1, 2.
One pipeline for all arms: UGA 18-action pad, sign clip + per-game return normalisation,
EpisodicLife + FireReset for training, sticky 0.25, eval sampled policy, 27,000-frame eval cap.
Budget: 32.0M steps per seed (multi 10M, specialists 5 x 2M, scratch/finetune 3 x 2M each),
96.0M for 3 seeds, checkpointed every 250k steps.

HNS reference scores: DQN Zoo `atari_data.py` (random/human, 30 no-op starts, 108k cap),
https://github.com/google-deepmind/dqn_zoo/blob/master/dqn_zoo/atari_data.py. UGA's protocol
differs (sticky actions, eval cap, pad), so UGA HNS is comparable across UGA arms and only
indicative against published numbers.

| Where | steps/s per process | 32.0M (1 seed) | 96.0M (3 seeds) |
|---|---|---|---|
| this sandbox CPU, multi-game smoke (measured) | 165-178 | ~52 h | ~156 h |
| Kaggle T4, **not measured** (planning range; run `--throughput` first) | 400 / 700 / 1000 | 22 / 13 / 9 h | 2 GPUs: ~33 / 19 / 13 h wall |

## Limitations

* **No GPU number exists.** Every Kaggle hour above is an estimate until the kit's
  `--throughput` step runs on Kaggle.
* The >= 3x CPU speed-up target was not met (1.55x same-session); the CNN update is the floor.
* Pong runs are one seed each and far below CleanRL's 10M-step budget. The 1.23M run shows
  learning (greedy +0.8, sampled -2.1 over 10 bimodal episodes) but says nothing yet about
  whether UGA reaches ~20 on Pong, or about seed variance.
* A resumed run restarts episodes and the 100-game telemetry window (the trainer carries no
  mid-episode state across processes); the LR schedule and reward-normaliser state do resume.
* Recurrent fast mode splits minibatches by env slot (num_envs must be divisible by
  num_minibatches) and back-propagates through the full 128-step rollout; it is tested for
  exact replay of rollout log-probs but has not been trained to a result.
* `reward_norm: per_game` is return-std scaling ("PopArt-lite"); there is no value-head
  rescaling (PopArt proper is out of scope).
* `AtariVectorEnvAdapter` (`vector_backend: ale`) accepts the new flags but was not used in any
  Stage 2 run; its preprocessing still differs from the evaluation path.
* Kaggle quota accounting for T4 x2 vs one T4 and session persistence were not verified.

## Failures and incidents

* Two early fast-engine profiles (112.2 and 67.7 steps/s) were taken under co-tenant load
  before the uint8 path and the segment-batched GRU replay existed; superseded and dropped from
  `profile.json` (noted there).
* The first fast recurrent replay looped the GRU over every time step and was slower than legacy
  (16 s of backward for 2 updates); replaced by one `nn.GRU` call per stretch between dones
  (same masking, tested for exact replay).
* The first LR-annealing version used the post-rollout step count (first update at 0.67 x lr);
  fixed to CleanRL's pre-rollout convention before any recorded run, with a test.
* The first 150k attempt was stopped after 3 updates to switch to <= 51,200-step chunks when
  co-tenant load cut speed to ~45 steps/s; the recorded run started from scratch.
* bf16 autocast microbenchmark: slower than fp32 by a large factor; abandoned.
* The sandbox rebooted twice during run (b): at 61,440 steps (resumed from the 51,200-step
  checkpoint) and at ~576,512 steps, ~11:56 UTC (resumed from 563,200). Each time telemetry rows
  past the checkpoint were dropped before resuming. Older checkpoints were deleted as the run
  went (kept every 256,000 steps and the latest), all outside the repo; no `.pt` is committed.

## Commands for the user's PC (Windows, PowerShell, CPU, 32 GB RAM)

Run from `universal-game-agent\` in the existing venv. One-time:

```powershell
cd universal-game-agent
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-atari.txt
python -m unittest discover -s tests
# Your machine's speed, before/after (about 5-8 min). Read "summary" at the end.
python experiments\stage2\throughput.py --rounds 2 --updates 2 --threads 4 --out $env:TEMP\uga_stage2_throughput.json
```

### A. CleanRL-faithful Pong anchor (10M steps, 3 seeds) -- the Stage 1 question, answered properly

```powershell
$seed = 1   # once each with 1, 2, 3; one PowerShell window per seed
$cfgDir = "$env:USERPROFILE\uga-stage2"; New-Item -ItemType Directory -Force $cfgDir | Out-Null
$cfg = "$cfgDir\pong_cleanrl_s$seed.yaml"
(Get-Content configs\stage2_pong_cleanrl.yaml) `
  -replace 'checkpoints/stage2_pong_cleanrl', "checkpoints/stage2_pong_cleanrl_s$seed" `
  -replace 'torch_threads: 0', 'torch_threads: 2' | Set-Content $cfg
python main.py train --config $cfg --seed $seed
python experiments\stage2\pong_eval.py --config $cfg --checkpoint "checkpoints\stage2_pong_cleanrl_s$seed\ppo_final.pt" `
  --telemetry "checkpoints\stage2_pong_cleanrl_s$seed\telemetry.csv" --out "$cfgDir\pong_cleanrl_s$seed.json"
```

Resume after an interruption (checkpoint every 50 updates = 51,200 steps; the LR schedule
continues because it is computed from the restored step count):

```powershell
$last = Get-ChildItem "checkpoints\stage2_pong_cleanrl_s$seed\ppo_*.pt" | Where-Object Name -ne 'ppo_final.pt' |
        Sort-Object { [int]($_.BaseName -replace '^ppo_', '') } | Select-Object -Last 1
python main.py train --config $cfg --seed $seed --resume $last.FullName
```

Pass criterion: `pong_eval.py`'s `ppo_sampled` mean over 10 episodes per seed, reported as
mean +- std over 3 seeds next to CleanRL's 20.36 +- 0.20.

### B. The Stage 2 benchmark on the PC (CPU; slow, see the hours table)

```powershell
$art = "D:\uga\stage2"   # outside the git checkout
$b = "experiments\stage2\benchmark.yaml"
python -m training.stage2 random --config $b --artifacts $art
python -m training.stage2 all    --config $b --artifacts $art --seeds 0 --threads 4 --device cpu --scale 0.25
python -m training.stage2 aggregate --config $b --artifacts $art --out-dir "$art\summary"
```

Rerunning the same `all` line resumes every unfinished run from its last 250k-step chunk
(`--scale 0.25` makes chunks 62.5k). `--max-hours 8` stops cleanly between chunks.

## Kaggle steps (summary; full procedure in `kaggle/README.md`)

1. New notebook -> Session options: **GPU T4 x2**, **Internet On**; import
   `kaggle/uga_stage2_kaggle.ipynb`.
2. `!python uga_stage2_kaggle.py --throughput` (3-5 min): measures real steps/s per GPU process
   and prints the implied hours. This is the first GPU number this project will have.
3. `!python uga_stage2_kaggle.py --all-seeds 0 1 --max-hours 11` (one seed per GPU; stops
   cleanly between 250k-step chunks; exit code 3 = paused).
4. Save Version (commit) so `/kaggle/working/uga_stage2` becomes the version's output; next
   session add it as an input and pass `--restore-from /kaggle/input/<name>/uga_stage2`.
5. Seed 2 in a later session (`--all-seeds 2`). `summary/summary.csv` holds the table.

