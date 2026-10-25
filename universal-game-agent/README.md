# Universal Game Agent

Pixel-based reinforcement-learning testbed. The current system trains a
recurrent actor-critic with PPO on a built-in Pong-like toy game, using
rendered pixels only. The long-term goal is a general agent that learns
arbitrary games from screen pixels and acts through keyboard/mouse — that
goal is **not** implemented; see Limitations.

## Information boundary (non-negotiable)

- **Input:** rendered pixels only.
- **Output:** abstract discrete actions (toy game) or OS input events (Windows interface).
- Learning code **MUST NOT** depend on game-specific internal state
  (no RAM, no APIs, no score hooks on the learning path).

## Current pipeline (implemented)

```text
toy pixels -> grayscale/84x84/normalize -> frame stack (4,84,84)
  -> CNN -> feature vector -> GRU -> actor logits + scalar value
  -> action -> toy game -> reward -> PPO update
```

## Training environment: Toy Pong (the only integrated env)

- `environment/toy_pong.py`: Pong-like paddle game, Gymnasium API
  (`reset(seed)` / `step` / `render` / `close`).
- Observation is a 64×48 RGB frame; the agent never sees coordinates,
  velocities, scores, or collision flags (`info` is always `{}`).
- Actions: `NOOP` / `LEFT` / `RIGHT`. Rewards: +1 paddle hit,
  −1 miss (terminates), 0 otherwise, 500-step truncation.
- Preprocessing (`environment/preprocessing.py`): BT.601 grayscale,
  bilinear resize to 84×84, float32 [0,1], 4-frame stack,
  configurable frame skip with action repeat + max-pool
  (`PreprocessingWrapper`).

## Model architecture (`agent/model.py`)

- Small Nature-style CNN (32→64→64 channels) over the stacked frames,
  Linear-256 feature vector, GRU (default 128 units, configurable layers).
- Separate heads: actor logits over the configured action space,
  critic scalar value. All dims configurable
  (`num_actions`, `in_channels`, `frame_size`, `feature_dim`,
  `hidden_size`, `num_layers`).
- API: `forward(obs, hidden)` (single step), `forward_sequence`
  (time-major chunks for PPO), `initial_state(batch)`,
  `save(path)` / `ActorCritic.load(path)`.

## Training algorithm (`training/ppo.py`)

- Custom PPO (no SB3): fixed-length rollouts, timeout-aware GAE (terminations
  mask, truncations bootstrap from the pre-reset obs/hidden), normalized
  advantages, clipped policy objective, clipped value objective, entropy
  bonus, gradient-norm clipping, sequential time-chunk minibatches with
  hidden carry across chunks and resets at episode boundaries.
- The single-environment loop is byte-for-byte the legacy behavior. With
  `ppo.num_envs > 1` the trainer drives a `SyncVectorEnv`
  (`environment/vec.py`: N envs in one process, stepped in order, no
  autoreset, explicit per-slot resets), built by `make_env_for_training`
  (`training/experiment.py`). The vector rollout is `[time, env]` with one
  hidden state per slot: GAE runs independently per slot while advantage
  normalization and the chunk loss pool across slots; boundary semantics are
  the single-path rules applied per slot (terminal step data preserved, only
  finished slots reset via `reset_env`, terminated slots bootstrap to zero,
  truncated slots bootstrap from their own final pre-reset obs/hidden).
- `PPOConfig` holds lr, gamma, GAE lambda, clip range, entropy/value
  coefficients, rollout length, minibatch size, epochs, total steps,
  grad-norm clip, seed, checkpoint settings, and the two knobs below.

## Parallel environments (`ppo.num_envs`)

