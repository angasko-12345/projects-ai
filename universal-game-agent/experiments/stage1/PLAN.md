# Stage 1: real games through one interface -- scope-locked plan

Written 2026-10-26 (Asia/Manila) **before** any product-code edit. Branch
`upgrade/uga-stage1-ale`, cut from `upgrade/uga-generalization` @ `eb432cc` (draft PR #31,
the arcade generalization pilot). This branch is **stacked on PR #31** and must merge after it.

## What exists (inspected, not assumed)

| Piece | Where | Fact that constrains this work |
|---|---|---|
| Game contract | `environment/arcade.py` | `Discrete(3)` NOOP/LEFT/RIGHT, raw RGB frames, `info == {}`, terminated vs truncated exclusive. |
| Pixel pipeline | `environment/preprocessing.py` | `PreprocessingWrapper`: action repeat `skip` with max-pool of the last two raw frames, BT.601 gray (float64), bilinear resize, `(num_stack, S, S)` float32 in [0,1]. ~1.4 ms per 210x160 frame here. Must stay byte-identical (legacy). |
| Vector env | `environment/vec.py` | `SyncVectorEnv`, **no autoreset**; caller resets finished slots with `reset_env(i)`. The PPO trainer detects it by `isinstance`. |
| Model | `agent/model.py` | Nature-CNN -> 256 -> GRU-128 -> actor(`num_actions`)/critic. `num_actions` is stored in `model_config` of every checkpoint. |
| PPO | `training/ppo.py` | Recurrent PPO; minibatches are sequential time chunks of `minibatch_size` steps x all slots; no LR annealing; value clipping uses `clip_range`; Adam eps 1e-8. `num_envs=1` path is byte-identical-guarded. |
| Config factory | `training/experiment.py:make_env_from_config` | `type: toy` (default) or `external`. `make_env_for_training(make_env, n)` builds `SyncVectorEnv([make_env]*n)`. |
| CLI | `main.py train/evaluate` | `train --resume PATH`; periodic `ppo_<steps>.pt` every `checkpoint_every_updates`. |
| Runner | `training/generalization.py` | arms multi / specialist / scratch / finetune (+ zero-shot eval of every run on every game), hard-wired to the arcade registry and `num_actions=3`. |
| ale-py | PyPI 0.12.1 (aarch64 + Windows wheels) | Bundles the ROMs since 0.9 ("Atari roms are packaged within the pip package", https://pypi.org/project/ale-py/ ; ALE release notes https://ale.farama.org/release_notes/). Licence GPL-2.0-only. Ships `AtariVectorEnv` (C++ threads) with autoreset modes `NextStep`/`SameStep`/`Disabled`. |

## Scope (locked)

1. **ALE adapter** `environment/atari_env.py` (new), behind the existing env contract:
   * `AtariGame`: wraps `ale_py.env.AtariEnv` with emulator `frameskip=1` (UGA's
     `PreprocessingWrapper(skip=4)` does action repeat + 2-frame max-pool, as CleanRL's
     `MaxAndSkipEnv` does), `repeat_action_probability` default **0.25** (ALE v5 default,
     Machado et al. 2018; applied per emulator frame like v5), minimal action set,
     `max_num_frames_per_episode` default **108000** (v5 default = 30 min), no-op reset
     (`noop_max` default 30, count drawn from the reset seed). Observation = raw
     `(210,160,3)` uint8 RGB screen; `info` is always `{}` (lives, frame numbers, RAM never
     leave the adapter). `terminated` = game over, `truncated` = frame cap, never both.
     Reward = emulator score delta (raw), allowed as feedback.
   * **Explicit-reset contract**: the single adapter never autoresets; a `step()` after a
     finished episode raises instead of silently continuing. Tested.
   * `RewardTransform` (flag `env.reward_transform`: `none` default | `sign`): sign clipping
     (CleanRL `ClipRewardEnv`) for **training only**; evaluation always reports raw game score.
     Running-return normalisation is NOT implemented (documented as out of scope).
   * Not ported from CleanRL on purpose: `EpisodicLifeEnv` (needs the lives counter as a
     training signal) and `FireResetEnv` (a per-game reset macro). Both are game-specific
     helpers; the agent must learn FIRE itself. This is a known deviation from the anchor.
   * Optional vector backend (flag `env.vector_backend: ale`) over `AtariVectorEnv` only if
     measured useful (see 5); it must present the UGA no-autoreset contract (SameStep mode:
     the terminal obs is returned untouched, `reset_env(i)` hands back the already-reset obs).
   * `env.type: atari` registered in `make_env_from_config`; `requirements-atari.txt`
     (`ale-py>=0.9`) is an optional extra; `requirements.txt` is unchanged.
2. **Universal action space** `environment/universal_actions.py` (new): the **UGA pad** = the
   18 ALE actions in ALE enum order (NOOP, FIRE, UP, RIGHT, LEFT, DOWN, diagonals, +FIRE
   variants) as one fixed controller. Every adapter declares its native legal actions by
   *name* (ALE: `get_action_meanings()` of the minimal set; arcade: `ACTION_MEANINGS`); a
   game-agnostic projection maps each pad action to the native action with the fewest
   mismatched components (horizontal, vertical, fire; opposite direction costs most), ties to
   the lowest native index. A `legal_mask` (pad actions that exist natively) is exposed for
   diagnostics. No per-game logic. `UniversalActionWrapper` (outermost) exposes
   `Discrete(18)` and `uga_action_space = "uga-pad18-v1"`.
   * Opt-in via `env.action_space: universal` (default `native` = legacy, byte-identical).
     Atari configs use `universal`; arcade can too (`experiments/generalization` unchanged).
   * **Checkpoint versioning**: when the env declares `uga_action_space`, the checkpoint gets
     `action_space: "uga-pad18-v1"`; legacy checkpoints have no key (= native). Loading a
     checkpoint into an env with a different declared action space raises a clear error.
     Old checkpoints stay loadable unchanged. No mask in PPO (unchanged algorithm); aliased
     pad actions are redundant, not illegal.
3. **Pixel-only test for Atari**: model input == a fresh `FrameStack` replay of the
   max-pooled rendered RGB frames; `info == {}` always; RAM / lives never reach the obs.
   Skips cleanly when ale-py is absent.
4. **Configs**: `configs/atari_pong.yaml` (anchor vs CleanRL `ppo_atari.py`,
   PongNoFrameskip-v4 20.36 +/- 0.20 at 10M steps, 3 seeds; hyperparameters from the
   source: 8 envs x 128 steps, 4 epochs, 4 minibatches, lr 2.5e-4 annealed, clip 0.1,
   ent 0.01, vf 0.5, gamma 0.99, lambda 0.95, max-grad 0.5; NoFrameskip-v4 = **no sticky
   actions**). Our anchor mirrors those knobs where UGA has them (sticky 0, clip 0.1,
   8x128, minibatch 32 steps x 8 slots = 256 samples) and lists the rest as deviations.
   `experiments/stage1/atari_pilot.yaml`: train **Pong, Breakout, SpaceInvaders**; near
   held-out **DemonAttack**; far held-out **Freeway**; arms reuse
   `training/generalization.py` via a `suite` section. Choice of games:
   * Train set = three canonical ALE games whose minimal sets are subsets of
     {NOOP, FIRE, LEFT, RIGHT, LEFTFIRE, RIGHTFIRE} (horizontal mover +- fire), with
     different objectives (rally, brick clearing, shooting).
   * DemonAttack: same minimal set as SpaceInvaders/Pong but new visuals and enemy
     dynamics -> near transfer with common controls.
   * Freeway: minimal set {NOOP, UP, DOWN}: vertical control never exercised in training,
     dense-ish reward, random ~0 -> far transfer / negative control, like `dodge`.
   * Boxing was considered and rejected as held-out: it uses all 18 pad actions, mixing
     "new controls" and "new game" in one number.
5. **Throughput**: env-only steps/s of the adapter (1 env, N envs via `SyncVectorEnv`,
   `AtariVectorEnv` if installed) and PPO training steps/s, written to
   `experiments/stage1/throughput.json` with hardware.
6. **Bounded runs here** (2 vCPU / 8 GB, one process at a time, torch threads <= 2):
   smoke tests; random (uniform over the UGA pad) + constant-NOOP baselines on all 5 games,
   10 seeded episodes, step-capped; a short Pong PPO sanity run (target 100-200k steps if
   it fits ~1-2 h). NOT run here: the 10M anchor or the full pilot; PowerShell commands
   with wall-clock estimates are written for the user's PC instead.
7. `experiments/stage1/RESULTS.md` with every number, seed, sps, hardware, limitation and
   failure. GIF only if Pillow is present and < 1 MB.
8. Full regression suite, evidence regeneration, `ruff check .`, push, draft PR, CI green.

## Invariants (tests must keep proving these)

* `num_envs=1` legacy training is byte-identical (`tests/test_vec_ppo_equivalence.py` etc.).
* No new import of ale-py / Pillow at module scope in any non-Atari path; CI without
  ale-py skips every ALE test cleanly (and an optional CI job installs ale-py).
* Model input is a pure function of rendered frames.
* No `.pt` committed; artifacts outside git.

## Out of scope

Running-return reward normalisation, action masking inside PPO, LR annealing, EpisodicLife /
FireReset, envpool, GPU, the external-window path (no SendInput is executed by anything here).
