"""Failure records (schema v3, additive)."""

from __future__ import annotations

import sqlite3

from ..failure import (
    Failure,
    FailureCategory,
    FailureSeverity,
    FailureSource,
    RecoveryState,
    RepairAction,
)

from ._json import _json, _loads


class FailureMixin:
    def count_failures(self) -> int:
        """Total failure rows; lets callers window the newest failures."""
        with self._lock:
            row = self.connection.execute("SELECT COUNT(*) AS total FROM failures").fetchone()
        return int(row["total"])

    def delete_failure(self, failure_id: str) -> bool:
        """Remove one failure row; True when a row was removed."""
        with self._lock:
            cursor = self.connection.execute("DELETE FROM failures WHERE id = ?", (failure_id,))
            self.connection.commit()
            return cursor.rowcount == 1

    @staticmethod
    def _loads_structured_evidence(value: str | None) -> dict[str, object] | None:
        decoded = _loads(value, None)
        return decoded if isinstance(decoded, dict) else None

    def _failure_from_row(self, row: sqlite3.Row) -> Failure:
        recovery = row["recovery_state"]
        return Failure(
            id=row["id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            agent_run_id=row["agent_run_id"],
            source=FailureSource(row["source"]),
            category=FailureCategory(row["category"]),
            severity=FailureSeverity(row["severity"]),
            retryable=bool(row["retryable"]),
            repairable=bool(row["repairable"]),
            evidence=row["evidence"],
            primary_error=row["primary_error"],
            verification_run_id=row["verification_run_id"],
            recommended_action=RepairAction(row["recommended_action"]),
            attempt=row["attempt"],
            repair_cycle=row["repair_cycle"],
            recovery_state=RecoveryState(recovery) if recovery else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            structured_evidence=self._loads_structured_evidence(row["structured_evidence"]),
        )

    def create_failure(self, failure: Failure) -> Failure:
        with self._lock:
            self.connection.execute(
                """INSERT INTO failures (
                    id, workflow_id, task_id, agent_run_id, source, category,
                    severity, retryable, repairable, evidence, primary_error,
                    verification_run_id, recommended_action, attempt, repair_cycle,
                    recovery_state, created_at, updated_at, structured_evidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    failure.id,
                    failure.workflow_id,
                    failure.task_id,
                    failure.agent_run_id,
                    failure.source.value
                    if isinstance(failure.source, FailureSource)
                    else str(failure.source),
                    failure.category.value
                    if isinstance(failure.category, FailureCategory)
                    else str(failure.category),
                    failure.severity.value
                    if isinstance(failure.severity, FailureSeverity)
                    else str(failure.severity),
                    int(failure.retryable),
                    int(failure.repairable),
                    failure.evidence,
                    failure.primary_error,
                    failure.verification_run_id,
                    failure.recommended_action.value
                    if isinstance(failure.recommended_action, RepairAction)
                    else str(failure.recommended_action),
                    failure.attempt,
                    failure.repair_cycle,
                    failure.recovery_state.value
                    if isinstance(failure.recovery_state, RecoveryState)
                    else failure.recovery_state,
                    failure.created_at,
                    failure.updated_at,
                    _json(failure.structured_evidence),
                ),
            )
            if failure.workflow_id is not None:
                self._event(
                    failure.workflow_id,
                    failure.task_id,
                    "failure-recorded",
                    failure.category.value
                    if isinstance(failure.category, FailureCategory)
                    else str(failure.category),
                )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM failures WHERE id = ?", (failure.id,)
            ).fetchone()
        return self._failure_from_row(row)

    def get_failure(self, failure_id: str) -> Failure:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM failures WHERE id = ?", (failure_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown failure: {failure_id}")
        return self._failure_from_row(row)

    def list_failures(
        self,
        workflow_id: str | None = None,
        task_id: str | None = None,
        category: FailureCategory | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Failure]:
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("Failure list limit and offset must be non-negative integers.")
        clauses: list[str] = []
        params: list[str | int] = []
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if category is not None:
            clauses.append("category = ?")
            params.append(
                category.value if isinstance(category, FailureCategory) else str(category)
            )
        query = "SELECT * FROM failures"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, rowid LIMIT ? OFFSET ?"
        with self._lock:
            rows = self.connection.execute(query, (*params, limit, offset)).fetchall()
        return [self._failure_from_row(row) for row in rows]
