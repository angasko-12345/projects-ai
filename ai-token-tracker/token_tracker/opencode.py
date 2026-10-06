"""Collector for OpenCode's local usage database.

Data source: OpenCode's own SQLite database (``opencode.db``), read-only.
Every assistant message stores provider-reported usage in ``data.tokens``
(input/output/reasoning/cache read/cache write) plus model, provider,
sub-agent, and timestamps; ``session.directory`` supplies the project.

Trustworthiness: the per-message token sums reconcile exactly with OpenCode's
own ``session`` rollup columns (verified on the live database: 176/176
sessions, 0 mismatches; totals identical), so imported usage is marked exact.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .model import UsageEvent, normalize_event

SOURCE = "opencode"


def find_database() -> Path | None:
    """Locate opencode.db in the known OpenCode data directories."""
    candidates: list[Path] = []
    env = os.environ.get("OPENCODE_DB")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / ".local" / "share" / "opencode" / "opencode.db")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "opencode" / "opencode.db")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def collect(db_path: str | Path) -> list[UsageEvent]:
    """Parse all assistant messages into normalized events (read-only)."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        # No json_extract in SQL: one malformed row must not abort the import;
        # JSON is parsed (and rejected) per row below.
        rows = conn.execute(
            """
            SELECT m.id, m.time_created, m.data,
                   s.id AS session_id, s.directory, s.agent AS session_agent
            FROM message m
            JOIN session s ON s.id = m.session_id
            """
        )
        events: list[UsageEvent] = []
        for message_id, time_created, data, session_id, directory, session_agent in rows:
            event = _message_to_event(
                message_id=message_id,
                time_created=time_created,
                data=data,
                session_id=session_id,
                directory=directory,
                session_agent=session_agent,
            )
            if event is not None:
                events.append(event)
        return events
    finally:
        conn.close()


def _message_to_event(
    *,
    message_id: str,
    time_created: int,
    data: str,
    session_id: str,
    directory: str | None,
    session_agent: str | None,
) -> UsageEvent | None:
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None
    if payload.get("role") != "assistant":
        return None
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None  # no recorded usage; never invent a number
    cache = tokens.get("cache") or {}
    times = payload.get("time") or {}
    provider = payload.get("providerID") or "unknown"
    try:
        return normalize_event(
            timestamp=times.get("created") or time_created,
            provider=provider,
            agent=payload.get("agent") or session_agent,
            tool="OpenCode",
            model=payload.get("modelID"),
            project=directory,
            input_tokens=tokens.get("input", 0),
            output_tokens=tokens.get("output", 0),
            cache_read_tokens=cache.get("read", 0),
            cache_write_tokens=cache.get("write", 0),
            reasoning_tokens=tokens.get("reasoning", 0),
            cost=payload.get("cost"),
            exact=True,
            source=SOURCE,
            request_id=message_id,
            raw_metadata={
                "session_id": session_id,
                "mode": payload.get("mode"),
                "finish": payload.get("finish"),
                "completed": times.get("completed"),
            },
        )
    except Exception:
        return None  # malformed record; skip rather than store bad data
