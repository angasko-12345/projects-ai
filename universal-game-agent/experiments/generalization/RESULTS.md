# Cross-game generalization: results (pilot, 2026-10-25/26)

Plan written before the code: [`PLAN.md`](PLAN.md). Config: [`config.yaml`](config.yaml).
Metrics: [`metrics/`](metrics/) (`summary.json`, `summary.csv`, `runs.csv`, `stats.json`,
`baselines.json`, one `runs/<run_id>.json` per training run with every eval episode). Failed and
diagnostic runs: [`diagnostics.json`](diagnostics.json). GIFs: [`gifs/`](gifs/). Checkpoints are
**not** in git (they stayed in the sandbox artifacts directory, `metrics/runs/*.json` name them as
`<artifacts>/runs/<run_id>/ckpt/ppo_final.pt`).

## Headline (read the caveats below before quoting any of it)

At this small CPU budget, on in-repo pixel minigames:

1. **One pixel-only PPO agent trained on three games at once learned all three** (beats random
   on each, pooled-episode Welch t = 3.7-4.3), and at the **same per-game sample count** it beat
   the single-game specialists on breakout (+2.50 vs +0.90) and catch (+0.03 vs -0.40; CI touches 0),
   tied on pong.
2. **Zero-shot on the held-out near-transfer game (`volley`)** it scored -0.13 ± 0.48 vs random
   -0.72 ± 0.06 (best constant -0.65). Better than random on 3/3 seeds, but two seeds by only
   +0.20 / +0.35 (4-7 episodes in 20); seed 2 (+1.20) carries the mean. Weak, real, not robust.
3. **Transfer efficiency is the clear result: fine-tuning the multi-game agent on `volley` beat
   training from scratch at every budget point on 3/3 seeds** (+0.42 ± 0.16 vs -0.70 ± 0.16 after
   30,720 steps; curve-mean difference +0.70 / +0.78 / +1.34 per seed; pooled t = 8.4). Scratch
   PPO never left random level within 30k steps.
4. **Far held-out `dodge` (opposite objective) transfers negatively**, as predicted: -0.22 vs random
   +1.37. The agent learned "go to the falling thing", which is exactly wrong there.

This is near transfer between hand-built games that share an action set, a bottom-paddle layout
and a reward shape. It is **not** general-purpose gameplay, and every agent here is far below the
privileged oracle (the best learned score is ~26 % of the oracle's, on breakout; fine-tuned
volley is ~5 %).

## Setup

| | |
|---|---|
| Games | `pong` (toy Pong rules), `catch`, `breakout` = train; `volley` = held-out (gravity arcs, wind, 3x3 cross ball, higher paddle, ground strip, decorative net); `dodge` = far held-out (avoid blocks). All 64x48 RGB, `Discrete(3)` NOOP/LEFT/RIGHT, new palette/sizes/speeds every episode (`environment/arcade.py`). |
| Agent input | Rendered pixels only: gray -> 48x48 -> 4-frame stack, frame skip 4 (max-pool of the last two frames). Env reward and terminated/truncated are training feedback only. No game id. `tests/test_pixel_only.py` enforces this. |
| Model / PPO | Unchanged repo `ActorCritic` (Nature CNN -> 256 -> GRU-128) with `frame_size: 48`; unchanged repo PPO, repo-default hyper-parameters (rollout 128, minibatch 32, 4 epochs, lr 2.5e-4, entropy 0.01, clip 0.2, gamma 0.99, lambda 0.95), `num_envs: 3`. |
| Multi-game | `SyncVectorEnv` of 3 slots, slot i plays `train[i % 3]` (round-robin), `check_spaces=True`. |
| Budgets (env decisions) | specialist 39,936 per game; multi 119,808 (= 3 x specialist, so equal per-game samples); scratch / fine-tune on volley 30,720 each, eval every 7,680. |
| Seeds | policy seeds 0, 1, 2 for every arm (torch seed = s; first-reset seeds 1000s + 17 + slot). |
| Eval | greedy, 20 episodes per (policy seed, game), episode seeds 10000 + 1000s + i (never used in training); baselines on the same episode seeds, acting at the same 4-frame decision period. "mean ± std" = mean and std over the 3 per-seed means. Episodes cap at 500 frames. |
| Hardware | Linux aarch64 sandbox, 2 vCPU shared with other jobs (load avg 1-7), 8 GB RAM, no GPU; Python 3.12.15, torch 2.14.1+cpu, numpy 2.5.3, 1 torch thread, at most 2 training processes at once. |

