# universal-game-agent product instructions

Local instructions for the `universal-game-agent/` package. The repository-wide contract
lives in `.agents/AGENTS.md` and outranks this file on process, roles, and collaboration.
Where this file and `.agents/AGENTS.md` disagree on this product's facts, this file is
correct.

## Identity

- A reinforcement-learning agent that plays Pong. The standard path is a Gymnasium toy
  environment; the experimental path plays a real external Pong window on Windows through
  screen capture and real keyboard input.
- The repository root is a container. This package is one of three products here; see
  `agentops/AGENTS.md` and `small-projects/mini-llm/AGENTS.md` for the others. No product
  is a subproject of another, and a change in one is not a change in another.
- Runtime dependencies are not standard-library only: `requirements.txt` lists `torch`,
  `gymnasium`, `numpy`, `pyyaml`, and `mss`, all unpinned. This differs from the other
  products in this repository.
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

- The current measured result for this product lives in
  `.agents/evidence/verification.json` (`product: "universal-game-agent"`), not in
  this file. Regenerate it with `python tools/evidence/generate.py
  universal-game-agent` from the repository root and check it with `python
  tools/evidence/check.py`. Run the suite for current truth. All tests are
  `unittest.TestCase`, and
  `tests/__init__.py` exists so discovery works as a package. Every test module imports
  `tests/_bootstrap.py` first, which puts `universal-game-agent/` on `sys.path`, so the
  command above and direct execution (`python tests/<file>.py`, from any working
  directory) run the same tests. Discovery from this directory stays the source of
  truth; direct execution is a convenience.
- **Bug state: no ACTIVE canonical roots for this product.** The authoritative per-root
  status list is `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` §0 — read a
  root's status there before acting on any bug, and take unfinished work from
  `.agents/pending_tasks.md`. For UGA specifically: ROOT-014 (finished-checkpoint resume),
  ROOT-036 (eval hit/miss semantics, now a **declared contract** rather than an inferred
  metric), ROOT-027 (attach-failure cleanup and results artifact), ROOT-037 (phase-2 leak on
  failure or Ctrl-C), and ROOT-038 (episode-boundary session loss not relaunchable) are
  FIXED; the UGA
  share of ROOT-034 remains PARTIALLY FIXED; ROOT-011 is CONTRACT GAP / DOCUMENTED and
  ROOT-026 is CONTRACT GAP / ACCEPTED DEBT, neither of which is a defect to fix on sight.
  ROOT-033 is an AgentOps-only root and does not belong in this product's bug list.
  ROOT-015..018 and ROOT-028 are DISPROVEN — do not revive the PPO entropy/GAE/GRU/curiosity
  claims.
- **Real open UGA work** (from `.agents/pending_tasks.md`): the STEP-3 exp02 re-run has
  still NOT been run — its pre-flight passed 2026-10-02 and the run was then cancelled
  because the machine was in use, so the exp02 verdict stays suspended. **Ask before
  starting it:** a long GUI run sends real `SendInput` keystrokes and holds a real window
  for three phases. The three-phase `training/external_experiment.py` orchestration is
  no longer open: it is covered end to end by fakes (see Known defects). Still open: the
  UGA share of ROOT-034.
- `.agents/AGENTS.md` defines no single repository-wide test command. The per-product command
  for this product is its `universal-game-agent/` row, and it must be run from that
  directory. Use the command above.
- Torch-dependent test files gate on `try: import torch` and skip cleanly when torch is
  absent. An all-skipped suite is not a pass.
- There is no configured lint, formatter, type-check, or coverage command. Do not invent
  one.
- Almost every test module resolves paths from `__file__` via `tests/_bootstrap.py`, so
  the suite is CWD-independent by construction; `tests/test_cwd_isolation.py` is the
  standing guard. The CWD-relative comparisons in `TestRunIsolation`
  (`tests/test_external_experiment.py:33,38,43`) look fragile but are not: they compare
  relative string literals against values built purely by string construction
  (`training/external_experiment.py:242,294,300`) and nothing is read from disk. The
  hazard is prospective — a new test that hardcodes a relative path or reads from the CWD
  passes under any CWD by accident. Do not add more, and re-derive this paragraph from
  `Select-String -Path tests/test_*.py -Pattern '__file__|os\.getcwd|mkdtemp\(cwd'` before
  repeating it.

