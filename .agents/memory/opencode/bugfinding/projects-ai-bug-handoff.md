# `projects-ai` Bug & Fix Handoff

**Re-audit date:** 2026-09-27  
**Repository:** `angasko-12345/projects-ai`  
**Branch:** `main`  
**Audited HEAD:** `bbe7f00141734f9a61aec7eb659a31b3b28e6927`
(`Deleted orchestrator.py due to incompatibility with project`)

> This is a fresh static re-audit of the current GitHub `main` tree
> against the previous handoff and the repository's current
> instruction/memory files.
>
> **Important limitation:** the GitHub connector can inspect the current
> remote tree, but it did not execute the two local Python test suites
> during this audit. Test counts below are therefore documented
> observations, not freshly executed results. Run the product-local
> suites before declaring anything fixed.
>
> **Priority:** current source and current tests outrank this document.
> Every finding must be re-proved before code is changed.

------------------------------------------------------------------------

## Status legend

- **ACTIVE BUG**: current code can produce an incorrect result, unsafe
  behavior, or data loss.
- **ACTIVE SECURITY BUG**: current code can leak or mishandle
  security-sensitive material.
- **ACTIVE RESEARCH / METRIC BUG**: experiments can produce misleading
  evidence.
- **ACTIVE GOVERNANCE BUG**: instructions or memory can cause agents to
  do the wrong thing.
- **ACTIVE COVERAGE GAP**: important behavior is not adequately
  regression-tested.
- **ACTIVE ARCHITECTURE DEBT**: maintainability/design issue without a
  demonstrated runtime failure.
- **ACTIVE DOC BUG**: repository documentation contradicts the current
  tree.
- **RESOLVED**: the current tree contains the fix; do not re-implement
  it unless a new regression exists.
- **SUPERSEDED / HISTORICAL**: an old finding or artifact should not be
  treated as current behavior.

------------------------------------------------------------------------

# Part I — Current AgentOps findings

## AOP-01 — AgentOps-created `.agentops/` can make an arbitrary target repository dirty

**Status:** ACTIVE BUG  
**Priority:** P0

### Evidence

`agentops/agentops/git.py` creates managed worktrees under
`<repository>/.agentops/worktrees/...`. `GitWorktreeManager.merge()`
refuses to merge whenever the base repository has any porcelain output.
AgentOps does not install or verify a target-repository `.gitignore`
rule for `.agentops/`.

That means a fresh target repo can see `?? .agentops/` and be rejected
by its own merge guard.

`agentops/README.md` also describes `<target-repo>/.agentops/` as
ignored, which is not true for arbitrary target repositories.

### Fix

Do not weaken dirty-tree protection. Choose one deliberate design:

1.  move AgentOps runtime state outside the user's Git worktree; or
2.  establish a safe, explicit ignore mechanism without modifying the
    user's tracked intent unexpectedly.

Then test both sides:

- AgentOps-generated state must not block a clean target repo.
- real user changes must still block merge/finalization.

------------------------------------------------------------------------

## AOP-02 — Custom workflow/DAG path still bypasses the authoritative review gate

**Status:** ACTIVE BUG  
**Priority:** P0

### Evidence

`execution_model.py` defines READY as passed+verified verification,
passed review, and evidence.

The standard high-level workflow calls `assert_workflow_ready()`.

The custom CLI path still computes readiness from workflow status plus
evidence, while the state workflow-status logic can mark a custom
workflow passed when all tasks are passed without requiring a
review-role task.

A custom workflow with verification but no review can therefore become
merge-eligible.

### Fix

Make custom DAG finalization use the same authoritative
merge-eligibility predicate as the standard path.

Do not create a second, weaker definition of READY.

### Regression tests

- verification + no review -\> not ready;
- failed review -\> not ready;
- missing evidence -\> not ready;
- passed verification + passed review + evidence -\> eligible.

------------------------------------------------------------------------

## AOP-03 — Agent stdout/stderr still reaches SQLite without the same redaction boundary

**Status:** ACTIVE SECURITY BUG  
**Priority:** P1

### Evidence

`workflow.py` still builds task results containing raw agent
stdout/stderr, while `StateStore` persists task results as text.

Other sinks already use redaction. The persistence policy introduced in
A8 does not itself make task-result persistence redaction-safe.

The repository's universal secret rule explicitly forbids persisting raw
secrets/prompts.

### Fix

Redact task-result and verification transcript text before SQLite
persistence, reusing the existing redaction implementation.

Audit every persistence path for:

- stdout;
- stderr;
- prompt-adjacent text;
- verification transcripts;
- failure evidence;
- event payloads;
- artifact metadata.

### Regression tests

Persist token-shaped and private-key-shaped test strings and prove they
are not stored verbatim.

------------------------------------------------------------------------

## AOP-04 — No GitHub Actions CI

**Status:** ACTIVE INFRASTRUCTURE / COVERAGE GAP  
**Priority:** P1

### Evidence

`.github/` currently has `copilot-instructions.md` but no workflow
files.

### Fix

Add the smallest useful CI matrix:

- AgentOps on Windows with its supported Python floor;
- UGA on Windows with its runtime dependencies.

Do not add arbitrary lint/type-check/coverage gates because neither
product configures them today.

The two product test commands are already documented separately, so CI
does not need a fake repository-wide command.

------------------------------------------------------------------------

## AOP-05 — A8 verification persistence false-success bug

**Status:** RESOLVED

### What was fixed

A swallowed persistence error could previously leave a verification
check non-terminal while the in-memory report still claimed PASSED.

A8 added fail-closed behavior for the verification evidence path. A lost
critical verification write now demotes the result instead of silently
certifying it.

### Do not regress

Do not replace the current critical persistence handling with broad
`except Exception: pass` behavior.

------------------------------------------------------------------------

## AOP-06 — Worktree provenance persistence duplication / silent loss

**Status:** RESOLVED

### What was fixed

CLI and GUI both used to build and persist `WorktreeRef` inline. A
failure was silently swallowed in both copies.

`finalize.record_worktree_provenance()` now centralizes the write and
records degradations.

### Do not regress

Do not recreate separate CLI/controller provenance writes.

------------------------------------------------------------------------

## AOP-07 — Persistence-failure policy exists, but enforcement remains incomplete in workflow fallbacks

**Status:** ACTIVE BUG / ROBUSTNESS GAP  
**Priority:** P1

### Evidence

`persistence.py` defines `MUST_FAIL_CLOSED`, including `task.update` and
critical verification writes.