## Per-game episode reward (mean ± std over 3 seeds)

| game | split | random | best constant | sticky random | PPO specialist (own game) | multi-game PPO | oracle (privileged) |
|---|---|---|---|---|---|---|---|
| pong | train | -0.83 ± 0.06 | -0.52 ± 0.12 (right) | -0.78 ± 0.09 | -0.02 ± 0.62 | -0.12 ± 0.19 | +7.58 ± 0.91 |
| catch | train | -0.78 ± 0.14 | -0.78 ± 0.05 (noop) | -0.87 ± 0.06 | -0.40 ± 0.25 | +0.03 ± 0.45 | +9.50 ± 0.41 |
| breakout | train | +0.63 ± 0.42 | +0.62 ± 0.20 (right) | +0.32 ± 0.21 | +0.90 ± 0.35 | +2.50 ± 0.32 | +9.75 ± 1.00 |
| volley | held-out (near) | -0.72 ± 0.06 | -0.65 ± 0.04 (noop) | -0.72 ± 0.02 | n/a (see scratch) | **-0.13 ± 0.48** (zero-shot) | +8.33 ± 0.96 |
| dodge | held-out (far) | +1.37 ± 0.44 | +0.88 ± 0.34 (left) | +2.32 ± 0.80 | n/a | -0.22 ± 0.15 (zero-shot) | +15.05 ± 2.09 |

Reward: +1 per scoring event (paddle hit / catch / brick / bounce / dodged block), -1 on the event
that ends the game. The oracle reads env internals (positions, velocities) and acts at the same
4-frame period; it is an upper bound, never a learnable policy. Acting every frame it scores higher
(`oracle_skip1` in `metrics/baselines.json`: 11.37 / 11.75 / 8.67 / 9.35 / 18.05).

Completion / survival (fraction of the 60 eval episodes reaching the 500-frame cap; breakout
wall-clear rate): every learned policy and every non-oracle baseline is at 0-7 % survival
(multi breakout 6.7 %, finetune volley 1.7 %); **no learned policy ever cleared a breakout wall**
(oracle 3.3 %). Oracle survival: 52 / 77 / 92 / 88 / 48 %.

Cross-play matrix (zero-shot of each specialist on every game; diagonal = own game):

| eval game | pong specialist | catch specialist | breakout specialist | multi |
|---|---|---|---|---|
| pong | -0.02 ± 0.62 | -0.60 ± 0.37 | -0.68 ± 0.24 | -0.12 ± 0.19 |
| catch | -0.67 ± 0.12 | -0.40 ± 0.25 | -0.72 ± 0.21 | +0.03 ± 0.45 |
| breakout | +0.68 ± 0.30 | +0.55 ± 0.28 | +0.90 ± 0.35 | +2.50 ± 0.32 |
| volley | -0.60 ± 0.14 | -0.58 ± 0.08 | -0.73 ± 0.06 | -0.13 ± 0.48 |
| dodge | +0.80 ± 0.36 | +0.32 ± 0.51 | +0.27 ± 0.33 | -0.22 ± 0.15 |

No single-game specialist transfers zero-shot to volley (all within ~0.15 of random).

## Held-out `volley`: zero-shot and fine-tune vs scratch

Per-seed zero-shot (multi agent, 20 episodes each) vs random / const-noop on the same episodes:
seed 0 -0.50 vs -0.70 / -0.60; seed 1 -0.45 vs -0.80 / -0.70; seed 2 +0.55 vs -0.65 / -0.65.
Pooled 60 vs 60 episodes: diff +0.58, Welch t 2.84, bootstrap 95 % CI [+0.22, +1.02].

| steps on volley | 0 | 7,680 | 15,360 | 23,040 | 30,720 | mean over curve |
|---|---|---|---|---|---|---|
| scratch (fresh net) | -0.67 ± 0.06 | -0.73 ± 0.06 | -0.68 ± 0.06 | -0.70 ± 0.16 | -0.70 ± 0.16 | -0.70 |
| fine-tune (from multi, same seed) | -0.13 ± 0.48 | +0.20 ± 0.49 | +0.40 ± 0.39 | +0.33 ± 0.14 | +0.42 ± 0.16 | +0.24 |

