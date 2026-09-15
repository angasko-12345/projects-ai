# Output — A1 Formal Execution/Result State Machine

> Delivered 2026-09-15. Full suite 251 OK (3 skips). Exe rebuilt (22 modules) + Release v0.1.3 asset replaced. Opencode review requested async (reply pending).

## What was built

- **New `agentops/execution_model.py` leaf** (no I/O, no SQLite): authoritative matrix as module docstring (AgentRun table, verification/task/workflow/recovery rules, success ladder process→agent→verification→review→merge→workflow), `StateTransitionError`, `AGENT_RUN_TRANSITIONS` (exact mirror of the old inline table, incl. documented `pending → terminal` allowance), 5 pure validators (`assert_agent_run_transition`, `assert_report_consistent`, `assert_task_completion`, `assert_workflow_ready`, `assert_no_fabricated_success`), `SUCCESS_LADDER` + `ladder_position`/`layer_requires`.
- **`state._transition` single-sources the matrix** (delegates; `ValueError` contract preserved — zero behavior change).
- **3 fail-loud workflow gates:** report consistency after kernel runs; task completion on PASSED before persist; READY gating on all three signals. `StateTransitionError` re-raised past the task-failure handler (violations abort loudly, never become failure records).
- **Unified `verification_evidence()`:** kernel run OR legacy transcript (was kernel-only).
- **Behavior change:** empty legacy command suites no longer verify (`bool(results) and all(...)`) — supersedes Review #2; recorded in decisions.md, user may overrule.

## Verification

- New `tests/test_execution_model.py`: 27 invariant tests OK.
- Suite: 251 passing (was 224), 3 environment skips.
- Validators initially broke 6 existing tests; each resolved as refined-rule (legacy transcript evidence ×5) or real fabrication fix (vacuous pass ×1) — see lessons.md.
- Exe: archive-inspected (22 `agentops.*` incl. `execution_model`), startup/shutdown smoke-tested with process-exit verification; Release v0.1.3 asset replaced (14,812,835 bytes).

## Files

- Created: `agentops/execution_model.py`, `tests/test_execution_model.py`
- Modified: `agentops/state.py`, `agentops/workflow.py`, `agentops/__init__.py`, `tests/test_review_regressions.py`
- Backup: `/tmp/agentops-backup-a1/diff.patch`

## Follow-ups

- Opencode architecture review RECEIVED and adjudicated 4/4 + note (see decisions.md 2026-09-15 A1R). Re-review offered async, non-blocking.
- A2 (AgentAdapter) is unblocked and next in Track A sequence.

## Addendum 2026-09-15 — A1R adjudication (all fixed, suite 256 OK)

- **[1] kernel-empty parity:** `run_verification` raises `ValueError` on zero-check profiles → generic handler → task FAILED + repair, same outcome as the legacy empty-suite rule. Regression tests at both layers (kernel raises; workflow task FAILED, no abort).
- **[2] honesty wired:** `assert_no_fabricated_success` post-conditions in `recover_agent_runs` / `recover_verification_runs` / `recover_tasks` + recovery-honesty test (no success statuses in any recover output).
- **[3] all-skipped closed + optional-failure correction:** PASSED requires `required_failures == 0` (narrowed from `failed_checks == 0` — optional failures are legal) and `passed_checks >= 1`.
- **[4] same-state no-op:** `_transition` early-returns (no UPDATE, no event); repeat-finish test proves it.
- **NOTE:** READY call sites pass actual task statuses.
- Exe rebuilt + smoke-tested; Release v0.1.3 asset replaced (14,814,649 bytes).
