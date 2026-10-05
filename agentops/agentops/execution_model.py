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
    review task, and non-empty verification evidence carried by that same
    verification task. The signals are a conjunction over one task, never a
    scan of the task pool that lets separate tasks donate separate signals.

    Recovery:   no success status may be produced without evidence
    (recovery honesty — mirrors the standing "never fabricate success"
    rule enforced by recover_* paths).
"""

from __future__ import annotations

from .agent_run import AgentRunStatus, TERMINAL_STATUSES
from .tasks import Task, TaskStatus
from .verification_model import VerificationReport, VerificationReportStatus


# Workflow readiness: the single READY rule for the whole product. Every entry
# point that can declare a workflow READY, or gate a merge on readiness, must
# assess through assess_workflow_readiness(); assert_workflow_ready() raises over
# the same reason strings so no path holds a weaker contract than another.

from collections.abc import Iterable
from dataclasses import dataclass

from .tasks import Task, TaskStatus


@dataclass(frozen=True)
class WorkflowReadiness:
    """Why a workflow is or is not READY, in terms an operator can act on."""

    workflow_id: str
    verification_ok: bool
    review_ok: bool
    evidence_present: bool
    reasons: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.reasons

    def summary(self) -> str:
        return "READY" if self.ready else "; ".join(self.reasons)


def _missing_readiness_reasons(
    verification_ok: bool, review_ok: bool, evidence_present: bool
) -> list[str]:
    reasons: list[str] = []
    if not verification_ok:
        reasons.append("no passed+verified verification task")
    if not review_ok:
        reasons.append("no passed review task")
    if not evidence_present:
        reasons.append("no verification evidence")
    return reasons


def assess_workflow_readiness(
    tasks: Iterable[Task],
    workflow_id: str = "<unknown>",
    *,
    verification_task_id: str | None = None,
    review_task_id: str | None = None,
) -> WorkflowReadiness:
    """Evaluate the READY contract over a workflow's tasks.

    READY requires all three of: a passed+verified verification task, a passed
    review task, and non-empty verification evidence carried by that same
    verification task. Evidence found only on a different verification task
    does not satisfy the contract, because the READY claim must rest on
    signals that actually coexist in one task.

    ``verification_task_id``/``review_task_id`` narrow the assessment to the
    tasks a caller is actually gating on (the standard flow's repair cycles
    create several verification and review tasks, and only the final pair
    decides the outcome). Omit them to assess the workflow as a whole, which
    is what the custom-DAG and CLI paths need.
    """
    scoped = list(tasks)

    def pool(task_id: str | None, role: str) -> list[Task]:
        candidates = scoped if task_id is None else [t for t in scoped if t.id == task_id]
        return [task for task in candidates if task.role == role]

    def is_evidence_backed(task: Task) -> bool:
        return bool(task.verification_run_id or (task.result or "").strip())

    def is_passed_and_verified(task: Task) -> bool:
        return task.status is TaskStatus.PASSED and task.verified is True

    verification_ok = any(
        is_passed_and_verified(task)
        for task in pool(verification_task_id, "verification")
    )
    review_ok = any(
        task.status is TaskStatus.PASSED
        for task in pool(review_task_id, "review")
    )
    # Evidence counts only when it is carried by a verification task that is
    # itself PASSED and verified. Scanning the pool for evidence alone lets a
    # sibling task donate it: one task supplies the PASSED+verified signal
    # while a different — even FAILED — task supplies the evidence, and the
    # workflow is declared READY on signals that never coexisted in one task.
    evidence_present = any(
        is_passed_and_verified(task) and is_evidence_backed(task)
        for task in pool(verification_task_id, "verification")
    )

    reasons = _missing_readiness_reasons(verification_ok, review_ok, evidence_present)
    return WorkflowReadiness(
        workflow_id, verification_ok, review_ok, evidence_present, tuple(reasons)
    )


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
        if report.passed_checks < 1:
            reasons.append("PASSED report has zero passed checks (no positive evidence)")
        # The invariant: a report cannot be PASSED while any required check is
        # unresolved. required_failures counts FAILED, TIMED_OUT, CANCELLED and
        # SKIPPED required checks, so this single test covers all four -- including
        # a required check skipped as inapplicable, which previously could not
        # block a PASSED report at all.
        if report.required_failures != 0:
            reasons.append(
                f"PASSED report has {report.required_failures} unresolved required "
                f"check(s); every required check must PASS"
            )
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
    if verification_ok and review_ok and evidence_present:
        return
    raise StateTransitionError(
        f"Workflow {workflow_id} is not READY: "
        + "; ".join(_missing_readiness_reasons(
            verification_ok, review_ok, evidence_present))
    )


def assert_tasks_ready(
    tasks: Iterable[Task],
    workflow_id: str = "<unknown>",
    *,
    verification_task_id: str | None = None,
    review_task_id: str | None = None,
) -> WorkflowReadiness:
    """Assert the authoritative readiness rule over real tasks.

    Every READY/merge gate should call this: it assesses the tasks and raises
    the same StateTransitionError as :func:`assert_workflow_ready` when a
    prerequisite is missing.
    """
    readiness = assess_workflow_readiness(
        tasks, workflow_id,
        verification_task_id=verification_task_id,
        review_task_id=review_task_id,
    )
    assert_workflow_ready(
        verification_ok=readiness.verification_ok,
        review_ok=readiness.review_ok,
        evidence_present=readiness.evidence_present,
        workflow_id=workflow_id,
    )
    return readiness


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
    "WorkflowReadiness",
    "assert_agent_run_transition",
    "assert_no_fabricated_success",
    "assert_report_consistent",
    "assert_task_completion",
    "assert_tasks_ready",
    "assert_workflow_ready",
    "assess_workflow_readiness",
    "ladder_position",
    "layer_requires",
]