Per-seed curves: scratch `[-0.6,-0.8,-0.6,-0.9,-0.9]`, `[-0.75,-0.75,-0.75,-0.5,-0.5]`,
`[-0.65,-0.65,-0.7,-0.7,-0.7]`; fine-tune `[-0.5,-0.35,0.2,0.15,0.2]`, `[-0.45,0.1,0.05,0.35,0.6]`,
`[0.55,0.85,0.95,0.5,0.45]`. Fine-tune minus scratch, curve mean, per seed: +0.70, +0.78, +1.34.
Pooled per point (60 vs 60 episodes): +0.53 (t 2.6) at 0, then +0.93 / +1.08 / +1.03 / +1.12
(t 3.3-4.6). Both parts of transfer show: a head start (step 0) **and** further improvement
(fine-tune gains +0.55 over 30k steps while scratch gains nothing).

"Equal small step budgets" means equal steps **on volley**. The fine-tuned agent had also seen
119,808 steps of the training games; scratch had not. That is the standard pre-train/transfer
comparison, not an equal-total-compute one. Scratch PPO did not start learning within 30k steps,
so a steps-to-threshold ratio cannot be computed (scratch never reached any threshold the
fine-tuned agent passed). The specialists suggest scratch needs roughly 20-40k steps before it
moves at all.

Forgetting check (fine-tuned agents back on the training games vs the multi agent, pooled):
pong -0.10, catch -0.33, breakout -0.17; every 95 % CI includes 0 (60 episodes per side is too
few to rule out moderate forgetting).

## Multi-game agent vs specialists (equal per-game samples)

Pooled 60 vs 60 episodes: breakout +1.60 (t 3.15, CI [+0.65, +2.60]); catch +0.43 (t 1.79,
CI [0.00, +0.92]); pong -0.10 (t -0.28). The multi run also performed 3x the gradient updates of a
specialist (312 vs 104), so "multi-game helps" and "more updates on a shared encoder help" are
not separated here.

## Runtime

| arm | steps per run | train seconds per run (3 seeds) | steps/s |
|---|---|---|---|
| multi | 119,808 | 1002 / 894 / 924 | 120 / 134 / 130 |
| specialist | 39,936 | 191-375 | 107-209 |
| scratch | 30,720 | 281 / 240 / 254 | 109 / 128 / 121 |
| fine-tune | 30,720 | 143 / 140 / 156 | 215 / 219 / 197 |

