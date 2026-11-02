"""AgentOpsController: agent discovery, ad-hoc agent runs and AgentRun records."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from ..state import StateStore
from .base import EventCallback
from .. import gui_controller as _ns


class AgentsMixin:
    """Agent discovery, ad-hoc agent runs and AgentRun records."""

    def detect_agents(self) -> dict[str, object]:
        return _ns.AgentRegistry(self._load_config()).detect()

    def list_agent_profiles(self) -> list[dict[str, object]]:
        """Capability-rich agent profiles for the Agents surface (read-only)."""
        profiles = _ns.AgentRegistry(self._load_config()).profiles()
        return [profile.to_dict() for profile in profiles.values()]

    def task_options(self) -> dict[str, object]:
        """Config-derived choices offered by the New Task flow."""
        config = self._load_config()
        return {
            "routing_enabled": bool(config.routing_enabled),
            "verification_profiles": sorted(config.verification_profiles),
            "default_verification_profile": config.default_verification_profile,
        }

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
            agent = _ns.AgentRegistry(config).get(agent_name)
            root = self._operation_root(directory)
            logs = _ns.LogManager(root / ".agentops" / "logs")
            state: StateStore | None = None
            try:
                state = _ns.StateStore(self._state_path(root))
                observer = state.agent_run_observer()
            except Exception:
                state = None
                observer = None
            try:
                runner = _ns.AgentRunner(
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
                        _ns.AgentRunStatus.COMPLETED.value
                        if result.succeeded
                        else _ns.AgentRunStatus.TIMED_OUT.value
                        if result.timed_out
                        else _ns.AgentRunStatus.TERMINATED.value
                        if result.terminated
                        else _ns.AgentRunStatus.FAILED.value
                    ),
                }
            )
        except _ns.OperationCancelled:
            callback({"kind": "cancelled"})
        except Exception as error:
            callback({"kind": "error", "error": error})
        finally:
            self._end_operation(cancel_event)
            callback({"kind": "thread-finished"})

    def delete_agent_run(self, directory: str | Path, run_id: str) -> dict[str, object]:
        """Remove an agent run with its linked verification runs.

        Failure rows keep plain-text references and stay readable. Refuses
        live runs and runs that follow-up runs still reference.
        """
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            run = state.get_agent_run(str(run_id))
            if self._record_status(run) in ("pending", "starting", "running"):
                raise ValueError("A live agent run cannot be deleted.")
            if not state.delete_agent_run(run.id):
                raise ValueError("This run has follow-up runs; delete those first.")
            return {"id": run.id, "workflow_id": run.workflow_id, "task_id": run.task_id}
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
        state = _ns.StateStore(self._state_path(root))
        try:
            run_status = _ns.AgentRunStatus(status) if status else None
            return [
                _ns.serialize_agent_run(run)
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
        state = _ns.StateStore(self._state_path(root))
        try:
            total = state.count_agent_runs()
            start = max(0, total - limit - offset)
            read_limit = min(limit, max(0, total - offset))
            rows = state.list_agent_runs(limit=read_limit, offset=start)
            rows.reverse()
            return [_ns.serialize_agent_run(run) for run in rows]
        finally:
            state.close()

    def get_agent_run(self, directory: str | Path, run_id: str) -> dict[str, object] | None:
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            try:
                return _ns.serialize_agent_run(state.get_agent_run(run_id))
            except KeyError:
                return None
        finally:
            state.close()
