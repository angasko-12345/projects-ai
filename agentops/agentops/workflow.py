"""Dependency-aware task orchestration with agent fallback and repairs."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .agent_run import (
    AgentRun,
    AgentRunContext,
    AgentRunMetadata,
    AgentRunObserver,
    AgentRunOutcome,
    AgentRunRelationship,
    AgentRunStatus,
    GitRunMetadataCollector,
)
from .agent_result import parse_agent_result
from .execution_model import (
    StateTransitionError,
    assert_report_consistent,
    assert_task_completion,
    assert_workflow_ready,
)
from .failure import (
    Failure,
    FailureCategory,
    FailureClassifier,
    FailureSeverity,
    FailureSource,
    InterruptionContext,
    RepairAction,
    RepairPlan,
    RetryPolicy,
    build_retry_prompt,
    new_failure_id,
)
from .config import AppConfig
from .registry import AgentRegistry
from .runner import AgentRunner, OperationCancelled, RunResult
from .state import StateStore
from .tasks import Task, TaskStatus, utc_now
from .verification import Verifier
from .verification_kernel import VerificationKernel
from .verification_model import VerificationReportStatus


def _safe_workflow_structured(stdout: object) -> object | None:
    """Best-effort structured fallback for post-hoc run recording."""
    try:
        if not isinstance(stdout, str):
            return None
        parsed = parse_agent_result(stdout)
        result = parsed.result
        extra_warnings = tuple(dict.fromkeys((*result.warnings, *parsed.warnings)))
        metadata = dict(result.metadata)
        metadata.setdefault("parse_mode", parsed.parse_mode.value)
        from dataclasses import replace
        return replace(result, warnings=extra_warnings, metadata=metadata).to_dict()
    except Exception:
        return None


class _RecordingObserver:
    """Delegate that reports whether the runner persisted a run."""

    def __init__(self, delegate: AgentRunObserver):
        self._delegate = delegate
        self.created_run_id: str | None = None

    def create_run(self, context: AgentRunContext) -> str | AgentRun:
        created = self._delegate.create_run(context)
        self.created_run_id = created.id if isinstance(created, AgentRun) else str(created)
        return created

    def mark_starting(self, run_id: str) -> None:
        self._delegate.mark_starting(run_id)

    def mark_running(self, run_id: str) -> None:
        self._delegate.mark_running(run_id)

    def finish_run(self, run_id: str, outcome: AgentRunOutcome) -> None:
        self._delegate.finish_run(run_id, outcome)

    def fail_run(self, run_id: str, error: BaseException, classification: str) -> None:
        self._delegate.fail_run(run_id, error, classification)


@dataclass(frozen=True)
class WorkflowResult:
    workflow_id: str
    ready: bool
    summary: str


class WorkflowEngine:
    def __init__(self, config: AppConfig, state: StateStore, registry: AgentRegistry,
                 runner: AgentRunner, verifier: Verifier,
                 run_observer: AgentRunObserver | None = None,
                 metadata_collector: Callable[[str | Path], AgentRunMetadata] | None = None,
                 verification_kernel: VerificationKernel | None = None):
        self.config = config
        self.state = state
        self.registry = registry
        self.runner = runner
        self.verifier = verifier
        self.run_observer = run_observer
        self.metadata_collector = metadata_collector
        self.verification_kernel = verification_kernel

    @property
    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.config.max_attempts,
            max_repair_cycles=self.config.max_repair_cycles,
            backoff_base_seconds=getattr(self.config, "backoff_base_seconds", 1.0),
            backoff_max_seconds=getattr(self.config, "backoff_max_seconds", 30.0),
            backoff_factor=getattr(self.config, "backoff_factor", 2.0),
        )

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
            evidence=(evidence or primary_error or task.result or "")[:4000] or None,
            primary_error=(primary_error or task.result or "")[:2000] or None,
            verification_run_id=verification_run_id,
            recommended_action=classification.recommended_action,
            attempt=max(1, task.attempts),
            repair_cycle=0,
            recovery_state=recovery_state,
        )
        try:
            return self.state.create_failure(failure)
        except Exception:
            return failure

    def recover_incomplete(self, workflow_id: str | None = None) -> dict[str, object]:
        """Crash recovery: detect incomplete operations and classify them.

        Runs the state-level recovery passes (agent runs, verification runs,
        tasks) and records a Failure row per recovered task with the matching
        interruption context. Never marks anything successful: recovered tasks
        stay FAILED and recovered runs stay TERMINATED/FAILED. Worktrees are
        preserved by the caller (CLI/GUI never deletes on failure/conflict).
        """
        summary = self.state.recover_all() if workflow_id is None else {
            "agent_runs": len(self.state.recover_agent_runs(workflow_id)),
            "verification_runs": len(self.state.recover_verification_runs(workflow_id)),
            "tasks": len(self.state.recover_tasks(workflow_id)),
        }
        states: list[str] = []
        tasks = self.state.list_tasks(workflow_id) if workflow_id else []
        for task in tasks:
            if task.status is not TaskStatus.FAILED:
                continue
            if task.result is None or "interrupted" not in task.result.lower():
                continue
            context = (
                InterruptionContext.VERIFICATION if task.role == "verification" else
                InterruptionContext.REVIEW if task.role == "review" else
                InterruptionContext.AGENT_EXECUTION
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
                evidence=task.result[:4000] if task.result else None,
                primary_error="Task was interrupted before a terminal outcome was recorded.",
                verification_run_id=task.verification_run_id,
                recommended_action=action,
                attempt=max(1, task.attempts),
                recovery_state=recovery_state,
            )
            try:
                self.state.create_failure(failure)
            except Exception:
                pass
        summary["recovery_states"] = states
        return summary

    def create_standard_workflow(self, description: str) -> tuple[str, list[Task]]:
        workflow_id = self.state.create_workflow(description)
        plan = self.state.add_task(Task(f"Plan a safe implementation for: {description}", "architecture", workflow_id,
                                        max_attempts=self.config.max_attempts))
        implementation = self.state.add_task(Task(f"Implement: {description}", "implementation", workflow_id,
                                                  dependencies=(plan.id,), max_attempts=self.config.max_attempts))
        verification = self.state.add_task(Task("Run configured project verification commands.", "verification", workflow_id,
                                                dependencies=(implementation.id,), max_attempts=1))
        review = self.state.add_task(Task(f"Review the completed change for: {description}", "review", workflow_id,
                                          dependencies=(verification.id,), max_attempts=self.config.max_attempts))
        return workflow_id, [plan, implementation, verification, review]

    def create_workflow(self, description: str, specifications: list[dict[str, object]]) -> tuple[str, list[Task]]:
        """Create a user-defined dependency graph from a validated workflow file."""
        identifiers: dict[str, str] = {}
        for index, specification in enumerate(specifications):
            identifier = specification.get("id", f"task-{index + 1}")
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise ValueError("Each workflow task needs a unique string id.")
            identifiers[identifier] = str(uuid4())
        self._validate_acyclic(specifications, identifiers)
        workflow_id = self.state.create_workflow(description)
        tasks: list[Task] = []
        for index, specification in enumerate(specifications):
            identifier = specification.get("id", f"task-{index + 1}")
            description_value = specification.get("description")
            role = specification.get("role")
            dependencies = specification.get("dependencies", [])
            if not isinstance(description_value, str) or not isinstance(role, str):
                raise ValueError("Each workflow task requires string description and role fields.")
            if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
                raise ValueError("Workflow task dependencies must be a list of task ids.")
            unknown = [item for item in dependencies if item not in identifiers]
            if unknown:
                raise ValueError(f"Unknown task dependencies: {', '.join(unknown)}")
            task = Task(description_value, role, workflow_id, id=identifiers[identifier],
                        dependencies=tuple(identifiers[item] for item in dependencies),
                        max_attempts=self.config.max_attempts)
            tasks.append(self.state.add_task(task))
        return workflow_id, tasks

    @staticmethod
    def _validate_acyclic(specifications: list[dict[str, object]], identifiers: dict[str, str]) -> None:
        dependencies_by_id: dict[str, set[str]] = {}
        for index, specification in enumerate(specifications):
            identifier = specification.get("id", f"task-{index + 1}")
            dependencies = specification.get("dependencies", [])
            if not isinstance(identifier, str) or not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
                raise ValueError("Workflow task dependencies must be a list of task ids.")
            if identifier in dependencies:
                raise ValueError(f"Workflow task '{identifier}' cannot depend on itself.")
            dependencies_by_id[identifier] = set(dependencies)
        unknown = sorted({dependency for dependencies in dependencies_by_id.values() for dependency in dependencies if dependency not in identifiers})
        if unknown:
            raise ValueError(f"Unknown task dependencies: {', '.join(unknown)}")
        resolved: set[str] = set()
        pending = dict(dependencies_by_id)
        while pending:
            ready = [identifier for identifier, dependencies in pending.items() if dependencies <= resolved]
            if not ready:
                raise ValueError("Workflow task dependencies contain a cycle.")
            for identifier in ready:
                resolved.add(identifier)
                pending.pop(identifier)

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
        self.state.refresh_workflow_status(workflow_id)

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
        try:
            if task.role == "verification":
                if self.verification_kernel is not None:
                    report = await self.verification_kernel.run_verification(
                        task.workflow_id, task.id, working_directory,
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
                    task.status = TaskStatus.PASSED if task.verified else TaskStatus.FAILED
                    if task.status is TaskStatus.FAILED:
                        self._record_verification_failure(task, report)
                else:
                    results = await self.verifier.run(working_directory, cancel_event)
                    # A1: an empty command suite is vacuous success and must
                    # not verify (supersedes Review #2 — see decisions.md).
                    succeeded = bool(results) and all(result.succeeded for result in results)
                    task.result = "\n".join(result.output for result in results)
                    # Legacy verification commands are verification evidence
                    # themselves; only agent-task success leaves verified False.
                    task.verified = succeeded
                    task.status = TaskStatus.PASSED if succeeded else TaskStatus.FAILED
                    if task.status is TaskStatus.FAILED:
                        classification = FailureClassifier.classify(
                            source=FailureSource.VERIFICATION,
                            error=task.result, role=task.role,
                        )
                        self.record_failure(
                            task, classification,
                            verification_run_id=task.verification_run_id,
                            evidence=task.result, primary_error="Legacy verification commands failed.",
                        )
            else:
                excluded = {task.assigned_agent} if task.assigned_agent and task.attempts > 1 else set()
                agent = self.registry.select(task.role, excluded)
                if agent is None:
                    task.status = TaskStatus.FAILED
                    if excluded:
                        task.result = (
                            f"No installed agent supports role '{task.role}' "
                            f"(already tried and excluded: {', '.join(sorted(excluded))})."
                        )
                        self.state.event(task.workflow_id, task.id, "no-agent-fallback", task.result)
                    else:
                        task.result = f"No installed agent supports role '{task.role}'."
                    self._record_no_agent_run(task, working_directory)
                    self.record_failure(
                        task,
                        FailureClassifier.classify(
                            source=FailureSource.ENVIRONMENT, error=task.result,
                            role=task.role, agent_available=False,
                        ),
                        evidence=task.result, primary_error=task.result,
                    )
                else:
                    task.assigned_agent = agent.config.name
                    result = await self._run_task_agent(
                        agent, task, working_directory, cancel_event=cancel_event
                    )
                    task.result = f"log={result.log_path}\n{result.stdout}\n{result.stderr}".strip()
                    if cancel_event is not None and cancel_event.is_set():
                        raise OperationCancelled
                    task.status = TaskStatus.PASSED if result.succeeded else TaskStatus.FAILED
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
                        evidence=task.result, primary_error="Task was cancelled.",
                    )
                except Exception:
                    pass
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
                        evidence=task.result, primary_error="Task was cancelled.",
                    )
                except Exception:
                    pass
                self.state.update_task(task)
            raise
        except Exception as error:
            task.status = TaskStatus.FAILED
            task.result = f"Task execution error: {error}"
            try:
                self.record_failure(
                    task,
                    FailureClassifier.classify(source=FailureSource.SYSTEM, error=str(error)),
                    evidence=task.result, primary_error=str(error),
                )
            except Exception:
                pass
        if task.status is TaskStatus.FAILED and task.attempts < task.max_attempts:
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

    def _record_agent_failure(self, task: Task, result) -> None:
        try:
            run = self.state.latest_agent_run(task.workflow_id, task.id)
            run_id = run.id if run is not None else None
        except Exception:
            run_id = None
        classification = FailureClassifier.classify(
            source=FailureSource.AGENT,
            exit_code=getattr(result, "exit_code", None),
            timed_out=bool(getattr(result, "timed_out", False)),
            cancelled=bool(getattr(result, "cancelled", False)),
            terminated=bool(getattr(result, "terminated", False)),
            error=task.result,
            role=task.role,
        )
        try:
            self.record_failure(
                task, classification, agent_run_id=run_id,
                verification_run_id=task.verification_run_id,
                evidence=task.result, primary_error=task.result,
            )
        except Exception:
            pass

    def _record_verification_failure(self, task: Task, report) -> None:
        failing = [check for check in getattr(report, "checks", ()) if check.status.value != "passed"]
        check_class = failing[0].check_class.value if failing else None
        try:
            run = self.state.latest_agent_run(task.workflow_id, task.id)
            run_id = run.id if run is not None else self._verification_source_run(task)
        except Exception:
            run_id = self._verification_source_run(task)
        classification = FailureClassifier.classify(
            source=FailureSource.VERIFICATION,
            error=task.result, check_class=check_class, role=task.role,
        )
        try:
            self.record_failure(
                task, classification, agent_run_id=run_id,
                verification_run_id=getattr(report, "run_id", None),
                evidence=task.result, primary_error="Verification report did not pass.",
            )
        except Exception:
            pass

    def _verification_source_run(self, task: Task) -> str | None:
        candidates = []
        for dependency in task.dependencies:
            try:
                run = self.state.latest_agent_run(task.workflow_id, dependency)
            except Exception:
                continue
            if run is not None:
                candidates.append(run)
        if not candidates:
            return None
        return max(candidates, key=lambda run: (run.created_at, run.id)).id

    def verification_evidence(self, workflow_id: str) -> list[Task]:
        """Return verification tasks backed by passed verification evidence.

        Kernel path: verified flag plus a linked VerificationReport run.
        Legacy path: verified flag plus the command-output transcript (the
        output IS the evidence — no run object exists there).  Single
        definition used by READY gating everywhere (Track A1).
        """
        return [
            task for task in self.state.list_tasks(workflow_id)
            if task.role == "verification" and task.verified and (
                task.verification_run_id or (task.result or "").strip()
            )
        ]

    def _run_observer_for_task(self) -> AgentRunObserver | None:
        if self.run_observer is not None:
            return self.run_observer
        factory = getattr(self.state, "agent_run_observer", None)
        if callable(factory):
            try:
                observer = factory()
            except Exception:
                return None
            return observer if hasattr(observer, "create_run") else None
        return None

    def _agent_parent(self, task: Task) -> tuple[AgentRunRelationship, str | None]:
        try:
            if task.role == "debugging":
                candidates = []
                for dependency in task.dependencies:
                    dependency_run = self.state.latest_agent_run(task.workflow_id, dependency)
                    if dependency_run is not None:
                        candidates.append(dependency_run)
                if candidates:
                    parent = max(candidates, key=lambda run: (run.created_at, run.id))
                    return AgentRunRelationship.REPAIR, parent.id
                parent = self.state.latest_agent_run(task.workflow_id, statuses=(AgentRunStatus.FAILED,))
                return AgentRunRelationship.REPAIR, parent.id if parent is not None else None
            if task.attempts > 1:
                parent = self.state.latest_agent_run(task.workflow_id, task.id)
                if parent is not None:
                    return AgentRunRelationship.RETRY, parent.id
        except Exception:
            return AgentRunRelationship.ROOT, None
        return AgentRunRelationship.ROOT, None

    def _agent_run_context(
        self,
        task: Task,
        prompt: str,
        working_directory: str | Path,
        agent: object | None = None,
    ) -> AgentRunContext:
        relationship, parent_run_id = self._agent_parent(task)
        resolved = str(Path(working_directory).resolve())
        if agent is None:
            return AgentRunContext(
                workflow_id=task.workflow_id,
                task_id=task.id,
                attempt=task.attempts,
                role=task.role,
                working_directory=resolved,
                worktree=resolved,
                prompt=prompt,
                parent_run_id=parent_run_id,
                relationship=relationship,
            )
        return AgentRunContext(
            agent=agent.config.name,
            executable=agent.executable or agent.config.command,
            role=task.role,
            model=getattr(agent.config, "model", None),
            workflow_id=task.workflow_id,
            task_id=task.id,
            attempt=task.attempts,
            working_directory=resolved,
            worktree=resolved,
            prompt=prompt,
            command=self.runner.build_command(agent, prompt),
            parent_run_id=parent_run_id,
            relationship=relationship,
        )

    def _record_no_agent_run(self, task: Task, working_directory: str | Path) -> None:
        observer = self._run_observer_for_task()
        if observer is None:
            return
        context = self._agent_run_context(task, "", working_directory)
        try:
            created = observer.create_run(context)
            run_id = created.id if isinstance(created, AgentRun) else str(created)
            observer.mark_starting(run_id)
            observer.mark_running(run_id)
            observer.finish_run(
                run_id,
                AgentRunOutcome(
                    status=AgentRunStatus.FAILED,
                    error=task.result,
                    failure_classification="no_agent",
                ),
            )
        except Exception:
            pass

    def _record_completed_run(
        self,
        observer: AgentRunObserver,
        context: AgentRunContext,
        result: RunResult,
        working_directory: str | Path,
    ) -> None:
        try:
            collector = self.metadata_collector or GitRunMetadataCollector()
            try:
                metadata = collector(working_directory)
            except Exception:
                metadata = AgentRunMetadata()
            status = (
                AgentRunStatus.TIMED_OUT if result.timed_out else
                AgentRunStatus.COMPLETED if result.succeeded else
                AgentRunStatus.FAILED
            )
            stdout = getattr(result, "stdout", "")
            created = observer.create_run(context)
            run_id = created.id if isinstance(created, AgentRun) else str(created)
            observer.finish_run(
                run_id,
                AgentRunOutcome(
                    status=status,
                    exit_code=result.exit_code,
                    duration_seconds=getattr(result, "duration_seconds", None),
                    cancelled=bool(getattr(result, "cancelled", False)),
                    timed_out=result.timed_out,
                    terminated=bool(getattr(result, "terminated", False)),
                    stdout_path=str(getattr(result, "stdout_path", result.log_path)),
                    stderr_path=str(getattr(result, "stderr_path", result.log_path)),
                    log_path=str(result.log_path),
                    files_changed=metadata.files_changed,
                    diff_stat=metadata.diff_stat,
                    failure_classification=(
                        None if status is AgentRunStatus.COMPLETED else
                        "timeout" if status is AgentRunStatus.TIMED_OUT else
                        "nonzero_exit"
                    ),
                    structured_result=getattr(result, "structured_result", None)
                    or _safe_workflow_structured(stdout),
                ),
            )
        except Exception:
            pass

    def _record_cancelled_run(
        self, observer: AgentRunObserver, context: AgentRunContext, duration: float | None
    ) -> None:
        try:
            created = observer.create_run(context)
            run_id = created.id if isinstance(created, AgentRun) else str(created)
            observer.finish_run(
                run_id,
                AgentRunOutcome(
                    status=AgentRunStatus.CANCELLED,
                    duration_seconds=duration,
                    cancelled=True,
                    failure_classification="cancelled",
                ),
            )
        except Exception:
            pass

    def _record_failed_run(
        self, observer: AgentRunObserver, context: AgentRunContext, error: BaseException, classification: str
    ) -> None:
        try:
            created = observer.create_run(context)
            run_id = created.id if isinstance(created, AgentRun) else str(created)
            observer.fail_run(run_id, error, classification)
        except Exception:
            pass

    def _supported_runner_kwargs(self) -> dict[str, object]:
        try:
            parameters = inspect.signature(self.runner.run_agent).parameters
        except (TypeError, ValueError):
            return {}
        accepts_keywords = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        )
        supported: dict[str, object] = {}
        for name in ("run_context", "run_observer", "metadata_collector"):
            if name in parameters or accepts_keywords:
                supported[name] = True
        return supported

    async def _run_task_agent(
        self,
        agent: object,
        task: Task,
        working_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        observer = self._run_observer_for_task()
        try:
            prompt = self._prompt_with_history(task, working_directory)
        except Exception:
            prompt = self._prompt(task, working_directory)
        context = self._agent_run_context(task, prompt, working_directory, agent)
        supported = self._supported_runner_kwargs()
        collector = self.metadata_collector or GitRunMetadataCollector()
        call_kwargs: dict[str, object] = {}
        if "run_context" in supported:
            call_kwargs["run_context"] = context
        if "run_observer" in supported and observer is not None:
            call_kwargs["run_observer"] = observer
        if "metadata_collector" in supported:
            call_kwargs["metadata_collector"] = collector
        started = monotonic()
        if "run_observer" in supported and observer is not None:
            # A runner that understands the observer normally owns the
            # complete pending -> running -> terminal sequence.  The recorder
            # preserves that behavior while retaining a fallback for runner
            # doubles that accept, but ignore, observer arguments.
            recorder = _RecordingObserver(observer)
            call_kwargs["run_observer"] = recorder
            try:
                result = await self.runner.run_agent(
                    agent, prompt, working_directory, task.id,
                    cancel_event=cancel_event, **call_kwargs,  # type: ignore[arg-type]
                )
            except OperationCancelled:
                if recorder.created_run_id is None:
                    self._record_cancelled_run(observer, context, monotonic() - started)
                raise
            except Exception as error:
                if recorder.created_run_id is None:
                    self._record_failed_run(observer, context, error, "process_error")
                raise
            if recorder.created_run_id is not None:
                return result
            self._record_completed_run(observer, context, result, working_directory)
            return result
        if "run_observer" in supported:
            return await self.runner.run_agent(
                agent, prompt, working_directory, task.id,
                cancel_event=cancel_event, **call_kwargs,  # type: ignore[arg-type]
            )
        run_id: str | None = None
        started = monotonic()
        if observer is not None:
            try:
                created = observer.create_run(context)
                run_id = created.id if isinstance(created, AgentRun) else str(created)
                observer.mark_starting(run_id)
                observer.mark_running(run_id)
            except Exception:
                run_id = None
        try:
            result = await self.runner.run_agent(
                agent, prompt, working_directory, task.id,
                cancel_event=cancel_event, **call_kwargs,  # type: ignore[arg-type]
            )
        except OperationCancelled:
            if observer is not None and run_id is not None:
                try:
                    observer.finish_run(
                        run_id,
                        AgentRunOutcome(
                            status=AgentRunStatus.CANCELLED,
                            duration_seconds=monotonic() - started,
                            cancelled=True,
                            failure_classification="cancelled",
                        ),
                    )
                except Exception:
                    pass
            raise
        except Exception as error:
            if observer is not None and run_id is not None:
                try:
                    observer.fail_run(run_id, error, "process_error")
                except Exception:
                    pass
            raise
        if observer is not None and run_id is not None:
            try:
                metadata = AgentRunMetadata()
                try:
                    metadata = collector(working_directory)
                except Exception:
                    pass
                status = (
                    AgentRunStatus.TIMED_OUT if result.timed_out else
                    AgentRunStatus.COMPLETED if result.succeeded else
                    AgentRunStatus.FAILED
                )
                observer.finish_run(
                    run_id,
                    AgentRunOutcome(
                        status=status,
                        exit_code=result.exit_code,
                        duration_seconds=result.duration_seconds,
                        cancelled=bool(getattr(result, "cancelled", False)),
                        timed_out=result.timed_out,
                        terminated=bool(getattr(result, "terminated", False)),
                        stdout_path=str(getattr(result, "stdout_path", result.log_path)),
                        stderr_path=str(getattr(result, "stderr_path", result.log_path)),
                        log_path=str(result.log_path),
                        files_changed=metadata.files_changed,
                        diff_stat=metadata.diff_stat,
                        failure_classification=None if status is AgentRunStatus.COMPLETED else "nonzero_exit",
                        structured_result=getattr(result, "structured_result", None)
                        or _safe_workflow_structured(result.stdout),
                    ),
                )
            except Exception:
                pass
        return result

    def _prompt_with_history(self, task: Task, working_directory: str | Path) -> str:
        base = (
            f"You are the {task.role} agent for AgentOps task {task.id}.\n"
            f"Workspace: {Path(working_directory).resolve()}\n"
            f"Request: {task.description}\n"
            "Work only in this workspace. Do not read secrets or alter AgentOps configuration. "
            "Summarize changes and tests in your final response. "
            "Optionally end with a JSON AgentResult object "
            '{"schema_version": 1, "status": "success|failure|partial|unknown", '
            '"summary": "...", "files_changed": [], "tests_run": 0, '
            '"tests_passed": 0, "tests_failed": 0}; plain-text summaries remain valid.'
        )
        if task.attempts <= 1:
            return base
        try:
            failures = self.state.list_failures(task.workflow_id, task.id, limit=1)
        except Exception:
            return base
        if not failures:
            return base
        try:
            return build_retry_prompt(base, failures[0])
        except Exception:
            return base

    @staticmethod
    def _prompt(task: Task, working_directory: str | Path) -> str:
        return (
            f"You are the {task.role} agent for AgentOps task {task.id}.\n"
            f"Workspace: {Path(working_directory).resolve()}\n"
            f"Request: {task.description}\n"
            "Work only in this workspace. Do not read secrets or alter AgentOps configuration. "
            "Summarize changes and tests in your final response. "
            "Optionally end with a JSON AgentResult object "
            '{"schema_version": 1, "status": "success|failure|partial|unknown", '
            '"summary": "...", "files_changed": [], "tests_run": 0, '
            '"tests_passed": 0, "tests_failed": 0}; plain-text summaries remain valid.'
        )

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
                # A1: READY requires verification + review + evidence.
                assert_workflow_ready(
                    verification_ok=True, review_ok=True,
                    evidence_present=bool(self.verification_evidence(workflow_id)),
                    workflow_id=workflow_id,
                )
                return WorkflowResult(workflow_id, True, "READY")
            return WorkflowResult(workflow_id, False, "Review did not pass.")
        implementation = self.state.get_task(tasks[1].id)
        if implementation.status is not TaskStatus.PASSED:
            return WorkflowResult(workflow_id, False, "Implementation did not pass; verification was not run.")
        for _ in range(self.config.max_repair_cycles):
            repair = self.state.add_task(Task("Repair the configured verification failure.\n" + (verification.result or ""),
                                              "debugging", workflow_id, dependencies=(implementation.id,),
                                              max_attempts=self.config.max_attempts))
            reverify = self.state.add_task(Task("Re-run configured project verification commands.", "verification", workflow_id,
                                                dependencies=(repair.id,), max_attempts=1))
            final_review = self.state.add_task(Task(f"Review repaired change for: {description}", "review", workflow_id,
                                                    dependencies=(reverify.id,), max_attempts=self.config.max_attempts))
            await self.execute(workflow_id, working_directory, cancel_event)
            verification = self.state.get_task(reverify.id)
            final_review = self.state.get_task(final_review.id)
            if verification.status is TaskStatus.PASSED and verification.verified:
                if final_review.status is TaskStatus.PASSED:
                    # A1: READY requires verification + review + evidence.
                    assert_workflow_ready(
                        verification_ok=True, review_ok=True,
                        evidence_present=bool(self.verification_evidence(workflow_id)),
                        workflow_id=workflow_id,
                    )
                    return WorkflowResult(workflow_id, True, "READY")
                return WorkflowResult(workflow_id, False, "Repair or review did not pass.")
        return WorkflowResult(workflow_id, False, "Repair or review did not pass.")
