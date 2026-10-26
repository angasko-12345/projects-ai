# Stage 2: multi-game Atari benchmark readiness -- scope-locked plan

Written 2026-10-26 (Asia/Manila) **before** any product-code edit. Branch
`upgrade/uga-stage2-benchmark`, cut from `upgrade/uga-stage1-ale` @ `d3ededa` (draft PR #34,
itself stacked on draft PR #31). Merge order: **#31 -> #34 -> this PR**.

## Inputs (inspected, not assumed)

* Stage 1 (`experiments/stage1/RESULTS.md`): ALE adapter + 18-action UGA pad work and are
  tested; the 150k-step Pong sanity run was **flat** (greedy collapsed to LEFTFIRE, -21);
  PPO trained at 68-74 steps/s on 8 envs under load ~3; env-only ran at 724 (sync) /
  2,877 (ALE C++ vector) decisions/s.
* Listed deviations from CleanRL `ppo_atari.py`: no LR annealing, Adam eps 1e-8, GRU in the
  model, unshuffled sequential minibatches, no EpisodicLife / FireReset, 18-action pad
  instead of the minimal set, bilinear (not area) resize.
* CleanRL reference, read from source (https://docs.cleanrl.dev/rl-algorithms/ppo/ and
  https://github.com/vwxyzjn/cleanrl/blob/master/cleanrl/ppo_atari.py `Args`): 8 envs x 128
  steps, `num_minibatches` 4 (minibatch 256), 4 epochs, lr 2.5e-4 linearly annealed to 0
  (`frac = 1 - (iteration-1)/num_iterations`), Adam eps 1e-5, gamma 0.99, lambda 0.95,
  clip 0.1 (also value clip), ent 0.01, vf 0.5, max-grad 0.5, `norm_adv` per minibatch,
  shuffled flat minibatches, orthogonal init (sqrt 2; actor 0.01; critic 1), Nature CNN ->
  512 -> heads, obs / 255 in-model; wrappers NoopReset(30), MaxAndSkip(4), EpisodicLife,
  FireReset (if FIRE in the minimal set), ClipReward, 84x84 gray, FrameStack(4).
  PongNoFrameskip-v4 at 10M steps: 20.36 +/- 0.20.
* Measured before any change (`profile.json`, label `legacy-anchor-8env`, idle machine,
  load ~0.4): legacy PPO **132.6 steps/s** on 8 envs; 74% of wall time is the update
  (`loss.backward` 51%, `forward_sequence` replay 19%), env stepping 16% (of which UGA
  preprocessing ~1.0 ms vs ALE 4 frames ~0.37 ms per decision), rollout forward 6%.
  Note this is ~1.9x Stage 1's figure: Stage 1 was measured under load ~3, so every
  before/after in Stage 2 is re-measured in one session on the same machine state.
* Measured compute floor (Nature CNN + 512 + head, 2 torch threads, this CPU): fwd+bwd+Adam
  on a 256-sample minibatch = ~0.22 s, so CleanRL's 16 minibatches per 1,024-step update
  cost ~3.5 s of pure CNN math. **A CleanRL-faithful update cannot run faster than
  ~290 steps/s on this CPU even with a free environment**; >=3x over 132.6 (= 400 sps) is
  therefore not reachable here without changing the algorithm. The throughput work targets
  every non-floor cost and the GPU path, and the result is reported against this floor.

## Scope (locked)

1. **Profile** (`experiments/stage2/profile_ppo.py` -> `profile.json`): wall-time split of the
   legacy and the new engine (env step, rollout forward, conversion/bookkeeping, replay
   forward, backward, optimizer).
2. **Fast engine** `training/fast_ppo.py` (new; flag `ppo.engine: fast`, default `legacy`):
   * one batched forward over all slots per step under `torch.inference_mode`; preallocated
     `[T, N, ...]` rollout buffers on the training device; observations stored **uint8**
     (0..255) and normalised in-model; one `np.stack` -> tensor per step (no per-slot copies);
   * recurrent models: batched masked GRU replay over all slots of a minibatch (hidden zeroed
     after each done, exactly the legacy boundary contract) instead of per-slot segment loops;
     minibatches = disjoint slot groups x full rollout (CleanRL `ppo_atari_lstm` scheme),
     shuffled per epoch when `shuffle_minibatches` is on;
   * non-recurrent models: flat shuffled minibatches (CleanRL `ppo_atari`); `shuffle` is
     rejected for recurrent models only in the flat-sample sense (slot groups are shuffled);
   * same termination/truncation semantics as legacy (terminated -> value 0; truncated ->
     bootstrap from the final pre-reset obs; never autoreset);
   * `ppo.torch_threads` (0 = leave torch default), `ppo.device: cpu | cuda | auto`;
   * the legacy `PPOTrainer` and its `num_envs=1` path are **not edited**; byte-identity
     tests stay as they are.
3. **CleanRL-faithful knobs** (fast engine only; legacy rejects non-default values):
   `adam_eps`, `anneal_lr` (+ `anneal_total_timesteps` so chunked runs anneal over the full
   budget and resume continues the schedule), `num_minibatches`, `shuffle_minibatches`,
   `adv_norm: global | minibatch`, `clip_vloss`; telemetry appends `approx_kl`,
   `old_approx_kl`, `clipfrac`, `explained_variance`, `learning_rate`, `mean_game_score`,
   `game_episodes` (+ per-update raw score) **after** the existing CSV columns (legacy CSV
   unchanged). `ppo.preset: cleanrl` fills all of them with the cited values (explicit
   config keys still win).
   * Env: `env.episodic_life` / `env.fire_reset` (default off) inside `AtariGame`. They use the
     ALE's own lives counter and game-over flag **inside the adapter**; only the
     `terminated` flag the trainer sees changes (life loss = training episode end, the game
     continues). Lives never reach the observation or `info`; observations stay pixel-only.
     Evaluation (`train=False`) always plays full games with both off.
   * Raw game score per *full game* is exposed to training telemetry only (adapter
     attribute drained by the trainer), so training curves report real Pong score even
     with sign clipping and episodic life.
   * `env.action_space: native` already gives the minimal action set (Stage 1).
   * Model: `model.recurrent: false` (frame-stack only, no GRU; hidden passes through
     untouched so evaluation code is unchanged), `model.init: orthogonal` (CleanRL init).
   * `env.obs_dtype: uint8` (default `float32` = legacy pixels): same BT.601 gray + bilinear
     resize computed in float32 with cached interpolation tables, rounded to uint8. Pixel
     difference vs legacy <= 1/255 + rounding; documented, tested.
4. **Stage 2 features** (flags, legacy defaults):
   * `model.encoder: nature | impala` (IMPALA-ResNet 16-32-32 as in CleanRL `ppo_procgen`).
   * `ppo.reward_norm: none | per_game`: per-game running std of the discounted return
     (gym `NormalizeReward` logic, one running estimate per game id) so large-score games do
     not dominate multi-game updates. Game ids come from the factory (`atari_game`), never
     from the observation; the model never sees them. State saved in the checkpoint.
   * Multi-game Atari vector env: `training/stage2.py` builds a `SyncVectorEnv` whose slots
     follow a fixed per-game mix (`suite.mix`, default 2 slots per train game).
   * Human-normalised score: `training/hns.py` with random/human reference scores copied from
     DeepMind DQN Zoo `atari_data.py`
     (https://github.com/google-deepmind/dqn_zoo/blob/master/dqn_zoo/atari_data.py;
     30-noop starts, 108k-frame cap). HNS = (score - random) / (human - random).
   * Checkpoint schema: fast-engine checkpoints carry `schema_version: 2` (+ engine, reward
     normaliser state, slot game ids); absent key = v1 (legacy, unchanged). A newer schema
     than the loader knows raises a clear error; v1 checkpoints still load.
5. **Benchmark definition** `experiments/stage2/benchmark.yaml`: 5 train games (Stage 1's
   Pong, Breakout, SpaceInvaders + Seaquest, Qbert), 3 held-out (DemonAttack, Freeway from
   Stage 1 + Asterix), arms random / specialist / multi / zero-shot / fine-tune vs scratch,
   3 seeds, budgets sized for Kaggle T4x2 (12 h sessions, ~30 GPU h/week, 4 CPUs), resume
   across sessions via chunked checkpoints and a wall-clock guard.
6. **Kaggle kit** `experiments/stage2/kaggle/` (script + notebook + README); no Kaggle login.
7. **Bounded sandbox runs** (one process at a time, chunks <= ~20 min, checkpoint/resume):
   (a) Pong 150,528 steps CleanRL-faithful + fast, compared with Stage 1's flat run;
   (b) Pong extended as far as ~2-3 h allows; (c) tiny multi-game smoke (few k steps)
   through multi-game env + per-game normalisation + HNS reporting.
8. `RESULTS.md`, regression suite, evidence regen, `ruff`, push, draft PR, CI green.

## Invariants

* Legacy training (engine `legacy`, every existing config) is byte-identical; every existing
  test stays green unchanged.
* No ale-py / Pillow import at module scope in non-Atari paths; CI without ale-py skips ALE
  tests; CI has no Pillow.
* Model input is a pure function of rendered frames; lives / RAM / game ids never reach it.
* No `.pt` committed; run artifacts outside git (or gitignored `checkpoints/`).

## Out of scope

envpool, PopArt value-head rescaling (per-game return std is the chosen "PopArt-lite"),
multi-GPU data parallel, running the full benchmark here, Kaggle sign-in, the external
window path (no SendInput executed).