(Throughput varies 2x with the shared box's load; train seconds exclude evaluation.) Main batch:
18 runs, 903,168 PPO steps, 1.81 h of single-core training time (two runs at a time), plus
evaluation; baselines 17 s. Pilot A and diagnostics added roughly 0.35 M more steps.

## Determinism

- Legacy path (step 1): two `python -m training.ppo --total-timesteps 1024 --seed 0` runs gave
  `torch.equal` weights for every tensor and identical per-update logs except `fps`
  (checkpoint files differ only by the pickled `checkpoint_dir` string).
- New path: `specialist_catch_s0` in the main batch and the earlier skip-4 diagnostic run of the
  same config and seed (different artifacts directory, different process, different box load)
  produced identical eval results on all five games (catch -0.15, pong -0.70, breakout +0.35,
  volley -0.50, dodge +0.95). A skip-1 catch run and a longer skip-1 catch run with the same seed
  logged identical per-update losses for all 78 updates before the longer run's first chunk
  boundary (the comparison stops there: the longer run was later resumed after a sandbox restart,
  and a resumed run restarts its episodes, so it is not bit-identical past that point).

## Failed experiments and deviations (recorded, not hidden)

- **Pilot A, frame skip 1 (the first committed config): nothing learned.** Multi-game 119,808
  steps (seed 0), catch specialist 39,936, volley scratch / fine-tune 30,720: all within episode
  noise of random; catch training reward flat at -0.65..-0.83. Diagnostics: lr 1e-3 (20k steps) no
  learning; catch at 60k steps no learning. A catch diagnostic at frame skip 4 started learning by
  30k steps, so `env.skip` became 4 for **every** arm and every baseline, and pilot A was discarded
  (numbers in `diagnostics.json`). This is a config change made after seeing pilot results; it was
  chosen on catch only (a training game), never on volley.
- Budgets were doubled once, before any training run, after a smoke run measured throughput
  (PLAN.md "Budget amendment").
- The sandbox restarted twice during the work (once in pilot A, once in the main batch). Runs restart from their last chunk
  checkpoint or from scratch; every run in `metrics/` completed with `resumed_from: null`
  (each interrupted run had no chunk checkpoint yet and was re-run from zero with the same seed).
- A skip-2 diagnostic was stopped before its first eval point (not measured).
- The first GIF recorder stepped one frame per decision (ignoring frame skip); fixed before the
  committed GIFs were made.

## Limitations

- Tiny budgets (40k / 120k / 30k decisions). Specialists are barely past random; all learned
  scores are far from the oracle. A null or reversed result at 10x budget would not surprise me.
- Three seeds; per-seed std is large on several cells (pong specialist ± 0.62, multi volley ± 0.48).
  20 eval episodes per seed, greedy only.
- Held-out `volley` is a *near* transfer target by design: same action set, bottom paddle, +1/-1
  reward shape, similar palette distribution. `dodge` shows that the learned skill does not carry
  to a different objective.
- Frame size 48 (not the legacy 84) and frame skip 4 were chosen for this compute; the legacy toy
  results elsewhere in the repo used 84 / skip 1.
- No ALE or third-party games; all five games were written for this experiment.

## Exact commands (this run)

From `universal-game-agent/` with the sandbox venv (`OMP_NUM_THREADS=1`, `PYTHONUNBUFFERED=1`):

```bash
python -m training.generalization baselines --artifacts $ART
for s in 0 1 2; do python -m training.generalization run --arm multi --seed $s --artifacts $ART; done
for s in 0 1 2; do python -m training.generalization run --arm finetune --seed $s --artifacts $ART; done
for s in 0 1 2; do python -m training.generalization run --arm scratch --seed $s --artifacts $ART; done
for s in 0 1 2; do for g in catch pong breakout; do
  python -m training.generalization run --arm specialist --game $g --seed $s --artifacts $ART; done; done
python -m training.generalization aggregate --artifacts $ART --out-dir $ART/summary
python experiments/generalization/stats.py $ART $ART/summary/stats.json
python -m training.generalization gif --artifacts $ART --seed 0 --out-dir experiments/generalization/gifs
```

(`python -m training.generalization all --artifacts $ART` does the same sequentially.)

## Rerun on Windows (PowerShell, CPU), larger budgets

From the repository root. Checkpoints go to `D:\tmp\uga-generalization` (outside git, per the
repo's temp-file rule); nothing here sends input or opens a window.

```powershell
cd universal-game-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install gymnasium numpy pyyaml pillow

# Same pilot as above (about 2-3 h on one core)
$env:OMP_NUM_THREADS = "1"
python -m training.generalization all --artifacts D:\tmp\uga-generalization

# 10x budgets (specialist ~400k, multi ~1.2M, held-out ~300k steps), one job per seed in parallel.
$art = "D:\tmp\uga-generalization-x10"
python -m training.generalization baselines --artifacts $art
$jobs = foreach ($s in 0, 1, 2) {
  Start-Job -ArgumentList $s, $art, (Get-Location).Path -ScriptBlock {
    param($s, $art, $dir)
    Set-Location $dir
    $env:OMP_NUM_THREADS = "1"
    $py = Join-Path $dir ".venv\Scripts\python.exe"
    & $py -m training.generalization run --arm multi --seed $s --scale 10 --artifacts $art
    & $py -m training.generalization run --arm finetune --seed $s --scale 10 --artifacts $art
    & $py -m training.generalization run --arm scratch --seed $s --scale 10 --artifacts $art
    foreach ($g in "pong", "catch", "breakout") {
      & $py -m training.generalization run --arm specialist --game $g --seed $s --scale 10 --artifacts $art
    }
  }
}
$jobs | Wait-Job | Receive-Job
python -m training.generalization aggregate --artifacts $art --out-dir "$art\summary"
python experiments\generalization\stats.py $art "$art\summary\stats.json"
python -m training.generalization gif --artifacts $art --seed 0 --out-dir "$art\gifs"
```

Each run writes `<artifacts>\runs\<run_id>\result.json` and skips itself when that file exists,
so an interrupted job can be started again (it resumes from the last chunk checkpoint; the
fine-tune and scratch arms checkpoint at every eval point). Three parallel jobs (~0.4-0.5 GB each)
fit easily in 32 GB; more jobs than physical cores slow every job down. `--scale 10` keeps eval
points at 10x spacing.

## GIFs (seed 0, first eval episode, 3x upscale, all < 40 KB)

`gifs/multi_s0_{pong,catch,breakout,volley,dodge}.gif` (multi-game agent, greedy; volley and dodge
are zero-shot), `gifs/finetune_s0_volley.gif`, `gifs/random_volley.gif`. One episode each, so they
illustrate behaviour; they are not evidence. The numbers above are.
