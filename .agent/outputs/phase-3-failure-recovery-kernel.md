# Output — Phase 3 (archived from tasks/task.md, 2026-09-14)

The first-class Failure, Repair, and Recovery Kernel is implemented in the dirty `D:/admin/code/projects/agentops` working tree (backed up to `/tmp/agentops-backup-failure-kernel/` before work began; full-suite green before and after).

## What was built

- New `agentops/failure.py` (leaf domain, no I/O, no LLM): `FailureCategory` (all 16 required values), `FailureSeverity`, `FailureSource`, `RepairAction` (retry_same_agent, retry_different_agent, repair_implementation, rerun_verification, request_approval, stop), `RecoveryState`, `InterruptionContext` (agent_execution, verification, review, worktree_creation, git_operation, merge + unknown), `Failure` record (all required fields), deterministic `FailureClassifier` (explicit flags + substring rules; unknown stays non-retryable STOP), `RetryPolicy` (max_attempts, max_repair_cycles, exponential backoff, cancellation-aware), `RepairPlan`, `build_retry_prompt` (inherited context), interruption classifier (never unknown/interrupted to success).
- Additive schema v3: `failures` table (run refs plain TEXT so no-agent failures record cleanly); `create/get/list_failures`, `recover_tasks` (stranded RUNNING tasks become FAILED with preserved evidence, never PASSED), `recover_all` (agent + verification + task passes). Existing `recover_agent_runs`/`recover_verification_runs` semantics kept; failed/cancelled/conflicted worktrees stay preserved.
- Workflow integration: every task outcome path (agent, verification, no-agent, cancelled, exception) records a Failure row; retries back off cancellation-aware within `max_attempts`/`max_repair_cycles`; next attempts inherit previous failure + verification evidence; every retry/repair is a distinct parent-linked AgentRun (RETRY/REPAIR); `plan_repair_for_failure` + `recover_incomplete` APIs.
- Config `runtime.backoff_base_seconds`/`backoff_max_seconds`/`backoff_factor` (appended with defaults; strict validation).
- Inspection: `agentops failures`, `agentops recover`, `agentops status` failure lines, controller `list_failures`/`recover_interrupted` + workflow payloads, GUI selected-task failure summaries, README docs. Allowlist/security model untouched.

## Verification

- Full suite: `146 tests OK (1 pre-existing platform skip)`, including 24 new `tests/test_failure_kernel.py` tests (all 16 categories reachable, determinism/no-LLM assertion, repair decisions, budgets, backoff schedule, inherited context, parent-linked runs, crash recovery for all 6 interruption contexts, never-success-without-evidence, simulated restart).
- Copilot (test review) + opencode (architecture review) requested via live Intercom with read-only `/tmp/agentops-review-failure-kernel/` snapshots; replies pending at close — re-request if findings arrive. fcc-claude not requested (`fcc-server` down).
- Rebuilt `dist/AgentOps.exe` (`14,756,616` bytes, includes `agentops.failure`), archive-inspected (16 `agentops.*` modules), smoke-tested startup/shutdown (lingering windowed process cleared with `cmd //c taskkill /F` per recorded lesson).

## Files

- Created: `agentops/failure.py`, `tests/test_failure_kernel.py`
- Modified: `agentops/state.py`, `agentops/workflow.py`, `agentops/config.py`, `agentops/cli.py`, `agentops/gui.py`, `agentops/gui_controller.py`, `agentops/__init__.py`, `README.md`
- Rebuilt: `dist/AgentOps.exe`

## Follow-ups

- Decide on a commit strategy for the accumulated uncommitted work (AgentRun + Verification Kernel + Failure Kernel on top of prior feature work).

## Addendum 2026-09-14 — copilot review adjudicated (4/4 fixed)

- Scoped recovery: `recover_agent_runs`/`recover_verification_runs` accept optional `workflow_id`; `recover_incomplete(workflow_id)` threads it through (cross-workflow mutation closed).
- Idempotent recovery: (task, recovery_state) key skips duplicate Failure rows on repeat passes.
- Cancellation repair now honors `RetryPolicy` (`next_attempt+1` within budget, STOP when exhausted).
- Permission-denied/eacces/eperm classify as ENVIRONMENT_FAILURE (REQUEST_APPROVAL), not UNKNOWN.
- 4 regression tests added; full suite `150 tests OK (1 pre-existing platform skip)`. No exe rebuild (no packaging-affecting change since the Phase 3 build).
- Opencode architecture review still pending at time of writing.

## Standing review/build/memory checklist (carried over)

Snapshot reviews via agentops run copilot/opencode/fcc-claude if available (read-only /tmp/agentops-review-*); fix valid findings; rebuild dist/AgentOps.exe only if packaging-affecting code changed + smoke test; update .agents/memory/*; append ## Output to the task record.
