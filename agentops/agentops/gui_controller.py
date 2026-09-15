"""Service facade used by the AgentOps desktop client.

This module keeps long-running operations off the Tkinter event loop and
provides a small cancellation contract.  It delegates all substantive work to
the existing AgentOps runner, workflow, Git, logging, and persistence modules.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .agent_run import AgentRun, AgentRunStatus, GitRunMetadataCollector
from .config import AppConfig, load_config
from .finalize import finalize_worktree
from .git import GitError, GitWorktreeManager, Worktree
from .logging import LogManager
from .registry import AgentRegistry
from .runner import AgentRunner, OperationCancelled
from .state import StateStore
from .verification import Verifier
from .verification_kernel import VerificationKernel
from .failure import Failure
from .verification_model import VerificationCheck, VerificationReport, VerificationRun
from .workflow import WorkflowEngine


def serialize_failure(failure: Failure) -> dict[str, object]:
    return {
        "id": failure.id,
        "workflow_id": failure.workflow_id,
        "task_id": failure.task_id,
        "agent_run_id": failure.agent_run_id,
        "source": failure.source.value if hasattr(failure.source, "value") else str(failure.source),
        "category": failure.category.value if hasattr(failure.category, "value") else str(failure.category),
        "severity": failure.severity.value if hasattr(failure.severity, "value") else str(failure.severity),
        "retryable": failure.retryable,
        "repairable": failure.repairable,
        "evidence": failure.evidence,
        "primary_error": failure.primary_error,
        "verification_run_id": failure.verification_run_id,
        "recommended_action": failure.recommended_action.value if hasattr(failure.recommended_action, "value") else str(failure.recommended_action),
        "attempt": failure.attempt,
        "repair_cycle": failure.repair_cycle,
        "recovery_state": failure.recovery_state.value if failure.recovery_state and hasattr(failure.recovery_state, "value") else failure.recovery_state,
        "created_at": failure.created_at,
        "updated_at": failure.updated_at,
    }


EventCallback = Callable[[dict[str, object]], None]


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


@dataclass
class AgentOpsController:
    """Lifecycle and discovery facade for one AgentOps configuration."""

    config_path: Path | None = None
    state_path: Path | None = None
    config: AppConfig | None = None
    _cancel_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _operation_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _active: bool = field(default=False, init=False, repr=False)

    def _load_config(self) -> AppConfig:
        return self.config if self.config is not None else load_config(self.config_path)

    def _operation_root(self, directory: str | Path) -> Path:
        try:
            return GitWorktreeManager().repository_root(directory)
        except GitError:
            return Path(directory)

    def _state_path(self, directory: str | Path) -> Path:
        if self.state_path is not None:
            return self.state_path
        return self._operation_root(directory) / ".agentops" / "state.sqlite"

    def cancel(self) -> None:
        """Request cancellation of the current operation, if one exists."""
        self._cancel_event.set()

    def _begin_operation(self) -> threading.Event:
        with self._operation_lock:
            if self._active:
                raise RuntimeError("An AgentOps operation is already running.")
            self._active = True
            self._cancel_event.clear()
            return self._cancel_event

    def _end_operation(self, event: threading.Event) -> None:
        if event is self._cancel_event:
            with self._operation_lock:
                self._active = False

    def detect_agents(self) -> dict[str, object]:
        return AgentRegistry(self._load_config()).detect()

    def run_agent(
        self,
        agent_name: str,
        prompt: str,
        directory: str | Path,
        callback: EventCallback,
    ) -> None:
        event = self._begin_operation()
        threading.Thread(target=self._run_agent_operation, args=(agent_name, prompt, Path(directory), event, callback),
                         daemon=True, name="agentops-agent-run").start()

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
                runner = AgentRunner(logs, config.pass_env_names, config.pass_env_prefixes,
                                     run_observer=observer)
                result = asyncio.run(runner.run_agent(agent, prompt, directory, cancel_event=cancel_event))
            finally:
                if state is not None:
                    state.close()
            callback({
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
                    AgentRunStatus.COMPLETED.value if result.succeeded
                    else AgentRunStatus.TIMED_OUT.value if result.timed_out
                    else AgentRunStatus.TERMINATED.value if result.terminated
                    else AgentRunStatus.FAILED.value
                ),
            })
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
    ) -> None:
        event = self._begin_operation()
        threading.Thread(target=self._run_task_operation, args=(description, Path(directory), event, callback),
                         daemon=True, name="agentops-task-run").start()

    def _run_task_operation(
        self,
        description: str,
        directory: Path,
        cancel_event: threading.Event,
        callback: EventCallback,
    ) -> None:
        state: StateStore | None = None
        manager: GitWorktreeManager | None = None
        worktree = None
        remove_worktree = False
        try:
            if not directory.is_dir():
                raise NotADirectoryError(f"Repository directory does not exist: {directory}")
            config = self._load_config()
            root = self._operation_root(directory)
            logs = LogManager(root / ".agentops" / "logs")
            state = StateStore(self._state_path(root))
            registry = AgentRegistry(config)
            run_observer = state.agent_run_observer()
            runner = AgentRunner(logs, config.pass_env_names, config.pass_env_prefixes,
                                 run_observer=run_observer, metadata_collector=GitRunMetadataCollector())
            verifier = Verifier(config.verification_commands, pass_env_names=config.pass_env_names,
                                pass_env_prefixes=config.pass_env_prefixes)
            verification_kernel = VerificationKernel(
                profiles=config.verification_profiles,
                default_profile=config.default_verification_profile,
                legacy_commands=config.verification_commands,
                pass_env_names=config.pass_env_names,
                pass_env_prefixes=config.pass_env_prefixes,
                logs=logs,
                state=state,
            )
            engine = WorkflowEngine(config, state, registry, runner, verifier,
                                    run_observer=run_observer,
                                    metadata_collector=GitRunMetadataCollector(),
                                    verification_kernel=verification_kernel)
            manager = GitWorktreeManager()
            worktree = manager.create(root, description)
            callback({"kind": "workflow-started", "worktree": str(worktree.path),
                      "base_branch": worktree.base_branch})
            result = asyncio.run(engine.run_high_level(description, worktree.path, cancel_event=cancel_event))
            if cancel_event.is_set():
                raise OperationCancelled
            changed = False
            if result.ready:
                finalization = finalize_worktree(manager, state, worktree, description, result.workflow_id,
                                                 config.max_attempts)
                if finalization.conflict_error is not None:
                    callback({"kind": "conflict", "error": finalization.conflict_error,
                              "worktree": str(worktree.path), "workflow_id": result.workflow_id})
                    return
                changed = finalization.changed
                remove_worktree = True
            callback({"kind": "workflow-result", "result": result, "ready": result.ready,
                      "workflow_id": result.workflow_id, "worktree": str(worktree.path),
                      "merged": changed})
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

    def list_workflows(
        self,
        directory: str | Path,
        limit: int = 25,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, object]:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            total = state.count_workflows(status)
            workflows: list[dict[str, object]] = []
            for row in state.list_workflows(limit=limit, offset=offset, status=status):
                tasks = state.list_tasks(row["id"])
                counts: dict[str, int] = {}
                for task in tasks:
                    key = str(task.status)
                    counts[key] = counts.get(key, 0) + 1
                runs = state.list_agent_runs(row["id"], limit=200)
                run_counts: dict[str, int] = {}
                for run in runs:
                    key = run.status.value
                    run_counts[key] = run_counts.get(key, 0) + 1
                workflows.append({
                    "id": row["id"],
                    "status": row["status"],
                    "description": row["description"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "task_count": len(tasks),
                    "task_counts": counts,
                    "run_count": len(runs),
                    "run_counts": run_counts,
                })
            return {"total": total, "limit": limit, "offset": offset, "workflows": workflows}
        finally:
            state.close()

    def get_workflow(self, directory: str | Path, workflow_id: str) -> dict[str, object] | None:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            workflow = state.get_workflow(workflow_id)
            if workflow is None:
                return None
            tasks = state.list_tasks(workflow["id"])
            runs = [serialize_agent_run(run) for run in state.list_agent_runs(workflow["id"], limit=200)]
            verifications = [
                serialize_verification_run(run)
                for run in state.list_verification_runs(workflow["id"], limit=50)
            ]
            return {"id": workflow["id"], "status": workflow["status"],
                    "description": workflow["description"], "tasks": tasks, "runs": runs,
                    "verifications": verifications}
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
                for run in state.list_verification_runs(workflow_id, task_id, limit=limit, offset=offset)
            ]
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

    def recover_interrupted(
        self, directory: str | Path, workflow_id: str | None = None
    ) -> dict[str, object]:
        root = self._operation_root(directory)
        state = StateStore(self._state_path(root))
        try:
            return dict(state.recover_all() if workflow_id is None else {
                "agent_runs": len(state.recover_agent_runs()),
                "verification_runs": len(state.recover_verification_runs()),
                "tasks": len(state.recover_tasks(workflow_id)),
            })
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
            entries.append({
                "path": str(path),
                "name": path.relative_to(logs.root).as_posix(),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
        return entries

    def read_log(self, directory: str | Path, name: str | Path, max_bytes: int = 65536) -> dict[str, object]:
        root = self._operation_root(directory)
        logs = LogManager(root / ".agentops" / "logs")
        text, truncated = logs.read_tail(name, max_bytes)
        return {"name": str(name), "path": str((logs.root / name).resolve()), "truncated": truncated, "text": text}

    def list_worktrees(self, directory: str | Path) -> list[dict[str, object]]:
        root = self._operation_root(directory)
        manager = GitWorktreeManager()
        entries: list[dict[str, object]] = []
        for info in manager.list_worktrees(root):
            try:
                status = manager.worktree_status(info.path)
            except GitError:
                status = "(status unavailable)"
            entries.append({
                "repository": str(info.repository),
                "path": str(info.path),
                "branch": info.branch,
                "head": info.head,
                "managed": info.managed,
                "status": status,
            })
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
        root = self._operation_root(directory)
        manager = GitWorktreeManager()
        info = manager.inspect_worktree(root, path)
        if str(info.get("status", "")).strip():
            raise GitError("Refusing to retry a merge with uncommitted worktree changes.")
        branch = info.get("branch")
        if not isinstance(branch, str) or not branch.startswith("agentops/"):
            raise GitError("Retry is only supported for managed agentops/* worktree branches.")
        worktree = Worktree(root, Path(str(info["path"])), branch,
                            manager.current_branch(root), manager.current_commit(root))
        manager.merge(worktree)
        return {"merged": True, "path": str(info["path"]), "branch": branch,
                "base_branch": worktree.base_branch}

    def latest_workflow(self, directory: str | Path) -> dict[str, object] | None:
        state = StateStore(self._state_path(directory))
        try:
            workflow = state.latest_workflow()
            if workflow is None:
                return None
            tasks = state.list_tasks(workflow["id"])
            runs = [serialize_agent_run(run) for run in state.list_agent_runs(workflow["id"], limit=200)]
            verifications = [
                serialize_verification_run(run)
                for run in state.list_verification_runs(workflow["id"], limit=50)
            ]
            return {"id": workflow["id"], "status": workflow["status"],
                    "description": workflow["description"], "tasks": tasks, "runs": runs,
                    "verifications": verifications}
        finally:
            state.close()
