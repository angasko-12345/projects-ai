# universal-game-agent product instructions

Local instructions for the `universal-game-agent/` package. The repository-wide contract
lives in `.agents/AGENTS.md` and outranks this file on process, roles, and collaboration.
Where this file and `.agents/AGENTS.md` disagree on this product's facts, this file is
correct.

## Identity

- A reinforcement-learning agent that plays Pong. The standard path is a Gymnasium toy
  environment; the experimental path plays a real external Pong window on Windows through
  screen capture and real keyboard input.
- The repository root is a container. This package is one of two products here; see
  `agentops/AGENTS.md` for the other. Neither is a subproject of the other, and a change in
  one is not a change in the other.
- Runtime dependencies are not standard-library only: `requirements.txt` lists `torch`,
  `gymnasium`, `numpy`, `pyyaml`, and `mss`, all unpinned. This differs from the other
  product in this repository.
- The external game path is **Windows-only by construction**: it depends on `ctypes`
  window management, `SendInput` keyboard injection, and MSS screen capture. MSS is not the
  only backend, though: `environment/external_game.py:363` also accepts `synthetic`, which
  needs no MSS and no window. Do not assume the path runs on another platform, and do not
  write tests that require a real window.

## Tests

Run from `universal-game-agent/`, never from the repository root:

```bat
cd universal-game-agent
python -m unittest discover -s tests
```

- 20 test modules, 261 tests, all `unittest.TestCase`, no skips. That count was observed at
  commit `a03e907` on 2026-09-26 and is a dated observation, not a contract. The count is
  volatile because tests get added, so it goes stale; run the suite for current truth.
  `tests/__init__.py` exists so discovery works as a package. The tests import `configs`,
  `training`, and `games` because discovery runs with `universal-game-agent/` as the working
  directory — no test bootstraps `sys.path` itself. Run the suite from that directory.
- `.agents/AGENTS.md` defines no single repository-wide test command. The per-product command
  for this product is its `universal-game-agent/` row, and it must be run from that
  directory. Use the command above.
- Torch-dependent test files gate on `try: import torch` and skip cleanly when torch is
  absent. An all-skipped suite is not a pass.
- There is no configured lint, formatter, type-check, or coverage command. Do not invent
  one.
- `__file__`-based resolution appears in only 4 of the 20 test modules:
  `tests/test_cli.py:10`, `tests/test_eval.py:50`, `tests/test_extern_pong.py:9`, and
  `tests/test_scaffold.py:7`. The other 16 do no filesystem access at all. The
  CWD-relative comparisons in `tests/test_external_experiment.py:28,31,33` compare relative
  string literals against values built purely by string construction
  (`training/external_experiment.py:112,118`); nothing is read from disk, so they are
  CWD-independent, not CWD-fragile. The hazard is prospective: a new test that hardcodes a
  relative path or reads from the CWD passes under any CWD by accident. Do not add more.

## CLI

```bat
python main.py <smoke-test|train|evaluate|experiment> --config <path>
```

`main.py` builds its environment through `training.experiment.make_env_from_config`, which
is the **toy** path. The external pipeline is run through
`python -m training.external_experiment --config <path>`, as recorded in the header of
`experiments/exp_external_pong_01.yaml`. Do not route external runs through `main.py
train`.

## Layering

The dependency direction is deliberate. Preserve it.

- `interface/` is the pure OS layer. It imports nothing from `environment/`, `training/`,
  or `agent/`.
- `environment/` never imports `training/` or `interface/` at module scope. The inversion
  is intentional: `environment/external_game.py` imports `interface` *inside*
  `make_external_env_from_config`, at function scope.
- `games/` imports nothing from the agent. `games/pong_logic.py` duplicates the pure logic
  in `environment/toy_pong.py` on purpose, so the external boundary can be tested against a
  real process without the agent in the loop. That near-duplicate is justified; do not
  "deduplicate" it.
- `agent/`, `environment/`, `interface/`, and `training/` re-export through lazy
  `__getattr__` in their `__init__.py` so torch stays out of import paths. Keep that
  pattern when adding exports.

## Configuration

- `configs/default.yaml` is the single baseline. `configs.load_config` is a bare
  `yaml.safe_load` with a top-level-mapping check: **there is no inheritance or `extends`
  mechanism.** Compose configs by copying.
- Experiment configs live in `experiments/`. The `model:` block is byte-identical across all
  five YAMLs, and nine `ppo:` keys are identical everywhere: `rollout_length`,
  `minibatch_size`, `learning_rate`, `gamma`, `gae_lambda`, `clip_range`, `entropy_coef`,
  `value_coef`, `max_grad_norm`. The remaining `ppo:` keys vary per config:
  `total_timesteps`, `update_epochs`, `seed`, `checkpoint_dir`, and
  `checkpoint_every_updates`. This is known duplication, not an endorsed pattern.
- `experiments/*_results.json` are tracked. New result files are not ignored, so a fresh
  run always shows up in `git status`. That is intentional: results are evidence.
- `checkpoint_every_updates` varies across configs (10, 50, 1000, 1000, 1000). Three of the
  1000s are functionally equivalent to disabled, because the guard at
  `training/ppo.py:376` (`if self.num_updates % cfg.checkpoint_every_updates == 0`) is
  never reached: `exp_toy_ppo_02_nocur` and `exp_toy_ppo_03_cur` finish at 234 updates
  (30000/128) and `exp_external_pong_01` at 8 (1024/128). This is undocumented drift.