But `workflow.py` still contains several broad exception handlers around
`record_failure()` and cancellation/failure persistence. The A8 table
therefore describes a policy more broadly than the current enforcement
surface actually implements.

`task.update` itself is not routed through the policy helper, even
though task persistence can carry claims such as `verified=True`.

### Fix

Audit every persistence fallback package-wide and make the policy
operational, not merely descriptive.

For each fallback:

- `SAFE_TO_DEGRADE`: record the degradation visibly and continue;
- `MUST_FAIL_CLOSED`: prevent the surrounding success state from being
  published.

Do not blindly classify every write as fail-closed. Preserve the
existing A8 intent.

### Regression tests

Patch the exact store method each fallback catches and prove the
resulting workflow state is still honest.

------------------------------------------------------------------------

## AOP-08 — GUI recovery can recover agent/verification runs across workflows

**Status:** ACTIVE BUG  
**Priority:** P1

### Evidence

`gui_controller.py:recover_interrupted()` accepts an optional
`workflow_id`, but when one is supplied it scopes only
`recover_tasks(workflow_id)`.

`recover_agent_runs()` and `recover_verification_runs()` are called
without the workflow filter, so they operate on all incomplete rows.

The core workflow engine already scopes recovery correctly.

### Fix

Thread `workflow_id` through all three recovery passes.

### Regression test

Create two workflows with incomplete rows, recover one workflow, and
prove the other workflow is untouched.

------------------------------------------------------------------------

## AOP-09 — Git subprocess decoding can throw `UnicodeDecodeError` on non-ASCII output

**Status:** ACTIVE ROBUSTNESS BUG  
**Priority:** P2

### Evidence

`git.py` calls `subprocess.run(..., text=True, capture_output=True)`
without explicit UTF-8 decoding/error replacement and catches only
`OSError` and `TimeoutExpired`.

Another part of AgentOps already established UTF-8 + replacement
decoding as the safer pattern.

### Fix

Decode Git output explicitly as UTF-8 with replacement, consistent with
`registry.py` and runner output handling.

### Regression test

Feed mocked non-ASCII Git output through every public Git command
wrapper and prove it becomes text rather than escaping as
`UnicodeDecodeError`.

------------------------------------------------------------------------

## AOP-10 — Verification counters do not reconcile for cancelled checks

**Status:** ACTIVE METRIC / REPORTING BUG  
**Priority:** P2

### Evidence

`verification_kernel._summarize_counts()` counts CANCELLED in
`required_failures` but excludes CANCELLED from `failed`.

That makes `total != passed + failed + skipped` when cancellation
occurs.

### Fix

Define one explicit counter contract and make all derived fields follow
it.

Cancellation can be a separate category if desired, but the
printed/serialized totals must reconcile.

### Regression test

Create a cancelled required check and assert all report counters are
mathematically consistent.

------------------------------------------------------------------------

## AOP-11 — CLI Unicode fallback assumes `sys.stdout.buffer` exists

**Status:** ACTIVE ROBUSTNESS BUG  
**Priority:** P2

### Evidence

`cli._print_text()` falls back to `sys.stdout.buffer` after a
`UnicodeEncodeError`.

Captured/replaced stdout objects do not necessarily expose `.buffer`.

### Fix

Use `getattr(sys.stdout, "buffer", None)` and provide a final text-only
replacement path when no binary buffer exists.

### Regression test

Use an in-memory stdout object without `.buffer` and force an encoding
failure.

------------------------------------------------------------------------

## AOP-12 — `agents.yaml` lists disabled `claude` in role preferences

**Status:** ACTIVE DOC / CONFIG DRIFT  
**Priority:** P2

### Evidence

`claude` is configured with `enabled: false` but remains in all four
role-preference arrays.

The registry skips it, so execution is not currently unsafe, but the
configuration lies to maintainers about the usable roster.

### Fix

Either remove disabled agents from default preference lists or
explicitly document why disabled agents remain listed.

Do not change enablement behavior as a side effect.

------------------------------------------------------------------------

## AOP-13 — Default config is still not packaged correctly in a normal wheel

**Status:** ACTIVE PACKAGING BUG  
**Priority:** P2

### Evidence

`agentops/config.py` resolves the default `agents/agents.yaml` relative
to the source tree while the wheel package includes `agentops*` rather
than the external `agents/` data directory.

The frozen executable bundles the file separately, so the problem is
specifically ordinary wheel installation.

### Fix

Package the default config as package data and use installed-resource
resolution while preserving frozen executable behavior.

### Regression test

Build a wheel, install it in a clean environment, and prove the default
configuration loads without source-tree files.

------------------------------------------------------------------------

## AOP-14 — GUI controller surface remains under-tested

**Status:** ACTIVE COVERAGE GAP  
**Priority:** P2

### Fix

Add focused tests for:

- serialization;
- recovery scoping;
- artifacts/events;
- cancellation;
- stale-operation handling;
- error propagation.

Do not chase a coverage percentage.

------------------------------------------------------------------------

## AOP-15 — CLI behavior remains under-tested

**Status:** ACTIVE COVERAGE GAP  
**Priority:** P2

### Fix

Add direct command tests for the important behavior in:

- task/workflow;
- status;
- verify;
- recover;
- event/artifact inspection;
- finalization/merge.

Use temporary repositories and fakes instead of real coding agents.

------------------------------------------------------------------------

## AOP-16 — SQLite migration compatibility needs a stronger end-to-end contract test

**Status:** ACTIVE COVERAGE GAP  
**Priority:** P2

### Fix

Create representative old-schema databases and prove:

- migration is additive;
- existing rows survive;
- new columns/tables appear;
- repeated migration is safe;
- migrated-table inserts name columns explicitly.

Current AgentOps schema is v7.

------------------------------------------------------------------------

## AOP-17 — Missing `{prompt}` should be stricter than a warning-only configuration error

**Status:** ACTIVE ROBUSTNESS GAP  
**Priority:** P2

### Problem

A command configuration without `{prompt}` can remain usable after only
a warning and can produce a malformed agent invocation.

### Fix

Reject missing prompt substitution in strict configuration validation,
with an explicit compatibility mode only if a real legacy caller needs
it.

------------------------------------------------------------------------

## AOP-18 — GUI is intentionally single-operation, but this should remain a conscious limitation

**Status:** ACTIVE ARCHITECTURE LIMITATION  
**Priority:** P3

The controller's operation lock effectively serializes work.

Do not implement a queue merely because one is conceptually useful. Add
a queue only when concurrent GUI operations become an actual product
requirement.

