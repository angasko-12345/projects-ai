"""SQLite storage for normalized usage events."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import (
    EVENT_COLUMNS,
    EventValidationError,
    UsageEvent,
    canonical_provider,
    event_from_row,
    event_to_row,
    normalize_event,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    provider TEXT NOT NULL,
    agent TEXT,
    tool TEXT,
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
    dedup_key TEXT,
    raw_metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);
CREATE TABLE IF NOT EXISTS pending_resync (
    source TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS collector_status (
    source TEXT PRIMARY KEY,
    tool TEXT,
    capability TEXT NOT NULL DEFAULT 'unavailable',
    last_sync TEXT,
    events INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""

_INSERT_SQL = ("INSERT INTO events ({cols}) VALUES ({placeholders})").format(
    cols=", ".join(EVENT_COLUMNS),
    placeholders=", ".join(f":{c}" for c in EVENT_COLUMNS),
)

_UPDATE_SQL = ("UPDATE events SET {assign} WHERE id = :id").format(
    assign=", ".join(f"{c} = :{c}" for c in EVENT_COLUMNS if c != "id")
)

BREAKDOWN_COLUMNS = {"provider", "model", "agent", "project", "tool"}

# Attribution fallback for rows that predate the ``tool`` column or an import
# that did not carry it: the stored source identifies the collecting agent.
_SOURCE_TOOL = {
    "opencode": "OpenCode",
    "kilo": "Kilo",
    "pi": "Pi",
    "omp": "Oh My Pi",
    "codex": "Codex",
    "hermes": "Hermes",
    "copilot": "GitHub Copilot",
    "claude": "Claude Code",
    "cline": "Cline",
    "dsh": "DeepSeek Harness",
}


def infer_tool(source: str | None, agent: str | None = None) -> str | None:
    """Tool attribution for a record: explicit source map, then agent, then source."""
    if source in _SOURCE_TOOL:
        return _SOURCE_TOOL[source]
    return agent or source


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive columns for databases created before tool/dedup existed."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "tool" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN tool TEXT")
    if "dedup_key" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN dedup_key TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_dedup ON events (dedup_key)")
    if conn.execute(
        "SELECT COUNT(*) FROM events WHERE dedup_key IS NULL OR tool IS NULL"
    ).fetchone()[0]:
        _backfill_identity(conn)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        _rekey_v1(conn)
    if version < 2:
        _rekey_v2(conn)
    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


# PRAGMA user_version of a database whose rows match the current collectors'
# event ids. 0 is every database created before the version was tracked.
SCHEMA_VERSION = 2


def _rekey_v1(conn: sqlite3.Connection) -> None:
    """Bring rows stored by pre-ad73f30 collectors onto the current event ids.

    Three collectors changed what feeds the content-addressed id, so a resync
    of an older database inserted every such event a second time:

    - claude: the provider segment is lowercased and ``open_router`` becomes
      ``openrouter``; ``<synthetic>`` placeholder rows are no longer emitted.
    - hermes: missing pricing is cost NULL instead of 0.0.
    - copilot: input tokens come from the vendor's exclusive count.

    Claude and hermes ids are a pure function of the stored row, so they are
    recomputed in place: history survives even when its source logs are gone
    (Claude Code prunes old transcripts by default). The old copilot input
    value cannot be inverted (it was clamped at zero and could come from
    either of two fields), so copilot is queued in ``pending_resync``: the
    next copilot ingest replaces each stale row it re-observes and keeps any
    row whose session file no longer exists. Other sources are untouched.
    Every step is idempotent, so a database already written by the current
    code (or already double-counted) comes out with one row per event.
    """
    conn.execute("DELETE FROM events WHERE source = 'claude' AND model = '<synthetic>'")
    for stored in conn.execute(
        "SELECT * FROM events WHERE source IN ('claude', 'hermes')"
    ).fetchall():
        row = dict(stored)
        provider = row["provider"]
        cost = row["cost"]
        if row["source"] == "claude":
            provider = canonical_provider(provider)
        elif cost == 0.0:
            cost = None
        event = normalize_event(
            timestamp=row["timestamp"],
            provider=provider,
            source=row["source"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read_tokens=row["cache_read_tokens"],
            cache_write_tokens=row["cache_write_tokens"],
            reasoning_tokens=row["reasoning_tokens"],
            cost=cost,
            agent=row["agent"],
            model=row["model"],
            project=row["project"],
            request_id=row["request_id"],
        )
        if event.id == row["id"]:
            continue
        if conn.execute("SELECT 1 FROM events WHERE id = ?", (event.id,)).fetchone():
            # A resync already stored this event under its new id.
            conn.execute("DELETE FROM events WHERE id = ?", (row["id"],))
        else:
            conn.execute(
                "UPDATE events SET id = ?, provider = ?, cost = ?, dedup_key = ? WHERE id = ?",
                (event.id, provider, cost, event.dedup_key, row["id"]),
            )
    if conn.execute("SELECT 1 FROM events WHERE source = 'copilot' LIMIT 1").fetchone():
        conn.execute("INSERT OR IGNORE INTO pending_resync (source) VALUES ('copilot')")


def _rekey_v2(conn: sqlite3.Connection) -> None:
    """Content-address Claude rows that were stored with a positional request id.

    Claude records without a ``requestId`` used to be stored with
    ``request_id = '__no_rid_<n>'``, where n is the record's position among
    all request ids seen in the scan. Any earlier record or file (and, for a
    pre-ad73f30 database, the <synthetic> rows that are no longer counted)
    shifts n, so a resync stored the same record again. The collector now
    stores such records with no request id; this re-keys existing rows the
    same way. Idempotent; a row whose new id already exists is dropped.
    """
    for stored in conn.execute(
        "SELECT * FROM events WHERE source = 'claude' AND substr(request_id, 1, 9) = '__no_rid_'"
    ).fetchall():
        row = dict(stored)
        event = normalize_event(
            timestamp=row["timestamp"],
            provider=row["provider"],
            source=row["source"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read_tokens=row["cache_read_tokens"],
            cache_write_tokens=row["cache_write_tokens"],
            reasoning_tokens=row["reasoning_tokens"],
            cost=row["cost"],
            agent=row["agent"],
            model=row["model"],
            project=row["project"],
            request_id=None,
        )
        if conn.execute("SELECT 1 FROM events WHERE id = ?", (event.id,)).fetchone():
            conn.execute("DELETE FROM events WHERE id = ?", (row["id"],))
        else:
            conn.execute(
                "UPDATE events SET id = ?, request_id = NULL, dedup_key = ? WHERE id = ?",
                (event.id, event.dedup_key, row["id"]),
            )


def _supersede_stale(conn: sqlite3.Connection, events: list[UsageEvent]) -> int:
    """Drop rows of a pending-resync source that ``events`` re-observe under a new id.

    The natural key of a re-observed event is (source, provider, model,
    timestamp): for copilot one session.shutdown per model. Returns the
    number of rows dropped.
    """
    pending = {row[0] for row in conn.execute("SELECT source FROM pending_resync")}
    dropped = 0
    for event in events:
        if event.source not in pending:
            continue
        dropped += conn.execute(
            "DELETE FROM events WHERE source = ? AND provider = ? AND model IS ? "
            "AND timestamp = ? AND id != ?",
            (event.source, event.provider, event.model, event.timestamp, event.id),
        ).rowcount
    return dropped


def clear_pending_resync(conn: sqlite3.Connection, source: str) -> None:
    """Mark a source's one-time resync done after a full successful ingest."""
    conn.execute("DELETE FROM pending_resync WHERE source = ?", (source,))
    conn.commit()


