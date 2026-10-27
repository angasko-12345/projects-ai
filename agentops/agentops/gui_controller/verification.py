"""AgentOpsController: verification runs and failure records."""

from __future__ import annotations

from pathlib import Path

from .. import gui_controller as _ns


class VerificationMixin:
    """Verification runs and failure records."""

    def delete_verification_run(self, directory: str | Path, run_id: str) -> dict[str, object]:
        """Remove a verification run with its checks and report."""
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
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
        state = _ns.StateStore(self._state_path(root))
        try:
            if not state.delete_failure(str(failure_id)):
                raise KeyError(f"Unknown failure: {failure_id}")
            return {"id": str(failure_id)}
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
        state = _ns.StateStore(self._state_path(root))
        try:
            return [
                _ns.serialize_verification_run(run)
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
        state = _ns.StateStore(self._state_path(root))
        try:
            total = state.count_verification_runs()
            start = max(0, total - limit - offset)
            read_limit = min(limit, max(0, total - offset))
            rows = state.list_verification_runs(limit=read_limit, offset=start)
            rows.reverse()
            return [_ns.serialize_verification_run(run) for run in rows]
        finally:
            state.close()

    def get_verification_run(self, directory: str | Path, run_id: str) -> dict[str, object] | None:
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            try:
                run = state.get_verification_run(run_id)
                report = state.get_verification_report_by_run(run_id)
                checks = state.list_verification_checks(run_id)
            except KeyError:
                return None
            return {
                "run": _ns.serialize_verification_run(run),
                "report": _ns.serialize_verification_report(report),
                "checks": [_ns.serialize_verification_check(check) for check in checks],
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
        state = _ns.StateStore(self._state_path(root))
        try:
            return [
                _ns.serialize_failure(failure)
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
        state = _ns.StateStore(self._state_path(root))
        try:
            total = state.count_failures()
            start = max(0, total - limit - offset)
            read_limit = min(limit, max(0, total - offset))
            rows = state.list_failures(limit=read_limit, offset=start)
            rows.reverse()
            return [_ns.serialize_failure(failure) for failure in rows]
        finally:
            state.close()