- `ppo.num_envs` (default 1, declared in `configs/default.yaml`) is read by
  `main.py train`, `python -m training.experiment`, and the module CLI
  `python -m training.ppo --num-envs N`. `num_envs == 1` builds a plain env
  instance — identical to legacy training; `num_envs > 1` wraps that many
  copies of the same env factory in `SyncVectorEnv`. The factory is the
  generic `make_env_from_config` (toy Pong by default); the shipped
  external-window runner (`training.external_experiment`) and the synthetic
  cadence matrix step a single env and neither offers vectorization, so the
  vector path is exercised by the toy tests only.
- `PPOConfig.__post_init__` validates `num_envs >= 1`; in vector mode
  `total_timesteps` must be divisible by `num_envs` or `train()` raises.
  `total_timesteps` counts total samples across slots, so the evaluation
  budget stays unchanged.
- Limits: `SyncVectorEnv` is synchronous and single-process — envs are
  stepped in order, so throughput comes from batching the model's
  forward/learning passes, not from parallel stepping. Vector rollouts
  report no per-update external reward component breakdown (`comp_sums` is
  empty); curiosity works and masks boundary steps via `dones`.

## Per-update CSV telemetry (`ppo.telemetry_path`)

- Default unset (or empty string) is disabled: no file, no extra work,
  byte-for-byte legacy output. Set `ppo.telemetry_path` under `ppo:` in a
  config and `PPOTrainer.train` appends one CSV row per completed update
  through `PPOUpdateCSVWriter` (`training/telemetry.py`), taken straight
  from the history row — no duplicated metric math, and nothing reads the
  file back.
- Columns are fixed by `FIELDNAMES`: the 18 scalar per-update history
  series (`timesteps`, `fps`, `policy_loss`, `value_loss`, `entropy`,
  `mean_reward`, `mean_ext_reward`, `mean_int_reward`, `predictor_loss`,
  `pixel_change`, `episodes`, `upd_episodes`, `upd_mean_length`,
  `upd_terminated`, `upd_truncated`, `upd_mean_ext`, `upd_mean_int`,
  `upd_mean_total`). The two dict series (`components`, `upd_action_share`)
  have no CSV encoding and are excluded. Column order is an append-only
  contract: added columns go at the end, never reorder or remove.
- Files are written at the exact configured path: open in append mode only,
  so existing rows are never truncated; the header is written once, when
  the file is created or empty; missing parent directories are created.
  Re-running with the same path appends to the previous file — deduplicate
  externally. A run with zero updates (e.g. a resumed checkpoint that
  already met the budget) still writes a header-only file.
- A relative path resolves against the process working directory, so
  configs should prefer an absolute path. `ppo.telemetry_path` has no CLI
  flag on `python -m training.ppo`; it is a config-only switch.

## Curiosity (`training/curiosity.py`)

- Optional forward-dynamics module: own CNN encoder predicts next
  features from (features, action); MSE is the intrinsic reward,
  normalized by running std, scaled (`scale`, default 0.1), clipped,
  masked to zero at episode boundaries. `total = ext + scale·int`;
  ext/int/total logged separately. Disabled by default
  (`curiosity.enabled: false`); disabled training is byte-for-byte the
  legacy behavior.

## Evaluation (`training/evaluate.py`)

- Separate env instance per episode; greedy argmax (default) or sampled
  actions; fixed seeds; per-episode hidden reset; `torch.no_grad`;
  caller train/eval mode restored. Reports mean/std/min/max reward,
  mean length, per-episode lists. Pure function of (model, env factory);
  never touches training state.

## Checkpoints and resume

`PPOTrainer.save_checkpoint(path)` stores model, optimizer,
timestep/update counters, both configs, and curiosity state.
`PPOTrainer.load_checkpoint(path, env)` restores everything and can
keep training (new episode started; counters preserved). Via CLI:
`main.py train --resume <ckpt> [--timesteps N]` or
`main.py evaluate --checkpoint <ckpt>` (accepts PPO-trainer and
`ActorCritic.save` formats).

## Windows interface (`interface/`, infrastructure only)

- `window.py`: find by title substring, liveness, live geometry,
  best-effort focus, external relaunch. Windows-only (ctypes).
