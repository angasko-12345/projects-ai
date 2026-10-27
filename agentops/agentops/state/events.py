"""Legacy event rows and the typed event timeline."""

from __future__ import annotations

import sqlite3
from uuid import uuid4

from ..events import Event, EventSeverity, EventType, coerce_event
from ..tasks import utc_now

from ._json import _json, _jsonable, _loads


class EventMixin:
    def count_events(self) -> int:
        """Total typed-event rows; lets callers page the newest events."""
        with self._lock:
            row = self.connection.execute("SELECT COUNT(*) AS total FROM typed_events").fetchone()
        return int(row["total"])

    def event(self, workflow_id: str, task_id: str | None, kind: str, detail: str) -> None:
        with self._lock:
            self._event(workflow_id, task_id, kind, detail)
            self.connection.commit()
        # Notify outside the lock: subscribers run on the publisher thread
        # and must never stall other store users (see EventBus docs).
        self._notify_bus(
            Event(
                workflow_id=workflow_id,
                task_id=task_id,
                type=EventType.NOTE,
                message=detail,
                payload={"legacy_kind": kind},
            )
        )

    def list_events(self, workflow_id: str, task_id: str | None = None) -> list[dict[str, object]]:
        """Legacy events as plain dicts (Phase 1: no Row leakage)."""
        query = "SELECT * FROM events WHERE workflow_id = ?" + (
            " AND task_id = ?" if task_id else ""
        )
        query += " ORDER BY rowid"
        params: tuple[str, ...] = (workflow_id,) if not task_id else (workflow_id, task_id)
        with self._lock:
            rows = self.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def _event(self, workflow_id: str, task_id: str | None, kind: str, detail: str) -> None:
        self.connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid4()), workflow_id, task_id, kind, detail, utc_now()),
        )

    def _notify_bus(self, event: Event) -> None:
        bus = getattr(self, "_event_bus", None)
        if bus is None:
            return
        try:
            bus.emit(event)
        except Exception:
            pass

    def record_typed_event(self, event: Event) -> Event:
        """Persist a versioned timeline event and notify subscribers.

        Also mirrors the event into the legacy ``events`` table (same
        transaction) so older readers using ``list_events`` keep seeing new
        activity. Events without a workflow id cannot mirror (the legacy
        column is NOT NULL) and live only in ``typed_events``.
        """
        normalized = coerce_event(event.to_dict() if isinstance(event, Event) else event)
        if normalized is None:
            raise ValueError("cannot record an empty event")
        with self._lock:
            self.connection.execute(
                """INSERT INTO typed_events (
                    id, workflow_id, task_id, agent_run_id, type, severity,
                    message, payload, schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalized.id,
                    normalized.workflow_id,
                    normalized.task_id,
                    normalized.agent_run_id,
                    normalized.type.value
                    if isinstance(normalized.type, EventType)
                    else str(normalized.type),
                    normalized.severity.value
                    if isinstance(normalized.severity, EventSeverity)
                    else str(normalized.severity),
                    normalized.message,
                    _json(_jsonable(normalized.payload)),
                    normalized.schema_version,
                    normalized.timestamp,
                ),
            )
            if normalized.workflow_id is not None:
                self._event(
                    normalized.workflow_id,
                    normalized.task_id,
                    normalized.type.value
                    if isinstance(normalized.type, EventType)
                    else str(normalized.type),
                    normalized.message or "",
                )
            self.connection.commit()
        self._notify_bus(normalized)
        return normalized

    def query_events(
        self,
        workflow_id: str | None = None,
        task_id: str | None = None,
        agent_run_id: str | None = None,
        event_type: EventType | str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Event]:
        """Chronologically ordered, paginated, filterable timeline query."""
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("Event query limit and offset must be non-negative integers.")
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
        if agent_run_id is not None:
            clauses.append("agent_run_id = ?")
            params.append(agent_run_id)
        if event_type is not None:
            clauses.append("type = ?")
            params.append(
                event_type.value if isinstance(event_type, EventType) else str(event_type)
            )
        query = "SELECT * FROM typed_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, rowid LIMIT ? OFFSET ?"
        params.extend([take, skip])
        with self._lock:
            rows = self.connection.execute(query, tuple(params)).fetchall()
        return [self._typed_event_from_row(row) for row in rows]

    def _typed_event_from_row(self, row: sqlite3.Row) -> Event:
        try:
            event_type = EventType(row["type"])
        except (ValueError, KeyError, TypeError):
            event_type = EventType.NOTE
        try:
            severity = EventSeverity(row["severity"])
        except (ValueError, KeyError, TypeError):
            severity = EventSeverity.INFO
        payload = _loads(row["payload"], {})
        return Event(
            id=row["id"],
            timestamp=row["created_at"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            agent_run_id=row["agent_run_id"],
            type=event_type,
            severity=severity,
            message=row["message"],
            payload=payload if isinstance(payload, dict) else {},
            schema_version=row["schema_version"] or 1,
        )
