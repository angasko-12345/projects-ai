"""Artifact records."""

from __future__ import annotations

import sqlite3

from ..artifacts import Artifact, ArtifactKind, ArtifactMetadata

from ._json import _json, _jsonable, _loads


class ArtifactMixin:
    def create_artifact_record(self, artifact: Artifact) -> Artifact:
        with self._lock:
            self.connection.execute(
                """INSERT INTO artifacts (
                    id, workflow_id, task_id, agent_run_id, kind, name,
                    rel_path, sha256, size_bytes, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact.id,
                    artifact.workflow_id,
                    artifact.task_id,
                    artifact.agent_run_id,
                    artifact.kind.value
                    if isinstance(artifact.kind, ArtifactKind)
                    else str(artifact.kind),
                    artifact.name,
                    artifact.rel_path,
                    artifact.sha256,
                    artifact.size_bytes,
                    _json(
                        _jsonable(
                            artifact.metadata.to_dict()
                            if isinstance(artifact.metadata, ArtifactMetadata)
                            else {}
                        )
                    ),
                    artifact.created_at,
                ),
            )
            self.connection.commit()
        return artifact

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        return self._artifact_from_row(row) if row is not None else None

    def list_artifacts(
        self,
        workflow_id: str | None = None,
        task_id: str | None = None,
        kind: ArtifactKind | str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("Artifact list limit and offset must be non-negative integers.")
        take = min(limit, 1000)
        skip = offset
        clauses: list[str] = []
        params: list[object] = []
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value if isinstance(kind, ArtifactKind) else str(kind))
        query = "SELECT * FROM artifacts"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, rowid LIMIT ? OFFSET ?"
        params.extend([take, skip])
        with self._lock:
            rows = self.connection.execute(query, tuple(params)).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def delete_artifact_record(self, artifact_id: str) -> bool:
        """Delete one artifact metadata row.

        Removes only the DB row; pair with ``ArtifactStore.delete`` to remove
        the file (the CLI ``artifacts --prune-keep`` path does both).
        """
        with self._lock:
            cursor = self.connection.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
            self.connection.commit()
            return cursor.rowcount > 0

    def _artifact_from_row(self, row: sqlite3.Row) -> Artifact:
        try:
            kind = ArtifactKind(row["kind"])
        except (ValueError, KeyError, TypeError):
            kind = ArtifactKind.CUSTOM
        metadata = _loads(row["metadata"], {})
        return Artifact(
            id=row["id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            agent_run_id=row["agent_run_id"],
            kind=kind,
            name=row["name"],
            rel_path=row["rel_path"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"] or 0,
            metadata=ArtifactMetadata.from_dict(metadata if isinstance(metadata, dict) else {}),
            created_at=row["created_at"],
        )