------------------------------------------------------------------------

## AOP-19 — `WorkflowEngine` and `StateStore` remain large architectural hotspots

**Status:** ACTIVE ARCHITECTURE DEBT  
**Priority:** P3

Future extraction candidates:

- Workflow planner/scheduler/executor/repair/recovery;
- StateStore repository-specific facades.

Preserve public interfaces and persistence behavior.

Do not make these giant refactors before reliability and benchmark work.

------------------------------------------------------------------------

## AOP-20 — Routing repository-characteristic input is often empty

**Status:** ACTIVE PRODUCT / ROUTING GAP  
**Priority:** P3

The router supports repository characteristics, but callers can provide
`{}`.

### Fix

Add only verified characteristics that measurably improve routing.

Potential examples: language, framework, test system, platform, or
repository-specific configuration.

Do not expand routing just to create more scoring logic.

------------------------------------------------------------------------

## AOP-21 — `SpawnFactory` typing remains incomplete

**Status:** ACTIVE MAINTAINABILITY GAP  
**Priority:** P3

Add accurate protocol typing without changing runtime behavior.

------------------------------------------------------------------------

## AOP-22 — Some "leaf" modules are not actually dependency leaves

**Status:** ACTIVE ARCHITECTURE DEBT  
**Priority:** P2

### Evidence

- `execution_model.py` describes itself as a leaf but imports
  `agent_run.py`, which imports subprocess-related code and Git helpers.
- `persistence.py` describes itself as a leaf but imports
  `logging.redact_text`, and `logging.py` is a filesystem-writing
  service module.
- `agent_run.py` reaches into a private Git no-window helper instead of
  using the public shared process-policy boundary.

### Fix

Do this incrementally. Extract pure helpers into genuinely pure leaves,
then update imports.

Do not introduce a new service layer solely to satisfy an aesthetic
dependency diagram.

------------------------------------------------------------------------

## AOP-23 — `ready_tasks()` performs writes inside a method named like a query

**Status:** ACTIVE DESIGN / MAINTAINABILITY GAP  
**Priority:** P3

The state query path can mark blocked tasks and persist those state
changes while callers perceive it as a read.

### Fix

Eventually split status discovery from state mutation, preserving atomic
task-claim behavior.

------------------------------------------------------------------------

## AOP-24 — `assert_workflow_ready()` call sites duplicate their own guard condition

**Status:** ACTIVE CODE-QUALITY GAP  
**Priority:** P3

The standard workflow checks the same boolean conditions before calling
`assert_workflow_ready()`, making the validator mostly redundant at
those sites.

### Fix

Make the invariant check authoritative at the transition point instead
of duplicating the condition outside it.

Do not remove the validator from the system.

------------------------------------------------------------------------

## AOP-25 — Build script claims reproducibility that it does not guarantee

**Status:** ACTIVE DOC / BUILD GAP  
**Priority:** P2

`build_windows_exe.py` only verifies that PyInstaller produced an
executable. The archive inspection and smoke test are manual, and
`AgentOps.spec` enables UPX without pinning a UPX version.

### Fix

Either narrow the documentation claim to "build" or make reproducibility
materially true by controlling the relevant tool inputs.

Keep archive inspection + startup/process-exit smoke testing in the
validation procedure.

------------------------------------------------------------------------

# Part II — Current Universal Game Agent findings

## UGA-01 — External experiment result artifacts remain contaminated by pre-fix phantom episodes

**Status:** ACTIVE RESEARCH / DATA QUALITY BUG  
**Priority:** P0

The code now contains reset-settling logic, but the historical external
result artifacts were produced before that correction.

### Fix

Regenerate clean external results after the current settle logic.

Keep old results only as explicitly labelled pre-fix artifacts.

Do not use them as evidence of learning.

------------------------------------------------------------------------

## UGA-02 — External `reset(seed=...)` still does not control the external game's seed

**Status:** ACTIVE REPRODUCIBILITY BUG  
**Priority:** P1

`ExternalGameEnv.reset()` accepts a seed but does not establish
deterministic control over the spawned external process.

The experiment records episode seed arrays anyway.

### Fix

Either:

- implement a genuine external-game seeding contract; or
- stop presenting the recorded seeds as deterministic controls.

A decorative seed is worse than an honest statement that the external
run is stochastic.

------------------------------------------------------------------------

## UGA-03 — Evaluation still infers hits/misses from reward sign

**Status:** ACTIVE METRIC BUG  
**Priority:** P1

`training/evaluate.py` increments hits for `reward > 0` and misses for
`reward < 0`.

That only means "hit/miss" for a very specific +/- reward contract.

### Fix

Add provider-reported structured events, for example an
`events()`/event-result interface, and use it for hit/miss counting.

Keep reward-sign counting only as a clearly documented fallback.

------------------------------------------------------------------------

## UGA-04 — Degenerate-episode distributions are not automatically flagged

**Status:** ACTIVE RESEARCH / METRIC BUG  
**Priority:** P1

A run dominated by one-step/no-event episodes can look statistically
meaningful while actually measuring reset artifacts.

### Fix

Add a results-contract guard that loudly flags pathological episode
distributions.

Do not silently remove bad episodes from the statistics.

A threshold should be documented and configurable/testable.

------------------------------------------------------------------------

## UGA-05 — Game-specific `extern_pong` logic remains hardcoded inside the generic external environment factory

**Status:** ACTIVE ARCHITECTURE BUG  
**Priority:** P1

`make_external_env_from_config()` explicitly accepts and constructs
`extern_pong` reward/termination providers while
`environment.reward.make_reward_from_config()` has a separate provider
registry/validation surface.

The generic external environment therefore still knows a specific game's
name.

### Fix

Inject provider objects or register providers through a generic factory
boundary.

The generic environment should not branch on game names.

------------------------------------------------------------------------

## UGA-06 — External Pong reward thresholds still have no complete YAML configuration surface

**Status:** ACTIVE CONFIGURATION BUG  
**Priority:** P2

When `reward.provider` is `extern_pong`, other reward mapping keys are
effectively discarded and `ExternPongReward()` is constructed with
defaults.

### Fix

Expose provider-specific thresholds explicitly in configuration, or
reject unsupported keys instead of pretending they are active.

### Test

Set each threshold in YAML and assert the provider receives it.

------------------------------------------------------------------------

## UGA-07 — External process launch/attach/stop lifecycle remains duplicated

**Status:** ACTIVE ARCHITECTURE DEBT  
**Priority:** P2

