# Universal Game Agent

General-purpose RL agent that learns to play arbitrary games **from pixels**,
acting through **keyboard/mouse/controller output**. Stage 0: scaffold only —
no neural network, PPO, curiosity, or external game control is implemented.

## Information boundary (non-negotiable)

- **Input:** rendered pixels only (`rgb_array`).
- **Output:** keyboard / mouse / controller actions.
- Learning code **MUST NOT** depend on game-specific internal state
  (no RAM, no APIs, no score hooks on the learning path).

## Intended architecture

```text
pixels -> visual encoder -> temporal memory -> policy/value network
       -> abstract action -> controller -> game
```

| Stage | Goal |
| ----- | ---- |
| 0 | Project structure, config, logging, runnable `main.py`, tests |
| 1 (done) | `environment/toy_pong.py`: pixel-only Pong toy (Gymnasium API, 64x48 RGB, NOOP/LEFT/RIGHT, +1 hit / -1 miss) |
| 2 (done) | `environment/preprocessing.py`: grayscale + 84x84 + [0,1] float32, frame stack, configurable skip, `PreprocessingWrapper` |
| 3 (done) | `agent/model.py`: CNN-GRU actor-critic + `training/ppo.py`: recurrent PPO (GAE, clipped loss, checkpoints). Validated: 30k-step toy run, eval miss rate 40% -> 10% |
| 4 | Temporal memory (frame-stack -> LSTM/Transformer) |
| 5 (done) | `interface/`: WindowManager + Screen/WindowCapture (mss) + ActionMapper (WASD/mouse) + GameInterface; agent sees pixels only |
| 6 (curiosity done) | `training/curiosity.py`: forward-dynamics prediction error + RMS norm + scale, wired into PPO (ext/int logged separately). A/B 30k steps: no-curiosity eval -0.35 (9/20 misses), curiosity eval -0.05 (1/20 misses) |

## Layout

```text
agent/         model.py (CNN-GRU actor-critic, game-independent)
environment/   toy_pong.py (pixel-only Pong toy) + preprocessing.py (84x84 stack pipeline)
interface/     window.py + capture.py + controller.py + adapter.py (GameInterface)
training/      ppo.py (PPO trainer) + logger.py
configs/       default.yaml + loader
tests/         unittest scaffold tests
checkpoints/   saved models (gitignored content)
experiments/   per-run output (gitignored content)
logs/          console+file logs
main.py        entry point
```

## Setup (Python 3.11+)

```bat
cd universal-game-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
python -m unittest discover -s tests -v
```

Demo the toy env (pixels-only policy, 5 episodes):

```bat
python -m environment.toy_pong --episodes 5 --seed 0
```

Demo the vision pipeline (toy frames -> stacked 84x84 obs):

```bat
python -m environment.preprocessing --episodes 3 --seed 0 --skip 2
```

Forward pass on random observations (needs torch):

```bat
python -m agent.model --batch-size 4 --steps 3
```

Short PPO run on the toy game (needs torch):

```bat
python -m training.ppo --total-timesteps 2048 --rollout-length 128 --seed 0
```

## Dependencies (minimal)

torch, gymnasium, stable-baselines3, numpy, pyyaml.
