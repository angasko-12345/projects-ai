"""Failure recording, repair planning and recovery of incomplete work."""

from __future__ import annotations


from ..failure import (
    Failure,
    FailureCategory,
    FailureClassifier,
    FailureEvidence,
    FailureSeverity,
    FailureSource,
    InterruptionContext,
    RepairPlan,
    new_failure_id,
)
from ..logging import redact_text
from ..tasks import Task, TaskStatus
from .support import _scrub_structured


class FailureRecordingMixin:
    """WorkflowEngine methods: failure recording, repair planning and recovery of incomplete work."""

    def plan_repair_for_failure(
        self,
        failure: Failure,
        *,
        attempt: int | None = None,
        repair_cycle: int = 0,
        alternate_agent_available: bool = False,
        approval_granted: bool = False,
    ) -> RepairPlan:
        policy = self.retry_policy
        return FailureClassifier.plan_repair(
            failure,
            attempt=attempt if attempt is not None else failure.attempt,
            repair_cycle=repair_cycle,
            policy=policy,
            alternate_agent_available=alternate_agent_available,
            approval_granted=approval_granted,
        )

    def record_failure(
        self,
        task: Task,
        classification,  # ClassificationResult
        *,
        agent_run_id: str | None = None,
        verification_run_id: str | None = None,
        evidence: str | None = None,
        primary_error: str | None = None,
        recovery_state=None,
        structured_evidence: FailureEvidence | dict[str, object] | None = None,
    ) -> Failure:
        failure = Failure(
            id=new_failure_id(),
            workflow_id=task.workflow_id,
            task_id=task.id,
            agent_run_id=agent_run_id,
            source=classification.source,
            category=classification.category,
            severity=classification.severity,
            retryable=classification.retryable,
            repairable=classification.repairable,
            # Redact before truncating: failure text carries raw stdout/stderr
            # and must not persist secrets (structured evidence is scrubbed
            # separately in _scrub_structured).
            evidence=redact_text(evidence or primary_error or task.result or "")[:4000] or None,
            primary_error=redact_text(primary_error or task.result or "")[:2000] or None,
            verification_run_id=verification_run_id,
            recommended_action=classification.recommended_action,
            attempt=max(1, task.attempts),
            repair_cycle=0,
            recovery_state=recovery_state,
            structured_evidence=_scrub_structured(
                structured_evidence.to_dict()
                if isinstance(structured_evidence, FailureEvidence)
                else dict(structured_evidence)
                if structured_evidence is not None
                else None
            ),
        )
        try:
            return self.state.create_failure(failure)
        except Exception as error:
            # A8 SAFE_TO_DEGRADE: the failure row is diagnostics. Losing it must
            # not mask the task outcome, but the loss is now visible.
            self.degradation.record(
                "failure.create",
                error,
                workflow_id=failure.workflow_id,
                task_id=failure.task_id,
            )
            return failure

    def recover_incomplete(self, workflow_id: str | None = None) -> dict[str, object]:
        """Crash recovery: detect incomplete operations and classify them.

        Runs the state-level recovery passes (agent runs, verification runs,
        tasks) and records a Failure row per recovered task with the matching
        interruption context. Never marks anything successful: recovered tasks
        stay FAILED and recovered runs stay TERMINATED/FAILED. Worktrees are
        preserved by the caller (CLI/GUI never deletes on failure/conflict).
        """
        summary = (
            self.state.recover_all()
            if workflow_id is None
            else {
                "agent_runs": len(self.state.recover_agent_runs(workflow_id)),
                "verification_runs": len(self.state.recover_verification_runs(workflow_id)),
                "tasks": len(self.state.recover_tasks(workflow_id)),
            }
        )
        states: list[str] = []
        tasks = self.state.list_tasks(workflow_id) if workflow_id else []
        for task in tasks:
            if task.status is not TaskStatus.FAILED:
                continue
            if task.result is None or "interrupted" not in task.result.lower():
                continue
            context = (
                InterruptionContext.VERIFICATION
                if task.role == "verification"
                else InterruptionContext.REVIEW
                if task.role == "review"
                else InterruptionContext.AGENT_EXECUTION
            )
            recovery_state, category, action = FailureClassifier.classify_interruption(context)
            # Idempotency: a repeated recovery pass must not mint a fresh
            # Failure per call for the same stranded task. The (task,
            # recovery_state) pair is the idempotency key.
            try:
                prior = self.state.list_failures(task.workflow_id, task.id, limit=50)
            except Exception:
                prior = []
            if any(getattr(item, "recovery_state", None) == recovery_state for item in prior):
                states.append(recovery_state.value)
                continue
            states.append(recovery_state.value)
            try:
                latest_run = self.state.latest_agent_run(task.workflow_id, task.id)
            except Exception:
                latest_run = None
            failure = Failure(
                id=new_failure_id(),
                workflow_id=task.workflow_id,
                task_id=task.id,
                agent_run_id=latest_run.id if latest_run is not None else None,
                source=FailureSource.SYSTEM,
                category=category,
                severity=FailureSeverity.HIGH,
                retryable=True,
                repairable=True,
                evidence=redact_text(task.result)[:4000] if task.result else None,
                primary_error="Task was interrupted before a terminal outcome was recorded.",
                verification_run_id=task.verification_run_id,
                recommended_action=action,
                attempt=max(1, task.attempts),
                recovery_state=recovery_state,
            )
            try:
                self.state.create_failure(failure)
            except Exception as error:
                # A8: this path bypasses record_failure(), so it needs its own
                # degradation record or a lost recovery row stays invisible.
                self.degradation.record(
                    "failure.create",
                    error,
                    workflow_id=failure.workflow_id,
                    task_id=failure.task_id,
                )
        summary["recovery_states"] = states
        return summary

    def _record_agent_failure(self, task: Task, result) -> None:
        try:
            run = self.state.latest_agent_run(task.workflow_id, task.id)
            run_id = run.id if run is not None else None
        except Exception:
            run_id = None
        stderr = getattr(result, "stderr", "") or ""
        # Agent commands embed the raw prompt, which may carry secrets: only
        # the executable is persisted as structured evidence.  Verification
        # check commands are allowlisted config and keep their full argv.
        raw_command = tuple(getattr(result, "command", ()) or ())
        evidence = FailureEvidence(
            source=FailureSource.AGENT,
            exit_code=getattr(result, "exit_code", None),
            timed_out=bool(getattr(result, "timed_out", False)),
            cancelled=bool(getattr(result, "cancelled", False)),
            terminated=bool(getattr(result, "terminated", False)),
            command=raw_command[:1],
            stderr_peek=redact_text(stderr)[-500:] or None,
        )
        classification = FailureClassifier.classify(
            source=FailureSource.AGENT,
            exit_code=getattr(result, "exit_code", None),
            timed_out=bool(getattr(result, "timed_out", False)),
            cancelled=bool(getattr(result, "cancelled", False)),
            terminated=bool(getattr(result, "terminated", False)),
            error=task.result,
            role=task.role,
            evidence=evidence,
            **self._usage_limit_kwargs(task),
        )
        failure = None
        try:
            failure = self.record_failure(
                task,
                classification,
                agent_run_id=run_id,
                verification_run_id=task.verification_run_id,
                evidence=task.result,
                primary_error=task.result,
                structured_evidence=evidence,
            )
        except Exception as persist_error:
            self.degradation.record(
                "failure.create",
                persist_error,
                workflow_id=task.workflow_id,
                task_id=task.id,
            )
        if classification.category is FailureCategory.USAGE_LIMIT:
            self._record_usage_limit_cooldown(
                task, failure, f"{task.result or ''}\n{redact_text(stderr)[-500:]}"
            )

    def _usage_limit_kwargs(self, task: Task) -> dict[str, object]:
        """Classifier opt-in: patterns only when ``usage_limits`` is configured."""
        limits = getattr(self.config, "usage_limits", None)
        if limits is None:
            return {}
        return {"usage_limit_patterns": limits.patterns_for(task.assigned_agent)}

    def _record_legacy_verification_failure(self, task: Task, results) -> None:
        """Record a failed legacy verification run with structured evidence.

        Legacy commands have no check class, so the evidence is persisted for
        inspection while the legacy text rules keep deciding the category.
        """
        failing = results[-1] if results else None
        evidence = FailureEvidence(
            source=FailureSource.VERIFICATION,
            exit_code=None if failing is None or failing.timed_out else failing.exit_code,
            timed_out=bool(failing.timed_out) if failing is not None else False,
            command=tuple(failing.command) if failing is not None else (),
        )
        classification = FailureClassifier.classify(
            source=FailureSource.VERIFICATION,
            error=task.result,
            role=task.role,
            evidence=evidence,
        )
        try:
            self.record_failure(
                task,
                classification,
                verification_run_id=task.verification_run_id,
                evidence=task.result,
                primary_error="Legacy verification commands failed.",
                structured_evidence=evidence,
            )
        except Exception as persist_error:
            self.degradation.record(
                "failure.create",
                persist_error,
                workflow_id=task.workflow_id,
                task_id=task.id,
            )

    @staticmethod
    def _check_attribute(check: object, name: str) -> object:
        try:
            return getattr(check, name, None)
        except Exception:
            return None

    def _record_verification_failure(self, task: Task, report) -> None:
        failing = [
            check
            for check in getattr(report, "checks", ()) or ()
            if self._check_attribute(check, "status") is None
            or getattr(self._check_attribute(check, "status"), "value", None) != "passed"
        ]
        first = failing[0] if failing else None
        raw_class = self._check_attribute(first, "check_class")
        if isinstance(raw_class, str):
            check_class = raw_class
        else:
            try:
                check_class = raw_class.value if raw_class is not None else None
            except Exception:
                check_class = None
        try:
            run = self.state.latest_agent_run(task.workflow_id, task.id)
            run_id = run.id if run is not None else self._verification_source_run(task)
        except Exception:
            run_id = self._verification_source_run(task)
        raw_command = self._check_attribute(first, "command") or ()
        try:
            command = tuple(raw_command)
        except Exception:
            command = ()
        evidence = FailureEvidence(
            source=FailureSource.VERIFICATION,
            check_class=check_class,
            exit_code=self._check_attribute(first, "exit_code"),
            command=command,
        )
        classification = FailureClassifier.classify(
            source=FailureSource.VERIFICATION,
            error=task.result,
            check_class=check_class,
            role=task.role,
            evidence=evidence,
        )
        try:
            verification_run_id = self._check_attribute(report, "run_id")
            self.record_failure(
                task,
                classification,
                agent_run_id=run_id,
                verification_run_id=verification_run_id
                if isinstance(verification_run_id, str)
                else None,
                evidence=task.result,
                primary_error="Verification report did not pass.",
                structured_evidence=evidence,
            )
        except Exception as persist_error:
            self.degradation.record(
                "failure.create",
                persist_error,
                workflow_id=task.workflow_id,
                task_id=task.id,
            )