`training/external_experiment.py` and `training/external_smoke.py`
contain overlapping but not identical process lifecycle code.

### Fix

Extract only the lifecycle/session helper:

- launch;
- attach wait/retry;
- title/PID isolation;
- stop/cleanup;
- liveness.

Keep reward, observation, and PPO logic outside it.

------------------------------------------------------------------------

## UGA-08 — Two different `run_experiment()` functions still have different contracts

**Status:** ACTIVE API CLARITY BUG  
**Priority:** P2

`training/ppo.py` exposes a toy helper named `run_experiment()`, while
`training/experiment.py` exposes the YAML-driven experiment runner with
the same name.

### Fix

Rename one or both to communicate the actual role, preserving
compatibility where practical.

Do not merge the unrelated implementations.

------------------------------------------------------------------------

## UGA-09 — `games/` import structure still relies on `sys.path` mutation

**Status:** ACTIVE IMPORT BUG  
**Priority:** P2

`games/extern_pong.py` still inserts its directory into `sys.path` and
then performs a flat `from pong_logic import ...` import.

### Fix

Use normal package imports and add `games/__init__.py` where required by
the chosen package layout.

### Regression test

Import the game through the intended package path from a clean working
directory.

------------------------------------------------------------------------

## UGA-10 — Duplicate image-resize implementations remain

**Status:** ACTIVE ARCHITECTURE DEBT  
**Priority:** P3

`interface/capture.py` and `environment/preprocessing.py` each own a
resizing implementation.

Only consolidate after proving identical semantics/performance for the
relevant callers.

------------------------------------------------------------------------

## UGA-11 — Virtual-key mappings are duplicated across config and code

**Status:** ACTIVE CONFIG DRIFT RISK  
**Priority:** P2

Arrow-key virtual-key codes are repeated across experiment YAML, smoke
code, controller lookup tables, and default action-table construction.

### Fix

Create one authoritative symbolic-key -\> VK mapping and keep
game-specific action tables separate.

### Test

Resolve the same symbolic key through every supported path and assert
the numeric code agrees.

------------------------------------------------------------------------

## UGA-12 — External experiment orchestration is still not fully tested

**Status:** ACTIVE COVERAGE GAP  
**Priority:** P1

Helpers are tested, but the complete baseline -\> train -\> final-eval
driver is not covered at the same level.

### Fix

Use fake process/window/session implementations to test:

- baseline;
- training;
- relaunch;
- checkpoint resume;
- final evaluation;
- result writing;
- cleanup;
- failure propagation.

Never require live SendInput in these tests.

------------------------------------------------------------------------

## UGA-13 — `main.py smoke-test` still exercises a toy path rather than the configured environment

**Status:** ACTIVE TEST / UX BUG  
**Priority:** P1

`cmd_smoke_test()` builds the configured environment, then separately
constructs `ToyPongEnv` + `FrameStack` for the model/rollout smoke path.

With an external config this can report a successful smoke test without
actually smoking the external pipeline.

### Fix

Make smoke testing branch on the actual configured environment, or
explicitly rename the existing command as toy-only and provide a
separate external-preflight path.

------------------------------------------------------------------------

## UGA-14 — `main.py train/evaluate/compare` can accept external configs without the external runner's lifecycle guarantees

**Status:** ACTIVE ARCHITECTURE / UX BUG  
**Priority:** P1

`training.experiment.make_env_from_config()` accepts `type: external`,
while the dedicated `training.external_experiment` runner owns the real
external process lifecycle, relaunch, baseline/final comparison, and
related safeguards.

This creates a dangerous split: the generic CLI can build an external
environment but lacks the guarantees of the dedicated runner.

### Fix

For external configs, either:

- fail fast with the sanctioned external command; or
- make the generic path fully support the same lifecycle contract.

The simpler and safer option is a clear error directing users to the
external runner.

Update README/help text to match.

------------------------------------------------------------------------

## UGA-15 — Checkpoint writes are still non-atomic

**Status:** ACTIVE DATA INTEGRITY BUG  
**Priority:** P1

`training/ppo.py.save_checkpoint()` calls `torch.save(..., final_path)`
directly.

An interrupted write can leave a truncated checkpoint.

### Fix

Write to a temporary file in the same directory, flush/close as
appropriate, then `os.replace()` it onto the final path.

Preserve the previous valid checkpoint when the new write fails.

------------------------------------------------------------------------

## UGA-16 — Checkpoints still lack a schema version

**Status:** ACTIVE COMPATIBILITY GAP  
**Priority:** P2

The checkpoint payload has no explicit schema version.

### Fix

Add a checkpoint schema version and explicit load handling:

- accept known old versions where practical;
- reject unknown future formats clearly.

------------------------------------------------------------------------

## UGA-17 — Checkpoint resume still does not restore RNG state

**Status:** ACTIVE REPRODUCIBILITY BUG  
**Priority:** P2

Current checkpoint payload restores model/optimizer/counters but not all
stochastic state.

### Fix

Persist and restore the RNG state actually used by the trainer:

- Python `random`;
- NumPy;
- PyTorch;
- controllable environment RNG state.

### Test

Save mid-run and verify a resumed continuation matches the uninterrupted
continuation under a deterministic test setup.

------------------------------------------------------------------------

## UGA-18 — Resume still accepts changed environment config without comparison

**Status:** ACTIVE EXPERIMENT / SAFETY BUG  
**Priority:** P1

`load_checkpoint()` restores `env_config`, but `main.py --resume` does
not compare the checkpoint environment configuration with the current
YAML environment configuration before continuing.

### Fix

Compare the relevant normalized environment configuration.

Mismatch should fail clearly or require an explicit override.

Never silently continue under a changed environment.

------------------------------------------------------------------------

## UGA-19 — Resumed-run FPS is still inflated

**Status:** ACTIVE METRIC BUG  
**Priority:** P2

`train()` computes FPS as cumulative
`num_timesteps / current invocation elapsed time`.

After resume the numerator includes previous runs, while the timer
restarts at zero.

### Fix

Track current-run steps/time separately from cumulative experiment
totals.

Report the distinction explicitly.

------------------------------------------------------------------------

## UGA-20 — Training from an already-complete checkpoint can still hit empty-history indexing

**Status:** ACTIVE BUG  
**Priority:** P1

When `num_timesteps >= total_timesteps`, the training loop can execute
zero updates and leave history lists empty, while `main.py` indexes the
last reward/episode entries.

The same zero-update shape can affect experiment wrappers.

### Fix

