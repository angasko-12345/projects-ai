# Session record — 2026-10-01 AgentOps failure-path audit

Four AgentOps defects assigned in one prompt, each investigated against current
source rather than the historical audit notes. All four reproduced, fixed, and
covered by regression tests. Commits `fb5f3f3` (code) and `11dfcd6` (memory),
both pushed to `origin/main`.

## What was verified before changing anything

Baseline `agentops/` suite: **382 tests, OK, 4 skipped**. Final: **407 tests, OK,
4 skips**. Every defect was reproduced with a throwaway script first:

| Lead | Reproduced? | Evidence |
|---|---|---|
| CLI readiness bypass | Yes | Workflow with verification but no review task reached `status == passed`, satisfying the old formula |
| `.agentops/` self-deadlock | Yes | Fresh repo → `?? .agentops/` → merge refused |
| Raw secret in `tasks.result` | Yes | `sk-FAKE-TEST-SECRET-NOT-REAL` present in the stored column |
| Stranded RUNNING verification run | Yes | Check-creation failure on the 2nd check left `status == running` |
| GUI recovery scope | Yes by inspection | `workflow_id` forwarded to `recover_tasks` only |

The four UGA findings called out as "do not blindly reintroduce" were out of
scope and untouched.

## Where the code stands now

- **Readiness:** `execution_model.assess_workflow_readiness()` is the single
  predicate; `assert_workflow_ready()` raises over the same reasons;
  `assert_tasks_ready()` and `WorkflowEngine.workflow_readiness()` are the entry
  points. CLI, GUI, and both `run_high_level` READY paths route through it.
- **Exclusion:** `git.py::_exclude_agentops_state` writes `/.agentops/` to
  `.git/info/exclude` on worktree creation. Dirty-base protection unchanged and
  still blocks genuine user edits.
- **Redaction:** agent output, legacy verification output, task execution
  errors, and the kernel report transcript. Log path retained.
- **Failure paths:** `_abort_setup` closes a partially-created run as terminal
  FAILED without masking the original error; GUI recovery scopes all three
  passes.

New test module: `agentops/tests/test_readiness_and_failure_paths.py` (24
tests). Each fix was verified by reverting it and watching the tests fail.

## Known open item

**`retry_merge` still merges without a readiness check.** Deliberately left
alone: it is a manual operator retry of an already-reviewed worktree, and gating
it would change existing behaviour without evidence that behaviour is wrong.
Tracked as **D9** in `.agents/memory/roadmap.md`. If a future session decides
this should be gated, `workflow_readiness()` is the predicate to use.

## Things a future session should not assume

- The `agentops/` and `universal-game-agent/` suites are **not** interchangeable
  and there is no repository-wide test command.
- `pytest` is installed but is **not** configured; use `unittest`.
- Test counts here are observations from one run, not contracts. Run the suite.
- Shell and file-writing gotchas that cost real time are in
  `../agentops-verification-workflow.md` and `../environment.md`. Read them
  before running anything.
- `environment.md` in the sibling `opencode/` folder contains a stale claim
  about a missing root `.gitignore`; see the correction in this folder's
  `environment.md`.

## Uncommitted state left in the tree

At the end of this session, `small-projects/mini-llm/data/tokenizer.json`
(deletion), `reel-001.html` (untracked), and five `universal-game-agent/`
files modified by another session were present and deliberately **not** staged
or committed. Confirm ownership before touching them.