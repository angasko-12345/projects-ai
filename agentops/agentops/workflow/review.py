"""Review verdict recording, security prescan and the review repair loop."""

from __future__ import annotations

import threading
from pathlib import Path

from ..execution_model import assert_tasks_ready
from ..logging import redact_text
from ..events import Event, EventSeverity, EventType
from ..tasks import Task, TaskStatus
from .support import (
    _scrub_structured,
    WorkflowResult,
    HANDOFF_RESULT_CHARS,
)
from .verdicts import (
    _requires_review_verdict,
    verdict_summary_for_task,
)
from .security import _local_security_scan


class ReviewRepairMixin:
    """WorkflowEngine methods: review verdict recording, security prescan and the review repair loop."""

    def _review_failure_context(self, review: Task) -> str:
        """Actionable text describing why the review rejected the change.

        `task.result` alone is not enough: a review that exhausts its attempts
        ends holding the LAST attempt's message. Observed live -- the review
        rejected the change, the retry found no alternative agent, and `result`
        became "No installed agent supports role 'review'". The finding survived
        only on the review task's failure rows, and repairing from `result` sent
        the debugging agent after a nonexistent routing bug.
        """
        parts: list[str] = []
        seen: set[str] = set()

        def add(text: str | None) -> None:
            value = (text or "").strip()
            if value and value not in seen:
                seen.add(value)
                parts.append(value)

        try:
            failures = self.state.list_failures(review.workflow_id, review.id, limit=20)
        except Exception:
            failures = []
        for failure in failures:
            add(failure.primary_error or failure.evidence)
        add(review.result)
        # The parsed blocking findings are the actionable repair context:
        # they name what the reviewer wants changed, while raw transcripts
        # bury it. They lead so a repair agent reads them first.
        try:
            blocking = (verdict_summary_for_task(review) or {}).get("blocking") or []
            if blocking:
                findings = "\n".join(f"- {redact_text(str(item))[:500]}" for item in blocking)
                parts.insert(0, f"Blocking review findings:\n{findings}")
        except Exception:
            pass
        context = "\n\n".join(parts)
        if len(context) > HANDOFF_RESULT_CHARS:
            context = context[:HANDOFF_RESULT_CHARS].rstrip() + "\n[truncated]"
        return context

    def _record_review_verdict(self, task: Task, verdict: object | None) -> None:
        """Persist the review/security verdict as a redacted timeline event."""
        try:
            if not _requires_review_verdict(task.role):
                return
            if verdict is None:
                payload: dict[str, object] = {
                    "verdict": "missing",
                    "approved": False,
                    "blocking": [],
                    "non_blocking": [],
                }
                severity = EventSeverity.WARNING
            else:
                payload = {
                    "verdict": verdict.verdict,
                    "approved": verdict.is_approved,
                    "blocking": [redact_text(item)[:500] for item in verdict.blocking],
                    "non_blocking": [redact_text(item)[:500] for item in verdict.non_blocking],
                }
                severity = EventSeverity.INFO if verdict.is_approved else EventSeverity.WARNING
            self.state.record_typed_event(
                Event(
                    workflow_id=task.workflow_id,
                    task_id=task.id,
                    type=EventType.REVIEW_COMPLETED,
                    severity=severity,
                    message=f"{task.role} verdict: {payload['verdict']}",
                    payload=_scrub_structured(payload),
                )
            )
        except Exception as error:
            self.degradation.record(
                "event.review_verdict",
                error,
                workflow_id=getattr(task, "workflow_id", None),
                task_id=getattr(task, "id", None),
            )

    def _security_prescan_reason(
        self,
        task: Task,
        working_directory: str | Path,
    ) -> str | None:
        """Fail-closed secret screen for the security role.

        Returns a redacted failure reason when the local scan finds
        credential-like patterns in the worktree diff, else ``None``. A
        positive local scan is demonstrated evidence -- like a failed
        verification -- so the task fails without invoking an agent: no
        agent verdict can waive a secret sitting in the diff. A scan that
        could not run is never silent: the degradation is recorded as a
        WARNING timeline event, because "no findings" must not be readable
        as "nothing to find". Never raises.
        """
        findings: list = []
        completed = False
        try:
            findings, completed = _local_security_scan(working_directory)
        except Exception:
            completed = False
        if not completed:
            try:
                self.state.record_typed_event(
                    Event(
                        workflow_id=task.workflow_id,
                        task_id=task.id,
                        type=EventType.NOTE,
                        severity=EventSeverity.WARNING,
                        message="local secret prescan did not complete; the "
                        "security agent reviews without prescan findings",
                        payload=_scrub_structured(
                            {"role": task.role, "reason": "prescan_unavailable"}
                        ),
                    )
                )
            except Exception:
                pass
        if not findings:
            return None
        try:
            lines = [
                "security policy: the local secret scan found "
                f"{len(findings)} credential-like pattern(s) in the change; "
                "remove them (or prove them inert) before this can merge.",
            ]
            for finding in findings[:10]:
                lines.append(f"- {finding.describe()}")
            if len(findings) > 10:
                lines.append(f"({len(findings) - 10} more omitted)")
            # Findings carry pattern names and locations only, never values;
            # redact defensively before persisting.
            return redact_text("\n".join(lines))
        except Exception:
            return "security policy: the local secret scan found credential-like patterns."

    async def _gate_repair_security(
        self,
        workflow_id: str,
        description: str,
        working_directory: str | Path,
        cancel_event: threading.Event | None,
        implementation_id: str,
        review_id: str,
    ) -> Task | None:
        """Fresh security check for a repaired change, when enabled.

        The pre-repair security verdict (if any) judged the unrepaired
        change, so a repair that passes re-review gets its own security
        task rather than inheriting the old one. Returns ``None`` when
        the security gate is disabled. Never merges; the caller decides.
        """
        try:
            if not getattr(self.config, "security_review", False):
                return None
            security = self.state.add_task(
                Task(
                    f"Security-review the repaired change for: {description}",
                    "security",
                    workflow_id,
                    dependencies=(review_id, implementation_id),
                    max_attempts=self.config.max_attempts,
                )
            )
        except Exception:
            return None
        await self.execute(workflow_id, working_directory, cancel_event)
        try:
            return self.state.get_task(security.id)
        except Exception:
            return None

    async def _repair_failed_review(
        self,
        workflow_id: str,
        description: str,
        working_directory: str | Path,
        cancel_event: threading.Event | None,
        implementation_id: str,
        review: Task,
    ) -> WorkflowResult:
        """Repair a demonstrated review rejection, then re-verify and re-review.

        Each cycle persists three real tasks -- debugging, verification, review
        -- so the repair is part of the run's history, not an invisible retry.
        Bounded by `workflows.max_review_rounds` (default 2); the debugging
        prompt leads with the parsed blocking findings as actionable repair
        context. READY is still decided only by `assert_tasks_ready`, so an
        exhausted budget returns not READY with the last verdict preserved.
        Rejected or unreviewed changes are never merged.
        """
        if review.status is not TaskStatus.FAILED:
            # Only a FAILED review demonstrated a problem. BLOCKED means the
            # review never ran, so repairing would invent a defect -- the
            # review-side twin of the UNVERIFIED rule in `run_high_level`.
            return WorkflowResult(
                workflow_id,
                False,
                f"Review was {review.status.value}; no review failure was "
                f"demonstrated, so no repair was attempted.",
            )
        max_rounds = getattr(self.config, "max_review_rounds", 2)
        try:
            max_rounds = max(0, int(max_rounds))
        except (TypeError, ValueError):
            max_rounds = 2
        review_context = self._review_failure_context(review)
        for _ in range(max_rounds):
            repair = self.state.add_task(
                Task(
                    "Repair the review findings.\n" + review_context,
                    "debugging",
                    workflow_id,
                    dependencies=(implementation_id,),
                    max_attempts=self.config.max_attempts,
                )
            )
            reverify = self.state.add_task(
                Task(
                    "Re-run configured project verification commands after a review repair.",
                    "verification",
                    workflow_id,
                    dependencies=(repair.id,),
                    max_attempts=1,
                )
            )
            re_review = self.state.add_task(
                Task(
                    f"Re-review the repaired change for: {description}",
                    "review",
                    workflow_id,
                    dependencies=(reverify.id, implementation_id),
                    max_attempts=self.config.max_attempts,
                )
            )
            await self.execute(workflow_id, working_directory, cancel_event)
            verification = self.state.get_task(reverify.id)
            review = self.state.get_task(re_review.id)
            review_context = self._review_failure_context(review)
            if verification.status is not TaskStatus.PASSED or not verification.verified:
                # The repair broke verification. That is a different defect than
                # the rejection this cycle was opened for, and re-sending the
                # same findings would not address it.
                return WorkflowResult(
                    workflow_id,
                    False,
                    f"Verification was {verification.status.value} after the "
                    f"review repair; the repaired change does not verify.",
                )
            if review.status is TaskStatus.PASSED:
                if getattr(self.config, "security_review", False):
                    security = await self._gate_repair_security(
                        workflow_id,
                        description,
                        working_directory,
                        cancel_event,
                        implementation_id,
                        review.id,
                    )
                    # An enabled gate must never read as a disabled one: None
                    # here means the security task could not be created or
                    # read, which is a failure, not a waiver.
                    if security is None or security.status is not TaskStatus.PASSED:
                        shown = security.status.value if security is not None else "unavailable"
                        return WorkflowResult(
                            workflow_id,
                            False,
                            f"Security review was {shown} after repair; "
                            "the repaired change is not ready.",
                        )
                ready = assert_tasks_ready(
                    self.state.list_tasks(workflow_id),
                    workflow_id,
                    verification_task_id=verification.id,
                    review_task_id=review.id,
                )
                return WorkflowResult(workflow_id, True, ready.summary())
            if review.status is not TaskStatus.FAILED:
                return WorkflowResult(
                    workflow_id,
                    False,
                    f"Review was {review.status.value} after repair; no further "
                    f"review failure was demonstrated.",
                )
        last_verdict = (verdict_summary_for_task(review) or {}).get("verdict", "missing")
        last_blocking = (verdict_summary_for_task(review) or {}).get("blocking") or []
        blocked = "; ".join(str(item) for item in last_blocking[:5])
        suffix = f" Last verdict: {last_verdict}."
        if blocked:
            suffix += f" Blocking findings: {redact_text(blocked)[:1000]}"
        return WorkflowResult(
            workflow_id,
            False,
            f"Review did not pass after {max_rounds} review round(s); the workflow is BLOCKED."
            + suffix,
        )