Explicitly support the no-op case or raise a clear domain error.

Test exact completion, checkpoint beyond target, and zero remaining
steps.

------------------------------------------------------------------------

## UGA-21 — `checkpoint_every_updates=0` can still crash late in training

**Status:** ACTIVE CONFIGURATION BUG  
**Priority:** P1

`PPOConfig.__post_init__()` validates other positive integer settings
but not `checkpoint_every_updates`.

The training loop eventually evaluates
`num_updates % checkpoint_every_updates`, producing division by zero.

### Fix

Require `checkpoint_every_updates > 0`, or explicitly document/use a
separate sentinel for "disabled".

Validate it before training begins.

------------------------------------------------------------------------

## UGA-22 — Failed external runs can lose their result report and leak process/log resources on attach failure

**Status:** ACTIVE BUG  
**Priority:** P1

`train_with_window_relaunch()` can exhaust relaunches and raise without
producing the normal results JSON.

`launch_phase2()` calls `launch_game()` and then `wait_attach()` without
a `try/finally`, so an attach timeout can leave the process and log file
open.

### Fix

Guarantee cleanup around every launched process.

Write a failure result record containing:

- phase;
- failure reason;
- relaunch count;
- last checkpoint;
- partial metrics;
- termination status.

The report should make clear that the experiment failed rather than
pretending it completed.

------------------------------------------------------------------------

## UGA-23 — Live capture `out_width/out_height` configuration is validated/recorded but ignored in live modes

**Status:** ACTIVE CONFIG / METADATA BUG  
**Priority:** P2

For `region` and `window` capture, the generic factory reads and
validates output dimensions but intentionally creates native-resolution
capture objects.

Native-resolution observation is currently intentional for external Pong
reward detection, but the configuration keys still look authoritative
and can be written into result metadata as if they were applied.

### Fix

Either:

- remove/rename the ignored fields for live modes; or
- make the distinction explicit in the schema as "model resize" versus
  "native capture".

Result metadata must describe the actual capture pipeline.

------------------------------------------------------------------------

## UGA-24 — External result metadata does not always point at the actual produced run directory

**Status:** ACTIVE RESEARCH / REPRODUCIBILITY BUG  
**Priority:** P2

The experiment can record the configured checkpoint directory while the
actual file is written below a PID/run-specific directory.

### Fix

Record both:

- normalized configured output root;
- exact produced checkpoint path;
- run identifier/title;
- commit/config identifier.

The metadata should be sufficient to locate the exact artifact without
guesswork.

------------------------------------------------------------------------

## UGA-25 — Logging configuration remains dead

**Status:** ACTIVE DEAD-CONFIG BUG  
**Priority:** P3

`configs/default.yaml` contains a `logging:` block and
`training/logger.py` defines `setup_logging()`, but production paths do
not wire them together.

### Fix

Either wire the config into production or remove/document it as
test/support code only.

------------------------------------------------------------------------

## UGA-26 — External README/config documentation is stale

**Status:** ACTIVE DOC BUG  
**Priority:** P2

The UGA README still contains statements that underdescribe or
misdescribe the current external-game implementation and config surface.

Examples from the audited tree include:

- omission/incomplete description of shipped external configs;
- "no external-game training loop" language that is now false;
- outdated reward/termination provider descriptions.

### Fix

Rewrite the affected sections from the current code and current
sanctioned commands.

Do not copy old memory text forward.

------------------------------------------------------------------------

## UGA-27 — Comparison command reports a sign-only verdict rather than uncertainty-aware evidence

**Status:** ACTIVE RESEARCH / METRIC DESIGN BUG  
**Priority:** P2

`main.py compare` currently turns a difference into a simple
greater-than/less-than/equal verdict.

A noisy external comparison can therefore look much stronger than it is.

### Fix

For controlled experiments, report:

- paired per-seed differences where seeds are genuinely controlled;
- sample size;
- dispersion/uncertainty;
- incomplete/invalid episode counts;
- the raw difference.

Do not turn noise into a binary learning claim.

------------------------------------------------------------------------

## UGA-28 — External action interface is not actually universal

**Status:** ACTIVE RESEARCH GAP  
**Priority:** P1

The current action table mostly represents discrete key and mouse
actions.

Arbitrary PC games can require:

- held actions;
- variable durations;
- simultaneous keys;
- continuous mouse movement;
- camera/mouse look;
- richer temporal actions.

### Fix

Expand the action abstraction only after a concrete multi-game benchmark
demonstrates which capabilities are actually required.

Do not redesign the neural network to solve an action-interface gap.

------------------------------------------------------------------------

## UGA-29 — Reset handling is not actually universal

**Status:** ACTIVE RESEARCH GAP  
**Priority:** P1

Reset and readiness semantics are currently tied to concrete
environment/window behavior.

### Fix

Formalize an external adapter contract for:

- reset;
- ready-to-play;
- terminal detection;
- post-terminal settling.

General visual reset discovery should be a later research problem, not
the first implementation step.

------------------------------------------------------------------------

## UGA-30 — Reward/objective handling is not actually universal

**Status:** ACTIVE RESEARCH GAP  
**Priority:** P1

External reward is still game-specific despite the universal-game goal.

### Fix

Evolve gradually:

`game-specific adapters` -\> `reusable visual events` -\>
`generic objective/event interface` -\> `objective inference research`.

Do not jump straight to automatic objective discovery.

------------------------------------------------------------------------

## UGA-31 — The "universal" claim is still experimentally unproven

**Status:** ACTIVE RESEARCH GAP  
**Priority:** P0

Toy Pong and an external Pong integration demonstrate a pipeline, not
arbitrary-game generalization.

### Fix

Use one shared learner across roughly 3-5 substantially different games
and measure:

- performance;
- sample efficiency;
- multiple seeds where controllable;
- behavior/action diversity;
- transfer;
- cross-game generalization.

Only broaden the claim after the evidence supports it.

------------------------------------------------------------------------

## UGA-32 — External Pong visual detector still has a narrow signal margin

**Status:** ACTIVE MEASUREMENT RISK  
**Priority:** P2

The current detector was hardened to native-resolution/edge-trigger
behavior, but normal UI/window red pixels remain close to the detector's
event range while hit flashes can be brief.

### Fix

Measure production-frame distributions for normal, hit, and miss states
in the exact capture region.

Choose a robust feature/threshold boundary and lock it with
deterministic tests.

------------------------------------------------------------------------

## UGA-33 — UGA checkpoint/result artifact ignore rules are still incomplete