- `checkpoint_dir` differs per config (`checkpoints`, `checkpoints/exp02_nocur`,
  `checkpoints/exp03_cur`, `checkpoints/extern_pong_01`). Exactly two trained
  `ppo_final.pt` files exist: `checkpoints/ppo_final.pt` and
  `checkpoints/extern_pong_01/ppo_final.pt`, which is what `checkpoints/README.md:8`
  records. The third `.pt` is `ppo_untrained.pt`, which is untrained. They are not
  interchangeable; identify a checkpoint by directory, never by filename alone.
- `configs/default.yaml` declares a `logging:` block, but no production path calls
  `training.logger.setup_logging`. That config key and that module are both dead. Do not
  rely on either without wiring it up first.

## Checkpoints

- `checkpoints/` holds trained PPO weights and is **gitignored on purpose**. Never commit a
  `.pt` file. The ignore rules live in `.gitignore` in this directory and use `**` so
  nested run directories are covered.
- `checkpoints/README.md` records the two documented checkpoints, which config produced
  them, and how to regenerate. Read it before touching anything in `checkpoints/`.
- The external-game weights in `checkpoints/extern_pong_01/` are the only copy and are not
  cheaply reproducible: every training step performs a live window capture and sends real
  key events, with a fixed 60 ms key hold plus 80 ms post-action delay per step. Treat any
  regeneration as an hours-long operation, not a command.

## Conventions

- Keep verification and reward logic deterministic and testable without a live window.
  Agent-reported success is not evidence; a passed test or a results JSON is.
- Use explicit argument arrays and never a shell.
- Do not persist prompts, secrets, credentials, tokens, or private keys. Reuse existing
  redaction mechanisms.
- On Windows, avoid raw Unicode writes that fail in legacy console encodings. This product
  has no no-console-window spawn helper and no `as_posix()` boundary rule; both are
  `agentops`-only and are stated in `agentops/AGENTS.md`.

## Known defects

Recorded so they are not rediscovered as if new. Fix them deliberately, not incidentally.

- `training/external_experiment.py` has **no tests for its orchestration**. Only the
  helpers (`_unique_title`, `_run_checkpoint_dir`, `_results_path`, `_apply_run_title`) are
  covered. The three-phase baseline -> train -> eval driver is untested, and its per-run
  isolation helpers have never completed a run end to end.
- `training/external_experiment.py:46-60` and `training/external_smoke.py:46-51` overlap —
  21 lines total, and they are not byte-identical and not equivalent.
  `external_experiment.launch_game` opens a log file and sets `close_fds=True` (`:50-57`)
  that `stop()` must release, while `external_smoke.py:46-51` is a bare
  `Popen(..., DEVNULL, DEVNULL)`. The attach-retry is inlined at
  `external_smoke.py:160-170` and lives in `external_experiment.py:63-88` as
  `wait_attach`/`stop`. A shared session helper would collapse it.
- `run_experiment` is defined twice with different meanings and different signatures:
  `training/ppo.py:383` is a toy-PPO CLI helper, `training/experiment.py:66` is the real
  YAML runner. Two same-named entry points with different contracts is a live trap for
  readers and agents. Rename one; do not merge them.
- `environment/external_game.py:369,414-426` hardcodes `extern_pong` as a first-class
  reward and termination provider, even though the module docstring at `:337` asserts that
  no game is hardcoded and nothing there names a specific game. Meanwhile
  `environment/reward.py:227` rejects `extern_pong` in `make_reward_from_config`, which the
  factory bypasses. The two disagree, and the game-specific code sits in the wrong layer.
- `games/` has no `__init__.py`. It works as an implicit namespace package, but
  `games/extern_pong.py:17-19` additionally mutates `sys.path` and uses a flat
  `from pong_logic import ...`, so the same module is importable under two names.
- Two resizers exist with no shared owner: `interface/capture.py:resize_rgb` and
  `environment/preprocessing.py:_resize_bilinear`.
- The virtual-key action mapping is defined in multiple places with no shared constant:
  decimal `vk: 37` in `experiments/exp_external_pong_01.yaml:23-24`, hex `0x25` in
  `training/external_smoke.py:37`, the hex name-to-code table at
  `interface/controller.py:20`, and the default action table built in
  `environment/external_game.py:_build_action_table`. `controller.py:20` is the generic
  `VK` name-to-code table, not an action table. Editing the mapping in one place will
  silently break the others.
- When `env.reward.provider` is `extern_pong`, every other key in the `reward:` mapping is
  discarded and `ExternPongReward()` is built with defaults. The thresholds in
  `environment/extern_pong_rewards.py` are constructor-only and have no YAML surface, and
  no test asserts that the other keys are ignored.
- `main.py` smoke-test builds a second, separate `ToyPongEnv` and `FrameStack` purely to
  print pipeline lines, so it never exercises the external path even when
  `env.type: external`.
- `logs/universal-game-agent.log` and the `extern_pong_*_phase*.log` files are stale: they
  reference a project path from before the directory moved and a `stable-baselines3`
  dependency check that `main.py` no longer performs. The zero-byte phase logs are still
  useful as a record of which phase of which run died; read them before deleting.