## CLI

```bat
python main.py <smoke-test|train|evaluate|experiment> --config <path>
```

`main.py` builds its environment through `training.experiment.make_env_from_config`, which
is the **toy** path. The external pipeline is run through
`python -m training.external_experiment --config <path>`, as recorded in the header of
`experiments/exp_external_pong_01.yaml`. Do not route external runs through `main.py
train`. The synthetic cadence matrix needs no window or input backend and runs through
`python -m training.cadence_experiment --config experiments/exp_cadence_synthetic.yaml`
(see "Cadence / reward investigation" below).

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
  six YAMLs, and nine `ppo:` keys are identical everywhere: `rollout_length`,
  `minibatch_size`, `learning_rate`, `gamma`, `gae_lambda`, `clip_range`, `entropy_coef`,
  `value_coef`, `max_grad_norm`. The remaining `ppo:` keys vary per config:
  `total_timesteps`, `update_epochs`, `seed`, `checkpoint_dir`, and
  `checkpoint_every_updates`. This is known duplication, not an endorsed pattern.
- `experiments/*_results.json` are tracked. New result files are not ignored, so a fresh
  run always shows up in `git status`. That is intentional: results are evidence.
- `checkpoint_every_updates` varies across configs (10, 50, 1000, 1000, 1000, 0). It is now
  validated as a non-negative int on config construction (`PPOConfig.__post_init__`), and
  `0` disables periodic writes while `train()` always writes `ppo_final.pt`. The old claim
  that a `1000` interval silently "never fires" described the pre-validation behaviour and
  is stale; intervals above the run length now simply produce no periodic checkpoint.
- `checkpoint_dir` differs per config (`checkpoints`, `checkpoints/exp02_nocur`,
  `checkpoints/exp03_cur`, `checkpoints/extern_pong_01`, `checkpoints/extern_pong_02`,
  `checkpoints/synthetic_cadence`). Exactly two *documented* trained `ppo_final.pt` files
  exist: `checkpoints/ppo_final.pt` and
  `checkpoints/extern_pong_01/ppo_final.pt`, which is what `checkpoints/README.md:8`
  records. Run-isolated `run-<pid>/` subdirectories and the four
  `checkpoints/synthetic_cadence/<cell>/` files written by the cadence matrix are
  regenerable in minutes and are deliberately undocumented (the third top-level `.pt`,
  `ppo_untrained.pt`, is untrained). They are not
  interchangeable; identify a checkpoint by directory, never by filename alone.
- `configs/default.yaml` declares a `logging:` block, but no production path calls
  `training.logger.setup_logging`. That config key and that module are both dead. Do not
  rely on either without wiring it up first.

## Cadence / reward investigation (2026-10-05, synthetic — no window, no SendInput)

Run it: `python -m training.cadence_experiment --config experiments/exp_cadence_synthetic.yaml`.
Tracked evidence: `experiments/exp_cadence_synthetic_results.json`.

Controls: a synthetic Pong session on a 60 fps virtual clock mirrors the external game's
timing model (per-decision key hold then release, red-ball hit latch, 1.0 s MISS banner,
auto re-serve) and is driven through the REAL `ExternalGameEnv`, `ExternPongReward`, and
`ExternPongTermination` with `env.timing.post_action_delay_ms: 0`, so the matrix owns the
decision period outright. The `model:` block and all nine shared `ppo:` keys are
byte-identical to exp01/exp02; only two knobs vary, across four cells:

| cell (artifact name) | decision period | key hold |
|---|---|---|
| current (matches exp01/02) | 147.6 ms | 60 ms |
| fast | 16.667 ms | 16.667 ms |
| current period + short hold | 147.6 ms | 16.667 ms |
| medium | 33.333 ms | 16.667 ms |

Per cell: six probe policies (lookahead oracle, reactive oracle, random, constant left,
constant right, no-op) for reward discrimination, then baseline eval → 16384-step PPO
(5 updates, seed 0) → final eval, 10 episodes each.

Findings, hypothesis by hypothesis (all read from the tracked results file):

- **Reward discrimination — SUPPORTED: the reward does discriminate.** At the current cell
  the lookahead oracle survives all 200 steps (10-episode mean +0.1) while random dies at
  5.6 steps (mean −1.0). Protocol asymmetry to know when reading means: `+1` pays only on
  the first hit after a serve, the latch clears only on a re-serve, and only a miss
  re-serves — a sustained rally pays 0, so a perfect policy's multi-episode mean converges
  to ~+0.1, not +1.