def _backfill_identity(conn: sqlite3.Connection) -> None:
    """Fill tool attribution from the source and recompute dedup keys."""
    rows = conn.execute("SELECT * FROM events WHERE dedup_key IS NULL OR tool IS NULL").fetchall()
    for stored in rows:
        row = dict(stored)
        event = event_from_row(row)  # recomputes dedup_key when absent
        tool = infer_tool(row["source"], row.get("agent"))
        conn.execute(
            "UPDATE events SET tool = ?, dedup_key = ? WHERE id = ?",
            (tool, event.dedup_key, row["id"]),
        )


def insert_events(conn: sqlite3.Connection, events: list[UsageEvent]) -> tuple[int, int, int]:
    """Upsert events keyed by id, deduplicated across sources by dedup_key.

    OpenCode writes assistant messages while streaming (all-zero tokens) and
    fills them in on completion, so a later import must refresh an already
    stored row rather than keep the stale first-seen values.

    Cross-source rule: the same underlying request seen by two collectors
    collapses onto the first stored row (never double-counted). If that row
    lacks tool/agent attribution and the incoming event has it, attribution
    is merged in.

    Returns (inserted, updated, unchanged).
    """
    inserted = updated = unchanged = 0
    _supersede_stale(conn, events)
    dedup: dict[str, dict] = {
        row["dedup_key"]: {
            "id": row["id"],
            "tool": row["tool"],
            "agent": row["agent"],
            "source": row["source"],
        }
        for row in conn.execute(
            "SELECT id, dedup_key, tool, agent, source FROM events WHERE dedup_key IS NOT NULL"
        )
    }
    for event in events:
        row = event_to_row(event)
        existing = conn.execute("SELECT * FROM events WHERE id = :id", row).fetchone()
        if existing is not None:
            if existing["source"] != event.source:
                # Same canonical event recorded by another collector: keep the
                # first-seen identity, only fill missing attribution.
                if not existing["tool"] and event.tool:
                    conn.execute(
                        "UPDATE events SET tool = ?, agent = COALESCE(agent, ?) WHERE id = ?",
                        (event.tool, event.agent, existing["id"]),
                    )
                    updated += 1
                else:
                    unchanged += 1
            elif {key: existing[key] for key in existing.keys()} == row:
                unchanged += 1
            else:
                conn.execute(_UPDATE_SQL, row)
                updated += 1
            continue
        stored = dedup.get(event.dedup_key)
        # Content dedup applies only ACROSS sources: within one source the id
        # is the identity, so two distinct requests that happen to carry
        # identical token counts in the same 5-second bucket stay separate.
        if stored is not None and stored["source"] != event.source:
            if not stored["tool"] and event.tool:
                conn.execute(
                    "UPDATE events SET tool = ?, agent = COALESCE(agent, ?) WHERE id = ?",
                    (event.tool, event.agent, stored["id"]),
                )
                stored["tool"] = event.tool
                updated += 1
            else:
                unchanged += 1
            continue
        conn.execute(_INSERT_SQL, row)
        dedup[event.dedup_key] = {
            "id": event.id,
            "tool": event.tool,
            "agent": event.agent,
            "source": event.source,
        }
        inserted += 1
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
               COUNT(cost) AS cost_events,
               COALESCE(SUM(CASE WHEN exact = 1 THEN total_tokens ELSE 0 END), 0) AS exact_tokens,
               COALESCE(SUM(CASE WHEN exact = 0 THEN total_tokens ELSE 0 END), 0) AS estimated_tokens,
               COALESCE(SUM(CASE WHEN exact = 1 THEN 1 ELSE 0 END), 0) AS exact_events
        FROM events {where}
        """,
        params,
    ).fetchone()
    return dict(row)


def daily_series(
    conn: sqlite3.Connection, days: int = 30, now: datetime | None = None
) -> list[tuple[str, int]]:
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


def breakdown(
    conn: sqlite3.Connection, column: str, since: str | None = None
) -> list[tuple[str, int, int]]:
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


def _guard_export_path(conn: sqlite3.Connection, path: str | Path) -> None:
    """Refuse a target that resolves to the live database file."""
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    if not db_file:
        return
    target = str(Path(path).resolve()).lower()
    if target == str(Path(db_file).resolve()).lower():
        raise ValueError(f"refusing to overwrite the tracker database: {db_file}")


def export_csv(conn: sqlite3.Connection, path: str | Path) -> int:
    """Write every normalized event to CSV; returns the row count."""
    _guard_export_path(conn, path)
    events = all_events(conn)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(EVENT_COLUMNS)
        for event in events:
            row = event_to_row(event)
            writer.writerow([row[c] for c in EVENT_COLUMNS])
    return len(events)


def export_json(conn: sqlite3.Connection, path: str | Path) -> int:
    """Write every normalized event as a JSON array; returns the row count."""
    _guard_export_path(conn, path)
    events = all_events(conn)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump([event_to_row(event) for event in events], handle, ensure_ascii=False)
        handle.write("\n")
    return len(events)


def _events_from_records(records: list[dict]) -> list[UsageEvent]:
    """Validate exported rows back into events; one bad row aborts the import.

    Rows go through ``normalize_event`` so coercions and validation are the
    same as for collector input (notably ``exact`` strings: "0" must stay an
    estimate), and the deterministic id is regenerated from the row's own
    values so re-imports deduplicate against existing rows.
    """
    events: list[UsageEvent] = []
    for number, row in enumerate(records, start=1):
        try:
            metadata = row.get("raw_metadata") or "{}"
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            events.append(
                normalize_event(
                    timestamp=row.get("timestamp"),
                    provider=row.get("provider"),
                    source=row.get("source"),
                    input_tokens=row.get("input_tokens"),
                    output_tokens=row.get("output_tokens"),
                    cache_read_tokens=row.get("cache_read_tokens"),
                    cache_write_tokens=row.get("cache_write_tokens"),
                    reasoning_tokens=row.get("reasoning_tokens"),
                    cost=row.get("cost"),
                    exact=row.get("exact"),
                    agent=row.get("agent"),
                    tool=row.get("tool") or infer_tool(row.get("source"), row.get("agent")),
                    model=row.get("model"),
                    project=row.get("project"),
                    request_id=row.get("request_id"),
                    raw_metadata=metadata,
                )
            )
        except (EventValidationError, TypeError, ValueError) as exc:
            raise ValueError(f"row {number}: {exc}") from exc
    return events


def import_csv(conn: sqlite3.Connection, path: str | Path) -> tuple[int, int, int]:
    """Import an exported CSV; returns (inserted, updated, unchanged).

    Accepts the current header and exports written before ``tool`` and
    ``dedup_key`` existed (they were appended to the schema, so the legacy
    header is the current one minus those two columns).
    """
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        legacy = [c for c in EVENT_COLUMNS if c not in ("tool", "dedup_key")]
        if reader.fieldnames not in (list(EVENT_COLUMNS), legacy):
            raise ValueError(f"unexpected CSV header: {reader.fieldnames!r}")
        records = list(reader)
    # Validate everything before writing anything: a bad row must leave the
    # database untouched, not a half-imported batch.
    return insert_events(conn, _events_from_records(records))


def import_json(conn: sqlite3.Connection, path: str | Path) -> tuple[int, int, int]:
    """Import an exported JSON array; returns (inserted, updated, unchanged)."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise ValueError("expected a JSON array of event objects")
    return insert_events(conn, _events_from_records(data))


