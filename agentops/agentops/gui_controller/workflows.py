"""AgentOpsController: dashboard, workflow runs, workflow records, readiness and recovery."""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from ..git import GitWorktreeManager
from ..state import StateStore
from ..tasks import Workflow
from .base import EventCallback
from .. import gui_controller as _ns


class WorkflowsMixin:
    """Dashboard, workflow runs, workflow records, readiness and recovery."""

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
        carrying a recovery state, or belonging to a failed workflow), and
        the newest timeline events.
        """
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
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
                _ns.serialize_verification_run(run)
                for run in state.list_verification_runs(limit=verification_limit)
            ]
            verification_status: dict[str, int] = {}
            for run in verifications:
                key = str(run.get("overall_status") or run.get("status") or "unknown")
                verification_status[key] = verification_status.get(key, 0) + 1
            failures = [
                _ns.serialize_failure(failure)
                for failure in state.list_failures(limit=failure_limit)
            ]
            # The retry/repair/recovery flags describe what the system can
            # still do on its own. A failure attached to a workflow that
            # ended FAILED also needs attention, because only the user can
            # resolve it (for example a HIGH-severity environment failure
            # whose recommended action is request_approval). Without this,
            # the dashboard claims "Nothing needs attention" next to a
            # failed workflow. Failures from workflows that recovered or
            # never failed stay out.
            failed_workflow_ids = {
                workflow.id for workflow in state.list_workflows(status="failed")
            }
            needs_attention = [
                failure
                for failure in failures
                if failure.get("retryable")
                or failure.get("repairable")
                or failure.get("recovery_state")
                or failure.get("workflow_id") in failed_workflow_ids
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
                pinned_roles = set(config.role_preferences) | set(_ns.STANDARD_TASK_ROLES)
                config = replace(config, role_preferences={role: (agent,) for role in pinned_roles})
            root = self._operation_root(directory)
            logs = _ns.LogManager(root / ".agentops" / "logs")
            state = _ns.StateStore(self._state_path(root))
            registry = _ns.AgentRegistry(config)
            run_observer = state.agent_run_observer()
            runner = _ns.AgentRunner(
                logs,
                config.pass_env_names,
                config.pass_env_prefixes,
                run_observer=run_observer,
                metadata_collector=_ns.GitRunMetadataCollector(),
                systemd_run_enabled=config.systemd_run_enabled,
            )
            verifier = _ns.Verifier(
                config.verification_commands,
                pass_env_names=config.pass_env_names,
                pass_env_prefixes=config.pass_env_prefixes,
                systemd_run_enabled=config.systemd_run_enabled,
            )
            verification_kernel = _ns.VerificationKernel(
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
            engine = _ns.WorkflowEngine(
                config,
                state,
                registry,
                runner,
                verifier,
                run_observer=run_observer,
                metadata_collector=_ns.GitRunMetadataCollector(),
                verification_kernel=verification_kernel,
            )
            manager = _ns.GitWorktreeManager()
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
            _ns.record_worktree_provenance(state, worktree, result.workflow_id, engine.degradation)
            if cancel_event.is_set():
                raise _ns.OperationCancelled
            changed = False
            if result.ready or _ns.implementation_passed(state, result.workflow_id):
                # One authoritative rule (finalize.finalize_for_outcome): READY
                # merges, not-READY commits and preserves. Previously the GUI
                # merged only on `result.ready` while the CLI also merged on
                # implementation-passed, so identical work was finalized
                # differently depending on the entry point.
                finalization = _ns.finalize_for_outcome(
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
        except _ns.OperationCancelled:
            callback({"kind": "cancelled"})
        except Exception as error:
            callback({"kind": "error", "error": error})
        finally:
            if state is not None:
                state.close()
            if worktree is not None and remove_worktree and manager is not None:
                with suppress(_ns.GitError):
                    manager.remove(worktree)
            self._end_operation(cancel_event)
            callback({"kind": "thread-finished"})

    def rename_workflow(
        self, directory: str | Path, workflow_id: str, description: str
    ) -> dict[str, object]:
        """Replace a workflow's description (its display name everywhere)."""
        text = str(description or "").strip()
        if not text:
            raise ValueError("A workflow description cannot be empty.")
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            workflow = state.get_workflow(str(workflow_id))
            if workflow is None:
                raise KeyError(f"Unknown workflow: {workflow_id}")
            workflow.description = text
            if not state.rename_workflow(workflow.id, text):
                raise KeyError(f"Unknown workflow: {workflow_id}")
            return _ns.serialize_workflow(workflow)
        finally:
            state.close()

    def delete_workflow(self, directory: str | Path, workflow_id: str) -> dict[str, object]:
        """Remove a workflow and every record that belongs to it.

        Refuses live work (a running workflow or any running task). The
        on-disk worktree and its branch are deliberately left alone for
        the Worktrees surface to clean up.
        """
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
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
                store = _ns.ArtifactStore(self._artifact_root(root))
                for artifact in artifacts:
                    if artifact.rel_path:
                        store.delete(artifact)
            removed = state.delete_workflow(workflow.id)
            if not removed.get("workflows"):
                raise KeyError(f"Unknown workflow: {workflow_id}")
            return {"id": workflow.id, "description": workflow.description, "removed": removed}
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
        state = _ns.StateStore(self._state_path(root))
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
        payload = _ns.serialize_workflow(workflow)
        review_verdicts: list[dict[str, object]] = []
        for task in tasks:
            try:
                summary = _ns.verdict_summary_for_task(task)
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
        state = _ns.StateStore(self._state_path(root))
        try:
            workflow = state.get_workflow(workflow_id)
            if workflow is None:
                return None
            tasks = [_ns.serialize_task(task) for task in state.list_tasks(workflow.id)]
            runs = [
                _ns.serialize_agent_run(run)
                for run in state.list_agent_runs(workflow.id, limit=200)
            ]
            verifications = [
                _ns.serialize_verification_run(run)
                for run in state.list_verification_runs(workflow.id, limit=50)
            ]
            failures = [
                _ns.serialize_failure(f) for f in state.list_failures(workflow.id, limit=50)
            ]
            ref = state.get_worktree_ref(workflow.id)
            payload = _ns.serialize_workflow(workflow)
            payload.update(
                {
                    "tasks": tasks,
                    "runs": runs,
                    "verifications": verifications,
                    "failures": failures,
                    "worktree_ref": _ns.serialize_worktree_ref(ref) if ref is not None else None,
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

    def interrupted_work(self, directory: str | Path) -> dict[str, int]:
        """Read-only interrupted-work counts for the shell recovery banner.

        Mirrors :meth:`recover_interrupted` without mutating anything: the
        counts are ``StateStore.count_interrupted_work``'s read-only copy of
        the ``recover_all()`` predicates, plus their ``total`` for display.
        """
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            counts = state.count_interrupted_work()
        finally:
            state.close()
        return {**counts, "total": sum(counts.values())}

    def recover_interrupted(
        self, directory: str | Path, workflow_id: str | None = None
    ) -> dict[str, object]:
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
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

    def workflow_readiness(self, directory: str | Path, workflow_id: str) -> dict[str, object]:
        """READY assessment for one workflow, as read by the GUI.

        A read-only projection of the single READY contract in
        :mod:`agentops.execution_model`, which the engine already uses to gate
        merges. The controller adds no readiness rule of its own, so a surface
        reading this can never claim a weaker or stronger merge state than the
        engine would act on.
        """
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            readiness = _ns.assess_workflow_readiness(state.list_tasks(workflow_id), workflow_id)
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

    def latest_workflow(self, directory: str | Path) -> dict[str, object] | None:
        """Phase 1+3: DTO-backed latest payload with serialized tasks + provenance."""
        state = _ns.StateStore(self._state_path(directory))
        try:
            workflow = state.latest_workflow()
            if workflow is None:
                return None
            tasks = [_ns.serialize_task(task) for task in state.list_tasks(workflow.id)]
            runs = [
                _ns.serialize_agent_run(run)
                for run in state.list_agent_runs(workflow.id, limit=200)
            ]
            verifications = [
                _ns.serialize_verification_run(run)
                for run in state.list_verification_runs(workflow.id, limit=50)
            ]
            ref = state.get_worktree_ref(workflow.id)
            payload = _ns.serialize_workflow(workflow)
            payload.update(
                {
                    "tasks": tasks,
                    "runs": runs,
                    "verifications": verifications,
                    "worktree_ref": _ns.serialize_worktree_ref(ref) if ref is not None else None,
                }
            )
            return payload
        finally:
            state.close()
