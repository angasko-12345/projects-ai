# Kaggle kit: UGA Stage 2 benchmark on T4 x2

Files: `uga_stage2_kaggle.py` (the runner wrapper) and `uga_stage2_kaggle.ipynb` (the same
steps as notebook cells). Nothing here signs in to Kaggle or anywhere else; you create the
notebook yourself. The repository is public, so the notebook only needs Internet access.

## What the numbers below are based on

* **Measured (CPU, this sandbox, 2 aarch64 vCPUs shared with other jobs):** see
  `experiments/stage2/throughput.json` and `RESULTS.md`. Env-only stepping of 8 Pong slots:
  ~794 decisions/s with the legacy float32 preprocessing, ~1,562 with `obs_dtype: uint8`.
* **Not measured: any GPU number.** The T4 speed is unknown until you run step 2 below.
  On a GPU the CNN update stops being the bottleneck, so a Kaggle process is expected to be
  bound by Python env stepping (one CPU core per process for the 10 slots). Treat every
  hour estimate here as a planning range until `throughput.json` from Kaggle replaces it.

## One-time setup (about 5 minutes)

1. kaggle.com -> **Create -> New Notebook**.
2. Right panel -> **Session options**: *Accelerator* = **GPU T4 x2**; *Internet* = **On**
   (needed for `git clone` and `pip install ale-py`; phone verification of the Kaggle
   account is required for both GPUs and Internet).
3. **File -> Import Notebook** -> upload `uga_stage2_kaggle.ipynb` from this folder (or paste
   its cells by hand).

## Each session

1. Run the first three cells (GPU check, fetch the script, `--help`).
2. **Measure** (3-5 min): `!python uga_stage2_kaggle.py --throughput`. It trains a 50k-step
   multi-game run per GPU (two processes in parallel), writes
   `/kaggle/working/uga_stage2/throughput.json`, and prints the aggregate steps/s and the
   implied hours for the full benchmark. Copy that JSON into the PR/RESULTS when you have it.
3. **Train**: `!python uga_stage2_kaggle.py --all-seeds 0 1 --max-hours 11`
   * one process per GPU (seed 0 on GPU 0, seed 1 on GPU 1), 2 torch CPU threads each;
   * every run checkpoints every 250k steps (`budgets.chunk_every`) and stops cleanly
     between chunks once 11 h have passed (exit code 3 = paused);
   * order per seed: multi -> 5 specialists -> (scratch, finetune) for each held-out game
     (finetune needs that seed's multi run).
4. **Save**: *Save Version* -> *Save & Run All (Commit)* is the only way the output survives
   a session if you run the notebook interactively; or start the training cell from a
   committed version in the first place (background run, up to 12 h). The artifacts are in
   `/kaggle/working/uga_stage2` (checkpoints `.pt`, `telemetry.csv`, `progress.json`,
   `result.json`, `summary/summary.csv`, `summary/summary.json`).
5. **Next session**: *Add Input* -> your notebook's previous version output, then
   `!python uga_stage2_kaggle.py --all-seeds 0 1 --max-hours 11 --restore-from /kaggle/input/<name>/uga_stage2`
   (finished runs are skipped, unfinished ones resume from their last chunk).
6. Seed 2: `--all-seeds 2` (runs on GPU 0 only; the second GPU idles). To keep both GPUs
   busy, run seed 2's plan in a later session next to a re-run of an unfinished seed.

Single run instead of the plan:
`!python uga_stage2_kaggle.py --arm specialist --game pong --seed 0 --max-hours 11`.
Quarter budget for a first pass: add `--scale 0.25` to every command (keep it the same for all
runs you intend to compare).

## Budget and hours

Steps per seed (agent decisions): multi 10.0M + specialist 5 x 2.0M + scratch 3 x 2.0M +
finetune 3 x 2.0M = **32.0M**; 3 seeds = **96.0M**. Hours of training per seed =
32.0M / (steps/s of one process) / 3600; evaluation adds roughly 5-15 min per run.

| steps/s per process (measure it!) | 1 seed | 3 seeds on 2 GPUs (wall) | Kaggle weeks at ~30 h/week* |
|---|---|---|---|
| 400 | 22.2 h | ~44 h | ~1.5 |
| 700 | 12.7 h | ~25 h | ~1 |
| 1000 | 8.9 h | ~18 h | ~0.6 |
| this sandbox CPU, fast engine (see RESULTS.md) | far too slow (days) | -- | -- |

\* Kaggle's GPU quota is about 30 h per week and a session ends after 12 h. Whether a
T4 x2 session draws quota at the same rate as a single T4 is a Kaggle policy detail this kit
does not verify -- check the quota meter after the first session. With `--scale 0.25` every
figure above is divided by 4.

## Failure modes and what to do

* `git clone failed` -> Internet is off in Session options.
* `ppo.device is 'cuda' but torch.cuda.is_available() is False` -> accelerator is not GPU;
  the wrapper passes `--device cpu` automatically when it sees no GPU, so this only appears
  when you call `training.stage2` yourself.
* A run's log (`/kaggle/working/uga_stage2/seed<N>.log`) ends with `[paused]` -> normal; restore
  and rerun next session.
* Out of disk: each run keeps one `ppo_final.pt` (~20 MB with Adam state) plus logs; 48 runs
  stay well under Kaggle's 20 GB working directory.