- **"compare01 proves the setup cannot learn" — REJECTED as evidence.** That run
  (`timestamp_utc 2026-09-25T12:27Z`) predates detector fix `b77bf4d` (2026-09-26): the
  ~607 px MISS banner was downscaled to 61 px, inside the hit band, so misses paid +1,
  `miss_min` never fired, no episode terminated, and both policies sat at the 200-step
  cap (10.83 vs 11.83 = event counts, skill-blind). Post-fix exp01/exp02 episodes end on
  the first miss.
- **"Slow decision cadence destroys learnability" — NOT SUPPORTED.** The lookahead oracle
  never misses at 147.6, 33.3, or 16.7 ms periods whenever the hold provides enough
  displacement. The binding constraint is control authority, not period: at 147.6 ms with
  a 16.667 ms hold (~5 px per press) the oracle dies at 7.9 steps, while that same cell's
  trained policy still improved hits 0.2 → 0.6 inside the pilot budget.
- **"Constant PRESS_LEFT in exp01/exp02 is PPO collapse" — REJECTED.** exp01 entropy is
  pinned at 1.0986 (= ln 3) over every recorded update and exp02 moves 1.082 → 1.020;
  greedy argmax over a near-uniform policy is a fixed action, no collapse needed. One
  matrix cell (medium) *did* collapse (entropy 0.06, ≥98 % action index 1 from update 1),
  so collapse happens — it just is not what exp01/exp02 exhibited. ROOT-015..018 and
  ROOT-028 remain DISPROVEN; nothing here revives them.
- **Convergence — no claim.** Final rolling reward was −0.47..−0.52 in every cell against
  the oracle's +0.1; 16384 steps is a pilot budget, not a convergence result.

Still requires a real external run: live confirmation of the 15-20 px per-decision
paddle displacement, and the STEP-3 exp02 re-run — both tracked in
`.agents/pending_tasks.md`. **Ask before any run that sends real `SendInput`.**

Observability shipped with this work: external results artifacts carry an
`observation_timing` fingerprint (`num_stack`, `obs_size`, `frame_skip`, per-action
`hold_ms`, `post_action_delay_ms`, `decision_period_ms_measured`, `history_span_ms`) plus
`metric_definitions`; PPO history carries a per-update `upd_action_share` dict; and
`env.timing.reset_settle_poll_s` (default 0.05, behaviour unchanged) exposes the
previously hard-coded reset-settle poll stride.

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

The authoritative per-root bug status list is
`.agents/memory/opencode/bugfinding/master-bug-synthesis.md` §0, and unfinished work is
`.agents/pending_tasks.md`. There are currently **no ACTIVE canonical roots** for this
product. ROOT-014 (finished-checkpoint resume), ROOT-036 (the declared reward-semantics
hit/miss contract), ROOT-027 (attach-failure cleanup plus the `status: failed`
results artifact), ROOT-037 (phase-2 env/process leak on a non-session failure or Ctrl-C),
and ROOT-038 (episode-boundary session loss not relaunchable) are fixed and covered by
tests; the only unfinished canonical root is
ROOT-034 (PARTIALLY FIXED). ROOT-011 is CONTRACT GAP / DOCUMENTED and ROOT-026 is
CONTRACT GAP / ACCEPTED DEBT — both are recorded debt, not bugs to fix on sight. The
entries below are design and maintainability hazards that predate the audit and are not
tracked as canonical roots.

- **Checkpoint resume is a no-op, not a restart.** `PPOTrainer.train()` returns immediately
  when `is_complete()` (the checkpoint already met `total_timesteps`) and keeps the existing
  `ppo_final.pt`; it prints which step count it is at and how to continue. Readers of the
  history must go through `training.ppo.summarize_history`, which tolerates the empty
  history that case produces. `experiments/*_results.json` now carry `training_updates` so
  "nothing was trained" is visible in the artifact instead of reading as a zero reward.
