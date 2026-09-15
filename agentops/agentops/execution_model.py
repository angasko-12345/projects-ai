"""Authoritative execution/result state machine (Track A, item A1).

This module is the single reference for what "success" means at every layer
of AgentOps.  It is a leaf: no I/O, no SQLite, no LLM.  All validators are
pure and total over well-typed input; they raise only
:class:`StateTransitionError` on rule violation, never on malformed data
(callers validate types before calling).

Success ladder (each layer requires the previous; none implies the next)::

    process success      exit code 0, no timeout/cancel/termination
        -> agent success      agent reports success (structured result)
        -> verification success   required checks PASSED (evidence, not claims)
        -> review approval    reviewer accepts the change
        -> merge eligible     all of the above hold for the same commit
        -> merge succeeded    git merge completed into the recorded base
        -> workflow success   every required task reached its own success

Authoritative transition matrix::

    AgentRun:  PENDING   -> STARTING, COMPLETED, FAILED,
                               CANCELLED, TIMED_OUT, TERMINATED
                           (PENDING -> RUNNING must go via STARTING; direct
                            PENDING -> RUNNING raises.)
               STARTING  -> RUNNING, FAILED, CANCELLED, TIMED_OUT, TERMINATED
               RUNNING   -> COMPLETED, FAILED, CANCELLED, TIMED_OUT, TERMINATED
               <terminal> -> (nothing; same-state is a no-op, anything else
                               raises StateTransitionError)

    The PENDING -> terminal allowance is deliberate compatibility behavior:
    post-hoc recording and failure fallbacks legitimately finish runs that
    never went through STARTING/RUNNING (see lessons 2026-09-14).  It is
    encoded here explicitly so it is a decision, not an accident.

    Verification: a PASSED report must describe a non-empty suite with zero
    required failures and at least one passed check.  Optional-check
    failures do NOT contradict PASSED (only required failures do).
    Vacuous success (PASSED with total_checks == 0, or with zero passed
    checks such as an all-skipped suite) is rejected: unknown/invalid
    output never becomes success.

    Task:       a PASSED verification-role task must carry verified=True and
    evidence of the run: a linked verification_run_id (kernel path) or a
    non-empty result transcript (legacy command path, whose output IS the
    evidence per the preserved legacy contract).  A PASSED implementation task must carry
    verified=False (agent success is not verification).  A PASSED review
    task must carry verified=False (reviews approve; they do not verify).

    Workflow:   READY requires a passed+verified verification task, a passed
    review task, and non-empty verification evidence for the same workflow.

    Recovery:   no success status may be produced without evidence
    (recovery honesty — mirrors the standing "never fabricate success"
    rule enforced by recover_* paths).
"""

from __future__ import annotations

from .agent_run import AgentRunStatus, TERMINAL_STATUSES
from .tasks import Task, TaskStatus
from .verification_model import VerificationReport, VerificationReportStatus


class StateTransitionError(ValueError):
    """A validated execution invariant was violated (fail-loud, never coerce)."""


SUCCESS_LADDER: tuple[str, ...] = (
    "process",
    "agent",
    "verification",
    "review",
    "merge",
    "workflow",
)


def ladder_position(layer: str) -> int:
    """Ordinal of a success layer; raises StateTransitionError for unknown layers."""
    try:
        return SUCCESS_LADDER.index(layer)
    except ValueError:
        raise StateTransitionError(f"Unknown success layer: {layer!r}") from None


def layer_requires(layer: str) -> tuple[str, ...]:
    """Layers that must already hold before `layer` can hold."""
    position = ladder_position(layer)
    return SUCCESS_LADDER[:position]


AGENT_RUN_TRANSITIONS: dict[AgentRunStatus, frozenset[AgentRunStatus]] = {
    AgentRunStatus.PENDING: frozenset({
        AgentRunStatus.STARTING,
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.TIMED_OUT,
        AgentRunStatus.TERMINATED,
    }),
    AgentRunStatus.STARTING: frozenset({
        AgentRunStatus.RUNNING,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.TIMED_OUT,
        AgentRunStatus.TERMINATED,
    }),
    AgentRunStatus.RUNNING: frozenset({
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.TIMED_OUT,
        AgentRunStatus.TERMINATED,
    }),
}