- `capture.py`: `MSSBackend` (needs `mss`) and scripted backend;
  fixed-region or window-following capture returning RGB pixels only.
- `controller.py`: data-driven `ActionMapper` (default table: NOOP,
  WASD, SPACE, SHIFT, mouse buttons, one mouse move) via SendInput,
  plus a recording backend for tests.
- `adapter.py`: `GameInterface(capture, controller, window?)` with
  exactly `capture()` / `execute(action)`.

## Experiment system (`training/experiment.py`, `experiments/`)

- `python -m training.experiment --config experiments/<name>.yaml` runs
  baseline eval → fresh-model training → final eval, writes
  `<name>_results.json` next to the config.
- Shipped configs: `exp_toy_ppo_01.yaml` (seed 0, 30k steps),
  `exp_toy_ppo_02_nocur.yaml` / `exp_toy_ppo_03_cur.yaml` (A/B pair,
  seed 1, 30k steps each).
- External-game experiments: `python -m training.external_experiment
  --config experiments/exp_external_pong_0N.yaml` — three phases against a
  real window with real SendInput keystrokes; **ask before starting a long
  run**. Results: `exp_external_pong_01-14676_results.json`,
  `exp_external_pong_02-15516_results.json`, and
  `exp_external_pong_compare01_results.json` (pre-detector-fix — its
  "rewards survival" note describes a bug fixed in `b77bf4d`, do not read it
  as a learning result).
- Synthetic cadence matrix (no window, no input backend):
  `python -m training.cadence_experiment --config
  experiments/exp_cadence_synthetic.yaml` runs probe policies plus
  baseline/train/eval per timing cell on a synthetic Pong that mirrors the
  measured external cadence, and writes
  `exp_cadence_synthetic_results.json`. Measured results, controlled
  variables, and supported/rejected hypotheses are recorded in
  `AGENTS.md` § "Cadence / reward investigation".

## Toy results (toy environment only)

Reward is +1 per paddle hit and −1 on the miss that ends the episode, so an
episode of 16 steps with reward −1 missed the first ball and an episode of
~58 steps with reward 0 hit the first ball, then missed its return. Every
number below is derived from the `baseline_eval`/`final_eval` episode
rewards and lengths in the named results JSON (20 fixed-seed greedy
episodes, 500-step cap) by `python -m training.toy_oracle`, which writes
`experiments/toy_oracle_results.json`.

- **Every eval episode in every saved toy run ends in a miss** (miss rate
  100%, 0/20 reach the 500-step cap, before and after training). No trained
  policy sustains a rally. Today's honest headline is "learns to hit the
  first ball", not "learns Pong".
- Seed 0, 30k steps, no curiosity (`exp_toy_ppo_01_results.json`):
  first-ball hit rate 60% → 90%; no episode hits a second ball. (The eval
  mean −0.40 → −0.10 is this change; an earlier README line read it as
  "miss rate 40% → 10%", which was wrong.)
- A/B, seed 1, 30k steps each: no-curiosity
  (`exp_toy_ppo_02_nocur_results.json`) first-ball hit rate 55% → 55%
  (one final episode reached 3 hits); curiosity
  (`exp_toy_ppo_03_cur_results.json`) 55% → 95%. One run per arm,
  suggestive only.
- **Upper bound:** a scripted oracle that reads the env's hidden ball and
  paddle positions (`training/toy_oracle.py`, privileged, never used for
  learning) survives all 20 episodes to the 500-step cap with 12 hits each.
- These numbers say nothing about arbitrary games, external-game
  training, or cross-game generalization.

## Limitations (explicitly not implemented)

- The Windows interface is infrastructure: no generic external-game
  reward/objective handling, no generic reset handling, no external-game
  training loop. "Any game" generalization is a future goal.
