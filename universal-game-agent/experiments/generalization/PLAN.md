# Cross-game generalization: scope-locked plan

Written 2026-10-25 (Asia/Manila) **before** any code edit, after reading the repo. Branch
`upgrade/uga-generalization`, cut from `origin/main` @ `b17c2da` with PR #29's branch
(`upgrade/uga-u0-honest-baseline` @ `7b091bf`, honest toy baseline + privileged toy oracle)
**merged in** (merge commit `3a9fadd`; the only conflict was `.agents/evidence/verification.json`,
resolved to main's copy and regenerated at the end).

## What exists (inspected, not assumed)

| Piece | Where | Relevant facts |
|---|---|---|
| Env interface | `environment/toy_pong.py` | Gymnasium API, `reset(seed)->(obs, info)`, `step->(obs, r, terminated, truncated, info)`, `Discrete(3)` = NOOP/LEFT/RIGHT, 64x48 RGB, `info == {}`, internals underscore-private. |
| Pixel pipeline | `environment/preprocessing.py` | `PreprocessingWrapper`: RGB -> gray -> bilinear resize -> `(4, S, S)` float32 stack. Game-agnostic. |
| Vector env | `environment/vec.py` | `SyncVectorEnv`, no autoreset, factories "expected homogeneous" (not verified). Heterogeneous factories with equal obs shape / action count already work mechanically. |
| Agent | `agent/model.py` | Nature-CNN -> Linear-256 -> GRU-128 -> actor/critic. `frame_size` is a constructor arg. Game-independent. |
| PPO | `training/ppo.py` | Recurrent PPO, timeout-aware GAE, single path (`num_envs=1`, legacy, byte-identical guarded by `tests/test_vec_ppo_equivalence.py`) and vector path (`_collect_vector`). `info` is discarded by both rollouts. |
| Eval | `training/evaluate.py` | Separate env per episode, fixed seeds, greedy; hit/miss only for declared `sign` semantics. |
| Oracle | `training/toy_oracle.py` (PR #29) | Privileged toy-Pong oracle (reads `_bx`, `_paddle_x`), labelled upper bound. |
| Tests | `tests/` | Baseline measured below. |

## Scope (locked)

In scope, smallest changes, additive only:

1. **Common game contract** (`environment/arcade.py`, new): shared action set `("NOOP","LEFT","RIGHT")`
   (exactly toy Pong's, so `ToyPongEnv` is untouched), raw 64x48 uint8 RGB frames, `info == {}`,
   terminated (game over / level cleared) vs truncated (`max_steps`) never both true, float rewards
   in [-1, +1]. A `check_game_env()` validator and a `GAMES` registry + `make_game_factory()` that
   returns the standard `PreprocessingWrapper` factory. Extends the existing Gymnasium contract; does
   not replace it.
2. **Games** (pure numpy, in-repo, procedural per-episode variation drawn from the env's seeded RNG:
   palette, object sizes, speeds):
   - train: `pong` (existing `ToyPongEnv` dynamics via a subclass that adds palette + speed/paddle
     variation; base class unchanged), `catch` (falling fruit, +1 catch / -1 miss ends),
     `breakout` (bricks, +1 per brick, -1 miss ends, clearing the wall terminates = completion).
   - held out (never trained on by the multi-game agent): `volley` -- a gravity ball (parabolic arcs,
     random wind), different shapes/palette, +1 per bounce, -1 miss ends. Shares the "intercept a
     moving object with a bottom paddle" skill with the training games but not their visuals or
     dynamics. This is a *near* transfer test and will be reported as such.
   - far held-out (zero-shot eval only, negative control): `dodge` -- avoid falling blocks
     (+1 per block that passes the paddle row without touching it, -1 on contact ends), same reward
     shape but the opposite objective; expected to NOT transfer zero-shot.
   ALE is out: no ROMs/pip on this box and it would not fit the compute.
3. **Pixel-only guarantee test**: obs fed to the model == fresh `FrameStack` replay of `env.render()`
   frames (pure function of pixels); hidden state mutations that do not change pixels do not change
   obs; PPO rollout buffers are identical when an env injects secret `info` fields; every registered
   game returns `info == {}`.
4. **Multi-game training**: no PPO change needed -- `SyncVectorEnv` over heterogeneous game factories
   (round-robin slot assignment, one slot per game). Model and PPO are game-agnostic; no game rule is
   hardcoded. Only change: `SyncVectorEnv` gains an opt-in check that slot obs shapes / action counts
   agree (default behaviour unchanged).
5. **Runner** `training/generalization.py` (new): train / eval / baselines / gif / aggregate, writes
   metrics JSON+CSV, checkpoints only under `/workspace/work/uga-generalization/artifacts`
   (user machine: any path outside the repo, or gitignored `checkpoints/`).
6. **Baselines**: uniform random, constant action (each of 3, best reported), sticky random
   (repeat p=0.8), per-game specialist PPO, privileged per-game oracle (labelled upper bound, reads
   internals, never on the learning path).

Out of scope: browser/Arcade work (issue #15), ALE, PPO algorithm changes, LR annealing, curiosity,
external-window path, README rewrite beyond a pointer. No `.pt` in git.

## Compute budget (scaled down -- stated, not hidden)

Sandbox: 2 vCPU shared with other jobs (load avg ~7), ~0.8-1.3 GB RAM free, no GPU. Measured
in-loop PPO throughput at the legacy 84x84 frame: ~45-55 steps/s; at 48x48 with 1 torch thread:
~80-95 steps/s (torch import alone costs ~30 s on this filesystem). So:

- Frame size **48** (model `frame_size: 48`, all arms identical); all other PPO hyper-parameters are
  the repo defaults (rollout 128, minibatch 32, 4 epochs, lr 2.5e-4, ent 0.01), `num_envs: 3`,
  `torch.set_num_threads(1)`, one training process at a time, chunked + resumable.
- Specialist PPO: 3 games x 3 seeds x **~40k** steps (39,936).
- Multi-game agent: 3 seeds x **~120k** steps (119,808 = 3 x specialist, i.e. the same per-game
  sample count as a specialist).
- Held-out `volley`: fine-tune-from-multi vs from-scratch, 3 seeds x **~30k** steps each (30,720),
  eval curve every 7,680 steps (0 / 7.7k / 15.4k / 23k / 30.7k).
- Eval: 20 fixed held-out-seed episodes per (policy, game), greedy; baselines 20 episodes x 3 seeds.

Budget amendment (made before any training run started): the first draft of this plan had
20k / 60k / 15k. A smoke run then measured ~165-230 steps/s in-loop at 48x48 once the box's other
load dropped, so every budget was doubled (`config.yaml`) to give PPO a fairer chance. Total
~900k PPO steps.

Roughly 1.5-3 h wall-clock here. If throughput collapses, cut the specialists to 2 seeds first and
say so. Run order puts the held-out transfer arms (multi -> scratch -> finetune per seed) before
the specialists so the headline comparison lands first. These budgets are pilot-scale: a null result at this
budget is not evidence that the approach cannot work. PowerShell commands for larger budgets go in
RESULTS.md.

## Metrics

Per (arm, game): mean +/- std over seeds of the per-seed mean episode reward (20 eval episodes),
mean episode length, survival-to-truncation rate, breakout wall-clear rate, positive-reward events
per episode. Held-out: zero-shot (multi-game agent and each specialist) vs random; transfer =
fine-tune vs scratch eval reward at each budget point and area under that curve. Runtime and
steps/s per run, hardware string.

## Acceptance (what may be claimed)

- "Generalizes zero-shot" only if the multi-game agent's held-out mean beats random by more than the
  across-seed std on all 3 seeds.
- "Transfers" only if fine-tune beats scratch at equal steps on >= 2 of 3 seeds and on the mean, with
  the numbers in RESULTS.md. Otherwise RESULTS.md says transfer was not shown.
- Never "general-purpose gameplay".

## Guardrails

- `num_envs=1` legacy path untouched; equivalence tests stay green.
- No SendInput / no window. Tests: `python -m unittest discover -s tests` from
  `universal-game-agent/`; `ruff check .` from repo root; evidence regenerated with
  `python tools/evidence/generate.py universal-game-agent` then `python tools/evidence/check.py`.
- GIFs < 1 MB each committed under `experiments/generalization/gifs/`.

## Baseline measured before edits (step 1)

- Suite (venv torch 2.14.1+cpu, numpy 2.5.3, Python 3, Linux): `Ran 511 tests ... OK (skipped=3)`,
  86 s wall. No pre-existing failures.
- Determinism: see RESULTS.md (two identical seeded toy-Pong runs, checkpoint hashes compared).
