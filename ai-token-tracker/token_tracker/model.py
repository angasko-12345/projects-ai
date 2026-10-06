"""Normalized usage event shared by collectors, imports, storage, and the dashboard.

Token invariants (stated once, relied on everywhere):
- ``input_tokens``: prompt tokens that were NOT served from cache.
- ``cache_read_tokens`` / ``cache_write_tokens``: cache sides, counted separately.
- ``output_tokens``: completion tokens excluding reasoning.
- ``reasoning_tokens``: thought tokens reported as a separate component by the
  source (verified against OpenCode: its own per-message ``tokens.total``
  equals input + output + cache_read + cache_write + reasoning on every
  sampled message with reasoning > 0).
- ``total_tokens = input + output + cache_read + cache_write + reasoning``.

``exact=True`` means the numbers came from provider-reported usage.
``exact=False`` means they are estimates. An estimate is never silently
relabelled as exact anywhere in this codebase.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)

# Canonical form is fixed-width so lexicographic order equals chronological order.
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class EventValidationError(ValueError):
    """A raw record cannot be normalized into a UsageEvent."""


@dataclass(frozen=True)
class UsageEvent:
    id: str
    timestamp: str
    provider: str
    agent: str | None
    tool: str | None
    model: str | None
    project: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int
    total_tokens: int
    cost: float | None
    exact: bool
    source: str
    request_id: str | None
    dedup_key: str
    raw_metadata: dict

    def metadata_json(self) -> str:
        return json.dumps(self.raw_metadata, sort_keys=True, default=str)


def parse_timestamp(value: object) -> str:
    """Normalize a timestamp source into canonical UTC ISO-8601 with milliseconds.

    Accepts ISO-8601 strings (with or without timezone), ``Z`` suffix, numeric
    epoch seconds or milliseconds, and ``YYYY-MM-DD HH:MM:SS``. Naive input is
    treated as UTC.
    """
    if isinstance(value, bool):
        raise EventValidationError(f"invalid timestamp: {value!r}")
    if isinstance(value, (int, float)):
        # Heuristic: values past year 5138 in seconds are really milliseconds.
        seconds = value / 1000.0 if value > 1e11 else float(value)
        try:
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise EventValidationError(f"invalid timestamp: {value!r}") from exc
        return dt.strftime(_TS_FORMAT)
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise EventValidationError("invalid timestamp: empty string")
        iso = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            dt = _parse_legacy(text)
    else:
        raise EventValidationError(f"invalid timestamp: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(_TS_FORMAT)


def _parse_legacy(text: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise EventValidationError(f"invalid timestamp: {text!r}")


def _coerce_tokens(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise EventValidationError(f"{field}: expected a non-negative integer, got {value!r}")
    if value is None or value == "":
        return 0
    if isinstance(value, str):
        value = value.strip().replace(",", "")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise EventValidationError(f"{field}: expected a non-negative integer, got {value!r}") from None
    if isinstance(value, float) and not value.is_integer():
        raise EventValidationError(f"{field}: expected a non-negative integer, got {value!r}")
    if number < 0:
        raise EventValidationError(f"{field}: negative token count {number}")
    return number


def _coerce_cost(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise EventValidationError(f"cost: expected a number, got {value!r}")
    if isinstance(value, str):
        value = value.strip().lstrip("$")
    try:
        cost = float(value)
    except (TypeError, ValueError):
        raise EventValidationError(f"cost: expected a number, got {value!r}") from None
    if cost != cost or cost in (float("inf"), float("-inf")):
        raise EventValidationError(f"cost: not a finite number: {value!r}")
    return cost


def _coerce_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def make_event_id(
    *,
    provider: str,
    timestamp: str,
    request_id: str | None,
    model: str | None,
    agent: str | None,
    project: str | None,
    tokens: tuple[int, ...],
    cost: float | None,
) -> str:
    """Deterministic identity used for duplicate handling.

    With a request_id the identity is the provider's own key (strongest).
    Without one, the event is content-addressed so re-importing the same file,
    or the same file under a new name, never double-counts.
    """
    if request_id:
        parts = ["rid", provider, request_id, timestamp]
    else:
        parts = [
            "content",
            provider,
            timestamp,
            model or "",
            agent or "",
            project or "",
            *(str(t) for t in tokens),
            "" if cost is None else format(cost, ".6f"),
        ]
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest


def make_dedup_key(*, provider: str, model: str | None, timestamp: str, tokens: tuple[int, ...]) -> str:
    """Canonical identity of an underlying request, independent of its source.

    Two collectors can observe the same API call (a tool and its backend
    provider, or an agent and a provider dashboard). Those must collapse to
    one canonical usage event. The key is content-addressed over provider,
    model, and the token counts, with the timestamp bucketed to 5 seconds so
    millisecond jitter between sources still matches while distinct requests
    fall into different buckets or differ in token counts.

    Request ids are deliberately not used: every source issues ids from its
    own namespace, so ids never align across sources.
    """
    try:
        iso = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
        bucket = str(int(datetime.fromisoformat(iso).timestamp()) // 5 * 5)
    except ValueError:
        bucket = ""
    return "\x1f".join(("ct", provider, model or "", *(str(t) for t in tokens), bucket))


def normalize_event(
    *,
    timestamp: object,
    provider: str,
    source: str,
    input_tokens: object = 0,
    output_tokens: object = 0,
    cache_read_tokens: object = 0,
    cache_write_tokens: object = 0,
    reasoning_tokens: object = 0,
    cost: object = None,
    exact: object = False,
    agent: object = None,
    tool: object = None,
    model: object = None,
    project: object = None,
    request_id: object = None,
    raw_metadata: object = None,
) -> UsageEvent:
    """Validate one raw record and build a normalized UsageEvent."""
    prov = _coerce_text(provider, "provider")
    if not prov:
        raise EventValidationError("provider: required, must be a non-empty string")
    src = _coerce_text(source, "source")
    if not src:
        raise EventValidationError("source: required, must be a non-empty string")

    ts = parse_timestamp(timestamp)
    tokens = (
        _coerce_tokens(input_tokens, "input_tokens"),
        _coerce_tokens(output_tokens, "output_tokens"),
        _coerce_tokens(cache_read_tokens, "cache_read_tokens"),
        _coerce_tokens(cache_write_tokens, "cache_write_tokens"),
        _coerce_tokens(reasoning_tokens, "reasoning_tokens"),
    )
    cost_n = _coerce_cost(cost)
    exact_b = _coerce_exact(exact)

    agent_t = _coerce_text(agent, "agent")
    tool_t = _coerce_text(tool, "tool")
    model_t = _coerce_text(model, "model")
    project_t = _coerce_text(project, "project")
    request_t = _coerce_text(request_id, "request_id")

    if raw_metadata is None:
        metadata: dict = {}
    elif isinstance(raw_metadata, dict):
        metadata = dict(raw_metadata)
    else:
        raise EventValidationError("raw_metadata: expected a mapping")

    total = sum(tokens)
    event_id = make_event_id(
        provider=prov,
        timestamp=ts,
        request_id=request_t,
        model=model_t,
        agent=agent_t,
        project=project_t,
        tokens=tokens,
        cost=cost_n,
    )
    # Zero-usage records (errors, streaming placeholders) must not merge with
    # each other: they carry no tokens to double-count, and collapsing them
    # would undercount requests. Keep them keyed to their source event.
    dedup_key = (
        f"zero|{event_id}"
        if total == 0
        else make_dedup_key(provider=prov, model=model_t, timestamp=ts, tokens=tokens)
    )
    return UsageEvent(
        id=event_id,
        timestamp=ts,
        provider=prov,
        agent=agent_t,
        tool=tool_t,
        model=model_t,
        project=project_t,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cache_read_tokens=tokens[2],
        cache_write_tokens=tokens[3],
        reasoning_tokens=tokens[4],
        total_tokens=total,
        cost=cost_n,
        exact=exact_b,
        source=src,
        request_id=request_t,
        dedup_key=dedup_key,
        raw_metadata=metadata,
    )


def _coerce_exact(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "exact"):
            return True
        if text in ("false", "0", "no", "estimated", "estimate"):
            return False
    raise EventValidationError(f"exact: expected a boolean, got {value!r}")


EVENT_COLUMNS = (
    "id",
    "timestamp",
    "provider",
    "agent",
    "tool",
    "model",
    "project",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
    "cost",
    "exact",
    "source",
    "request_id",
    "dedup_key",
    "raw_metadata",
)


def event_to_row(event: UsageEvent) -> dict:
    """Named-column row for INSERTs (safe against additive migrations)."""
    return {
        "id": event.id,
        "timestamp": event.timestamp,
        "provider": event.provider,
        "agent": event.agent,
        "tool": event.tool,
        "model": event.model,
        "project": event.project,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "cache_read_tokens": event.cache_read_tokens,
        "cache_write_tokens": event.cache_write_tokens,
        "reasoning_tokens": event.reasoning_tokens,
        "total_tokens": event.total_tokens,
        "cost": event.cost,
        "exact": 1 if event.exact else 0,
        "source": event.source,
        "request_id": event.request_id,
        "dedup_key": event.dedup_key,
        "raw_metadata": event.metadata_json(),
    }


def event_from_row(row: dict) -> UsageEvent:
    metadata = row.get("raw_metadata") or "{}"
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
        except json.JSONDecodeError:
            parsed = {"_unparsed": metadata}
    else:
        parsed = metadata
    tokens = (
        int(row.get("input_tokens") or 0),
        int(row.get("output_tokens") or 0),
        int(row.get("cache_read_tokens") or 0),
        int(row.get("cache_write_tokens") or 0),
        int(row.get("reasoning_tokens") or 0),
    )
    # dedup_key is derived from the row's own values when absent (pre-migration
    # rows, old exports), so identity never depends on a stored value being
    # in sync with its columns.
    dedup_key = row.get("dedup_key") or ""
    if not dedup_key:
        dedup_key = (
            f"zero|{row['id']}"
            if sum(tokens) == 0
            else make_dedup_key(
                provider=row["provider"],
                model=row.get("model"),
                timestamp=row["timestamp"],
                tokens=tokens,
            )
        )
    return UsageEvent(
        id=row["id"],
        timestamp=row["timestamp"],
        provider=row["provider"],
        agent=row.get("agent"),
        tool=row.get("tool"),
        model=row.get("model"),
        project=row.get("project"),
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cache_read_tokens=tokens[2],
        cache_write_tokens=tokens[3],
        reasoning_tokens=tokens[4],
        total_tokens=int(row.get("total_tokens") or 0),
        cost=row.get("cost"),
        exact=bool(row.get("exact")),
        source=row["source"],
        request_id=row.get("request_id"),
        dedup_key=dedup_key,
        raw_metadata=parsed,
    )
