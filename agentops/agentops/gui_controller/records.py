"""AgentOpsController: timeline events, stored artifacts and run logs."""

from __future__ import annotations

from pathlib import Path

from .. import gui_controller as _ns


class RecordsMixin:
    """Timeline events, stored artifacts and run logs."""

    def delete_artifact(self, directory: str | Path, artifact_id: str) -> dict[str, object]:
        """Remove an artifact's file and its metadata row together."""
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            artifact = state.get_artifact(str(artifact_id))
            if artifact is None:
                raise KeyError(f"Unknown artifact: {artifact_id}")
            store = _ns.ArtifactStore(self._artifact_root(root))
            try:
                store.delete(artifact)
            except _ns.ArtifactError as error:
                raise ValueError(str(error)) from error
            state.delete_artifact_record(artifact.id)
            return {"id": artifact.id, "name": artifact.name}
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
        state = _ns.StateStore(self._state_path(root))
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
        state = _ns.StateStore(self._state_path(root))
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
        state = _ns.StateStore(self._state_path(root))
        try:
            artifact = state.get_artifact(artifact_id)
            if artifact is None:
                raise ValueError(f"Unknown artifact {artifact_id}")
            store = _ns.ArtifactStore(self._artifact_root(root))
            try:
                text = store.read_text(artifact, max_bytes)
            except _ns.ArtifactError as error:
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
        logs = _ns.LogManager(root / ".agentops" / "logs")
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
        logs = _ns.LogManager(root / ".agentops" / "logs")
        text, truncated = logs.read_tail(name, max_bytes)
        return {
            "name": str(name),
            "path": str((logs.root / name).resolve()),
            "truncated": truncated,
            "text": text,
        }