- PPO vectorization exists but is synchronous and single-process: envs step
  in order inside one `SyncVectorEnv`, so it batches the model's work rather
  than parallelizing the environment. `total_timesteps` must divide evenly
  by `num_envs`; the per-update reward component breakdown is unavailable in
  vector mode; only the toy path gains vectorization, an external-window run
  steps a single env. Modest CPU budgets remain the rule.
- Checkpoints embed config dicts without a schema version.

## Setup (Python 3.11+)

```bat
cd universal-game-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py smoke-test
python -m unittest discover -s tests -v
```

## Commands

`main.py` subcommands (thin wrappers over the modules below):

| Purpose | Command |
|---|---|
| Pipeline smoke test | `python main.py smoke-test [--config CONFIG]` |
| Train (PPO) | `python main.py train [--config CONFIG] [--timesteps N] [--seed S] [--checkpoint-dir DIR] [--resume CKPT]` |
| Evaluate | `python main.py evaluate [--config CONFIG] [--checkpoint CKPT] [--episodes N] [--seed S] [--sampled]` |
| Full experiment | `python main.py experiment --config experiments/exp_toy_ppo_01.yaml` |
| External-window experiment (real keystrokes — ask first) | `python -m training.external_experiment --config experiments/exp_external_pong_02.yaml` |
| Synthetic cadence matrix (no window) | `python -m training.cadence_experiment --config experiments/exp_cadence_synthetic.yaml` |
| Full test suite | `python -m unittest discover -s tests` |
| Toy oracle bound + rally metrics of saved toy runs (privileged, no torch) | `python -m training.toy_oracle --out experiments/toy_oracle_results.json` |
| Toy env demo (pixels-only policy) | `python -m environment.toy_pong --episodes 5 --seed 0` |
| Vision pipeline demo | `python -m environment.preprocessing --episodes 3 --seed 0 --skip 2` |
| Random-obs forward pass (torch) | `python -m agent.model --batch-size 4 --steps 3` |
| Short PPO run, module form (torch) | `python -m training.ppo --total-timesteps 2048 --rollout-length 128 --seed 0 [--num-envs N]` |
| Standalone Pong test game | `python games/extern_pong.py --title ExternPong --seed 0` |
| External-window OS-loop smoke (no learning) | `python -m training.external_smoke --title ExternPongSmoke --steps 50 --mode random` |
| Live reward-detector diagnostic (no input) | `python -m training.reward_diagnostic --launch --title DiagGame --seed 3 --captures 300` |

## Standalone test game (`games/`, not connected to PPO)

`games/extern_pong.py` is an independent tkinter process (own window,
pixels + keyboard only) for testing the external-game boundary. Controls:
Left/Right or A/D move, P pause, R re-serve, Q/Escape quit. Flags:
`--title`, `--seed`, `--fps`, `--auto-quit SEC`. No sockets, files, or
state API cross the process boundary.

## Configuration

`configs/default.yaml` is the truthful baseline: `env` feeds
`make_env_from_config`, `model` feeds `ActorCritic` (plus `num_actions`
from the env), `ppo` feeds `PPOConfig`, `curiosity` feeds
`CuriosityConfig`, `eval` sets eval episodes, `logging` sets log level/dir.
`from_dict` loaders warn on unknown keys instead of silently dropping them.

External games (`env.type: external`, default is `toy`) are built by
`make_external_env_from_config` from `capture` (synthetic|region|window),
`lifecycle`, `actions` (`default` table or explicit `ActionDef` list,
`recording|sendinput` backend), `timing` (delays, step/timeout limits),
`reward` (`null`), `termination` (`never|step_limit`), `num_stack`,
`obs_size`. Live capture additionally requires the explicit safeguard
`allow_live_capture: true`; without it only display-free `synthetic`
capture is built, and no game launches or input can happen from config
alone. `train`/`evaluate`/`smoke-test` select the env through `--config`.

## Dependencies

torch, gymnasium, numpy, pyyaml, mss (screen capture only).