**Status:** ACTIVE REPO-HYGIENE BUG  
**Priority:** P2

`universal-game-agent/.gitignore` now correctly covers nested `.pt`,
`.zip`, and `.pkl` checkpoints, but other plausible model/data artifacts
are not covered (`.pth`, `.safetensors`, `.ckpt`, `.bin`, `.npz`).

`logs/*.log` only matches one directory depth.

### Fix

Extend the product-specific ignore rules to the file formats the project
can actually generate, and make log exclusion recursive where needed.

Do not recreate the root `.gitignore` merely for product-local
artifacts.

------------------------------------------------------------------------

## UGA-34 — UGA has no project packaging metadata and dependencies are unpinned

**Status:** ACTIVE BUILD / REPRODUCIBILITY GAP  
**Priority:** P2

`requirements.txt` lists Torch, Gymnasium, NumPy, PyYAML, and MSS with
no version specifiers, and UGA has no `pyproject.toml`.

### Fix

Add explicit project metadata and a reproducible dependency strategy
only if the project is ready for packaging/benchmark reproducibility.

Do not invent a complicated packaging system just for appearance.

------------------------------------------------------------------------

## UGA-35 — `experiments/.gitkeep` ignore rule is a no-op

**Status:** ACTIVE DOC / HYGIENE GAP  
**Priority:** P3

`experiments/*/` ignores subdirectories, while `!experiments/.gitkeep`
does not undo anything at that path.

### Fix

Remove the misleading rule or replace it with a pattern that matches the
actual intent.

------------------------------------------------------------------------

# Part III — Current governance / `.agents` findings

## GOV-01 — `.agents/AGENTS.md` points to a moved/nonexistent Oh-My-Pi file

**Status:** ACTIVE GOVERNANCE BUG  
**Priority:** P0

### Evidence

The universal contract still lists `.agents/ohmypiagents.md` as the
Oh-My-Pi instruction location.

The actual current file is `.agents/memory/oh-my-pi/ohmypiagents.md`,
after the 2026-09-26 move.

### Fix

Update every canonical-location reference together in one
decision-logged change.

Then grep the repository for the old path and prove it is gone from
active instructions.

------------------------------------------------------------------------

## GOV-02 — `tasks/after-task.md` is still globally required but remains AgentOps-specific

**Status:** ACTIVE GOVERNANCE BUG  
**Priority:** P0

### Evidence

Universal startup instructions route all agents through
`tasks/after-task.md`.

That file tells agents to run the AgentOps test suite, rebuild
`AgentOps.exe`, and follow AgentOps-specific packaging steps.

A UGA task can therefore execute the wrong post-task process.

### Fix

Split the procedure into:

- repository-wide after-task rules;
- AgentOps product procedure;
- UGA product procedure.

The universal dispatcher should select the relevant product section.

------------------------------------------------------------------------

## GOV-03 — Test baseline counts remain contradictory/stale across instruction and memory files

**Status:** ACTIVE GOVERNANCE BUG  
**Priority:** P1

### Evidence

Current files reference multiple incompatible counts, including
357/4-skipped, 261, 273, 268, and older 224-style baselines.

The repository's own lessons already warn that hard-coded counts rot
rapidly.

### Fix

Use the test command as the contract.

When a count is retained, include the commit/date that produced it and
explicitly label it as historical.

------------------------------------------------------------------------

## GOV-04 — `architecture.md` still contains retired Intercom session names

**Status:** ACTIVE GOVERNANCE BUG  
**Priority:** P0

### Evidence

`architecture.md` still names `opencode-arch`, `codex-builder`, and
`agy-reviewer` as canonical targets.

Current `team.md` explicitly says those names are retired and must not
be tasked.

### Fix

Replace session names with a pointer to the role/liveness rule in
`team.md`.

Ephemeral session names should never be stored as durable identities.

------------------------------------------------------------------------

## GOV-05 — Memory startup guidance still mixes universal and AgentOps-only knowledge

**Status:** ACTIVE GOVERNANCE BUG  
**Priority:** P1

`.agents/AGENTS.md` and Pi instructions still route substantial work
through a single shared memory set whose canonical files are heavily
AgentOps-specific.

UGA has product-specific memory under `.agents/memory/oh-my-pi/` and
product-local instructions, but the startup routing does not clearly
dispatch by product.

### Fix

Make memory loading product-aware:

- shared governance memory;
- AgentOps product memory;
- UGA product memory;
- agent-specific operational notes.

Do not stuff all historical material into every task.

------------------------------------------------------------------------

## GOV-06 — Memory folder split is only partially reflected in canonical documentation

**Status:** ACTIVE DOC / GOVERNANCE BUG  
**Priority:** P1

`architecture.md` still says the memory folder split is "planned, not
implemented", despite the Oh-My-Pi instruction file already having moved
into `memory/oh-my-pi/`.

### Fix

Update the architecture description to the actual current layout and
clearly define which paths are canonical.

------------------------------------------------------------------------

## GOV-07 — Superseded `ohmypimemory.md` remains in the default memory tree

**Status:** ACTIVE GOVERNANCE HYGIENE GAP  
**Priority:** P2

The file is explicitly labelled SUPERSEDED, which prevents some
confusion, but it still contains old snapshots and false historical
statements.

### Fix

Archive/remove it from the default read path. Keep only a clearly
labelled historical record if it has incident value.

------------------------------------------------------------------------

## GOV-08 — Old `audit.md` is still labelled canonical and contains since-fixed architecture/defect claims

**Status:** ACTIVE GOVERNANCE BUG  
**Priority:** P1

The file is a 2026-09-13 v0.1.1 audit with 79 tests and an old module
map. It also contains an old redaction weakness that was later fixed.

### Fix

Rename/retitle it as a historical audit and remove it from the default
pre-work read path.

------------------------------------------------------------------------

## GOV-09 — Stale collaboration policy still exists in project/roadmap memory

**Status:** ACTIVE GOVERNANCE BUG  
**Priority:** P1

Some project/roadmap entries still describe the older "Pi only" writer
and narrower reviewer roster, conflicting with the 2026-09-26 policy
that allows Pi or Oh-My-Pi as sole writer and Antigravity as a
quota-gated reviewer.

### Fix

Correct current-state summaries while preserving old policy entries as
historical decisions.

Do not silently rewrite append-only decision history.

------------------------------------------------------------------------

## GOV-10 — Old free-tier troubleshooting documentation contains contradictory probe claims

**Status:** ACTIVE DOC BUG  
**Priority:** P2

