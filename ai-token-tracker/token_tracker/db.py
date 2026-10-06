"""SQLite storage for normalized usage events."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import EVENT_COLUMNS, UsageEvent, event_from_row, event_to_row

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    provider TEXT NOT NULL,
    agent TEXT,
    model TEXT,
    project TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL,
    exact INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    request_id TEXT,
    raw_metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);
"""

_INSERT_SQL = (
    "INSERT INTO events ({cols}) VALUES ({placeholders})"
).format(
    cols=", ".join(EVENT_COLUMNS),
    placeholders=", ".join(f":{c}" for c in EVENT_COLUMNS),
)

_UPDATE_SQL = (
    "UPDATE events SET {assign} WHERE id = :id"
).format(assign=", ".join(f"{c} = :{c}" for c in EVENT_COLUMNS if c != "id"))

BREAKDOWN_COLUMNS = {"provider", "model", "agent", "project"}


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    return conn


def insert_events(conn: sqlite3.Connection, events: list[UsageEvent]) -> tuple[int, int, int]:
    """Upsert events keyed by id; re-import reconciles rows the source updated.

    OpenCode writes assistant messages while streaming (all-zero tokens) and
    fills them in on completion, so a later import must refresh an already
    stored row rather than keep the stale first-seen values.

    Returns (inserted, updated, unchanged).
    """
    inserted = updated = unchanged = 0
    for event in events:
        row = event_to_row(event)
        existing = conn.execute("SELECT * FROM events WHERE id = :id", row).fetchone()
        if existing is None:
            conn.execute(_INSERT_SQL, row)
            inserted += 1
        elif {key: existing[key] for key in existing.keys()} == row:
            unchanged += 1
        else:
            conn.execute(_UPDATE_SQL, row)
            updated += 1
    conn.commit()
    return inserted, updated, unchanged


def totals(conn: sqlite3.Connection, since: str | None = None) -> dict:
    """Aggregate totals, optionally restricted to timestamps >= since.

    ``since`` is a date prefix like ``2026-10-06`` (timestamps are canonical
    UTC ISO-8601, so lexicographic comparison is chronological).
    """
    where = ""
    params: tuple = ()
    if since:
        where = "WHERE timestamp >= ?"
        params = (since,)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS events,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
               COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
               COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens,
               COALESCE(SUM(cost), 0) AS cost,
               COALESCE(SUM(CASE WHEN exact = 1 THEN total_tokens ELSE 0 END), 0) AS exact_tokens,
               COALESCE(SUM(CASE WHEN exact = 0 THEN total_tokens ELSE 0 END), 0) AS estimated_tokens,
               COALESCE(SUM(CASE WHEN exact = 1 THEN 1 ELSE 0 END), 0) AS exact_events
        FROM events {where}
        """,
        params,
    ).fetchone()
    return dict(row)


def daily_series(conn: sqlite3.Connection, days: int = 30, now: datetime | None = None) -> list[tuple[str, int]]:
    """Zero-filled daily total_tokens for the last ``days`` UTC days inclusive."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    rows = conn.execute(
        "SELECT substr(timestamp, 1, 10) AS day, SUM(total_tokens) AS total "
        "FROM events WHERE substr(timestamp, 1, 10) >= ? GROUP BY day",
        (start.isoformat(),),
    )
    by_day = {day: int(total) for day, total in rows}
    series = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        series.append((day, by_day.get(day, 0)))
    return series


def breakdown(conn: sqlite3.Connection, column: str, since: str | None = None) -> list[tuple[str, int, int]]:
    """[(key, total_tokens, events)] for a schema column, largest first."""
    if column not in BREAKDOWN_COLUMNS:
        raise ValueError(f"cannot group by {column!r}")
    where = ""
    params: list = []
    if since:
        where = "WHERE timestamp >= ?"
        params.append(since)
    rows = conn.execute(
        f"SELECT COALESCE(NULLIF({column}, ''), '(unknown)') AS key, "
        f"SUM(total_tokens) AS total, COUNT(*) AS events "
        f"FROM events {where} GROUP BY key ORDER BY total DESC, key ASC",
        params,
    )
    return [(key, int(total), int(events)) for key, total, events in rows]


def all_events(conn: sqlite3.Connection) -> list[UsageEvent]:
    rows = conn.execute("SELECT * FROM events ORDER BY timestamp, id")
    return [event_from_row(dict(row)) for row in rows]


def export_csv(conn: sqlite3.Connection, path: str | Path) -> int:
    """Write every normalized event to CSV; returns the row count."""
    events = all_events(conn)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(EVENT_COLUMNS)
        for event in events:
            row = event_to_row(event)
            writer.writerow([row[c] for c in EVENT_COLUMNS])
    return len(events)
