"""Worktree provenance records."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from ..git import WorktreeRef
from ..tasks import utc_now


class WorktreeRefMixin:
    def _worktree_ref_from_row(self, row: sqlite3.Row) -> WorktreeRef:
        return WorktreeRef(
            id=row["id"],
            workflow_id=row["workflow_id"],
            path=row["path"],
            branch=row["branch"],
            base_branch=row["base_branch"],
            base_commit=row["base_commit"],
            created_at=row["created_at"],
        )

    def record_worktree_ref(self, ref: WorktreeRef) -> WorktreeRef:
        """Persist one worktree provenance record (Phase 3)."""
        if not ref.id:
            ref.id = str(uuid4())
        if not ref.created_at:
            ref.created_at = utc_now()
        with self._lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO worktree_refs (
                    id, workflow_id, path, branch, base_branch, base_commit, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    ref.id,
                    ref.workflow_id,
                    ref.path,
                    ref.branch,
                    ref.base_branch,
                    ref.base_commit,
                    ref.created_at,
                ),
            )
            self.connection.commit()
        return ref

    def get_worktree_ref(self, workflow_id: str) -> WorktreeRef | None:
        """Latest persisted provenance for a workflow, if any."""
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM worktree_refs WHERE workflow_id = ? ORDER BY rowid DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
        return self._worktree_ref_from_row(row) if row is not None else None

    def find_worktree_ref_by_path(self, path: str | Path) -> WorktreeRef | None:
        """Look up persisted provenance by worktree path (retry/merge path)."""
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM worktree_refs WHERE path = ? ORDER BY rowid DESC LIMIT 1",
                (str(path),),
            ).fetchone()
        return self._worktree_ref_from_row(row) if row is not None else None

    def list_worktree_refs(self, workflow_id: str | None = None) -> list[WorktreeRef]:
        query = "SELECT * FROM worktree_refs"
        params: tuple[str, ...] = ()
        if workflow_id is not None:
            query += " WHERE workflow_id = ?"
            params = (workflow_id,)
        query += " ORDER BY rowid"
        with self._lock:
            rows = self.connection.execute(query, params).fetchall()
        return [self._worktree_ref_from_row(row) for row in rows]
