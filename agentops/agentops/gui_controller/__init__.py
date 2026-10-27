"""Service facade used by the AgentOps desktop client.

This module keeps long-running operations off the Qt GUI thread and
provides a small cancellation contract.  It delegates all substantive work to
the existing AgentOps runner, workflow, Git, logging, and persistence modules.
"""

# Names imported here are re-exported on purpose: callers import them from
# agentops.gui_controller and tests patch them there.
# ruff: noqa: F401

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from collections.abc import Callable

from ..agent_run import AgentRun, AgentRunStatus, GitRunMetadataCollector
from ..artifacts import ArtifactError, ArtifactStore
from ..config import AppConfig, load_config
from ..finalize import (
    finalize_for_outcome,
    implementation_passed,
    record_worktree_provenance,
)
from ..git import GitError, GitWorktreeManager, Worktree, WorktreeRef
from ..tasks import Task, Workflow
from ..logging import LogManager
from ..registry import AgentRegistry
from ..runner import AgentRunner, OperationCancelled
from ..state import StateStore
from ..verification import Verifier
from ..verification_kernel import VerificationKernel
from ..execution_model import assess_workflow_readiness
from ..failure import Failure
from ..verification_model import VerificationCheck, VerificationReport, VerificationRun
from ..workflow import WorkflowEngine, STANDARD_TASK_ROLES, verdict_summary_for_task
from .serializers import (
    serialize_failure,
    serialize_task,
    serialize_workflow,
    serialize_worktree_ref,
    serialize_verification_check,
    serialize_verification_run,
    serialize_verification_report,
    serialize_agent_run,
)


EventCallback = Callable[[dict[str, object]], None]


