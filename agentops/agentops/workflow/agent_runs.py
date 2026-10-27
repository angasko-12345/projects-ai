"""Agent-run observation, recording and the agent invocation itself."""

from __future__ import annotations

import inspect
import threading
from pathlib import Path
from time import monotonic

from ..agent_run import (
    AgentRun,
    AgentRunContext,
    AgentRunMetadata,
    AgentRunObserver,
    AgentRunOutcome,
    AgentRunRelationship,
    AgentRunStatus,
    GitRunMetadataCollector,
)
from ..runner import OperationCancelled, RunResult
from ..tasks import Task
from .support import _RecordingObserver
from .verdicts import _safe_workflow_structured


class AgentRunMixin:
    """WorkflowEngine methods: agent-run observation, recording and the agent invocation itself."""

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
                parent = self.state.latest_agent_run(
                    task.workflow_id, statuses=(AgentRunStatus.FAILED,)
                )
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

    def _note_run_degradation(
        self,
        error: BaseException,
        *,
        run_id: str | None = None,
        task: Task | None = None,
        context: AgentRunContext | None = None,
    ) -> None:
        """A8: a post-hoc run record that could not be stored is a lost run.

        The runner's own observer notifications are covered in ``runner.py``;
        these are the engine-side fallbacks used when the runner did not create
        the run itself.  Without them the loss was completely silent.
        """
        self.degradation.record(
            "agent_run.create" if run_id is None else "agent_run.transition",
            error,
            run_id=run_id,
            workflow_id=getattr(context, "workflow_id", None) or getattr(task, "workflow_id", None),
            task_id=getattr(context, "task_id", None) or getattr(task, "id", None),
        )

    def _record_no_agent_run(self, task: Task, working_directory: str | Path) -> None:
        observer = self._run_observer_for_task()
        if observer is None:
            return
        context = self._agent_run_context(task, "", working_directory)
        run_id: str | None = None
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
        except Exception as error:
            self._note_run_degradation(error, run_id=run_id, task=task, context=context)

    def _record_completed_run(
        self,
        observer: AgentRunObserver,
        context: AgentRunContext,
        result: RunResult,
        working_directory: str | Path,
    ) -> None:
        run_id: str | None = None
        try:
            collector = self.metadata_collector or GitRunMetadataCollector()
            try:
                metadata = collector(working_directory)
            except Exception:
                metadata = AgentRunMetadata()
            status = (
                AgentRunStatus.TIMED_OUT
                if result.timed_out
                else AgentRunStatus.COMPLETED
                if result.succeeded
                else AgentRunStatus.FAILED
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
                        None
                        if status is AgentRunStatus.COMPLETED
                        else "timeout"
                        if status is AgentRunStatus.TIMED_OUT
                        else "nonzero_exit"
                    ),
                    structured_result=getattr(result, "structured_result", None)
                    or _safe_workflow_structured(stdout),
                ),
            )
        except Exception as error:
            self._note_run_degradation(error, run_id=run_id, context=context)

    def _record_cancelled_run(
        self, observer: AgentRunObserver, context: AgentRunContext, duration: float | None
    ) -> None:
        run_id: str | None = None
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
        except Exception as error:
            self._note_run_degradation(error, run_id=run_id, context=context)

    def _record_failed_run(
        self,
        observer: AgentRunObserver,
        context: AgentRunContext,
        error: BaseException,
        classification: str,
    ) -> None:
        run_id: str | None = None
        try:
            created = observer.create_run(context)
            run_id = created.id if isinstance(created, AgentRun) else str(created)
            observer.fail_run(run_id, error, classification)
        except Exception as persist_error:
            self._note_run_degradation(persist_error, run_id=run_id, context=context)

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
                    agent,
                    prompt,
                    working_directory,
                    task.id,
                    cancel_event=cancel_event,
                    **call_kwargs,  # type: ignore[arg-type]
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
                agent,
                prompt,
                working_directory,
                task.id,
                cancel_event=cancel_event,
                **call_kwargs,  # type: ignore[arg-type]
            )
        run_id: str | None = None
        started = monotonic()
        if observer is not None:
            try:
                created = observer.create_run(context)
                run_id = created.id if isinstance(created, AgentRun) else str(created)
                observer.mark_starting(run_id)
                observer.mark_running(run_id)
            except Exception as error:
                self._note_run_degradation(error, run_id=run_id, context=context)
                run_id = None
        try:
            result = await self.runner.run_agent(
                agent,
                prompt,
                working_directory,
                task.id,
                cancel_event=cancel_event,
                **call_kwargs,  # type: ignore[arg-type]
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
                except Exception as error:
                    self._note_run_degradation(error, run_id=run_id, context=context)
            raise
        except Exception as error:
            if observer is not None and run_id is not None:
                try:
                    observer.fail_run(run_id, error, "process_error")
                except Exception as persist_error:
                    self._note_run_degradation(persist_error, run_id=run_id, context=context)
            raise
        if observer is not None and run_id is not None:
            try:
                metadata = AgentRunMetadata()
                try:
                    metadata = collector(working_directory)
                except Exception:
                    pass
                status = (
                    AgentRunStatus.TIMED_OUT
                    if result.timed_out
                    else AgentRunStatus.COMPLETED
                    if result.succeeded
                    else AgentRunStatus.FAILED
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
                        failure_classification=None
                        if status is AgentRunStatus.COMPLETED
                        else "nonzero_exit",
                        structured_result=getattr(result, "structured_result", None)
                        or _safe_workflow_structured(result.stdout),
                    ),
                )
            except Exception as persist_error:
                self._note_run_degradation(persist_error, run_id=run_id, context=context)
        return result
