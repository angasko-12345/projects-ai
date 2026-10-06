"""Collectors for known local agent/provider usage sources.

Each collector reads one real local data source (SQLite database, session
JSONL files, or a rollout log), read-only, and produces normalized
``UsageEvent`` records. Rules:

- Never estimate a token count. A source either reports usage or the
  collector marks it unavailable with the reason recorded in
  ``collector_status``.
- Historical import: collectors scan the full local history each sync;
  deduplication (db.insert_events) makes repeated syncs idempotent.
- Registry order is attribution order: agent-side collectors run before
  provider-side ones, so the tool attribution lands on the first-seen
  canonical row.

The registry also holds known-unavailable sources (provider dashboards that
are key-gated, deny this key, or only expose aggregate usage that would
double-count agent-side rows) so the coverage indicator can report them
instead of silently omitting them.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from . import db, opencode
from .model import EventValidationError, UsageEvent, normalize_event


class CollectorError(Exception):
    """A collector could not read its source."""


def _canonical(ts: object) -> str:
    """Any supported timestamp (epoch seconds/millis or ISO) -> canonical UTC ISO."""
    if isinstance(ts, (int, float)):
        epoch = float(ts)
        if epoch > 1e12:  # milliseconds
            epoch /= 1000.0
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    else:
        text = str(ts).strip()
        dt = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)


def _first_existing(candidates: Iterable[Path]) -> Path | None:
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def _user_home() -> Path:
    return Path.home()


# --------------------------------------------------------------- kilo


def find_kilo() -> Path | None:
    env = os.environ.get("KILO_DB")
    return _first_existing(
        ([Path(env)] if env else [])
        + [
            _user_home() / ".kilo" / "share" / "kilo.db",
            Path("D:/admin/code/cli_files/kilo/home/share/kilo.db"),
        ]
    )


def collect_kilo(path: Path) -> list[UsageEvent]:
    """Kilo stores one row per message; assistant rows carry usage in data.tokens."""
    conn = _open_ro(path)
    try:
        rows = conn.execute(
            "SELECT m.id, m.time_created, m.data, m.session_id, s.directory, s.agent "
            "FROM message m LEFT JOIN session s ON s.id = m.session_id"
        ).fetchall()
    finally:
        conn.close()
    events: list[UsageEvent] = []
    for message_id, time_created, data_json, session_id, directory, session_agent in rows:
        try:
            data = json.loads(data_json)
        except (TypeError, ValueError):
            continue
        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            continue  # rows without usage (user messages, metadata)
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        cost = data.get("cost")
        try:
            events.append(
                normalize_event(
                    timestamp=_canonical(time_created),
                    provider=data.get("providerID") or "kilo",
                    agent=data.get("agent") or session_agent,
                    tool="Kilo",
                    model=data.get("modelID"),
                    project=directory,
                    input_tokens=tokens.get("input", 0),
                    output_tokens=tokens.get("output", 0),
                    cache_read_tokens=cache.get("read", 0),
                    cache_write_tokens=cache.get("write", 0),
                    reasoning_tokens=tokens.get("reasoning", 0),
                    cost=cost if isinstance(cost, (int, float)) else None,
                    exact=True,
                    source="kilo",
                    request_id=str(message_id),
                    raw_metadata={
                        "session_id": session_id,
                        "reported_total": tokens.get("total"),
                    },
                )
            )
        except EventValidationError:
            continue
    return events
# --------------------------------------------------------------- claude code


def find_claude() -> Path | None:
    env = os.environ.get("CLAUDE_PROJECTS")
    return _first_existing(
        ([Path(env)] if env else [])
        + [
            _user_home() / ".claude" / "projects",
            Path("D:/admin/code/cli_files/claude/home/projects"),
        ]
    )


def _parse_claude_model(model_str: str | None) -> tuple[str, str]:
    """Split router model string into (provider, model).

    Format observed: "<protocol>/<provider>/<vendor>/<model>" or
    "<protocol>/<provider>/<model>". Falls back to provider="anthropic".
    """
    if not model_str:
        return "anthropic", "unknown"
    parts = model_str.split("/")
    if len(parts) >= 3 and parts[0] in ("anthropic", "openai", "google", "bedrock", "vertex"):
        provider = parts[1]
        model = "/".join(parts[2:])
        return provider, model
    # Plain model like "claude-sonnet-4-5" or "<synthetic>"
    return "anthropic", model_str


def collect_claude(path: Path) -> list[UsageEvent]:
    """Claude Code projects: JSONL files per session; assistant messages carry usage.

    Usage fields follow Anthropic API: input_tokens (excludes cache), cache_read_input_tokens,
    cache_creation_input_tokens, output_tokens (includes thinking), thinking_tokens.
    Duplicate request_id records appear within and across session files (forked copies);
    dedupe by keeping earliest timestamp per request_id.
    """
    events: list[UsageEvent] = []
    # Group records by request_id to dedupe streaming forks and file copies.
    by_rid: dict[str, list[dict]] = {}
    for file in sorted(path.rglob("*.jsonl")):
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            rid = obj.get("requestId")
            if not isinstance(rid, str) or not rid:
                # No request id (synthetic): treat as separate event per record
                rid = f"__no_rid_{len(by_rid)}"
            by_rid.setdefault(rid, []).append(obj)

    for rid, records in by_rid.items():
        # Keep the earliest timestamp record for this request id.
        canonical = min(
            records,
            key=lambda r: r.get("timestamp") or r.get("time") or "",
        )
        msg = canonical.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        model_str = msg.get("model") or canonical.get("model")
        provider, model = _parse_claude_model(model_str)
        timestamp = canonical.get("timestamp") or canonical.get("time")
        project = canonical.get("cwd")
        cost = canonical.get("totalCostUSD")
        # Anthropic: output_tokens includes thinking; cache_* are separate.
        input_tok = int(usage.get("input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        output_total = int(usage.get("output_tokens") or 0)
        thinking = int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
        output_excl = max(output_total - thinking, 0)
        try:
            events.append(
                normalize_event(
                    timestamp=_canonical(timestamp),
                    provider=provider,
                    agent=None,
                    tool="Claude Code",
                    model=model,
                    project=project,
                    input_tokens=input_tok,
                    output_tokens=output_excl,
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write,
                    reasoning_tokens=thinking,
                    cost=cost if isinstance(cost, (int, float)) else None,
                    exact=True,
                    source="claude",
                    request_id=rid,
                    raw_metadata={
                        "entrypoint": canonical.get("entrypoint"),
                        "version": canonical.get("version"),
                        "model_raw": model_str,
                    },
                )
            )
        except EventValidationError:
            continue
    return events


# --------------------------------------------------------------- cline


def find_cline() -> Path | None:
    env = os.environ.get("CLINE_SESSIONS")
    return _first_existing(
        ([Path(env)] if env else [])
        + [
            _user_home() / ".cline" / "data" / "sessions",
            Path("D:/admin/code/cli_files/cline/home/data/sessions"),
        ]
    )


def collect_cline(path: Path) -> list[UsageEvent]:
    """Cline session directories: *.messages.json files with per-assistant-message metrics.

    Metrics fields: inputTokens, outputTokens, cacheReadTokens, cacheWriteTokens.
    Observed pattern shows inputTokens INCLUDES cacheReadTokens (cacheRead_{k+1} == inputTokens_k
    across consecutive messages). Compute input_excl = max(0, input - cacheRead - cacheWrite).
    Model/provider from modelInfo per message; project from session metadata cwd.
    """
    events: list[UsageEvent] = []
    for msg_file in sorted(path.rglob("*.messages.json")):
        try:
            data = json.loads(msg_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # Read companion session meta for cwd
        session_file = msg_file.with_suffix("").with_suffix(".json")
        cwd: str | None = None
        session_model: str | None = None
        session_provider: str | None = None
        if session_file.exists():
            try:
                meta = json.loads(session_file.read_text(encoding="utf-8"))
                cwd = meta.get("cwd")
                session_model = meta.get("model")
                session_provider = meta.get("provider")
            except (OSError, ValueError):
                pass
        for msg in data.get("messages", []):
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            metrics = msg.get("metrics")
            if not isinstance(metrics, dict):
                continue
            model_info = msg.get("modelInfo") or {}
            provider = model_info.get("provider") or session_provider or "unknown"
            model = model_info.get("id") or session_model
            ts = msg.get("ts")
            request_id = msg.get("id")
            input_total = int(metrics.get("inputTokens") or 0)
            cache_read = int(metrics.get("cacheReadTokens") or 0)
            cache_write = int(metrics.get("cacheWriteTokens") or 0)
            output_tok = int(metrics.get("outputTokens") or 0)
            # Reasoning tokens occasionally present
            reasoning = int(metrics.get("reasoningTokenCount") or 0)
            input_excl = max(input_total - cache_read - cache_write, 0)
            try:
                events.append(
                    normalize_event(
                        timestamp=_canonical(ts),
                        provider=provider,
                        agent=None,
                        tool="Cline",
                        model=model,
                        project=cwd,
                        input_tokens=input_excl,
                        output_tokens=max(output_tok - reasoning, 0),
                        cache_read_tokens=cache_read,
                        cache_write_tokens=cache_write,
                        reasoning_tokens=reasoning,
                        cost=None,
                        exact=True,
                        source="cline",
                        request_id=request_id,
                        raw_metadata={
                            "session_file": msg_file.name,
                            "input_total_reported": input_total,
                        },
                    )
                )
            except EventValidationError:
                continue
    return events


# --------------------------------------------------------------- deepseek harness (dsh)


def find_dsh() -> Path | None:
    env = os.environ.get("DSH_SESSIONS")
    return _first_existing(
        ([Path(env)] if env else [])
        + [
            Path("D:/admin/code/cli_files/dsh/sessions"),
        ]
    )


def _decompress_zstd(data: bytes) -> str:
    """Decompress zstd using stdlib (3.14+) or backport."""
    try:
        import compression.zstd as zstd
        return zstd.decompress(data).decode("utf-8")
    except ImportError:
        try:
            import zstandard as zstd
            return zstd.decompress(data).decode("utf-8")
        except ImportError:
            raise CollectorError("no zstd decoder available (need Python 3.14+ or zstandard package)")


def collect_dsh(path: Path) -> list[UsageEvent]:
    """DeepSeek Harness session files: zstd-compressed JSONL with assistant/message usage.

    Record types: model/selection (provider+model), request/context (provider+model),
    assistant/message (usage: inputTokens, outputTokens, totalTokens, cacheReadTokens).
    Verified: inputTokens + outputTokens + cacheReadTokens == totalTokens (input excludes cache).
    """
    events: list[UsageEvent] = []
    for file in sorted(path.rglob("session.v4.jsonl.zstd")):
        try:
            compressed = file.read_bytes()
        except OSError:
            continue
        try:
            text = _decompress_zstd(compressed)
        except CollectorError:
            # Record as error and continue other files
            raise
        lines = text.splitlines()
        # Track current provider/model from preceding events in file order.
        current_provider = "codecraft"
        current_model = "unknown"
        session_cwd: str | None = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            rtype = obj.get("type")
            data = obj.get("data")
            if not isinstance(data, dict):
                data = {}
            if rtype in ("model/selection", "request/context"):
                if data.get("provider"):
                    current_provider = data["provider"]
                if data.get("model") or data.get("modelName"):
                    current_model = data.get("model") or data.get("modelName") or current_model
            elif rtype == "session":
                session_cwd = data.get("cwd") or session_cwd
            elif rtype == "assistant/message":
                usage = data.get("usage")
                if not isinstance(usage, dict):
                    continue
                request_id = obj.get("id")
                timestamp = obj.get("time")
                input_tok = int(usage.get("inputTokens") or 0)
                output_tok = int(usage.get("outputTokens") or 0)
                cache_read = int(usage.get("cacheReadTokens") or 0)
                # totalTokens verified: input + output + cache_read == total
                reasoning = 0  # not reported separately in dsh
                try:
                    events.append(
                        normalize_event(
                            timestamp=_canonical(timestamp),
                            provider=current_provider,
                            agent=None,
                            tool="DeepSeek Harness",
                            model=current_model,
                            project=session_cwd,
                            input_tokens=input_tok,
                            output_tokens=output_tok,
                            cache_read_tokens=cache_read,
                            cache_write_tokens=0,
                            reasoning_tokens=reasoning,
                            cost=None,
                            exact=True,
                            source="dsh",
                            request_id=request_id,
                            raw_metadata={
                                "session_file": file.name,
                            },
                        )
                    )
                except EventValidationError:
                    continue
    return events


# --------------------------------------------------------------- unavailable markers


def _unavailable_collector(source: str, tool: str, reason: str, kind: str = "provider") -> Collector:
    return Collector(
        source=source,
        tool=tool,
        capability="unavailable",
        kind=kind,
        unavailable_reason=reason,
    )


# ------------------------------------------------------- pi / oh-my-pi


def find_pi() -> Path | None:
    env = os.environ.get("PI_SESSIONS")
    return _first_existing(
        ([Path(env)] if env else [])
        + [
            _user_home() / ".pi" / "agent" / "sessions",
            Path("D:/admin/code/cli_files/pi/home/agent/sessions"),
        ]
    )


def find_omp() -> Path | None:
    env = os.environ.get("OMP_SESSIONS")
    return _first_existing(
        ([Path(env)] if env else [])
        + [
            _user_home() / ".omp" / "agent" / "sessions",
            Path("D:/admin/code/cli_files/oh-my-pi/home/agent/sessions"),
        ]
    )


def collect_session_dir(path: Path, *, source: str, tool: str) -> list[UsageEvent]:
    """Parse Pi-family session JSONL files (shared format).

    Each file: a ``session`` record (cwd, session id) plus ``message``
    records; assistant messages carry provider usage. Zero-usage rows are
    kept (consistent with the OpenCode collector); messages without a usage
    block are skipped; malformed lines are skipped individually.
    """
    events: list[UsageEvent] = []
    for file in sorted(path.rglob("*.jsonl")):
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        parsed: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                parsed.append(obj)
        # First pass: session metadata (record order varies across versions).
        session_id: str | None = None
        cwd: str | None = None
        for obj in parsed:
            if obj.get("type") == "session":
                session_id = obj.get("id") or session_id
                cwd = obj.get("cwd") or cwd
                break
        for obj in parsed:
            if obj.get("type") != "message":
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            cost = usage.get("cost")
            cost_total = cost.get("total") if isinstance(cost, dict) else None
            try:
                events.append(
                    normalize_event(
                        timestamp=_canonical(obj.get("timestamp") or msg.get("timestamp")),
                        provider=msg.get("provider") or "unknown",
                        agent=None,
                        tool=tool,
                        model=msg.get("model"),
                        project=cwd,
                        input_tokens=usage.get("input", 0),
                        output_tokens=usage.get("output", 0),
                        cache_read_tokens=usage.get("cacheRead", 0),
                        cache_write_tokens=usage.get("cacheWrite", 0),
                        reasoning_tokens=usage.get("reasoning", 0),
                        cost=cost_total if isinstance(cost_total, (int, float)) else None,
                        exact=True,
                        source=source,
                        request_id=obj.get("id"),
                        raw_metadata={
                            "session_id": session_id,
                            "api": msg.get("api"),
                            "reported_total": usage.get("totalTokens"),
                        },
                    )
                )
            except EventValidationError:
                continue
    return events


def collect_pi(path: Path) -> list[UsageEvent]:
    return collect_session_dir(path, source="pi", tool="Pi")


def collect_omp(path: Path) -> list[UsageEvent]:
    return collect_session_dir(path, source="omp", tool="Oh My Pi")


# --------------------------------------------------------------- codex


def find_codex() -> Path | None:
    env = os.environ.get("CODEX_SESSIONS")
    return _first_existing(
        ([Path(env)] if env else [])
        + [
            _user_home() / ".codex" / "sessions",
            Path("D:/admin/code/cli_files/codex/home/sessions"),
        ]
    )


def collect_codex(path: Path) -> list[UsageEvent]:
    """Codex rollout logs: token_usage_record rows carry per-response usage.

    usage.input_tokens includes cached input and output_tokens includes
    reasoning output; both are split so the tracker invariant holds
    (input_excl + cache_read + cache_write + output_excl + reasoning = total).
    Model comes from the preceding turn_context; provider from session_meta.
    """
    events: list[UsageEvent] = []
    for file in sorted(path.rglob("rollout-*.jsonl")):
        cwd: str | None = None
        model: str | None = None
        provider = "openai"
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            if kind == "session_meta":
                cwd = payload.get("cwd") or cwd
                provider = payload.get("model_provider") or provider
            elif kind == "turn_context":
                model = payload.get("model") or model
            elif kind == "token_usage_record":
                usage = payload.get("usage")
                if not isinstance(usage, dict):
                    continue
                input_total = int(usage.get("input_tokens") or 0)
                cache_read = int(usage.get("cached_input_tokens") or 0)
                cache_write = int(usage.get("cache_write_input_tokens") or 0)
                output_total = int(usage.get("output_tokens") or 0)
                reasoning = int(usage.get("reasoning_output_tokens") or 0)
                try:
                    events.append(
                        normalize_event(
                            timestamp=_canonical(obj.get("timestamp")),
                            provider=provider,
                            agent=None,
                            tool="Codex",
                            model=model,
                            project=cwd,
                            input_tokens=max(input_total - cache_read - cache_write, 0),
                            output_tokens=max(output_total - reasoning, 0),
                            cache_read_tokens=cache_read,
                            cache_write_tokens=cache_write,
                            reasoning_tokens=reasoning,
                            exact=True,
                            source="codex",
                            request_id=payload.get("response_id"),
                            raw_metadata={
                                "session_id": payload.get("session_id"),
                                "turn_id": payload.get("turn_id"),
                                "reported_total": usage.get("total_tokens"),
                            },
                        )
                    )
                except EventValidationError:
                    continue
    return events


# -------------------------------------------------------------- hermes


def find_hermes() -> Path | None:
    env = os.environ.get("HERMES_STATE_DB")
    return _first_existing(
        ([Path(env)] if env else [])
        + [
            _user_home() / ".hermes" / "state.db",
            Path("D:/admin/code/cli_files/hermes/state.db"),
        ]
    )


def collect_hermes(path: Path) -> list[UsageEvent]:
    """Hermes session_model_usage: API-counted rollups per session/model.

    Token counts come straight from the API counters (exact). Costs carry
    their provenance (actual vs estimated) in metadata; the token numbers are
    never derived from cost.
    """
    conn = _open_ro(path)
    try:
        rows = conn.execute(
            "SELECT u.session_id, u.model, u.billing_provider, u.billing_base_url, "
            "u.task, u.api_call_count, u.input_tokens, u.output_tokens, "
            "u.cache_read_tokens, u.cache_write_tokens, u.reasoning_tokens, "
            "u.estimated_cost_usd, u.actual_cost_usd, u.cost_status, u.cost_source, "
            "u.first_seen, u.last_seen, s.cwd "
            "FROM session_model_usage u LEFT JOIN sessions s ON s.id = u.session_id"
        ).fetchall()
    finally:
        conn.close()
    events: list[UsageEvent] = []
    for row in rows:
        (
            session_id, model, billing_provider, billing_base_url, task, api_calls,
            input_tokens, output_tokens, cache_read, cache_write, reasoning,
            estimated_cost, actual_cost, cost_status, cost_source,
            first_seen, last_seen, cwd,
        ) = row
        actual = float(actual_cost or 0.0)
        estimated = float(estimated_cost or 0.0)
        cost = actual if actual > 0.0 else (estimated if estimated > 0.0 else 0.0)
        try:
            events.append(
                normalize_event(
                    timestamp=_canonical(last_seen or first_seen),
                    provider=billing_provider or "unknown",
                    agent=None,
                    tool="Hermes",
                    model=model,
                    project=cwd,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write,
                    reasoning_tokens=reasoning,
                    cost=cost,
                    exact=True,
                    source="hermes",
                    raw_metadata={
                        "session_id": session_id,
                        "task": task,
                        "requests": api_calls,
                        "first_seen": first_seen,
                        "billing_base_url": billing_base_url,
                        "cost_status": cost_status,
                        "cost_source": cost_source,
                        "estimated_cost_usd": estimated,
                        "actual_cost_usd": actual,
                    },
                )
            )
        except EventValidationError:
            continue
    return events


# ------------------------------------------------------ github copilot


def find_copilot() -> Path | None:
    appdata = os.environ.get("APPDATA")
    candidates: list[Path] = []
    if appdata:
        base = Path(appdata)
        candidates += [
            base / "Code" / "User" / "globalStorage" / "github.copilot-chat" / "session-state",
            base / "Code - Insiders" / "User" / "globalStorage" / "github.copilot-chat" / "session-state",
        ]
    candidates.append(Path("D:/admin/code/cli_files/copilot/home/session-state"))
    found = _first_existing(candidates)
    if found is None or not any(found.rglob("events.jsonl")):
        return None
    return found


def collect_copilot(path: Path) -> list[UsageEvent]:
    """Copilot Chat emits one session.shutdown event per session with
    modelMetrics per model (session-level totals, tokenDetails breakdown).

    input tokenDetails.input.tokenCount includes cache reads/writes and
    outputTokens includes reasoning; both are split to satisfy the tracker
    invariant. Reported request cost units are kept in metadata only (their
    unit is not documented, so no dollar figure is asserted).
    """
    events: list[UsageEvent] = []
    for file in sorted(path.rglob("events.jsonl")):
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "session.shutdown":
                continue
            data = obj.get("data")
            metrics = data.get("modelMetrics") if isinstance(data, dict) else None
            if not isinstance(metrics, dict):
                continue
            for model, model_metrics in metrics.items():
                if not isinstance(model_metrics, dict):
                    continue
                usage = model_metrics.get("usage") or {}
                details = model_metrics.get("tokenDetails") or {}
                input_total = int(
                    (details.get("input") or {}).get("tokenCount")
                    or usage.get("inputTokens")
                    or 0
                )
                cache_read = int(usage.get("cacheReadTokens") or 0)
                cache_write = int(usage.get("cacheWriteTokens") or 0)
                output_total = int(usage.get("outputTokens") or 0)
                reasoning = int(usage.get("reasoningTokens") or 0)
                requests = model_metrics.get("requests") or {}
                try:
                    events.append(
                        normalize_event(
                            timestamp=_canonical(obj.get("timestamp")),
                            provider="github-copilot",
                            agent=None,
                            tool="GitHub Copilot",
                            model=model,
                            project=None,
                            input_tokens=max(input_total - cache_read - cache_write, 0),
                            output_tokens=max(output_total - reasoning, 0),
                            cache_read_tokens=cache_read,
                            cache_write_tokens=cache_write,
                            reasoning_tokens=reasoning,
                            exact=True,
                            source="copilot",
                            raw_metadata={
                                "session_file": file.name,
                                "shutdown_id": obj.get("id"),
                                "requests": requests.get("count"),
                                "reported_cost_units": requests.get("cost"),
                            },
                        )
                    )
                except EventValidationError:
                    continue
    return events


# ------------------------------------------------------------- registry


@dataclass(frozen=True)
class Collector:
    source: str
    tool: str
    capability: str  # "exact" | "estimated" | "unavailable"
    kind: str  # "agent" | "provider"
    find: Callable[[], Path | None] | None = None
    collect: Callable[[Path], list[UsageEvent]] | None = None
    unavailable_reason: str | None = None


REGISTRY: list[Collector] = [
    Collector("opencode", "OpenCode", "exact", "agent", opencode.find_database, opencode.collect),
    Collector("kilo", "Kilo", "exact", "agent", find_kilo, collect_kilo),
    Collector("hermes", "Hermes", "exact", "agent", find_hermes, collect_hermes),
    Collector("codex", "Codex", "exact", "agent", find_codex, collect_codex),
    Collector("pi", "Pi", "exact", "agent", find_pi, collect_pi),
    Collector("omp", "Oh My Pi", "exact", "agent", find_omp, collect_omp),
    Collector("copilot", "GitHub Copilot", "exact", "agent", find_copilot, collect_copilot),
    Collector("claude", "Claude Code", "exact", "agent", find_claude, collect_claude),
    Collector("cline", "Cline", "exact", "agent", find_cline, collect_cline),
    Collector("dsh", "DeepSeek Harness", "exact", "agent", find_dsh, collect_dsh),
    # Provider-side dashboards: recorded as unavailable so coverage is honest.
    Collector(
        "openrouter", "OpenRouter", "unavailable", "provider",
        unavailable_reason=(
            "activity API returned HTTP 403 for the configured OPENROUTER_API_KEY; "
            "provider-side import disabled to avoid double counting (agent rows already attribute OpenRouter)"
        ),
    ),
    Collector(
        "gemini", "Google Gemini", "unavailable", "provider",
        unavailable_reason=(
            "usage API exposes aggregate-only totals that would double count agent-side Google rows; "
            "excluded by design"
        ),
    ),
    Collector(
        "openai", "OpenAI", "unavailable", "provider",
        unavailable_reason="no OPENAI_API_KEY configured; usage API not queried",
    ),
    # Installed agents with no usable local token data:
    _unavailable_collector(
        "antigravity",
        "Antigravity",
        "local stores contain no token/usage fields (conversations are protobuf blobs, logs lack usage); no API access",
        kind="agent",
    ),
    _unavailable_collector(
        "freebuff",
        "FreeBuff/Codebuff",
        "only context-size telemetry (contextTokenCount, creditsUsed); no per-request input/output token counts",
        kind="agent",
    ),
    _unavailable_collector(
        "free-claude-code",
        "free-claude-code (fcc)",
        "local database empty (code_sessions/runs/items/prompts all 0 rows); wrappers delegate to other CLIs whose usage lands in those CLIs' own stores",
        kind="agent",
    ),
    _unavailable_collector(
        "gemini-cli",
        "Gemini CLI",
        "no local session/usage data found (config only); conversation data in Antigravity stores lacks usage fields",
        kind="agent",
    ),
]


def sync_all(conn: sqlite3.Connection) -> dict[str, dict]:
    """Run every collector; one source's failure never stops the others.

    Returns {source: {"status": "ok"|"error"|"unavailable", ...}} with counts
    or the error message; every outcome is persisted to collector_status.
    """
    results: dict[str, dict] = {}
    for collector in REGISTRY:
        if collector.unavailable_reason is not None:
            db.record_sync(
                conn,
                source=collector.source,
                tool=collector.tool,
                capability="unavailable",
                error=collector.unavailable_reason,
            )
            results[collector.source] = {
                "status": "unavailable",
                "error": collector.unavailable_reason,
            }
            continue
        try:
            assert collector.find is not None and collector.collect is not None
            path = collector.find()
            if path is None:
                raise CollectorError("data source not found in known locations")
            events = collector.collect(path)
            inserted, updated, unchanged = db.insert_events(conn, events)
            stored = conn.execute(
                "SELECT COUNT(*) FROM events WHERE source = ?", (collector.source,)
            ).fetchone()[0]
            db.record_sync(
                conn,
                source=collector.source,
                tool=collector.tool,
                capability=collector.capability,
                events=int(stored),
            )
            results[collector.source] = {
                "status": "ok",
                "inserted": inserted,
                "updated": updated,
                "unchanged": unchanged,
                "stored": int(stored),
            }
        except Exception as exc:  # noqa: BLE001 - partial failure isolation
            message = f"{type(exc).__name__}: {exc}"
            db.record_sync(
                conn,
                source=collector.source,
                tool=collector.tool,
                capability="unavailable",
                error=message,
            )
            results[collector.source] = {"status": "error", "error": message}
    return results
