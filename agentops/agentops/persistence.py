"""Persistence-failure policy (roadmap A8).

AgentOps has a number of places where a write to :class:`StateStore` can fail
while the surrounding operation still has an honest result: recording a
diagnostic failure row, emitting a routing decision, notifying a run observer.
Silently swallowing those loses evidence with no signal.

The opposite failure is worse. Some writes are the *only* thing that makes a
reported outcome true: if a verification check's terminal state was never
stored, an in-memory report that still claims ``passed`` fabricates evidence
that no durable record supports, and the workflow then marks a task verified
on the strength of it.

This module gives every such write one classification:

``SAFE_TO_DEGRADE``
    The operation's result stays honest without the write, so the failure is
    reported (WARNING event + in-memory entry) and execution continues.

``MUST_FAIL_CLOSED``
    Continuing would publish a terminal state that persistence does not
    support.  The caller must not report success for that state.

Unknown operations default to ``MUST_FAIL_CLOSED``: an unclassified write is a
policy hole, and the safe default for a new write is the strict one.

This module is a leaf.  It imports no SQLite, subprocess, or GUI layer; the
optional warning-event emitter is injected by the caller.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from .events import Event, EventSeverity, EventType
from .logging import redact_text


class PersistencePolicy(StrEnum):
    """How a failed persistence write may be handled."""

    SAFE_TO_DEGRADE = "safe_to_degrade"
    MUST_FAIL_CLOSED = "must_fail_closed"


# Every persistence write that currently has a fallback.  Keep this table
# exhaustive: adding a `except` around a store call means adding a row here.
PERSISTENCE_POLICIES: Mapping[str, PersistencePolicy] = {
    # Diagnostics: the operation is still correct without the row, but the
    # loss must be visible instead of silent.
    "failure.create": PersistencePolicy.SAFE_TO_DEGRADE,
    "event.routing_decision": PersistencePolicy.SAFE_TO_DEGRADE,
    # AgentRun lifecycle notifications.  A stranded run is recoverable by
    # `recover_agent_runs`, so execution continues — but the run must not be
    # left silently unrecorded.
    "agent_run.create": PersistencePolicy.SAFE_TO_DEGRADE,
    "agent_run.transition": PersistencePolicy.SAFE_TO_DEGRADE,
    # Critical transitions: these are what make a reported outcome true.
    # A report whose checks were not stored must not claim `passed`, a task
    # must not be marked COMPLETED unpersisted, and a merge conflict must
    # not be reported as handled when its debugging task was not recorded.
    "verification_check.create": PersistencePolicy.MUST_FAIL_CLOSED,
    "verification_check.start": PersistencePolicy.MUST_FAIL_CLOSED,
    "verification_check.finish": PersistencePolicy.MUST_FAIL_CLOSED,
    "verification_run.create": PersistencePolicy.MUST_FAIL_CLOSED,
    "verification_run.finish": PersistencePolicy.MUST_FAIL_CLOSED,
    "verification_report.create": PersistencePolicy.MUST_FAIL_CLOSED,
    "task.update": PersistencePolicy.MUST_FAIL_CLOSED,
    "merge.conflict_task": PersistencePolicy.MUST_FAIL_CLOSED,
}


def policy_for(operation: str) -> PersistencePolicy:
    """Return the policy for one persistence operation (strict by default)."""
    return PERSISTENCE_POLICIES.get(operation, PersistencePolicy.MUST_FAIL_CLOSED)


@dataclass(frozen=True)
class Degradation:
    """One persistence write that failed while its operation continued."""

    operation: str
    policy: PersistencePolicy
    error_type: str
    message: str
    workflow_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "policy": self.policy.value,
            "error_type": self.error_type,
            "message": self.message,
            "workflow_id": self.workflow_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
        }

    def summary(self) -> str:
        scope = self.task_id or self.workflow_id or self.run_id or "-"
        return f"{self.operation} failed to persist ({self.error_type}) for {scope}"


def event_emitter(record: Callable[..., object]) -> Callable[[Degradation], None]:
    """Adapt a ``StateStore.record_typed_event``-shaped callable for the recorder.

    The store stays injected, so this module never imports SQLite.  The
    emitted event is a WARNING because a lost write is a degraded operation,
    not a failed one.
    """
    def emit(entry: Degradation) -> None:
        record(Event(
            workflow_id=entry.workflow_id,
            task_id=entry.task_id,
            agent_run_id=entry.run_id,
            type=EventType.PERSISTENCE_DEGRADED,
            severity=EventSeverity.WARNING,
            message=f"Persistence degraded: {entry.summary()}",
            payload=entry.to_dict(),
        ))
    return emit


class DegradationRecorder:
    """Collect persistence degradations and surface them as warnings.

    The recorder never raises: a failure to report a failed write must not
    become a second failure.  ``emit`` is injected by the caller (normally a
    thin wrapper over ``StateStore.record_typed_event``) so this module stays
    free of any persistence import.
    """

    def __init__(self, emit: Callable[[object], None] | None = None, limit: int = 200):
        self._emit = emit
        self._limit = max(1, int(limit))
        self._degradations: list[Degradation] = []

    @property
    def degradations(self) -> tuple[Degradation, ...]:
        return tuple(self._degradations)

    @property
    def has_fail_closed(self) -> bool:
        """True when a must-fail-closed write was lost."""
        return any(
            item.policy is PersistencePolicy.MUST_FAIL_CLOSED
            for item in self._degradations
        )

    def record(
        self,
        operation: str,
        error: BaseException | str,
        *,
        workflow_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> Degradation:
        """Record one failed write and try to emit a warning event."""
        if isinstance(error, BaseException):
            error_type = type(error).__name__
            message = redact_text(str(error))[:500]
        else:
            error_type = "PersistenceError"
            message = redact_text(str(error))[:500]
        entry = Degradation(
            operation=operation,
            policy=policy_for(operation),
            error_type=error_type,
            message=message,
            workflow_id=workflow_id,
            task_id=task_id,
            run_id=run_id,
        )
        self._degradations.append(entry)
        if len(self._degradations) > self._limit:
            del self._degradations[: len(self._degradations) - self._limit]
        if self._emit is not None:
            try:
                self._emit(entry)
            except Exception:
                # Reporting a degradation must never escalate into a failure.
                pass
        return entry

    def clear(self) -> None:
        self._degradations.clear()


__all__ = [
    "PERSISTENCE_POLICIES",
    "Degradation",
    "DegradationRecorder",
    "PersistencePolicy",
    "event_emitter",
    "policy_for",
]