The old Pi/OpenCode free-tier runbook contains contradictions about
which endpoint/status/body shape is supported. Later work documented a
different verified route and fixed the provider configuration, but the
old text remains a trap for agents.

### Fix

Mark the old runbook historical or rebuild it strictly from verified
current behavior.

Do not keep contradictory probes in the default operational path.

------------------------------------------------------------------------

## GOV-11 — Governance uses fragile `path:line` anchors

**Status:** ACTIVE GOVERNANCE MAINTAINABILITY BUG  
**Priority:** P2

Several UGA known-defect references no longer point at the code they
describe.

### Fix

Prefer `file + symbol + invariant`. Re-anchor line references whenever
code moves.

------------------------------------------------------------------------

## GOV-12 — Root repository has no committed `.gitignore`

**Status:** ACTIVE GOVERNANCE / HYGIENE ISSUE WITH EXPLICIT DECISION
CONFLICT  
**Priority:** P1

Machine-local ignores live in `.git/info/exclude`, which is not shared
by clones.

However, `decisions.md` explicitly records a choice not to recreate the
root `.gitignore` and instead to scope the checkpoint fix inside UGA.

### Fix

Do not silently add the root file.

Make an explicit new repository decision. If the decision remains "no
root ignore file", document how fresh clones should handle machine-local
artifacts. If the decision changes, update the rule and commit the
shared ignore set intentionally.

------------------------------------------------------------------------

## GOV-13 — No concise root README for the two-product repository

**Status:** ACTIVE DOC GAP  
**Priority:** P2

The repository contains two independent products plus a meta/agent
layer, but no top-level human-facing README is present in the current
tree.

### Fix

Add a small root README describing:

- what the repository contains;
- AgentOps;
- UGA;
- the two test commands;
- the role of `.agents/`;
- the license.

Do not duplicate the product READMEs.

------------------------------------------------------------------------

## GOV-14 — No `.gitattributes` or `.editorconfig`

**Status:** ACTIVE REPO-HYGIENE GAP  
**Priority:** P2

The repository has mixed Windows tooling and UTF-8 text but no explicit
line-ending/editor policy.

### Fix

Add only if the repository owner wants a shared text/line-ending policy.
Keep it minimal.

------------------------------------------------------------------------

## GOV-15 — Root `skills-lock.json` is stale/broken

**Status:** ACTIVE GOVERNANCE / TOOLING BUG  
**Priority:** P1

The tracked lockfile contains skill paths that no longer match the
flattened `.agents/skills/` layout, reports hashes that no longer
correspond to the current skill files, and covers only part of the
skills currently used by the repository's canonical instructions.

### Fix

Either regenerate the lockfile from the actual current skill tree or
remove the lockfile if the repository does not need a checked-in lock.

If retained, every path and hash must be verified and the scope must
include all skills whose state is meant to be locked.

Do not leave a lockfile that falsely implies reproducibility.

------------------------------------------------------------------------

## GOV-16 — Vendored/external skills need verified attribution/licensing

**Status:** ACTIVE COMPLIANCE GAP  
**Priority:** P2

The repository tracks externally sourced skills, but the review found
incomplete attribution/license information.

### Fix

Inventory the external material and add verified attribution/NOTICE
information. Do not invent licenses.

------------------------------------------------------------------------

## GOV-17 — Machine-local paths are not centrally documented

**Status:** ACTIVE GOVERNANCE GAP  
**Priority:** P2

Instructions warn agents not to run `git add -A`, but the repository
does not have one authoritative list of clone-local artifacts that are
expected to exist outside Git.

### Fix

Add a concise machine-local section to the universal instructions and
keep it separate from product source truth.

------------------------------------------------------------------------

## GOV-18 — No enforced provenance/version marker for memory facts

**Status:** ACTIVE GOVERNANCE GAP  
**Priority:** P2

The repository has repeatedly corrected stale memory because facts were
not tied tightly enough to the tree they described.

### Fix

Consider a lightweight `verified-at: <commit> <date>` header for
current-state memory files plus a maintenance rule that a
source-affecting change invalidates the marker.

Do not force historical logs to pretend they are current.

------------------------------------------------------------------------

# Part IV — Repository hygiene and metadata findings

## HYG-01 — `agentops/tests/__init__.py` is absent while another instruction path assumes package-style test imports

**Status:** ACTIVE COVERAGE / DOC CONSISTENCY GAP  
**Priority:** P2

UGA's test package has `tests/__init__.py`; AgentOps does not. Some
documentation uses explicit `tests.test_*` imports.

### Fix

Either add the package marker or remove the package-style documentation.
Test discovery itself remains the source of truth.

------------------------------------------------------------------------

## HYG-02 — UGA dependency metadata is unpinned

**Status:** ACTIVE REPRODUCIBILITY GAP  
**Priority:** P2

Covered by UGA-34. Keep here only as a cross-repo hygiene reminder:
model results are sensitive to the ML stack version, so reproducible
experiments need a reproducible environment description.

------------------------------------------------------------------------

## HYG-03 — Current `requirements.txt` mojibake finding is resolved

**Status:** RESOLVED

The current `universal-game-agent/requirements.txt` contains the
readable UTF-8 comment; the earlier mojibake defect is no longer
present.

Do not reintroduce it through PowerShell 5.1 encoding mistakes.

------------------------------------------------------------------------

# Part V — Things the previous handoff got wrong or that have changed

These are important because coding agents often re-find them and waste
time fixing already-resolved problems.

## CHG-01 — Nested UGA checkpoint ignore bug is resolved

The subproject now uses recursive `.pt/.zip/.pkl` checkpoint patterns.
The previous flat-rule bug that missed nested `extern_pong_*` checkpoint
files is fixed.

Remaining issue: broader file-format coverage is incomplete, tracked as
UGA-33.

------------------------------------------------------------------------

## CHG-02 — Root `orchestrator.py` dead prototype is resolved by deletion

Commit `bbe7f001...` deleted the previously-added root `orchestrator.py`
because it conflicted with the actual repository design.

Do not re-add it as a competing orchestrator.

------------------------------------------------------------------------

## CHG-03 — External Pong reset-settle fix is present

The current external environment includes bounded terminal-state
settling before a new episode starts.

The code fix is real; the old contaminated experiment data is still not
automatically valid.

------------------------------------------------------------------------

## CHG-04 — External Pong native-frame + edge-trigger reward hardening is present

The current detector uses native-resolution frames and once-only event
semantics rather than the earlier downscaled-banner behavior.

