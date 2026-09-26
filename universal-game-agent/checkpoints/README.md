# checkpoints/

Trained PPO weights. These files are intentionally not tracked in git: they are
large binary artifacts, and reproducing a training run is expensive. Committing
them would add tens of megabytes of opaque binary to history that cannot be
reviewed, diffed, or reverted meaningfully.

The two trained checkpoints documented below are both named `ppo_final.pt`, so
identify them by directory. Other `.pt` files may appear here from ad-hoc runs;
they are ignored by the same rules.

## checkpoints/extern_pong_01/ppo_final.pt

- 12378159 bytes (11.8 MB)
- PPO weights for the external game: the agent observes a live external window
  via MSS screen capture and acts with real keyboard input via Windows
  `SendInput`. The agent never imports the game.
- Produced by config `experiments/exp_external_pong_01.yaml`
  (`checkpoint_dir: "checkpoints/extern_pong_01"`).
- Reported on by `experiments/exp_external_pong_compare01_results.json`, which
  names this exact path as the trained checkpoint. That report records 1024
  training steps and 8 updates, and states that both the trained and untrained
  policies hit the 200-step truncation cap every episode, so the reported mean
  rewards (10.83 vs 11.83) do not separate the two policies.
- This is the only copy. It is not cheaply reproducible: every training step
  performs a live window capture and sends real key events, and each step pays
  a fixed cost of 60 ms key hold plus 80 ms post-action delay (see
  `env.timing` in the config). The toy environment trains at roughly 105 fps;
  this one runs orders of magnitude slower because the game window must be
  driven in real time.

## checkpoints/ppo_final.pt

- 2189359 bytes (2.1 MB)
- PPO weights for the toy Pong baseline, written by
  `experiments/exp_toy_ppo_01.yaml` (`checkpoint_dir: "checkpoints"`).
- Loadable for evaluation with:

  ```
  python main.py evaluate --checkpoint checkpoints/ppo_final.pt
  ```

## Git handling

These files are gitignored on purpose and must not be committed. The ignore
rules live in `universal-game-agent/.gitignore` and use `**` patterns so that
nested run directories such as `extern_pong_01/` are covered, not just files
placed directly in `checkpoints/`.

`checkpoints/.gitkeep` is tracked for one reason only: to keep this otherwise
empty directory present in a fresh clone. It is not a placeholder for weights.

## Regenerating

Weights are reproducible in principle, but the external-game run is slow
because it drives a real window in real time.

- External game: `python -m training.external_experiment --config
  experiments/exp_external_pong_01.yaml` (run command recorded in the config
  header).
- Toy environment: `python main.py train --config experiments/exp_toy_ppo_01.yaml`,
  which writes `ppo_final.pt` into the directory named by
  `ppo.checkpoint_dir`.

Regenerating a run overwrites the file at its `checkpoint_dir`, so copy any
weights you want to keep out of this directory first.