def record_sync(
    conn: sqlite3.Connection,
    *,
    source: str,
    tool: str,
    capability: str,
    events: int | None = None,
    error: str | None = None,
) -> None:
    """Upsert one collector's status row.

    Success passes ``events`` (stored event count for the source); the error
    is cleared and ``last_sync`` stamped now. Failure passes ``error`` only:
    ``last_sync`` and ``events`` keep their previous successful values so the
    UI still shows when the source last worked.
    """
    synced = error is None
    conn.execute(
        """
        INSERT INTO collector_status (source, tool, capability, last_sync, events, error)
        VALUES (:source, :tool, :capability, :last_sync, COALESCE(:events, 0), :error)
        ON CONFLICT(source) DO UPDATE SET
            tool = excluded.tool,
            capability = excluded.capability,
            last_sync = COALESCE(excluded.last_sync, collector_status.last_sync),
            events = CASE WHEN :events IS NULL
                          THEN collector_status.events ELSE :events END,
            error = excluded.error
        """,
        {
            "source": source,
            "tool": tool,
            "capability": capability,
            "last_sync": datetime.now(timezone.utc).isoformat() if synced else None,
            "events": events if synced else None,
            "error": error,
        },
    )
    conn.commit()


def collector_statuses(conn: sqlite3.Connection) -> list[dict]:
    """All known collector status rows, unavailable ones first-class."""
    rows = conn.execute(
        "SELECT source, tool, capability, last_sync, events, error "
        "FROM collector_status ORDER BY tool COLLATE NOCASE, source"
    ).fetchall()
    return [dict(row) for row in rows]


def default_db_path() -> Path:
    """Where the tracker stores usage when no override is configured.

    Lives here, not in ``gui.py``, so the default-location rule is Qt-free and
    unit-testable without the optional desktop dependency: the GUI calls this
    and only the windowing itself needs PySide6.
    """
    env = os.environ.get("AI_TOKEN_TRACKER_DB")
    if env:
        path = Path(env)
        return path / "usage.db" if path.is_dir() else path
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/ai-token-tracker/usage.db")
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ai-token-tracker" / "usage.db"