- **Hit/miss counts require a declared reward semantics.** `environment/reward.py` defines
  `SIGN_SEMANTICS`/`GENERIC_SEMANTICS` and `reward_semantics_of`. Only `ToyPongEnv` and
  `ExternPongReward` declare `sign`; `evaluate()` reports `episode_hits`/`mean_hits` ONLY
  for them and otherwise omits those keys entirely (absence, not `0.0`, is the encoding).
  An undeclared or unrecognised semantics is `generic`, and `PreprocessingWrapper`
  downgrades to `generic` when `skip > 1`, because summing rewards across frames breaks the
  one-event-per-decision-step invariant. Adding a provider that pays `+1` per surviving
  step must not be reported as a hit count.
- **Capture output size is per-mode, and the live modes ignore it on purpose.**
  `capture.out_width`/`out_height` are honoured only by `synthetic`; `region` returns the
  captured rectangle (defaulting to the out size only when `capture.region.width/height`
  are absent) and `window` returns the native rect. Native frames are what the
  red-pixel-count reward/termination bands need. Documented in
  `make_external_env_from_config`; ROOT-011 is a contract decision, not a resize bug.
- **Verified native red-pixel baseline (Step-3 pre-flight, 2026-10-02, live window
  capture, 240 steps through the real external path).** Native frame is `279x336` (the
  `320x240` canvas plus window chrome). Red-pixel counts: **0 steps in the ambiguous
  `200..300` gap**, and the MISS banner count (`>= 300`) matched the number of misses
  exactly (7 banners / 7 misses). Window chrome contributes no red above the hit band.
  Counts of `1..7` red pixels do occur (23 steps) — below `hit_min=8`, so they classify
  as "normal" and cannot produce a false hit; a subsequent in-band frame still pays the
  rising-edge `+1`. `env.reward_semantics` reports `"sign"` for this provider, as the
  ROOT-036 contract requires.
- **Decision cadence and paddle displacement (measured/derived 2026-10-05).** The
  exp01/exp02 results artifacts measure 6.68 and 6.78 decisions/s = 149.7 / 147.6 ms per
  decision; the configured 60 ms key hold + 80 ms post-action delay accounts for 140 ms
  of that, the rest overhead. Per-decision paddle displacement is **15-20 px**: 60 ms of
  key-down spans 3-4 ticks of the 60 fps game loop at 5 px per tick, and the synthetic
  cadence cell (`tests/test_cadence_experiment.py::TestPressTiming`) verifies exactly
  that against the mirrored tick model. The earlier "5 px per decision" claim in this
  file conflated one frame with one decision and is withdrawn; a live-window measurement
  of the real displacement is still pending (`.agents/pending_tasks.md`). The hit-yield
  caveat for exp01/exp02 readers still holds: a pixel-chase policy scored about 2 hits
  per 7 misses, so low hit yield is cadence/control, not detector loss.
- `training/external_experiment.py` orchestration is covered end to end by
  `tests/test_external_experiment_orchestration.py` (fakes only, no window): the exact
  phase order, state and config propagation into both envs, the isolated run-checkpoint
  handoff from phase 2 to phase 3, per-phase teardown, failure propagation with a
  `status: "failed"` artifact, Ctrl-C cleanup, and window-loss relaunch/resume. Three
  invariants are load-bearing — reverting any one of them turns that suite red, so do not
  "simplify" them away:
  - `train_with_window_relaunch` releases the env *and* the launched game process on
    **every** exit, including a non-session failure and a `KeyboardInterrupt`. The
    session-loss branch and the success branch are both skipped in those cases, so
    without an `except BaseException` arm the run leaves a real window up, still taking
    `SendInput`.
  - `SessionUnavailableError` (a `RuntimeError` subclass) is what
    `ExternalGameEnv._ensure_session()` raises when the session is gone and `attach()`
    cannot rebind, and it is in `_SESSION_ERRORS`. A plain `RuntimeError` there is
    indistinguishable from an ordinary bug, so a window lost between decisions ended the
    experiment instead of relaunching it.
  - The success report aggregates training via `summarize_history`, not direct indexing,
    and carries `training_updates`. A resumed checkpoint that already met the budget runs
    no update, so the history is legitimately empty (ROOT-014) and blind indexing raises
    `KeyError` on a run that should have reported cleanly.
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
  `from pong_logic import ...`, so the same module is importable under two names. This is
  ROOT-026, classified CONTRACT GAP / ACCEPTED DEBT — it is not a defect to fix on sight.
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
