# Plan — Phase 3 Failure, Repair, and Recovery Kernel (archived from tasks/task.md; all subtasks done 2026-09-14)

## Subtask 1 — Inspect current implementation + Phase 2 changes
Agent: pi
Depends on: none
Status: done

Read agent_run.py, verification_model.py, verification_kernel.py, workflow.py, state.py (recover_*), runner.py, config.py, cli.py. Backup dirty tree to /tmp/agentops-backup-failure-kernel/ (done 2026-09-14).

---

## Subtask 2 — Failure domain (failure.py leaf)
Agent: pi
Depends on: Subtask 1
Status: done

Create agentops/failure.py: FailureCategory (16 values), FailureSeverity, FailureSource, RepairAction (retry_same_agent, retry_different_agent, repair_implementation, rerun_verification, request_approval, stop), RecoveryState, Failure dataclass (all required fields), deterministic FailureClassifier.classify (no LLM), RetryPolicy (max_attempts, max_repair_cycles, backoff, cancellation, inherited context), RepairPlan builder, crash-recovery classifier (interruption contexts: agent_execution, verification, review, worktree_creation, git_operation, merge — never unknown/interrupted to success without evidence).

---

## Subtask 3 — Persistence (schema v3 failures table + recovery)
Agent: pi
Depends on: Subtask 2
Status: done

Additive failures table + config runtime.failure/retry knobs; StateStore create/get/list failures, link verification_run_id + agent_run_id, recover_tasks/workflows crash states, keep existing recover_agent_runs/recover_verification_runs semantics, preserve failed/cancelled/conflicted worktrees.

---

## Subtask 4 — Workflow integration (bounded retries + distinct AgentRuns)
Agent: pi
Depends on: Subtask 3
Status: done

Wire classifier+policy into WorkflowEngine: every retry/repair creates distinct AgentRun with parent link (RETRY/REPAIR), inherits previous context + verification evidence, backoff, cancellation, max_attempts/max_repair_cycles enforcement, repair decisions incl. request_approval/stop.

---

## Subtask 5 — CLI/GUI inspection
Agent: pi
Depends on: Subtask 4
Status: done

Add agentops failures/status inspection, controller pass-throughs, GUI selected-task failure summary. Keep allowlist/security model intact.

---

## Subtask 6 — Tests + full suite
Agent: pi
Depends on: Subtask 5
Status: done (146 tests OK, 1 pre-existing platform skip)

New tests/test_failure_kernel.py: every failure category, deterministic classifier (no LLM), repair decisions, bounded retries/backoff/cancel, parent-linked AgentRuns, crash-recovery for all 6 interruption contexts, never-success-without-evidence, interruption simulation. Run python -m unittest discover -s tests.

---

## Subtask 7 — Reviews + build + memory
Agent: pi
Depends on: Subtask 6
Status: done except reviewer replies pending (copilot + opencode requested via live Intercom, read-only snapshots; fcc-claude not requested — server down)