def assert_agent_run_transition(
    current: AgentRunStatus | str, target: AgentRunStatus | str
) -> None:
    """Raise StateTransitionError unless current -> target is a legal move."""
    try:
        current_status = current if isinstance(current, AgentRunStatus) else AgentRunStatus(current)
    except ValueError:
        raise StateTransitionError(f"Unknown AgentRun status: {current!r}") from None
    try:
        target_status = target if isinstance(target, AgentRunStatus) else AgentRunStatus(target)
    except ValueError:
        raise StateTransitionError(f"Unknown AgentRun status: {target!r}") from None
    if current_status is target_status:
        return
    if current_status in TERMINAL_STATUSES:
        raise StateTransitionError(
            f"Terminal AgentRun status {current_status.value} cannot become {target_status.value}"
        )
    allowed = AGENT_RUN_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed:
        raise StateTransitionError(
            f"Invalid AgentRun transition: {current_status.value} -> {target_status.value}"
        )


def assert_report_consistent(report: VerificationReport) -> None:
    """Raise StateTransitionError if a report contradicts its own counts."""
    reasons: list[str] = []
    if report.overall_status is VerificationReportStatus.PASSED:
        if report.total_checks == 0:
            reasons.append("PASSED report describes an empty check suite (vacuous success)")
        if report.required_failures != 0:
            reasons.append(f"PASSED report has {report.required_failures} required failures")
        if report.passed_checks < 1:
            reasons.append("PASSED report has zero passed checks (no positive evidence)")
    if reasons:
        raise StateTransitionError(
            f"Inconsistent VerificationReport {report.id}: " + "; ".join(reasons)
        )


def assert_task_completion(task: Task) -> None:
    """Raise StateTransitionError if a PASSED task violates completion rules.

    Only PASSED tasks are checked: FAILED/BLOCKED/PENDING/RUNNING tasks carry
    whatever evidence the failure path recorded and are always legal.
    """
    if task.status is not TaskStatus.PASSED:
        return
    if task.role == "verification":
        reasons: list[str] = []
        if task.verified is not True:
            reasons.append("verification task PASSED without verified=True")
        if not task.verification_run_id and not (task.result or "").strip():
            reasons.append(
                "verification task PASSED with neither a linked verification "
                "run nor a legacy result transcript"
            )
        if reasons:
            raise StateTransitionError(f"Task {task.id}: " + "; ".join(reasons))
        return
    if task.verified:
        raise StateTransitionError(
            f"Task {task.id}: {task.role} task PASSED with verified=True "
            "(only verification evidence verifies)"
        )


def assert_workflow_ready(
    *, verification_ok: bool, review_ok: bool, evidence_present: bool,
    workflow_id: str = "<unknown>",
) -> None:
    """Raise StateTransitionError if a READY claim lacks any required signal."""
    reasons: list[str] = []
    if not verification_ok:
        reasons.append("no passed+verified verification task")
    if not review_ok:
        reasons.append("no passed review task")
    if not evidence_present:
        reasons.append("no verification evidence")
    if reasons:
        raise StateTransitionError(
            f"Workflow {workflow_id} is not READY: " + "; ".join(reasons)
        )


def assert_no_fabricated_success(
    status_value: str, evidence_present: bool, what: str
) -> None:
    """Raise StateTransitionError if success is claimed without evidence.

    Shared by recovery paths (and any future writer): interrupted or unknown
    work must stay failed/cancelled/unresolved until fresh evidence exists.
    """
    normalized = status_value.strip().lower()
    if normalized in {"completed", "passed", "ready", "merged", "success"} and not evidence_present:
        raise StateTransitionError(
            f"{what}: success status {status_value!r} without evidence"
        )


__all__ = [
    "AGENT_RUN_TRANSITIONS",
    "SUCCESS_LADDER",
    "StateTransitionError",
    "assert_agent_run_transition",
    "assert_no_fabricated_success",
    "assert_report_consistent",
    "assert_task_completion",
    "assert_workflow_ready",
    "ladder_position",
    "layer_requires",
]
