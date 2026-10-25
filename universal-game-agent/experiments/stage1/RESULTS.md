# Stage 1 results: real games through one interface

Branch `upgrade/uga-stage1-ale` (stacked on draft PR #31). Plan locked before coding:
`PLAN.md`. Everything below was measured in the Hark sandbox on 2026-10-26 (Asia/Manila):
Linux aarch64, **2 vCPU, 8 GB**, Python 3.12.15, torch 2.14.1+cpu, numpy 2.5.3, ale-py 0.12.1.
The sandbox is shared: other jobs (compilers) ran concurrently for part of the time
(load average 4-7 on 2 vCPU), so every speed here is a lower bound for this hardware.
No window, no SendInput, no GPU.

## What ran

| Run | Command | Result |
|---|---|---|
| Regression suite before any change | `python -m unittest discover -s tests` | 540 tests, OK, 3 skipped (72 s) |
| Regression suite after, ale-py + Pillow installed | same | 573 tests, OK, 3 skipped (40 s) |
| Regression suite after, ale_py and PIL made unimportable (CI condition) | same, via an import blocker | 573 tests, OK, 19 skipped (the 3 old + 16 ALE) |
| Baselines, 5 games x 2 policies x 10 episodes | `python -m training.atari_baselines --config experiments/stage1/atari_pilot.yaml --out experiments/stage1/baselines.json --episodes 10 --seeds 0 --max-decisions 6750` | `baselines.json` (492 s) |
| Throughput | `python experiments/stage1/throughput.py --seconds 15 --ppo-updates 2` | `throughput.json` |
| Pong PPO sanity run, 150,528 steps, seed 1 | `python main.py train --config <copy of configs/atari_pong.yaml with total_timesteps 150528, checkpoint_every_updates 20, artifacts outside git>` | `pong_sanity.json` |
| Sanity eval + GIFs | `python experiments/stage1/sanity_eval.py ...` | `pong_sanity.json`, `gifs/` |

Not run here, by design: the 10M-step Pong anchor and the full Atari pilot (see the
commands section; at the measured speed they are days on this box).

## Baselines (raw game score, 10 episodes, seed 0, episode seeds 10000..10009)

Pilot evaluation settings: sticky actions 0.25, 30 no-op resets, skip 4, UGA 18-action
pad (uniform random over the pad, i.e. random over the projected native actions with the
pad's multiplicities), 27,000-frame episode cap = 6,750 decisions.

| Game | Policy | Mean | Std | Min..max | Mean length (decisions) | Hit cap | Decisions/s |
|---|---|---|---|---|---|---|---|
| pong | random | -19.80 | 0.87 | -21..-18 | 960 | 0/10 | 412 |
| pong | const_noop | -21.00 | 0.00 | -21..-21 | 761 | 0/10 | 385 |
| breakout | random | 1.80 | 2.04 | 0..6 | 224 | 0/10 | 268 |
| breakout | const_noop | 0.00 | 0.00 | 0..0 | 6747 | 10/10 | 405 |
| space_invaders | random | 122.00 | 85.71 | 30..320 | 414 | 0/10 | 273 |
| space_invaders | const_noop | 0.00 | 0.00 | 0..0 | 689 | 0/10 | 314 |
| demon_attack | random | 140.50 | 69.51 | 40..250 | 1048 | 0/10 | 325 |
| demon_attack | const_noop | 0.00 | 0.00 | 0..0 | 3140 | 0/10 | 438 |
| freeway | random | 0.00 | 0.00 | 0..0 | 2045 | 0/10 | 384 |
| freeway | const_noop | 0.00 | 0.00 | 0..0 | 2045 | 0/10 | 259 |

Adapter sanity check: these are in the range of published random-agent scores
(e.g. the C51 paper's random column lists Space Invaders 148.0, Demon Attack 152.1,
Freeway 0.0 — read from a search excerpt of https://arxiv.org/pdf/1707.06887, not
re-verified, and under a different protocol: no sticky actions, 30 no-op starts, 5 min cap).
`const_noop` never presses FIRE, so Breakout never launches the ball and runs to the cap —
the reason the pilot caps episodes at 27,000 frames instead of v5's 108,000.

## Throughput (`throughput.json`)

Env-only (Pong, random pad actions, includes UGA preprocessing for `sync`):

| Backend | 1 env | 4 envs | 8 envs |
|---|---|---|---|
| `sync` = `SyncVectorEnv` of `AtariGame` + `PreprocessingWrapper` (sequential, Python) | 742 | 788 | 724 decisions/s |
| `ale` = `AtariVectorEnv` (C++ threads, its own preprocessing), 2 threads | 1,551 | 2,482 | 2,877 decisions/s |

(Recorded run: load average ~2.8-3.6, written into `throughput.json`. A first run under load
~7 measured 480 / 522 / 487 and 1,361 / 1,760 / 2,093.)

PPO training with the anchor model/ppo blocks (84x84, CNN -> 512 -> GRU-128, 4 epochs):

| Envs | Backend | torch threads | Steps/s |
|---|---|---|---|
| 1 | sync | 2 | 60 (load ~3; 36 under load ~7) |
| 8 | sync | 2 | 68 (load ~3; 44 in a first run under load ~7) |
| 8 | ale | 2 | 74 (load ~3; 51 under load ~7) |

Finding: training is bound by the model (forward + 4-epoch recurrent update), not by the
emulator. `AtariVectorEnv` is 2-4x faster env-only but only ~10-15% faster end to end, and its
preprocessing differs from the single-env eval path (area vs bilinear resize), so the
shipped configs keep `vector_backend: sync`. It is usable behind the flag and tested.

## Pong PPO sanity run (`pong_sanity.json`, `gifs/`)

Config: `configs/atari_pong.yaml` hyperparameters unchanged (8 envs x 128, clip 0.1, sticky 0,
sign-clipped training reward, UGA pad, 84x84, CNN-512-GRU-128) with `total_timesteps: 150528`
(147 updates), `checkpoint_every_updates: 20`, seed 1, artifacts outside git. Wall clock ~32 min
of training (79 steps/s average; 61 steps/s in the first segment under load 7, 71-110 steps/s
per 20-update segment afterwards).

Training curve (rolling mean of the last 100 training episodes = Pong score, since clipped
and raw Pong rewards are identical; policy entropy, max ln 18 = 2.890):

| Steps | 10k | 20k | 41k | 61k | 82k | 102k | 123k | 143k | 150.5k |
|---|---|---|---|---|---|---|---|---|---|
| Rolling return | -19.75 (8 ep) | -20.12 | -20.12 | -20.35 | -20.36 | -20.38 | -20.35 | -20.33 | -20.35 |
| Entropy | 2.886 | 2.844 | 2.861 | 2.792 | 2.808 | 2.833 | 2.703 | 2.588 | 2.624 |

(The window restarts empty after the resume at 20,480 steps; from 41k on it holds 17-100
post-resume episodes. 143 post-resume episodes in total.)

Evaluation on the same 10 episode seeds (10000..10009), raw score, sticky 0:

| Policy | Mean | Std | Mean length |
|---|---|---|---|
| PPO greedy (150k) | -21.0 | 0.0 | 761 |
| PPO sampled (150k) | -20.7 | 0.64 | 830 |
| Uniform random pad | -20.4 | 0.66 | 915 |
| Constant NOOP | -21.0 | 0.0 | 761 |

**Verdict: flat. No learning signal is visible at 150k steps.** The greedy policy collapsed
onto pad action 17 (DOWNLEFTFIRE -> Pong's LEFTFIRE) for every step and loses 21-0 exactly
like constant NOOP; the sampled policy is indistinguishable from random. Entropy is only
starting to fall (2.89 -> 2.62). This is not evidence against the adapter (the pixel-only
and contract tests are), and it is not evidence that UGA PPO cannot learn Pong: 150k steps is
1.5% of the 10M anchor, and Pong PPO curves are typically still near -21 at this budget.
Whether UGA PPO reaches CleanRL's ~20 is exactly what the 10M anchor below must answer.

GIFs (Pillow was present in the sandbox; 480 raw frames each, 160x210, played back at 25 fps
= 2.4x slow motion): `gifs/pong_random.gif` (62 KB) and `gifs/pong_ppo.gif` (42 KB; the
agent's green paddle parks at the bottom edge, matching the constant LEFTFIRE choice).


## Commands for the user's PC (Windows, PowerShell, CPU)

Run from `universal-game-agent\`. One-time setup (adds only ale-py to the existing venv):

```powershell
cd universal-game-agent
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-atari.txt
python -m unittest discover -s tests          # ALE tests now run instead of skipping
# Measure YOUR steps/s first (about 3-5 min); every estimate below scales with it.
python experiments\stage1\throughput.py --seconds 10 --ppo-updates 3 --threads 4 --out $env:TEMP\uga_throughput.json
```

Hours for a run = steps / (PPO steps/s from that JSON, `ppo` row with `num_envs: 8`) / 3600.

### A. Pong correctness anchor (10M steps, 3 seeds)

Each seed gets its own config copy so checkpoints and telemetry never mix (checkpoints
land in the gitignored `checkpoints\atari_pong_s<seed>\`; run from `universal-game-agent\`). Running the three
seeds in three PowerShell windows at once (2 torch threads each) uses the cores better than
running them one after another, because env stepping is single-threaded Python.

```powershell
$seed = 1   # run once each with 1, 2, 3 (one window per seed)
$cfgDir = "$env:USERPROFILE\uga-stage1"; New-Item -ItemType Directory -Force $cfgDir | Out-Null
$cfg  = "$cfgDir\atari_pong_s$seed.yaml"   # outside the repo, so git status stays clean
(Get-Content configs\atari_pong.yaml) -replace 'checkpoints/atari_pong', "checkpoints/atari_pong_s$seed" | Set-Content $cfg
$env:OMP_NUM_THREADS = "2"
python main.py train --config $cfg --seed $seed
python main.py evaluate --config $cfg --seed $seed --episodes 10 --checkpoint "checkpoints\atari_pong_s$seed\ppo_final.pt"
```

Resume after an interruption (a checkpoint is written every 50 updates = 51,200 steps):

```powershell
$last = Get-ChildItem "checkpoints\atari_pong_s$seed\ppo_*.pt" | Where-Object Name -ne 'ppo_final.pt' |
        Sort-Object { [int]($_.BaseName -replace '^ppo_', '') } | Select-Object -Last 1
python main.py train --config $cfg --seed $seed --resume $last.FullName
```

Telemetry to read the learning curve: `checkpoints\atari_pong_s<seed>\telemetry.csv`
(`timesteps`, `mean_reward` = rolling mean of the last 100 training episodes; Pong's
clipped and raw rewards are identical because every point is +-1). Before resuming, delete
the CSV rows whose `timesteps` exceed the checkpoint's step count, or they appear twice.

Pass criterion to write into RESULTS.md: final greedy eval mean over 10 episodes per seed,
reported as mean +- std over the 3 seeds, next to CleanRL's 20.36 +- 0.20. Below ~18 at 10M
means one of the listed deviations matters and must be explained before Stage 2.

### B. Atari pilot (3 train games, 2 held out, 3 seeds)

```powershell
$art = "D:\uga\stage1_pilot"        # any folder OUTSIDE the git checkout
$cfg = "experiments\stage1\atari_pilot.yaml"
python -m training.generalization baselines --config $cfg --artifacts $art        # seeds 0 1 2, ~30 min
```

Then one window per seed (`$s = 0`, `1`, `2`); rerunning the same block after any
interruption skips finished runs and resumes the rest from their last 200,448-step chunk:

```powershell
$s = 0
$env:OMP_NUM_THREADS = "2"
python -m training.generalization run --config $cfg --artifacts $art --arm multi --seed $s --threads 2
foreach ($g in 'pong', 'breakout', 'space_invaders') {
  python -m training.generalization run --config $cfg --artifacts $art --arm specialist --game $g --seed $s --threads 2
}
python -m training.generalization run --config $cfg --artifacts $art --arm scratch  --seed $s --threads 2
python -m training.generalization run --config $cfg --artifacts $art --arm finetune --seed $s --threads 2
```

When all three seeds are done:

```powershell
python -m training.generalization aggregate --config $cfg --artifacts $art --out-dir experiments\stage1\pilot
```

`--scale 0.25` on every `run` line gives a quarter-budget first pass (about 3.5M steps per
seed instead of 14.0M) if the full budget is too long; use the same scale for every arm.
Commit `experiments\stage1\pilot\` (summary JSON/CSV only, never `.pt`).

### Wall-clock estimates

Measured here: 68 steps/s (`throughput.json`, 8 envs, load ~3) to 79 steps/s (sanity-run
average) on 2 aarch64 vCPUs with 2 torch threads. Your PC's number from `throughput.py` is
the one to use; the table shows what it implies.

| PPO steps/s (one process) | Pong anchor, 1 seed (10.0M) | Pilot, 1 seed (14.0M + evals) | Pilot at `--scale 0.25`, 1 seed |
|---|---|---|---|
| 80 (this sandbox) | 35 h | 49 h + up to ~2 h eval | 12 h + ~1 h |
| 150 | 19 h | 26 h + ~1.5 h | 6.5 h + ~1 h |
| 250 | 11 h | 16 h + ~1 h | 3.9 h + ~0.5 h |

Pilot steps per seed: multi 6,001,920 + 3 x specialist 2,000,640 + scratch 1,000,704 +
finetune 1,000,704 = 14,005,248. Evaluation overhead: every run evaluates greedily on all 5
games (10 episodes each, capped at 6,750 decisions); scratch/finetune also evaluate the
held-out game at 5 curve points. Three seeds in three windows run concurrently; the total
then depends on how many cores the PC has (2 torch threads per process).


## Limitations and known deviations

- The anchor config mirrors CleanRL's knobs where UGA has them, but not: LR annealing,
  Adam eps 1e-5, shuffled minibatches, EpisodicLife, FireReset, the feed-forward policy
  (UGA has a GRU), Pong's 6-action head (UGA uses the 18-action pad, 12 of which alias).
  A 10M-step score below CleanRL's would need these ruled out before blaming the adapter.
- `reward_transform` implements sign clipping only; running-return normalisation is not
  implemented.
- `AtariVectorEnvAdapter` resets eagerly inside ALE (its reset seed is ALE's, not the
  caller's) and uses ALE's preprocessing; opt-in only.
- Baselines: one policy seed (10 episodes) here; the PC run uses the config's 3 seeds.
- Sandbox speeds were measured on a shared, loaded 2-vCPU machine.

## Failures and incidents

- The sandbox rebooted once during the sanity run (at 28,672 steps). The run resumed from
  the periodic checkpoint at 20,480 steps (`main.py train --resume`); telemetry rows after
  20,480 from the lost segment were dropped so no step appears twice. A resumed run is not
  bit-identical to an uninterrupted one (episodes restart, the 100-episode reward window
  restarts empty).
