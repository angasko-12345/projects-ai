"""WorkflowEngine: the orchestration core, composed from the method mixins."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from time import time as wall_clock

from ..agent_run import (
    AgentRunMetadata,
    AgentRunObserver,
)
from ..agent_result import AgentResultStatus
from ..execution_model import (
    StateTransitionError,
    assert_report_consistent,
    assert_task_completion,
    assert_tasks_ready,
)
from ..failure import (
    FailureClassifier,
    FailureSource,
    RetryPolicy,
)
from ..logging import redact_text
from ..persistence import DegradationRecorder, event_emitter
from ..verdict import parse_verdict
from ..config import AppConfig
from ..headroom import HeadroomGate
from ..headroom_source import build_headroom_gate
from ..registry import AgentRegistry
from ..repo_inspection import RepositoryInspector
from ..routing import AgentRouter
from ..runner import AgentRunner, OperationCancelled
from ..state import StateStore
from ..tasks import Task, TaskStatus, utc_now
from ..verification import Verifier
from ..verification_kernel import VerificationKernel
from ..verification_model import VerificationReportStatus
from .support import WorkflowResult
from .verdicts import (
    _requires_review_verdict,
    _structured_agent_status,
    _explicit_success_denied,
)
from .security import _is_security_role
from .planning import PlanningMixin
from .routing import RoutingMixin
from .failures import FailureRecordingMixin
from .readiness import ReadinessMixin
from .agent_runs import AgentRunMixin
from .prompts import PromptMixin
from .review import ReviewRepairMixin


class WorkflowEngine(
    PlanningMixin,
    RoutingMixin,
    FailureRecordingMixin,
    ReadinessMixin,
    AgentRunMixin,
    PromptMixin,
    ReviewRepairMixin,
):
    def __init__(
        self,
        config: AppConfig,
        state: StateStore,
        registry: AgentRegistry,
        runner: AgentRunner,
        verifier: Verifier,
        run_observer: AgentRunObserver | None = None,
        metadata_collector: Callable[[str | Path], AgentRunMetadata] | None = None,
        verification_kernel: VerificationKernel | None = None,
        router: AgentRouter | None = None,
        degradation: DegradationRecorder | None = None,
        repo_inspector: RepositoryInspector | None = None,
        headroom_gate: HeadroomGate | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.config = config
        self.state = state
        self.registry = registry
        self.runner = runner
        self.verifier = verifier
        self.run_observer = run_observer
        self.metadata_collector = metadata_collector
        self.verification_kernel = verification_kernel
        self.router = router
        if self.router is None and getattr(config, "routing_enabled", True):
            self.router = AgentRouter()
        self.repo_inspector = (
            repo_inspector if repo_inspector is not None else RepositoryInspector()
        )
        # Opt-in routing gates.  Without a ``headroom`` config section there
        # is no gate, and without ``usage_limits`` no cooldown is read or
        # written: routing and classification stay exactly as before.
        self.clock = clock if clock is not None else wall_clock
        self.headroom_gate = (
            headroom_gate
            if headroom_gate is not None
            else build_headroom_gate(getattr(config, "headroom", None))
        )
        self._headroom_last_unavailable: str | None = None
        self._gate_rejections: dict[str, tuple[str, ...]] = {}
        # A8: persistence failures are classified instead of swallowed.  The
        # recorder emits a WARNING event through the same store it watches, so
        # it degrades to an in-memory entry when the store is the thing that broke.
        if degradation is None and getattr(state, "record_typed_event", None) is not None:
            degradation = DegradationRecorder(emit=event_emitter(state.record_typed_event))
        self.degradation = degradation if degradation is not None else DegradationRecorder()

    @property
    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.config.max_attempts,
            max_repair_cycles=self.config.max_repair_cycles,
            backoff_base_seconds=getattr(self.config, "backoff_base_seconds", 1.0),
            backoff_max_seconds=getattr(self.config, "backoff_max_seconds", 30.0),
            backoff_factor=getattr(self.config, "backoff_factor", 2.0),
        )

    async def execute(
        self,
        workflow_id: str,
        working_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> None:
        semaphore = asyncio.Semaphore(self.config.concurrency)

        async def run_limited(task: Task) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled
            async with semaphore:
                await self._execute_task(task, working_directory, cancel_event)

        cancelled = False
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise OperationCancelled
                ready = self.state.ready_tasks(workflow_id)
                if not ready:
                    break
                pending = [asyncio.create_task(run_limited(task)) for task in ready]
                try:
                    await asyncio.gather(*pending)
                except BaseException:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    raise
        except OperationCancelled:
            # Remembered for the refresh below: cancellation is a terminal
            # workflow outcome even when no task ever started to derive it.
            cancelled = True
            raise
        finally:
            # Derive the workflow row on EVERY exit, including cancellation:
            # OperationCancelled used to skip this refresh, so a cancelled
            # workflow stayed `pending` forever (its tasks were already
            # terminal, so `recover` could not repair it either) and the CLI
            # and GUI kept reporting a stopped operation as active. The
            # derivation only reads persisted task state -- a cancelled run
            # carries a FAILED task, so the row becomes FAILED, never success;
            # the cancelled flag covers cancellation before the first claim,
            # where every task is still pending and nothing derives failure.
            self.state.refresh_workflow_status(workflow_id, cancelled=cancelled)

    async def _execute_task(
        self,
        task: Task,
        working_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled
        claimed = self.state.claim_task(task.id)
        if claimed is None:
            # Another worker claimed this task concurrently; running it again
            # would duplicate work and corrupt task history.
            return
        task = claimed
        started = True
        # A well-formed REJECT verdict is the reviewer's judgment of this
        # change, not a flaky run. Re-running the same reviewer on the same,
        # unchanged code would let a second roll overturn it and reach READY
        # with no repair; the rejection must go to _repair_failed_review.
        verdict_rejected = False
        try:
            if task.role == "verification":
                if self.verification_kernel is not None:
                    report = await self.verification_kernel.run_verification(
                        task.workflow_id,
                        task.id,
                        working_directory,
                        source_agent_run_id=self._verification_source_run(task),
                        cancel_event=cancel_event,
                    )
                    task.verification_run_id = report.run_id
                    # A1: the report must be self-consistent before it can
                    # verify anything (fail-loud: violations propagate via
                    # the StateTransitionError re-raise below, never coerce).
                    assert_report_consistent(report)
                    # An agent claiming success can never set this flag: only a
                    # passed VerificationReport marks a task verified.
                    task.verified = report.overall_status is VerificationReportStatus.PASSED
                    task.result = report.transcript
                    if task.verified:
                        task.status = TaskStatus.PASSED
                    elif report.overall_status is VerificationReportStatus.UNVERIFIED:
                        # The procedure ran but established nothing -- e.g. every
                        # configured check was inapplicable to this working tree.
                        # There is no demonstrated defect, so BLOCKED, not FAILED:
                        # FAILED would send the workflow into repair logic for a
                        # defect no check actually found.
                        task.status = TaskStatus.BLOCKED
                    else:
                        task.status = TaskStatus.FAILED
                    if task.status is TaskStatus.FAILED:
                        self._record_verification_failure(task, report)
                else:
                    results = await self.verifier.run(working_directory, cancel_event)
                    # A1: an empty command suite is vacuous success and must
                    # not verify (supersedes Review #2 — see decisions.md).
                    succeeded = bool(results) and all(result.succeeded for result in results)
                    # Verification output is persisted evidence, so it follows
                    # the same redaction policy as agent output.
                    task.result = redact_text("\n".join(result.output for result in results))
                    # Legacy verification commands are verification evidence
                    # themselves; only agent-task success leaves verified False.
                    task.verified = succeeded
                    task.status = TaskStatus.PASSED if succeeded else TaskStatus.FAILED
                    if task.status is TaskStatus.FAILED:
                        self._record_legacy_verification_failure(task, results)
            else:
                if _is_security_role(task.role):
                    prescan_reason = self._security_prescan_reason(task, working_directory)
                    if prescan_reason is not None:
                        task.status = TaskStatus.FAILED
                        task.result = prescan_reason
                        self._record_review_verdict(task, None)
                        self.record_failure(
                            task,
                            FailureClassifier.classify(
                                source=FailureSource.REVIEW,
                                error=prescan_reason,
                                role=task.role,
                            ),
                            evidence=prescan_reason,
                            primary_error=prescan_reason,
                        )
                        task.finished_at = utc_now()
                        self.state.update_task(task)
                        return
                excluded = (
                    {task.assigned_agent} if task.assigned_agent and task.attempts > 1 else set()
                )
                repository_characteristics = None
                if getattr(self, "router", None) is not None:
                    # The bounded filesystem walk runs in a worker thread so a
                    # slow or hostile tree cannot block the event loop.
                    # Inspection is scoring input only: a failure here (an
                    # override that raises, a shut-down executor) degrades to
                    # empty hints so selection still routes, as it did when
                    # the call sat inside _select_agent's router guard.
                    try:
                        repository_characteristics = await asyncio.to_thread(
                            self._repository_characteristics,
                            working_directory,
                        )
                    except Exception:
                        repository_characteristics = {}
                    if self._routing_gate_active():
                        # The headroom command may take seconds; keep it
                        # (and the cooldown read) off the event loop too.
                        try:
                            routing_gate = await asyncio.to_thread(self._routing_gate, task)
                        except Exception:
                            routing_gate = ({}, [])
                    else:
                        routing_gate = None
                else:
                    routing_gate = None
                agent = self._select_agent(
                    task,
                    excluded,
                    working_directory,
                    repository_characteristics,
                    routing_gate=routing_gate,
                )
                if agent is None:
                    task.status = TaskStatus.FAILED
                    gate_rejections = self._gate_rejections.pop(task.id, ())
                    if gate_rejections:
                        task.result = (
                            f"No eligible agent for role '{task.role}': no installed agent supports it "
                            "after routing-gate rejections (" + " | ".join(gate_rejections) + ")."
                        )
                        if excluded:
                            task.result += (
                                f" Already tried and excluded: {', '.join(sorted(excluded))}."
                            )
                        self.state.event(
                            task.workflow_id, task.id, "no-agent-fallback", task.result
                        )
                    elif excluded:
                        task.result = (
                            f"No installed agent supports role '{task.role}' "
                            f"(already tried and excluded: {', '.join(sorted(excluded))})."
                        )
                        self.state.event(
                            task.workflow_id, task.id, "no-agent-fallback", task.result
                        )
                    else:
                        task.result = f"No installed agent supports role '{task.role}'."
                    self._record_no_agent_run(task, working_directory)
                    # Gate reasons are free text (window labels, policy names):
                    # classify on the fixed sentence so they cannot steer the
                    # category; the full text is still the recorded evidence.
                    classify_text = (
                        f"No installed agent supports role '{task.role}'."
                        if gate_rejections
                        else task.result
                    )
                    self.record_failure(
                        task,
                        FailureClassifier.classify(
                            source=FailureSource.ENVIRONMENT,
                            error=classify_text,
                            role=task.role,
                            agent_available=False,
                        ),
                        evidence=task.result,
                        primary_error=task.result,
                    )
                else:
                    task.assigned_agent = agent.config.name
                    result = await self._run_task_agent(
                        agent, task, working_directory, cancel_event=cancel_event
                    )
                    # Redact before persisting: the tasks table is a durable
                    # boundary and agent output can carry credential-like
                    # material. The log path still points at the full
                    # transcript for operators who need it.
                    task.result = (
                        f"log={result.log_path}\n"
                        f"{redact_text(result.stdout or '')}\n"
                        f"{redact_text(result.stderr or '')}"
                    ).strip()
                    if cancel_event is not None and cancel_event.is_set():
                        raise OperationCancelled
                    # exit 0 is evidence the PROCESS ran, not that the WORK was
                    # done. Check the role's evidence contract before crediting
                    # success: an agent that exited cleanly having changed nothing
                    # must not PASS a role whose job is to change code.
                    # Likewise an exit-0 run whose structured result explicitly
                    # says failure/partial did not do the work: exit_code == 0
                    # must never override the agent's own failure signal.
                    satisfied, reason = self._evidence_satisfied(task, result, working_directory)
                    agent_status = _structured_agent_status(result)
                    # Only an explicit SUCCESS (or no structured payload at all,
                    # the legacy plaintext path) counts as agent success.
                    # FAILURE, PARTIAL, and UNKNOWN must never be promoted.
                    structured_ok = agent_status in (None, AgentResultStatus.SUCCESS)
                    success_denied = _explicit_success_denied(result)
                    verdict_ok = True
                    verdict_reason = ""
                    verdict = None
                    if _requires_review_verdict(task.role):
                        verdict = parse_verdict(result.stdout)
                        verdict_ok = verdict is not None and verdict.is_approved
                        if verdict is None:
                            verdict_reason = (
                                "review verdict missing or malformed; expected the final output line "
                                "to contain a valid structured verdict"
                            )
                        elif not verdict.is_approved:
                            if verdict.blocking:
                                findings = redact_text("; ".join(verdict.blocking))[:1000]
                                verdict_reason = f"review verdict rejected the change; blocking findings: {findings}"
                            else:
                                verdict_reason = "review verdict rejected the change"
                    self._record_review_verdict(task, verdict)
                    verdict_rejected = verdict is not None and not verdict.is_approved
                    task.status = (
                        TaskStatus.PASSED
                        if (
                            result.succeeded
                            and satisfied
                            and structured_ok
                            and not success_denied
                            and verdict_ok
                        )
                        else TaskStatus.FAILED
                    )
                    if not satisfied and result.succeeded:
                        task.result = f"{task.result}\n\n{reason}".strip()
                    if not structured_ok and result.succeeded and satisfied:
                        task.result = (
                            f"{task.result}\n\nagent_status={agent_status.value}: the agent exited "
                            "cleanly but its structured result reports "
                            f"{agent_status.value}, so the task did not pass"
                        ).strip()
                    if success_denied and result.succeeded and satisfied and structured_ok:
                        task.result = (
                            f"{task.result}\n\nthe agent exited cleanly but its structured "
                            "output explicitly reports success=false, so the task did not pass"
                        ).strip()
                    if verdict_reason:
                        task.result = f"{task.result}\n\n{verdict_reason}".strip()
                    if task.status is TaskStatus.FAILED:
                        self._record_agent_failure(task, result)
        except StateTransitionError:
            # A1: invariant violations are programmer errors — never fold
            # them into task failure records; abort loudly so the suite
            # (or the operator) sees the real bug.
            raise
        except asyncio.CancelledError:
            if started:
                task.status = TaskStatus.FAILED
                task.result = "Task was cancelled."
                task.finished_at = utc_now()
                try:
                    self.record_failure(
                        task,
                        FailureClassifier.classify(source=FailureSource.SYSTEM, cancelled=True),
                        evidence=task.result,
                        primary_error="Task was cancelled.",
                    )
                except Exception as persist_error:
                    # A8: a lost failure row must stay visible even while
                    # the cancellation outcome itself is being recorded.
                    self.degradation.record(
                        "failure.create",
                        persist_error,
                        workflow_id=task.workflow_id,
                        task_id=task.id,
                    )
                self.state.update_task(task)
            raise
        except OperationCancelled:
            if started:
                task.status = TaskStatus.FAILED
                task.result = "Task was cancelled."
                task.finished_at = utc_now()
                try:
                    self.record_failure(
                        task,
                        FailureClassifier.classify(source=FailureSource.SYSTEM, cancelled=True),
                        evidence=task.result,
                        primary_error="Task was cancelled.",
                    )
                except Exception as persist_error:
                    # A8: a lost failure row must stay visible even while
                    # the cancellation outcome itself is being recorded.
                    self.degradation.record(
                        "failure.create",
                        persist_error,
                        workflow_id=task.workflow_id,
                        task_id=task.id,
                    )
                self.state.update_task(task)
            raise
        except Exception as error:
            task.status = TaskStatus.FAILED
            # An exception message can embed command output or a credential.
            task.result = redact_text(f"Task execution error: {error}")
            try:
                self.record_failure(
                    task,
                    FailureClassifier.classify(source=FailureSource.SYSTEM, error=str(error)),
                    evidence=task.result,
                    primary_error=str(error),
                )
            except Exception as persist_error:
                self.degradation.record(
                    "failure.create",
                    persist_error,
                    workflow_id=task.workflow_id,
                    task_id=task.id,
                )
        if (
            task.status is TaskStatus.FAILED
            and task.attempts < task.max_attempts
            and not verdict_rejected
        ):
            # Bounded retry: consult the deterministic policy (attempt budget
            # enforced by max_attempts) and back off, respecting cancellation.
            # Every retry creates a distinct AgentRun linked to its parent via
            # _agent_parent (RETRY/REPAIR); inherited context is injected via
            # _prompt_with_history on the next attempt.
            try:
                await self.retry_policy.asleep(task.attempts + 1)
            except asyncio.CancelledError:
                task.finished_at = utc_now()
                self.state.update_task(task)
                raise
            if cancel_event is not None and cancel_event.is_set():
                task.finished_at = utc_now()
                self.state.update_task(task)
                raise OperationCancelled
            task.status = TaskStatus.PENDING
            task.result = f"Attempt {task.attempts} failed; retrying.\n{task.result}"
        if task.status in {TaskStatus.PASSED, TaskStatus.FAILED}:
            task.finished_at = utc_now()
        if task.status is TaskStatus.PASSED:
            # A1: completion rules checked outside the try above so a
            # violation propagates instead of becoming a task failure.
            assert_task_completion(task)
        self.state.update_task(task)

    async def run_high_level(
        self,
        description: str,
        working_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> WorkflowResult:
        workflow_id, tasks = self.create_standard_workflow(description)
        await self.execute(workflow_id, working_directory, cancel_event)
        verification = self.state.get_task(tasks[2].id)
        if verification.status is TaskStatus.PASSED and verification.verified:
            review = self.state.get_task(tasks[3].id)
            if review.status is TaskStatus.PASSED:
                if len(tasks) > 4 and _is_security_role(tasks[4].role):
                    security = self.state.get_task(tasks[4].id)
                    if security.status is not TaskStatus.PASSED:
                        return WorkflowResult(
                            workflow_id,
                            False,
                            f"Security review was {security.status.value}; "
                            "the change is not ready.",
                        )
                # One authoritative READY rule: verification + review + evidence.
                ready = assert_tasks_ready(
                    self.state.list_tasks(workflow_id),
                    workflow_id,
                    verification_task_id=verification.id,
                    review_task_id=review.id,
                )
                return WorkflowResult(workflow_id, True, ready.summary())
            # Verification passed and the review rejected the change: a
            # demonstrated defect, so the same bounded repair policy that
            # covers a failed verification applies here.
            return await self._repair_failed_review(
                workflow_id,
                description,
                working_directory,
                cancel_event,
                tasks[1].id,
                review,
            )
        implementation = self.state.get_task(tasks[1].id)
        if implementation.status is not TaskStatus.PASSED:
            return WorkflowResult(
                workflow_id, False, "Implementation did not pass; verification was not run."
            )
        if verification.status is not TaskStatus.FAILED:
            # A repair cycle is warranted only by a DEMONSTRATED verification
            # failure. BLOCKED means verification could not obtain applicable
            # evidence (report UNVERIFIED) -- there is no failure to repair, and
            # sending a "Repair the configured verification failure" task would
            # re-introduce exactly the semantic collapse UNVERIFIED exists to
            # prevent: "I could not prove this succeeded" becoming "this failed".
            # Nothing was demonstrated wrong, so there is nothing to fix and no
            # review to gate. An operator needs applicable verification, not a
            # repair loop against an absent signal.
            return WorkflowResult(
                workflow_id,
                False,
                f"Verification was {verification.status.value}; no verification "
                f"failure was demonstrated, so no repair was attempted.",
            )
        for _ in range(self.config.max_repair_cycles):
            repair = self.state.add_task(
                Task(
                    "Repair the configured verification failure.\n" + (verification.result or ""),
                    "debugging",
                    workflow_id,
                    dependencies=(implementation.id,),
                    max_attempts=self.config.max_attempts,
                )
            )
            reverify = self.state.add_task(
                Task(
                    "Re-run configured project verification commands.",
                    "verification",
                    workflow_id,
                    dependencies=(repair.id,),
                    max_attempts=1,
                )
            )
            final_review = self.state.add_task(
                Task(
                    f"Review repaired change for: {description}",
                    "review",
                    workflow_id,
                    dependencies=(reverify.id,),
                    max_attempts=self.config.max_attempts,
                )
            )
            await self.execute(workflow_id, working_directory, cancel_event)
            verification = self.state.get_task(reverify.id)
            final_review = self.state.get_task(final_review.id)
            if verification.status is TaskStatus.PASSED and verification.verified:
                if final_review.status is TaskStatus.PASSED:
                    if getattr(self.config, "security_review", False):
                        security = await self._gate_repair_security(
                            workflow_id,
                            description,
                            working_directory,
                            cancel_event,
                            implementation.id,
                            final_review.id,
                        )
                        # An enabled gate must never read as a disabled one:
                        # None means the security task could not be created or
                        # read, which is a failure, not a waiver.
                        if security is None or security.status is not TaskStatus.PASSED:
                            return WorkflowResult(
                                workflow_id,
                                False,
                                "Repair or review did not pass; the security "
                                "review could not be completed.",
                            )
                    # One authoritative READY rule, scoped to this repair cycle's
                    # verification/review pair.
                    ready = assert_tasks_ready(
                        self.state.list_tasks(workflow_id),
                        workflow_id,
                        verification_task_id=verification.id,
                        review_task_id=final_review.id,
                    )
                    return WorkflowResult(workflow_id, True, ready.summary())
                return WorkflowResult(workflow_id, False, "Repair or review did not pass.")
        return WorkflowResult(workflow_id, False, "Repair or review did not pass.")