@dataclass
class AgentOpsController:
    """Lifecycle and discovery facade for one AgentOps configuration."""

    config_path: Path | None = None
    state_path: Path | None = None
    config: AppConfig | None = None
    _cancel_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _operation_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _active: bool = field(default=False, init=False, repr=False)
    _root_cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _root_cache_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _load_config(self) -> AppConfig:
        return self.config if self.config is not None else load_config(self.config_path)

    def _operation_root(self, directory: str | Path) -> Path:
        # Cache git-root lookups: the GUI resolves this on every poll, and
        # each miss spawns a git process.  Roots essentially never move
        # within a controller's lifetime; failures are NOT cached.  The
        # lock makes the miss path single-flight so concurrent pollers do
        # not each spawn git for the same key.
        key = str(directory)
        with self._root_cache_lock:
            cached = self._root_cache.get(key)
            if cached is not None:
                return Path(cached)
            try:
                root = GitWorktreeManager().repository_root(directory)
            except GitError:
                return Path(directory)
            self._root_cache[key] = str(root)
            return root

    def _state_path(self, directory: str | Path) -> Path:
        if self.state_path is not None:
            return self.state_path
        return self._operation_root(directory) / ".agentops" / "state.sqlite"

    def _artifact_root(self, directory: str | Path) -> Path:
        return self._state_path(directory).parent / "artifacts"

    def cancel(self) -> None:
        """Request cancellation of the current operation, if one exists."""
        self._cancel_event.set()

    def _begin_operation(self) -> threading.Event:
        with self._operation_lock:
            if self._active:
                raise RuntimeError("An AgentOps operation is already running.")
            self._active = True
            # Fresh identity per operation: _end_operation's identity check
            # must reject a stale worker's event once a newer operation runs.
            self._cancel_event = threading.Event()
            return self._cancel_event

    def _end_operation(self, event: threading.Event) -> None:
        if event is self._cancel_event:
            with self._operation_lock:
                self._active = False

    def detect_agents(self) -> dict[str, object]:
        return AgentRegistry(self._load_config()).detect()

    def list_agent_profiles(self) -> list[dict[str, object]]:
        """Capability-rich agent profiles for the Agents surface (read-only)."""
        profiles = AgentRegistry(self._load_config()).profiles()
        return [profile.to_dict() for profile in profiles.values()]

    def task_options(self) -> dict[str, object]:
        """Config-derived choices offered by the New Task flow."""
        config = self._load_config()
        return {
            "routing_enabled": bool(config.routing_enabled),
            "verification_profiles": sorted(config.verification_profiles),
            "default_verification_profile": config.default_verification_profile,
        }

    def dashboard_summary(
        self,
        directory: str | Path,
        workflow_limit: int = 8,
        event_limit: int = 15,
        failure_limit: int = 10,
        verification_limit: int = 10,
    ) -> dict[str, object]:
        """One composed read for the Dashboard.

        Everything comes from persisted state: aggregate task counts, active
        workflow counts, recent workflow rollups, verification health over the
        newest verification runs, actionable failures (retryable, repairable,
        or carrying a recovery state), and the newest timeline events.
        """
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            task_counts: dict[str, int] = {}
            for task in state.list_tasks():
                key = str(task.status)
                task_counts[key] = task_counts.get(key, 0) + 1
            active = {status: state.count_workflows(status) for status in ("pending", "running")}
            recent = [
                self._workflow_payload(state, workflow)
                for workflow in state.list_workflows(limit=workflow_limit)
            ]
            verifications = [
                serialize_verification_run(run)
                for run in state.list_verification_runs(limit=verification_limit)
            ]
            verification_status: dict[str, int] = {}
            for run in verifications:
                key = str(run.get("overall_status") or run.get("status") or "unknown")
                verification_status[key] = verification_status.get(key, 0) + 1
            failures = [
                serialize_failure(failure) for failure in state.list_failures(limit=failure_limit)
            ]
            needs_attention = [
                failure
                for failure in failures
                if failure.get("retryable")
                or failure.get("repairable")
                or failure.get("recovery_state")
            ]
            total_events = state.count_events()
            events = [
                event.to_dict()
                for event in state.query_events(
                    limit=event_limit, offset=max(0, total_events - event_limit)
                )
            ]
            events.reverse()  # newest first for the activity feed
            return {
                "repository": root.as_posix(),
                "total_workflows": state.count_workflows(),
                "active_workflows": active,
                "task_counts": task_counts,
                "recent_workflows": recent,
                "latest_workflow_id": recent[0].get("id") if recent else None,
                "verification_status": verification_status,
                "recent_verifications": verifications,
                "failure_count": len(failures),
                "needs_attention": needs_attention,
                "recent_failures": failures,
                "recent_events": events,
            }
        finally:
            state.close()

    def run_agent(
        self,
        agent_name: str,
        prompt: str,
        directory: str | Path,
        callback: EventCallback,
    ) -> None:
        event = self._begin_operation()
        threading.Thread(
            target=self._run_agent_operation,
            args=(agent_name, prompt, Path(directory), event, callback),
            daemon=True,
            name="agentops-agent-run",
        ).start()

    def _run_agent_operation(
        self,
        agent_name: str,
        prompt: str,
        directory: Path,
        cancel_event: threading.Event,
        callback: EventCallback,
    ) -> None:
        try:
            if not directory.is_dir():
                raise NotADirectoryError(f"Repository directory does not exist: {directory}")
            config = self._load_config()
            agent = AgentRegistry(config).get(agent_name)
            root = self._operation_root(directory)
            logs = LogManager(root / ".agentops" / "logs")
            state: StateStore | None = None
            try:
                state = StateStore(self._state_path(root))
                observer = state.agent_run_observer()
            except Exception:
                state = None
                observer = None
            try:
                runner = AgentRunner(
                    logs,
                    config.pass_env_names,
                    config.pass_env_prefixes,
                    run_observer=observer,
                    systemd_run_enabled=config.systemd_run_enabled,
                )
                result = asyncio.run(
                    runner.run_agent(agent, prompt, directory, cancel_event=cancel_event)
                )
            finally:
                if state is not None:
                    state.close()
            callback(
                {
                    "kind": "result",
                    "agent": result.agent,
                    "succeeded": result.succeeded,
                    "timed_out": result.timed_out,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_seconds": result.duration_seconds,
                    "log_path": str(result.log_path),
                    "run_id": result.run_id,
                    "run_status": (
                        AgentRunStatus.COMPLETED.value
                        if result.succeeded
                        else AgentRunStatus.TIMED_OUT.value
                        if result.timed_out
                        else AgentRunStatus.TERMINATED.value
                        if result.terminated
                        else AgentRunStatus.FAILED.value
                    ),
                }
            )
        except OperationCancelled:
            callback({"kind": "cancelled"})
        except Exception as error:
            callback({"kind": "error", "error": error})
        finally:
            self._end_operation(cancel_event)
            callback({"kind": "thread-finished"})

    def run_task(
        self,
        description: str,
        directory: str | Path,
        callback: EventCallback,
        *,
        agent: str | None = None,
        verification_profile: str | None = None,
    ) -> None:
        """Start a high-level task workflow on a background thread.

        ``agent`` pins the operator's explicit strategy: every standard task
        role and every configured role preference resolves to that agent
        first. Availability, role, and capability gates still apply, so an
        ineligible pinned agent falls back exactly as the router's documented
        preference behavior allows. ``verification_profile`` selects the
        verification profile for this workflow instead of the configuration
        default; an unknown profile fails the operation with a clear error
        instead of silently running different checks.
        """
        event = self._begin_operation()
        threading.Thread(
            target=self._run_task_operation,
            args=(description, Path(directory), event, callback, agent, verification_profile),
            daemon=True,
            name="agentops-task-run",
        ).start()

    def _run_task_operation(
        self,
        description: str,
        directory: Path,
        cancel_event: threading.Event,
        callback: EventCallback,
        agent: str | None = None,
        verification_profile: str | None = None,
    ) -> None:
        state: StateStore | None = None
        manager: GitWorktreeManager | None = None
        worktree = None
        remove_worktree = False
        try:
            if not directory.is_dir():
                raise NotADirectoryError(f"Repository directory does not exist: {directory}")
            config = self._load_config()
            if agent is not None:
                pinned_roles = set(config.role_preferences) | set(STANDARD_TASK_ROLES)
                config = replace(config, role_preferences={role: (agent,) for role in pinned_roles})
            root = self._operation_root(directory)
            logs = LogManager(root / ".agentops" / "logs")
            state = StateStore(self._state_path(root))
            registry = AgentRegistry(config)
            run_observer = state.agent_run_observer()
            runner = AgentRunner(
                logs,
                config.pass_env_names,
                config.pass_env_prefixes,
                run_observer=run_observer,
                metadata_collector=GitRunMetadataCollector(),
                systemd_run_enabled=config.systemd_run_enabled,
            )
            verifier = Verifier(
                config.verification_commands,
                pass_env_names=config.pass_env_names,
                pass_env_prefixes=config.pass_env_prefixes,
                systemd_run_enabled=config.systemd_run_enabled,
            )
            verification_kernel = VerificationKernel(
                profiles=config.verification_profiles,
                default_profile=(
                    verification_profile
                    if verification_profile is not None
                    else config.default_verification_profile
                ),
                legacy_commands=config.verification_commands,
                pass_env_names=config.pass_env_names,
                pass_env_prefixes=config.pass_env_prefixes,
                logs=logs,
                state=state,
                systemd_run_enabled=config.systemd_run_enabled,
            )
            engine = WorkflowEngine(
                config,
                state,
                registry,
                runner,
                verifier,
                run_observer=run_observer,
                metadata_collector=GitRunMetadataCollector(),
                verification_kernel=verification_kernel,
            )
            manager = GitWorktreeManager()
            worktree = manager.create(root, description)
            callback(
                {
                    "kind": "workflow-started",
                    "worktree": str(worktree.path),
                    "base_branch": worktree.base_branch,
                }
            )
            result = asyncio.run(
                engine.run_high_level(description, worktree.path, cancel_event=cancel_event)
            )
            # Phase 3: persist provenance against the real workflow id so
            # retry/merge validate against stored base even after restart.
            record_worktree_provenance(state, worktree, result.workflow_id, engine.degradation)
            if cancel_event.is_set():
                raise OperationCancelled
            changed = False
            if result.ready or implementation_passed(state, result.workflow_id):
                # One authoritative rule (finalize.finalize_for_outcome): READY
                # merges, not-READY commits and preserves. Previously the GUI
                # merged only on `result.ready` while the CLI also merged on
                # implementation-passed, so identical work was finalized
                # differently depending on the entry point.
                finalization = finalize_for_outcome(
                    manager,
                    state,
                    worktree,
                    description,
                    result.workflow_id,
                    config.max_attempts,
                    ready=result.ready,
                )
                if finalization.conflict_error is not None:
                    callback(
                        {
                            "kind": "conflict",
                            "error": finalization.conflict_error,
                            "worktree": str(worktree.path),
                            "workflow_id": result.workflow_id,
                        }
                    )
                    return
                changed = finalization.merged
                remove_worktree = result.ready
            callback(
                {
                    "kind": "workflow-result",
                    "result": result,
                    "ready": result.ready,
                    "workflow_id": result.workflow_id,
                    "worktree": str(worktree.path),
                    "merged": changed,
                }
            )
        except OperationCancelled:
            callback({"kind": "cancelled"})
        except Exception as error:
            callback({"kind": "error", "error": error})
        finally:
            if state is not None:
                state.close()
            if worktree is not None and remove_worktree and manager is not None:
                with suppress(GitError):
                    manager.remove(worktree)
            self._end_operation(cancel_event)
            callback({"kind": "thread-finished"})

    def list_tasks(
        self,
        directory: str | Path,
        workflow_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """All tasks (optionally one workflow/status), newest first.

        Read-only facade for the Tasks surface; ``state.list_tasks`` stores
        rows oldest-first, so the payload is reversed for display.
        """
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("Task list limit and offset must be non-negative integers.")
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            tasks = state.list_tasks(workflow_id)
            if status is not None:
                tasks = [
                    task
                    for task in tasks
                    if (task.status.value if hasattr(task.status, "value") else str(task.status))
                    == status
                ]
            tasks.reverse()
            return [serialize_task(task) for task in tasks[offset : offset + limit]]
        finally:
            state.close()

    # -- task management -------------------------------------------------
    #: Roles the Tasks surface allows when recategorizing. The engine builds
    #: the standard four plus ad-hoc debugging tasks, so anything outside
    #: this set would render as a custom DAG node the pipeline never owns.
    TASK_ROLES = STANDARD_TASK_ROLES + ("debugging",)

    @staticmethod
    def _task_status(task: Task) -> str:
        status = task.status
        return str(getattr(status, "value", status))

    def rename_task(
        self, directory: str | Path, task_id: str, description: str
    ) -> dict[str, object]:
        """Replace a task's description (its display name everywhere)."""
        text = str(description or "").strip()
        if not text:
            raise ValueError("A task description cannot be empty.")
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            task = state.get_task(str(task_id))
            if self._task_status(task) == "running":
                raise ValueError("A running task cannot be renamed.")
            task.description = text
            state.update_task(task)
            state.refresh_workflow_status(task.workflow_id)
            return serialize_task(task)
        finally:
            state.close()

    def recategorize_task(
        self, directory: str | Path, task_id: str, role: str
    ) -> dict[str, object]:
        """Move a task to another role (its category in the pipeline)."""
        wanted = str(role or "").strip().lower()
        if wanted not in self.TASK_ROLES:
            raise ValueError(f"Unknown role {role!r}; choose one of {', '.join(self.TASK_ROLES)}.")
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            task = state.get_task(str(task_id))
            status = self._task_status(task)
            if status not in ("pending", "blocked", "failed"):
                raise ValueError(
                    f"Only pending, blocked, or failed tasks can change role (task is {status})."
                )
            task.role = wanted
            state.update_task(task)
            state.refresh_workflow_status(task.workflow_id)
            return serialize_task(task)
        finally:
            state.close()

    def delete_task(self, directory: str | Path, task_id: str) -> dict[str, object]:
        """Remove a task row; recorded runs keep their history text.

        Refuses live work (a running task) and the workflow's last task,
        so execution and the header status always have something to derive
        from.
        """
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            task = state.get_task(str(task_id))
            if self._task_status(task) == "running":
                raise ValueError("A running task cannot be deleted.")
            siblings = state.list_tasks(task.workflow_id)
            if len(siblings) <= 1:
                raise ValueError("A workflow keeps at least one task.")
            removed = state.delete_task(task.id)
            if not removed:
                raise KeyError(f"Unknown task: {task_id}")
            state.refresh_workflow_status(task.workflow_id)
            return {"id": task.id, "workflow_id": task.workflow_id, "description": task.description}
        finally:
            state.close()

    @staticmethod
    def _record_status(record: object) -> str:
        status = getattr(record, "status", "")
        return str(getattr(status, "value", status))

    def rename_workflow(
        self, directory: str | Path, workflow_id: str, description: str
    ) -> dict[str, object]:
        """Replace a workflow's description (its display name everywhere)."""
        text = str(description or "").strip()
        if not text:
            raise ValueError("A workflow description cannot be empty.")
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            workflow = state.get_workflow(str(workflow_id))
            if workflow is None:
                raise KeyError(f"Unknown workflow: {workflow_id}")
            workflow.description = text
            if not state.rename_workflow(workflow.id, text):
                raise KeyError(f"Unknown workflow: {workflow_id}")
            return serialize_workflow(workflow)
        finally:
            state.close()

    def delete_workflow(self, directory: str | Path, workflow_id: str) -> dict[str, object]:
        """Remove a workflow and every record that belongs to it.

        Refuses live work (a running workflow or any running task). The
        on-disk worktree and its branch are deliberately left alone for
        the Worktrees surface to clean up.
        """
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            workflow = state.get_workflow(str(workflow_id))
            if workflow is None:
                raise KeyError(f"Unknown workflow: {workflow_id}")
            if self._record_status(workflow) == "running":
                raise ValueError("A running workflow cannot be deleted.")
            live = [
                task.id
                for task in state.list_tasks(workflow.id)
                if self._task_status(task) == "running"
            ]
            if live:
                raise ValueError("A workflow with running tasks cannot be deleted.")
            artifacts = []
            offset = 0
            while True:
                page = state.list_artifacts(workflow_id=workflow.id, limit=1000, offset=offset)
                if not page:
                    break
                artifacts.extend(page)
                offset += len(page)
            if artifacts:
                store = ArtifactStore(self._artifact_root(root))
                for artifact in artifacts:
                    if artifact.rel_path:
                        store.delete(artifact)
            removed = state.delete_workflow(workflow.id)
            if not removed.get("workflows"):
                raise KeyError(f"Unknown workflow: {workflow_id}")
            return {"id": workflow.id, "description": workflow.description, "removed": removed}
        finally:
            state.close()

    def delete_agent_run(self, directory: str | Path, run_id: str) -> dict[str, object]:
        """Remove an agent run with its linked verification runs.

        Failure rows keep plain-text references and stay readable. Refuses
        live runs and runs that follow-up runs still reference.
        """
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            run = state.get_agent_run(str(run_id))
            if self._record_status(run) in ("pending", "starting", "running"):
                raise ValueError("A live agent run cannot be deleted.")
            if not state.delete_agent_run(run.id):
                raise ValueError("This run has follow-up runs; delete those first.")
            return {"id": run.id, "workflow_id": run.workflow_id, "task_id": run.task_id}
        finally:
            state.close()

    def delete_verification_run(self, directory: str | Path, run_id: str) -> dict[str, object]:
        """Remove a verification run with its checks and report."""
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            run = state.get_verification_run(str(run_id))
            if self._record_status(run) in ("pending", "running"):
                raise ValueError("A live verification run cannot be deleted.")
            if not state.delete_verification_run(run.id):
                raise KeyError(f"Unknown verification run: {run_id}")
            return {"id": run.id, "workflow_id": run.workflow_id, "task_id": run.task_id}
        finally:
            state.close()

    def delete_failure(self, directory: str | Path, failure_id: str) -> dict[str, object]:
        """Remove one failure record."""
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            if not state.delete_failure(str(failure_id)):
                raise KeyError(f"Unknown failure: {failure_id}")
            return {"id": str(failure_id)}
        finally:
            state.close()

    def delete_artifact(self, directory: str | Path, artifact_id: str) -> dict[str, object]:
        """Remove an artifact's file and its metadata row together."""
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            artifact = state.get_artifact(str(artifact_id))
            if artifact is None:
                raise KeyError(f"Unknown artifact: {artifact_id}")
            store = ArtifactStore(self._artifact_root(root))
            try:
                store.delete(artifact)
            except ArtifactError as error:
                raise ValueError(str(error)) from error
            state.delete_artifact_record(artifact.id)
            return {"id": artifact.id, "name": artifact.name}
        finally:
            state.close()

    def list_workflows(
        self,
        directory: str | Path,
        limit: int = 25,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, object]:
        """Phase 1: StateStore returns Workflow DTOs; controller emits plain dicts."""
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            total = state.count_workflows(status)
            workflows: list[dict[str, object]] = []
            for workflow in state.list_workflows(limit=limit, offset=offset, status=status):
                workflows.append(self._workflow_payload(state, workflow))
            return {"total": total, "limit": limit, "offset": offset, "workflows": workflows}
        finally:
            state.close()

    @staticmethod
    def _workflow_payload(state: StateStore, workflow: Workflow) -> dict[str, object]:
        """One workflow header plus task/run rollups for list surfaces."""
        tasks = state.list_tasks(workflow.id)
        counts: dict[str, int] = {}
        for task in tasks:
            key = str(task.status)
            counts[key] = counts.get(key, 0) + 1
        runs = state.list_agent_runs(workflow.id, limit=200)
        run_counts: dict[str, int] = {}
        for run in runs:
            key = run.status.value
            run_counts[key] = run_counts.get(key, 0) + 1
        payload = serialize_workflow(workflow)
        review_verdicts: list[dict[str, object]] = []
        for task in tasks:
            try:
                summary = verdict_summary_for_task(task)
            except Exception:
                summary = None
            if summary is not None:
                review_verdicts.append({"task_id": task.id, **summary})
        payload.update(
            {
                "task_count": len(tasks),
                "task_counts": counts,
                "run_count": len(runs),
                "run_counts": run_counts,
                "review_verdicts": review_verdicts,
            }
        )
        return payload

    def get_workflow(self, directory: str | Path, workflow_id: str) -> dict[str, object] | None:
        """Phase 1+3: DTO-backed payload with serialized tasks + stored provenance."""
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            workflow = state.get_workflow(workflow_id)
            if workflow is None:
                return None
            tasks = [serialize_task(task) for task in state.list_tasks(workflow.id)]
            runs = [
                serialize_agent_run(run) for run in state.list_agent_runs(workflow.id, limit=200)
            ]
            verifications = [
                serialize_verification_run(run)
                for run in state.list_verification_runs(workflow.id, limit=50)
            ]
            failures = [serialize_failure(f) for f in state.list_failures(workflow.id, limit=50)]
            ref = state.get_worktree_ref(workflow.id)
            payload = serialize_workflow(workflow)
            payload.update(
                {
                    "tasks": tasks,
                    "runs": runs,
                    "verifications": verifications,
                    "failures": failures,
                    "worktree_ref": serialize_worktree_ref(ref) if ref is not None else None,
                    "review_verdicts": [
                        dict(task["review_verdict"], task_id=task["id"])
                        for task in tasks
                        if task.get("review_verdict") is not None
                    ],
                }
            )
            return payload
        finally:
            state.close()

    def list_agent_runs(
        self,
        directory: str | Path,
        workflow_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            run_status = AgentRunStatus(status) if status else None
            return [
                serialize_agent_run(run)
                for run in state.list_agent_runs(workflow_id, task_id, run_status, limit, offset)
            ]
        finally:
            state.close()

    def list_recent_agent_runs(
        self,
        directory: str | Path,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """Newest agent runs first, windowed from the end of history.

        ``state.list_agent_runs`` stores rows oldest-first; the Runs surface
        wants the newest window without paging through the whole table.
        ``offset`` steps further back into older history.
        """
        if not isinstance(limit, int) or limit < 1 or not isinstance(offset, int) or offset < 0:
            raise ValueError("Run list limit must be positive and offset non-negative.")
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            total = state.count_agent_runs()
            start = max(0, total - limit - offset)
            read_limit = min(limit, max(0, total - offset))
            rows = state.list_agent_runs(limit=read_limit, offset=start)
            rows.reverse()
            return [serialize_agent_run(run) for run in rows]
        finally:
            state.close()

    def get_agent_run(self, directory: str | Path, run_id: str) -> dict[str, object] | None:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            try:
                return serialize_agent_run(state.get_agent_run(run_id))
            except KeyError:
                return None
        finally:
            state.close()

    def list_verification_runs(
        self,
        directory: str | Path,
        workflow_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            return [
                serialize_verification_run(run)
                for run in state.list_verification_runs(
                    workflow_id, task_id, limit=limit, offset=offset
                )
            ]
        finally:
            state.close()

    def list_recent_verification_runs(
        self,
        directory: str | Path,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """Newest verification runs first, windowed from the end of history."""
        if not isinstance(limit, int) or limit < 1 or not isinstance(offset, int) or offset < 0:
            raise ValueError("Run list limit must be positive and offset non-negative.")
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            total = state.count_verification_runs()
            start = max(0, total - limit - offset)
            read_limit = min(limit, max(0, total - offset))
            rows = state.list_verification_runs(limit=read_limit, offset=start)
            rows.reverse()
            return [serialize_verification_run(run) for run in rows]
        finally:
            state.close()

    def get_verification_run(self, directory: str | Path, run_id: str) -> dict[str, object] | None:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            try:
                run = state.get_verification_run(run_id)
                report = state.get_verification_report_by_run(run_id)
                checks = state.list_verification_checks(run_id)
            except KeyError:
                return None
            return {
                "run": serialize_verification_run(run),
                "report": serialize_verification_report(report),
                "checks": [serialize_verification_check(check) for check in checks],
            }
        finally:
            state.close()

    def list_failures(
        self,
        directory: str | Path,
        workflow_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            return [
                serialize_failure(failure)
                for failure in state.list_failures(workflow_id, task_id, limit=limit, offset=offset)
            ]
        finally:
            state.close()

    def list_recent_failures(
        self,
        directory: str | Path,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """Newest failures first, windowed from the end of history."""
        if not isinstance(limit, int) or limit < 1 or not isinstance(offset, int) or offset < 0:
            raise ValueError("Failure list limit must be positive and offset non-negative.")
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            total = state.count_failures()
            start = max(0, total - limit - offset)
            read_limit = min(limit, max(0, total - offset))
            rows = state.list_failures(limit=read_limit, offset=start)
            rows.reverse()
            return [serialize_failure(failure) for failure in rows]
        finally:
            state.close()

    def interrupted_work(self, directory: str | Path) -> dict[str, int]:
        """Read-only interrupted-work counts for the shell recovery banner.

        Mirrors :meth:`recover_interrupted` without mutating anything: the
        counts are ``StateStore.count_interrupted_work``'s read-only copy of
        the ``recover_all()`` predicates, plus their ``total`` for display.
        """
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            counts = state.count_interrupted_work()
        finally:
            state.close()
        return {**counts, "total": sum(counts.values())}

    def recover_interrupted(
        self, directory: str | Path, workflow_id: str | None = None
    ) -> dict[str, object]:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            # Every pass must honour workflow_id: scoping only the task pass
            # would terminate another workflow's live rows.
            return dict(
                state.recover_all()
                if workflow_id is None
                else {
                    "agent_runs": len(state.recover_agent_runs(workflow_id)),
                    "verification_runs": len(state.recover_verification_runs(workflow_id)),
                    "tasks": len(state.recover_tasks(workflow_id)),
                }
            )
        finally:
            state.close()

    def query_events(
        self,
        directory: str | Path,
        workflow_id: str | None = None,
        task_id: str | None = None,
        agent_run_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            return [
                event.to_dict()
                for event in state.query_events(
                    workflow_id, task_id, agent_run_id, event_type, limit, offset
                )
            ]
        finally:
            state.close()

    def list_artifacts(
        self,
        directory: str | Path,
        workflow_id: str | None = None,
        task_id: str | None = None,
        kind: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            return [
                artifact.to_dict()
                for artifact in state.list_artifacts(workflow_id, task_id, kind, limit, offset)
            ]
        finally:
            state.close()

    def read_artifact(
        self, directory: str | Path, artifact_id: str, max_bytes: int = 65536
    ) -> dict[str, object]:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            artifact = state.get_artifact(artifact_id)
            if artifact is None:
                raise ValueError(f"Unknown artifact {artifact_id}")
            store = ArtifactStore(self._artifact_root(root))
            try:
                text = store.read_text(artifact, max_bytes)
            except ArtifactError as error:
                raise ValueError(str(error)) from error
            return {"artifact": artifact.to_dict(), "text": text}
        finally:
            state.close()

    def list_logs(
        self,
        directory: str | Path,
        task_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("Log list limit must be a positive integer.")
        root = self._operation_root(directory)
        logs = LogManager(root / ".agentops" / "logs")
        entries: list[dict[str, object]] = []
        for path in logs.list_logs(task_id)[:limit]:
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append(
                {
                    "path": str(path),
                    "name": path.relative_to(logs.root).as_posix(),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
        return entries

    def read_log(
        self, directory: str | Path, name: str | Path, max_bytes: int = 65536
    ) -> dict[str, object]:
        root = self._operation_root(directory)
        logs = LogManager(root / ".agentops" / "logs")
        text, truncated = logs.read_tail(name, max_bytes)
        return {
            "name": str(name),
            "path": str((logs.root / name).resolve()),
            "truncated": truncated,
            "text": text,
        }

    def list_worktrees(self, directory: str | Path) -> list[dict[str, object]]:
        root = self._operation_root(directory)
        manager = GitWorktreeManager()
        entries: list[dict[str, object]] = []
        for info in manager.list_worktrees(root):
            try:
                status = manager.worktree_status(info.path)
            except GitError:
                status = "(status unavailable)"
            entries.append(
                {
                    "repository": str(info.repository),
                    "path": str(info.path),
                    "branch": info.branch,
                    "head": info.head,
                    "managed": info.managed,
                    "status": status,
                }
            )
        return entries

    def inspect_worktree(self, directory: str | Path, path: str | Path) -> dict[str, object]:
        root = self._operation_root(directory)
        return GitWorktreeManager().inspect_worktree(root, path)

    def cleanup_worktree(
        self,
        directory: str | Path,
        path: str | Path,
        delete_unmerged_branch: bool = False,
    ) -> dict[str, object]:
        root = self._operation_root(directory)
        return GitWorktreeManager().cleanup_worktree(root, path, delete_unmerged_branch)

    def retry_merge(self, directory: str | Path, path: str | Path) -> dict[str, object]:
        """Re-attempt a merge for a preserved worktree (manual recovery path).

        This is an explicit operator action, not a readiness decision, so it
        deliberately does not run the READY contract: the workflow that
        produced the worktree may no longer exist in state, and the operator is
        choosing to merge now. The normal flow already gates merges on
        ``result.ready`` (see ``_run_operation`` and ``cli.run``).

        The safety gates here are the ones that still matter for a blind
        re-merge: the worktree must be clean, must be a managed ``agentops/*``
        branch, and must merge against the stored base branch/commit when
        provenance exists, so a moved base is still refused by ``merge()``.
        """
        root = self._operation_root(directory)
        manager = GitWorktreeManager()
        info = manager.inspect_worktree(root, path)
        if str(info.get("status", "")).strip():
            raise GitError("Refusing to retry a merge with uncommitted worktree changes.")
        branch = info.get("branch")
        if not isinstance(branch, str) or not branch.startswith("agentops/"):
            raise GitError("Retry is only supported for managed agentops/* worktree branches.")
        state = StateStore(self._state_path(root))
        try:
            stored = state.find_worktree_ref_by_path(str(info["path"]))
        finally:
            state.close()
        if stored is not None:
            # Stored provenance wins: the retry must target the original base
            # branch/commit, not whatever HEAD happens to be now.
            worktree = Worktree(
                root, Path(str(info["path"])), stored.branch, stored.base_branch, stored.base_commit
            )
        else:
            worktree = Worktree(
                root,
                Path(str(info["path"])),
                branch,
                manager.current_branch(root),
                manager.current_commit(root),
            )
        manager.merge(worktree)
        return {
            "merged": True,
            "path": str(info["path"]),
            "branch": worktree.branch,
            "base_branch": worktree.base_branch,
            "used_stored_provenance": stored is not None,
        }

    def workflow_readiness(self, directory: str | Path, workflow_id: str) -> dict[str, object]:
        """READY assessment for one workflow, as read by the GUI.

        A read-only projection of the single READY contract in
        :mod:`agentops.execution_model`, which the engine already uses to gate
        merges. The controller adds no readiness rule of its own, so a surface
        reading this can never claim a weaker or stronger merge state than the
        engine would act on.
        """
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            readiness = assess_workflow_readiness(state.list_tasks(workflow_id), workflow_id)
        finally:
            state.close()
        return {
            "workflow_id": readiness.workflow_id,
            "ready": readiness.ready,
            "reasons": list(readiness.reasons),
            "verification_ok": readiness.verification_ok,
            "review_ok": readiness.review_ok,
            "evidence_present": readiness.evidence_present,
        }

    def get_worktree_ref(self, directory: str | Path, workflow_id: str) -> dict[str, object] | None:
        """Phase 3: expose persisted provenance for one workflow."""
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            ref = state.get_worktree_ref(workflow_id)
            return serialize_worktree_ref(ref) if ref is not None else None
        finally:
            state.close()

    def list_worktree_refs(
        self, directory: str | Path, workflow_id: str | None = None
    ) -> list[dict[str, object]]:
        """Phase 3: list persisted provenance records."""
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            return [serialize_worktree_ref(ref) for ref in state.list_worktree_refs(workflow_id)]
        finally:
            state.close()

    def latest_workflow(self, directory: str | Path) -> dict[str, object] | None:
        """Phase 1+3: DTO-backed latest payload with serialized tasks + provenance."""
        state = StateStore(self._state_path(directory))
        try:
            workflow = state.latest_workflow()
            if workflow is None:
                return None
            tasks = [serialize_task(task) for task in state.list_tasks(workflow.id)]
            runs = [
                serialize_agent_run(run) for run in state.list_agent_runs(workflow.id, limit=200)
            ]
            verifications = [
                serialize_verification_run(run)
                for run in state.list_verification_runs(workflow.id, limit=50)
            ]
            ref = state.get_worktree_ref(workflow.id)
            payload = serialize_workflow(workflow)
            payload.update(
                {
                    "tasks": tasks,
                    "runs": runs,
                    "verifications": verifications,
                    "worktree_ref": serialize_worktree_ref(ref) if ref is not None else None,
                }
            )
            return payload
        finally:
            state.close()
