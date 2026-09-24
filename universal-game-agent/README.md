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

- Custom single-environment PPO (no SB3): fixed-length rollouts,
  timeout-aware GAE (terminations mask, truncations bootstrap from the
  pre-reset obs/hidden), normalized advantages, clipped policy objective,
  clipped value objective, entropy bonus, gradient-norm clipping,
  sequential time-chunk minibatches with hidden carry across chunks and
  resets at episode boundaries.
- `PPOConfig` holds lr, gamma, GAE lambda, clip range, entropy/value
  coefficients, rollout length, minibatch size, epochs, total steps,
  grad-norm clip, seed, checkpoint settings.

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

- `PPOTrainer.save_checkpoint(path)` stores model, optimizer,
  timestep/update counters, both configs, and curiosity state.
- `PPOTrainer.load_checkpoint(path, env)` restores everything and can
  keep training (new episode started; counters preserved). No CLI;
  use from Python.

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

## Toy results (toy environment only)

- Seed 0, 30k steps, no curiosity: eval mean −0.40 → −0.10
  (miss rate 40% → 10%, 20 fixed-seed greedy episodes).
- A/B, seed 1, 30k steps each: no-curiosity −0.45 → −0.35 (9/20 misses);
  curiosity −0.45 → −0.05 (1/20 misses). One run per arm — suggestive,
  not proof that curiosity always helps.
- These numbers say nothing about arbitrary games, external-game
  training, or cross-game generalization.

## Limitations (explicitly not implemented)

- The Windows interface is infrastructure: no generic external-game
  reward/objective handling, no generic reset handling, no external-game
  training loop. "Any game" generalization is a future goal.
- Single-environment PPO (no vectorization); modest CPU budgets only.
- Checkpoints embed config dicts without a schema version.

## Setup (Python 3.11+)

```bat
cd universal-game-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
python -m unittest discover -s tests -v
```

## Commands

| Purpose | Command |
|---|---|
| Sanity check | `python main.py` |
| Full test suite | `python -m unittest discover -s tests` |
| Toy env demo (pixels-only policy) | `python -m environment.toy_pong --episodes 5 --seed 0` |
| Vision pipeline demo | `python -m environment.preprocessing --episodes 3 --seed 0 --skip 2` |
| Random-obs forward pass (torch) | `python -m agent.model --batch-size 4 --steps 3` |
| Short PPO run (torch) | `python -m training.ppo --total-timesteps 2048 --rollout-length 128 --seed 0` |
| Full experiment | `python -m training.experiment --config experiments/exp_toy_ppo_01.yaml` |

`training/evaluate.py` and checkpoint resume (`PPOTrainer.load_checkpoint`)
are Python APIs without CLIs.

## Configuration

`configs/default.yaml` is the truthful baseline: `env` feeds
`make_env_from_config`, `model` feeds `ActorCritic` (plus `num_actions`
from the env), `ppo` feeds `PPOConfig`, `curiosity` feeds
`CuriosityConfig`, `eval` sets eval episodes, `logging` sets log level/dir.
`PPOConfig.from_dict` / `CuriosityConfig.from_dict` ignore unknown keys.

## Dependencies

torch, gymnasium, numpy, pyyaml, mss (screen capture only).
