"""Controller-boundary serializers: domain objects -> plain dicts for the GUI."""

from __future__ import annotations

from ..agent_run import AgentRun
from ..failure import Failure
from ..git import WorktreeRef
from ..tasks import Task, Workflow
from ..verification_model import VerificationCheck, VerificationReport, VerificationRun
from .. import gui_controller as _ns


def serialize_failure(failure: Failure) -> dict[str, object]:
    return {
        "id": failure.id,
        "workflow_id": failure.workflow_id,
        "task_id": failure.task_id,
        "agent_run_id": failure.agent_run_id,
        "source": failure.source.value if hasattr(failure.source, "value") else str(failure.source),
        "category": failure.category.value
        if hasattr(failure.category, "value")
        else str(failure.category),
        "severity": failure.severity.value
        if hasattr(failure.severity, "value")
        else str(failure.severity),
        "retryable": failure.retryable,
        "repairable": failure.repairable,
        "evidence": failure.evidence,
        "primary_error": failure.primary_error,
        "verification_run_id": failure.verification_run_id,
        "recommended_action": failure.recommended_action.value
        if hasattr(failure.recommended_action, "value")
        else str(failure.recommended_action),
        "attempt": failure.attempt,
        "repair_cycle": failure.repair_cycle,
        "recovery_state": failure.recovery_state.value
        if failure.recovery_state and hasattr(failure.recovery_state, "value")
        else failure.recovery_state,
        "structured_evidence": dict(failure.structured_evidence)
        if failure.structured_evidence
        else None,
        "created_at": failure.created_at,
        "updated_at": failure.updated_at,
    }


def serialize_task(task: Task) -> dict[str, object]:
    """Phase 1: Task dataclass -> plain dict at the controller boundary."""
    try:
        review_verdict = _ns.verdict_summary_for_task(task)
    except Exception:
        review_verdict = None
    return {
        "id": task.id,
        "workflow_id": task.workflow_id,
        "description": task.description,
        "role": task.role,
        "assigned_agent": task.assigned_agent,
        "dependencies": list(task.dependencies),
        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
        "attempts": task.attempts,
        "max_attempts": task.max_attempts,
        "result": task.result,
        "verified": task.verified,
        "verification_run_id": task.verification_run_id,
        "review_verdict": review_verdict,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "updated_at": task.updated_at,
    }


def serialize_workflow(workflow: Workflow) -> dict[str, object]:
    """Phase 1: Workflow dataclass -> plain dict at the controller boundary."""
    return {
        "id": workflow.id,
        "description": workflow.description,
        "status": workflow.status.value
        if hasattr(workflow.status, "value")
        else str(workflow.status),
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
    }


def serialize_worktree_ref(ref: WorktreeRef) -> dict[str, object]:
    """Phase 3: persisted worktree provenance -> plain dict."""
    return {
        "id": ref.id,
        "workflow_id": ref.workflow_id,
        "path": ref.path,
        "branch": ref.branch,
        "base_branch": ref.base_branch,
        "base_commit": ref.base_commit,
        "created_at": ref.created_at,
    }


def serialize_verification_check(check: VerificationCheck) -> dict[str, object]:
    return {
        "id": check.id,
        "run_id": check.run_id,
        "workflow_id": check.workflow_id,
        "task_id": check.task_id,
        "profile_name": check.profile_name,
        "name": check.name,
        "check_class": check.check_class.value,
        "command": list(check.command),
        "working_directory": check.working_directory,
        "timeout_seconds": check.timeout_seconds,
        "required": check.required,
        "policy": check.policy.value,
        "status": check.status.value,
        "exit_code": check.exit_code,
        "started_at": check.started_at,
        "ended_at": check.ended_at,
        "duration_seconds": check.duration_seconds,
        "stdout_path": check.stdout_path,
        "stderr_path": check.stderr_path,
        "failure_reason": check.failure_reason,
        "created_at": check.created_at,
        "updated_at": check.updated_at,
    }


def serialize_verification_run(run: VerificationRun) -> dict[str, object]:
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "task_id": run.task_id,
        "profile_name": run.profile_name,
        "profile_snapshot": dict(run.profile_snapshot),
        "mode": run.mode.value if hasattr(run.mode, "value") else str(run.mode),
        "concurrency": run.concurrency,
        "status": run.status.value,
        "source_agent_run_id": run.source_agent_run_id,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "duration_seconds": run.duration_seconds,
        "total_checks": run.total_checks,
        "passed_checks": run.passed_checks,
        "failed_checks": run.failed_checks,
        "skipped_checks": run.skipped_checks,
        "required_failures": run.required_failures,
        "overall_status": run.overall_status.value if run.overall_status else None,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def serialize_verification_report(report: VerificationReport) -> dict[str, object]:
    return {
        "id": report.id,
        "run_id": report.run_id,
        "workflow_id": report.workflow_id,
        "task_id": report.task_id,
        "profile_name": report.profile_name,
        "total_checks": report.total_checks,
        "passed_checks": report.passed_checks,
        "failed_checks": report.failed_checks,
        "skipped_checks": report.skipped_checks,
        "required_failures": report.required_failures,
        "duration_seconds": report.duration_seconds,
        "overall_status": report.overall_status.value,
        "generated_at": report.generated_at,
        "transcript": report.transcript,
    }


def serialize_agent_run(run: AgentRun) -> dict[str, object]:
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "task_id": run.task_id,
        "attempt": run.attempt,
        "agent": run.agent,
        "executable": run.executable,
        "role": run.role,
        "model": run.model,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "duration_seconds": run.duration_seconds,
        "status": run.status.value,
        "exit_code": run.exit_code,
        "cancelled": run.cancelled,
        "timed_out": run.timed_out,
        "terminated": run.terminated,
        "command": list(run.command or ()),
        "command_metadata": dict(run.command_metadata),
        "working_directory": run.working_directory,
        "worktree": run.worktree,
        "prompt_metadata": dict(run.prompt_metadata),
        "stdout_path": run.stdout_path,
        "stderr_path": run.stderr_path,
        "log_path": run.log_path,
        "files_changed": list(run.files_changed),
        "diff_stat": run.diff_stat,
        "error": run.error,
        "failure_classification": run.failure_classification,
        "relationship": run.relationship.value,
        "parent_run_id": run.parent_run_id,
        "retry_of": run.retry_of,
        "repair_of": run.repair_of,
        "structured_result": run.structured_result,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }
