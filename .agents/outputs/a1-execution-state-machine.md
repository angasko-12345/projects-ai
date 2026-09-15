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

- Opencode architecture review reply pending (requested via live session; non-blocking).
- A2 (AgentAdapter) is unblocked and next in Track A sequence.