Remaining detector-margin risk is tracked in UGA-32.

------------------------------------------------------------------------

## CHG-05 — External window-loss relaunch/resume support is present

The current experiment runner includes bounded relaunch/resume behavior
and records relaunch counts.

The orchestration failure-report/cleanup gap remains UGA-22.

------------------------------------------------------------------------

## CHG-06 — Key-hold release safety is hardened

The action path contains cleanup logic intended to release keys when
input execution raises.

Do not reopen this as a new bug without a fresh reproduction.

------------------------------------------------------------------------

## CHG-07 — AgentOps ProcessRuntime is already centralized

Agent execution and verification use the shared process runtime. Do not
create a second process-management subsystem.

------------------------------------------------------------------------

## CHG-08 — AgentOps structured failure evidence is already implemented

`FailureEvidence` and structured-first deterministic failure
classification are present.

Do not replace them with an LLM classifier.

------------------------------------------------------------------------

## CHG-09 — AgentOps capability routing is already implemented

The router, adapter boundary, capability profiles, explainable
decisions, and fallback are present.

The remaining issue is useful repository-characteristic input, tracked
as AOP-20.

------------------------------------------------------------------------

## CHG-10 — AgentOps success-state model is already centralized

`execution_model.py` is the authoritative success ladder and READY
validator.

The active problem is custom-DAG enforcement, not absence of a state
model.

------------------------------------------------------------------------

# Part VI — Research/product gaps that are not ordinary bugs

## GAP-01 — AgentOps still needs outcome evidence, not just engineering complexity

The key unanswered product question is whether AgentOps actually
improves coding-agent outcomes compared with direct agent usage.

Recommended controlled benchmark arms:

1.  direct agent;
2.  direct agent + tests;
3.  AgentOps + verification;
4.  AgentOps + verification + review;
5.  AgentOps + verification + review + bounded repair.

Measure:

- task completion;
- tests passed;
- regressions;
- verification catches;
- review rejections;
- repair recovery;
- wall time;
- runs/attempts;
- human intervention.

Start with roughly 10-20 real tasks. Do not optimize the system against
the benchmark after seeing results.

------------------------------------------------------------------------

## GAP-02 — UGA has not demonstrated generalization beyond Pong

A shared CNN+GRU/PPO learner on Toy Pong and an external Pong plumbing
path is not evidence of arbitrary-game generalization.

The next meaningful benchmark is several substantially different games
with one shared learner and a multi-seed evaluation where control is
possible.

------------------------------------------------------------------------

## GAP-03 — Real Windows should be an integration benchmark, not the main RL laboratory

External capture/input is slow because each step pays real OS/game
timing costs.

Use fast simulated environments for most algorithm development and use
real Windows primarily for end-to-end validation.

Do not enlarge the model to compensate for an environment-throughput
bottleneck.

------------------------------------------------------------------------

# Part VII — Recommended execution order

## Phase A — Stop correctness holes first

1.  AOP-01: target `.agentops/` dirty-tree failure.
2.  AOP-02: custom-DAG review bypass.
3.  AOP-03 + AOP-07: persistence/redaction correctness.
4.  AOP-08: workflow-scoped GUI recovery.
5.  UGA-15 through UGA-21: checkpoint/resume correctness.
6.  UGA-22: external failure cleanup/reporting.
7.  UGA-03 + UGA-04: experiment metric integrity.

## Phase B — Make the repository instructions trustworthy

1.  GOV-01: fix moved Oh-My-Pi path.
2.  GOV-02: split after-task procedure by product.
3.  GOV-03: remove/qualify stale test counts.
4.  GOV-04: remove ephemeral retired Intercom names.
5.  GOV-05/GOV-06: make memory loading/current layout product-aware.
6.  GOV-08: archive old audit.
7.  GOV-09: reconcile current collaboration policy without rewriting
    history.
8.  GOV-15: repair or remove `skills-lock.json`.

## Phase C — Add evidence infrastructure

AgentOps: CI + benchmark harness.  
UGA: fast multi-game benchmark + clean result provenance.

## Phase D — Only then do architecture expansion

Consider larger refactors only when a measured benchmark shows a real
bottleneck.

------------------------------------------------------------------------

# Part VIII — Coding-agent rules for this handoff

1.  Current source \> current tests \> explicit user instructions \>
    current decisions \> older memory \> assumptions.
2.  Reproduce a finding before fixing it.
3.  Write the regression test first for a real bug.
4.  Do not implement every item in one mega-task.
5.  Do not rewrite architecture to fix a metric problem.
6.  Do not enlarge UGA's neural network to solve
    reward/reset/action-interface problems.
7.  Do not revive `orchestrator.py`.
8.  Do not recreate a second process runtime in AgentOps.
9.  Do not replace deterministic verification/failure logic with an LLM.
10. Do not trust remembered test counts.
11. Do not trust ephemeral Intercom session names from memory.
12. Do not use `git add -A` in this repository's mixed working tree.
13. Reviewers remain read-only; Pi or Oh-My-Pi is the sole writer, never
    both concurrently.
14. Run the correct product-local test suite from the product directory.
15. For any current-state documentation change, verify the code claim
    immediately before committing the documentation.
16. Never persist credentials, raw prompts, or secret-bearing diagnostic
    output.
17. Do not claim an external UGA experiment is deterministic unless the
    external reset/seed contract actually makes it deterministic.
18. Do not call an experiment "learning" from a sign-only reward delta
    without validating episode integrity and uncertainty.

------------------------------------------------------------------------

# Final audit judgment

The previous handoff was **not** complete enough for the current tree
because several issues had moved while the repository was being changed.

The most important current problems are:

- AgentOps can still fight its own dirty-worktree guard on arbitrary
  target repos.
- Custom DAG workflows can still bypass the review requirement.
- SQLite still needs the same redaction boundary as other AgentOps
  sinks.
- A8 persistence policy is only partially enforced outside the
  verification kernel.
- GUI recovery has a workflow-scope bug.
- UGA checkpoint/resume semantics still have several
  correctness/reproducibility holes.
- External experiment failure handling still has cleanup/reporting gaps.
- The repository instruction layer itself still contains stale paths,
  stale agent identities, stale product procedures, and stale memory
  claims.

The repo is not "all fucked". A substantial amount of the core
architecture is already solid. The problem is that the remaining defects
cluster exactly where a system like this lives or dies: **state truth,
evidence truth, experiment truth, and instruction truth**.

Do not answer that with another giant framework. Fix the boundaries,
prove them with tests, then measure the products.
