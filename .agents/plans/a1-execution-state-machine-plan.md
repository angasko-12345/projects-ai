# Plan — A1 Formal Execution/Result State Machine (P0)

> Sharpened from the roadmap A1 paragraph into file-level actions 2026-09-15. Owner: pi (writer); opencode (architecture review, async).

## Why this shape

- `evaluate_execution` already owns the five-way distinction; `state._transition` already owns an inline AgentRun matrix; `workflow.py` already enforces verified-before-PASSED and evidence-before-READY **by convention**. A1 makes the cross-object rules explicit, single-sourced, and fail-loud without changing any passing behavior.

## Subtask 1 — New leaf `agentops/execution_model.py` (no I/O, no SQLite)
Status: pending

- Module docstring = the authoritative transition matrix (AgentRun table, verification consistency, task completion, workflow READY, success ladder). No `docs/` dir needed.
- `class StateTransitionError(ValueError)` — single fail-loud signal.
- `AGENT_RUN_TRANSITIONS: dict[AgentRunStatus, frozenset[AgentRunStatus]]` — the exact matrix, including the `pending → terminal` compatibility allowance (documented, with the lessons.md reason).
- Pure validators (all total, never raise on malformed input — they raise only `StateTransitionError` on rule violation):
  1. `assert_agent_run_transition(current, target)` — terminal→anything raises; unknown current raises.
  2. `assert_report_consistent(report)` — PASSED report must have `failed_checks == 0`, `required_failures == 0`, `overall_status is PASSED`; a report with `total_checks == 0` must not be PASSED (no vacuous success).
  3. `assert_task_completion(task)` — PASSED verification-role task requires `verified is True` and `verification_run_id` set; PASSED review-role task must not set `verified` (reviews don't verify); implementation PASSED must have `verified is False` (agent success ≠ verified).
  4. `assert_workflow_ready(*, verification_ok, review_ok, evidence_present)` — `ready=True` requires all three; returns the reasons list when violated (raised inside the message).
  5. `assert_no_fabricated_success(status_value, evidence_present, what)` — success status with no evidence raises (recovery-honesty rule, reusable by `recover_*` callers later in A5).
- `SUCCESS_LADDER` tuple + `ladder_position()` helper documenting process → agent → verification → review → merge → workflow (each requires the previous; none implies the next).

## Subtask 2 — Single-source the AgentRun matrix in `state.py`
Status: pending

- `state._transition` imports `AGENT_RUN_TRANSITIONS` + `assert_agent_run_transition` instead of its inline table. Semantics preserved exactly (including `current is target` no-op). No behavior change; suite proves it.

## Subtask 3 — Wire validators into `workflow.py` (3 call sites, fail-loud)
Status: pending

1. After kernel verification run: `assert_report_consistent(report)` before setting `task.verified` (fetch checks not needed — report carries counts).
2. In `_execute_task` before `update_task` when `task.status is PASSED`: `assert_task_completion(task)`.
3. In `run_high_level` before each READY return: `assert_workflow_ready(verification_ok=..., review_ok=..., evidence_present=bool(evidence))` — need to check what evidence means there (currently READY derives from task statuses only; wire `verification_evidence()`).

## Subtask 4 — Tests `tests/test_execution_model.py` (≥12 tests)
Status: pending

Matrix (terminal→running raises, pending→completed allowed, same-state no-op), report (PASSED-with-failed-required raises, empty-suite PASSED raises, FAILED report passes validation), task (unverified verification-task PASSED raises, verified implementation PASSED raises, verified review PASSED raises), workflow (READY without evidence raises, READY without review raises), honesty (success-without-evidence raises), wiring (kernel PASSED report flows; full-suite green).

## Subtask 5 — Memory + review + commit + push
Status: done

Roadmap A1 marked DONE; architecture/decisions/lessons/project updated; Output file written; opencode review requested via live `opencode-projects-12884` (reply pending, non-blocking); exe rebuilt + Release asset replaced; committed + pushed.
