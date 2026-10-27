"""Headroom ledger: remaining-limit readings from local, credential-free sources.

Each provider reports how much of a usage window is used and when it resets.
This module reads those reports where they already exist on the machine and
returns them in one documented shape (see README, "Headroom"). It never
estimates a limit from token counts, never stores a credential, and marks
missing or out-of-date readings as such instead of filling them in.

Sources:

* ``codex``: ``rate_limits`` snapshots that Codex writes into its rollout
  session files on ``event_msg``/``token_count`` events (server-reported
  percentages per window). Read-only.
* ``claude``: the opt-in status line tee (:mod:`token_tracker.statusline_tee`)
  appends the ``rate_limits`` object Claude Code hands to the status line to a
  local JSONL ledger. Without the tee there is no local Claude limit data.
* ``openrouter``: ``GET /api/v1/key``, only when ``OPENROUTER_API_KEY`` is
  already set in the environment. The key is sent to OpenRouter and nowhere
  else; it is never written or printed. Without a key the source is skipped.

No PySide6 import anywhere on this path, and nothing here writes to the usage
database: the GUI and the sync pipeline are untouched.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from . import statusline_tee

SCHEMA = "ai-token-tracker/headroom"
SCHEMA_VERSION = 1

WARN_AT = 80.0
CRITICAL_AT = 95.0

DEFAULT_MAX_CODEX_FILES = 40
DEFAULT_STALE_CAP_S = 6 * 3600
DEFAULT_STALE_UNKNOWN_WINDOW_S = 3600
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
OPENROUTER_ENV = "OPENROUTER_API_KEY"
OPENROUTER_TIMEOUT_S = 5.0

# Provider status values.
OK = "ok"
NO_DATA = "no_data"
NOT_CONFIGURED = "not_configured"
SKIPPED = "skipped"
ERROR = "error"


# ------------------------------------------------------------ primitives


def _num(value: object) -> float | None:
    """A finite number, or None (bools and strings are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _epoch(value: object) -> float | None:
    """Epoch seconds from epoch seconds/milliseconds or an ISO-8601 string."""
    number = _num(value)
    if number is not None:
        return number / 1000.0 if number > 1e11 else number
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text[-1] in "Zz":
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def iso_utc(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_label(minutes: int | None) -> str:
    """Label a window by its length, never by its slot (Codex sometimes puts
    the weekly window in the ``primary`` slot)."""
    if minutes is None or minutes <= 0:
        return "unknown"
    if minutes == 10080:
        return "weekly"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def default_stale_after(window_minutes: int | None) -> float:
    """A reading older than this is ``stale``: a quarter of the window, capped
    at six hours; one hour when the window length is unknown."""
    if not window_minutes:
        return float(DEFAULT_STALE_UNKNOWN_WINDOW_S)
    return float(min(window_minutes * 60 / 4, DEFAULT_STALE_CAP_S))


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def make_window(
    *,
    source: str,
    window: str,
    now: float,
    as_of: float | None,
    unit: str,
    used: float | None,
    limit: float | None,
    used_percent: float | None,
    resets_at: float | None,
    window_minutes: int | None = None,
    limit_id: str | None = None,
    reached: bool = False,
    plan_type: str | None = None,
    reset_interval: str | None = None,
    resets_at_basis: str | None = "reported",
    stale_after: float | None = None,
) -> dict:
    """One window entry of the schema, with freshness and state derived."""
    if used_percent is None and used is not None and limit:
        used_percent = used / limit * 100.0
    age = None if as_of is None else max(now - as_of, 0.0)
    threshold = stale_after if stale_after is not None else default_stale_after(window_minutes)
    if resets_at is not None and resets_at <= now:
        freshness = "expired"
    elif age is None:
        freshness = "unknown"
    elif age > threshold:
        freshness = "stale"
    else:
        freshness = "fresh"

    if freshness in ("expired", "unknown"):
        state = "unknown"
    elif reached or (used_percent is not None and used_percent >= 100.0):
        state = "exhausted"
    elif used_percent is None:
        state = "unknown"
    elif used_percent >= CRITICAL_AT:
        state = "critical"
    elif used_percent >= WARN_AT:
        state = "warn"
    else:
        state = "ok"

    elapsed_percent = pace = None
    if window_minutes and resets_at is not None and as_of is not None:
        span = window_minutes * 60.0
        elapsed_percent = min(max((as_of - (resets_at - span)) / span * 100.0, 0.0), 100.0)
        if freshness != "expired" and used_percent is not None and elapsed_percent > 0:
            pace = used_percent / elapsed_percent

    remaining = None
    if used is not None and limit is not None:
        remaining = max(limit - used, 0.0)

    return {
        "source": source,
        "window": window,
        "limit_id": limit_id,
        "window_minutes": window_minutes,
        "unit": unit,
        "used": _round(used, 4),
        "limit": _round(limit, 4),
        "remaining": _round(remaining, 4),
        "used_percent": _round(used_percent),
        "resets_at": iso_utc(resets_at),
        "resets_at_basis": resets_at_basis if resets_at is not None else None,
        "reset_interval": reset_interval,
        "as_of": iso_utc(as_of),
        "age_seconds": None if age is None else int(age),
        "freshness": freshness,
        "state": state,
        "reached": bool(reached),
        "elapsed_percent": _round(elapsed_percent),
        "pace": _round(pace),
        "plan_type": plan_type,
    }


def _provider(
    provider: str,
    source: str,
    status: str,
    *,
    detail: str | None = None,
    location: str | None = None,
    windows: list[dict] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "provider": provider,
        "source": source,
        "status": status,
        "detail": detail,
        "location": location,
        "windows": windows or [],
        "warnings": warnings or [],
    }


def _read_jsonl(path: Path) -> tuple[list[dict], int]:
    """Objects from a JSONL file, plus the number of malformed lines skipped."""
    objs: list[dict] = []
    bad = 0
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            objs.append(obj)
        else:
            bad += 1
    return objs, bad


# ------------------------------------------------------------------ codex


def _codex_files(root: Path, max_files: int) -> list[Path]:
    from . import collectors  # lazy: keeps this module importable on its own

    files = collectors._codex_rollout_files(root)

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    files.sort(key=mtime, reverse=True)
    return files[:max_files]


def read_codex(
    now: float,
    *,
    root: Path | None = None,
    max_files: int = DEFAULT_MAX_CODEX_FILES,
    stale_after: float | None = None,
) -> dict:
    source = "codex-rollout"
    if root is None:
        from . import collectors

        root = collectors.find_codex()
    if root is None:
        return _provider(
            "codex",
            source,
            NOT_CONFIGURED,
            detail="no Codex sessions dir found (~/.codex/sessions, "
            "~/.codex/archived_sessions, or CODEX_SESSIONS)",
        )
    location = str(root)
    files = _codex_files(Path(root), max_files)
    if not files:
        return _provider(
            "codex", source, NO_DATA, location=location, detail="no rollout-*.jsonl files"
        )

    # (limit_id, window key) -> best snapshot seen so far
    latest: dict[tuple[str, object], dict] = {}
    warnings: list[str] = []
    bad_lines = bad_windows = unreadable = 0
    for file in files:
        try:
            objs, bad = _read_jsonl(file)
        except OSError:
            unreadable += 1
            continue
        bad_lines += bad
        for obj in objs:
            payload = obj.get("payload")
            if obj.get("type") != "event_msg" or not isinstance(payload, dict):
                continue
            limits = payload.get("rate_limits")
            if not isinstance(limits, dict):
                continue  # null on codex exec / API-key sessions: missing, not 0%
            observed = _epoch(obj.get("timestamp"))
            if observed is None:
                bad_windows += 1
                continue
            limit_id = (
                limits.get("limit_id") if isinstance(limits.get("limit_id"), str) else "codex"
            )
            plan = limits.get("plan_type") if isinstance(limits.get("plan_type"), str) else None
            reached_type = limits.get("rate_limit_reached_type")
            snapshot_windows = []
            for slot in ("primary", "secondary"):
                w = limits.get(slot)
                if w is None:
                    continue  # a null window is missing, never 0%
                if not isinstance(w, dict):
                    bad_windows += 1
                    continue
                pct = _num(w.get("used_percent"))
                if pct is None:
                    bad_windows += 1
                    continue
                minutes_n = _num(w.get("window_minutes"))
                minutes = int(minutes_n) if minutes_n and minutes_n > 0 else None
                resets = _epoch(w.get("resets_at"))
                if resets is None:
                    in_s = _num(w.get("resets_in_seconds"))
                    resets = observed + in_s if in_s is not None else None
                snapshot_windows.append(
                    {
                        "slot": slot,
                        "pct": pct,
                        "minutes": minutes,
                        "resets": resets,
                        "observed": observed,
                        "plan": plan,
                        "limit_id": limit_id,
                        "reached": False,
                    }
                )
            if reached_type and snapshot_windows:
                # the server says a limit was hit: the fullest window of this snapshot
                max(snapshot_windows, key=lambda s: s["pct"])["reached"] = True
            for snap in snapshot_windows:
                key = (limit_id, snap["minutes"] if snap["minutes"] is not None else snap["slot"])
                held = latest.get(key)
                if held is None or snap["observed"] >= held["observed"]:
                    latest[key] = snap

    if bad_lines:
        warnings.append(f"{bad_lines} malformed JSON line(s) skipped")
    if bad_windows:
        warnings.append(
            f"{bad_windows} rate_limits window(s) with missing or invalid fields skipped"
        )
    if unreadable:
        warnings.append(f"{unreadable} rollout file(s) could not be read")
    if not latest:
        return _provider(
            "codex",
            source,
            NO_DATA,
            location=location,
            warnings=warnings,
            detail=f"no rate_limits in the {len(files)} newest rollout file(s) "
            "(codex exec and API-key sessions record none)",
        )

    windows = []
    for (limit_id, _), snap in sorted(
        latest.items(), key=lambda kv: (kv[0][0], kv[1]["minutes"] or 0)
    ):
        windows.append(
            make_window(
                source=source,
                window=window_label(snap["minutes"]) if snap["minutes"] else snap["slot"],
                now=now,
                as_of=snap["observed"],
                unit="percent",
                used=snap["pct"],
                limit=100.0,
                used_percent=snap["pct"],
                resets_at=snap["resets"],
                window_minutes=snap["minutes"],
                limit_id=limit_id,
                reached=snap["reached"],
                plan_type=snap["plan"],
                stale_after=stale_after,
            )
        )
    return _provider(
        "codex",
        source,
        OK,
        location=location,
        windows=windows,
        warnings=warnings,
        detail=f"newest snapshot per window from {len(files)} newest rollout file(s)",
    )


# ----------------------------------------------------------------- claude

_CLAUDE_WINDOWS = (
    ("five_hour", "5h", 300),
    ("seven_day", "weekly", 10080),
    ("spend_limit", "spend", None),
)


def read_claude(now: float, *, path: Path | None = None, stale_after: float | None = None) -> dict:
    source = "claude-statusline"
    path = Path(path) if path is not None else statusline_tee.claude_ledger_path()
    location = str(path)
    if not path.is_file():
        return _provider(
            "claude",
            source,
            NOT_CONFIGURED,
            location=location,
            detail="status line tee not installed or never ran (no ledger file); "
            "headless claude -p never runs a status line",
        )
    try:
        objs, bad = _read_jsonl(path)
    except OSError as exc:
        return _provider(
            "claude", source, ERROR, location=location, detail=f"cannot read ledger: {exc}"
        )
    warnings = [f"{bad} malformed JSON line(s) skipped"] if bad else []
    latest: dict[str, tuple[float, dict]] = {}
    bad_windows = 0
    for obj in objs:
        observed = _epoch(obj.get("observed_at"))
        limits = obj.get("rate_limits")
        if observed is None or not isinstance(limits, dict):
            bad_windows += 1
            continue
        for key, _, _ in _CLAUDE_WINDOWS:
            w = limits.get(key)
            if w is None:
                continue
            if not isinstance(w, dict) or _num(w.get("used_percentage")) is None:
                bad_windows += 1
                continue
            held = latest.get(key)
            if held is None or observed >= held[0]:
                latest[key] = (observed, w)
    if bad_windows:
        warnings.append(
            f"{bad_windows} record(s) or window(s) with missing or invalid fields skipped"
        )
    if not latest:
        return _provider(
            "claude",
            source,
            NO_DATA,
            location=location,
            warnings=warnings,
            detail="ledger has no rate_limits (only Pro/Max or spend-limited gateway "
            "sessions report them, after the first response)",
        )
    windows = []
    for key, label, minutes in _CLAUDE_WINDOWS:
        if key not in latest:
            continue
        observed, w = latest[key]
        pct = _num(w.get("used_percentage"))
        resets = _epoch(w.get("resets_at"))
        if key == "spend_limit":
            used_usd, limit_usd = _num(w.get("used_usd")), _num(w.get("limit_usd"))
            dollars = used_usd is not None and limit_usd is not None
            windows.append(
                make_window(
                    source=source,
                    window=label,
                    now=now,
                    as_of=observed,
                    unit="usd" if dollars else "percent",
                    used=used_usd if dollars else pct,
                    limit=limit_usd if dollars else 100.0,
                    used_percent=pct,
                    resets_at=resets,
                    window_minutes=None,
                    limit_id=key,
                    reset_interval=w.get("period") if isinstance(w.get("period"), str) else None,
                    stale_after=stale_after,
                )
            )
        else:
            windows.append(
                make_window(
                    source=source,
                    window=label,
                    now=now,
                    as_of=observed,
                    unit="percent",
                    used=pct,
                    limit=100.0,
                    used_percent=pct,
                    resets_at=resets,
                    window_minutes=minutes,
                    limit_id=key,
                    stale_after=stale_after,
                )
            )
    return _provider(
        "claude",
        source,
        OK,
        location=location,
        windows=windows,
        warnings=warnings,
        detail="newest status line snapshot per window",
    )


# ------------------------------------------------------------- openrouter

Fetcher = Callable[[str, float], object]


def _http_fetch(key: str, timeout: float) -> object:
    request = urllib.request.Request(
        OPENROUTER_KEY_URL,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "ai-token-tracker-headroom"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https URL)
        return json.loads(response.read().decode("utf-8"))


def _next_utc_midnight(now: float) -> float:
    day = datetime.fromtimestamp(now, tz=timezone.utc).date()
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() + 86400


def read_openrouter(
    now: float,
    *,
    env: Mapping[str, str] | None = None,
    offline: bool = False,
    fetch: Fetcher | None = None,
    timeout: float = OPENROUTER_TIMEOUT_S,
    stale_after: float | None = None,
) -> dict:
    source = "openrouter-key"
    env = os.environ if env is None else env
    if offline:
        return _provider("openrouter", source, SKIPPED, detail="--offline: network sources skipped")
    key = env.get(OPENROUTER_ENV, "").strip()
    if not key:
        return _provider(
            "openrouter",
            source,
            NOT_CONFIGURED,
            detail=f"{OPENROUTER_ENV} not set; optional source skipped",
        )
    fetch = fetch or _http_fetch
    try:
        body = fetch(key, timeout)
    except urllib.error.HTTPError as exc:
        return _provider(
            "openrouter",
            source,
            ERROR,
            location=OPENROUTER_KEY_URL,
            detail=f"HTTP {exc.code} from /api/v1/key",
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        return _provider(
            "openrouter",
            source,
            ERROR,
            location=OPENROUTER_KEY_URL,
            detail=f"network error: {type(reason).__name__}",
        )
    except ValueError:
        return _provider(
            "openrouter", source, ERROR, location=OPENROUTER_KEY_URL, detail="response was not JSON"
        )
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return _provider(
            "openrouter",
            source,
            ERROR,
            location=OPENROUTER_KEY_URL,
            detail="response has no data object",
        )

    windows = []
    limit = _num(data.get("limit"))
    if limit is not None:
        remaining = _num(data.get("limit_remaining"))
        used = None if remaining is None else max(limit - remaining, 0.0)
        windows.append(
            make_window(
                source=source,
                window="key_credits",
                now=now,
                as_of=now,
                unit="usd",
                used=used,
                limit=limit,
                used_percent=None,
                resets_at=None,
                limit_id="key_limit",
                reset_interval=data.get("limit_reset")
                if isinstance(data.get("limit_reset"), str)
                else None,
                stale_after=stale_after,
            )
        )
    budget = data.get("effective_budget")
    if isinstance(budget, dict) and _num(budget.get("limit_usd")) is not None:
        windows.append(
            make_window(
                source=source,
                window="budget",
                now=now,
                as_of=now,
                unit="usd",
                used=_num(budget.get("spend_usd")),
                limit=_num(budget.get("limit_usd")),
                used_percent=None,
                resets_at=_epoch(budget.get("resets_at")),
                limit_id=budget.get("scope") if isinstance(budget.get("scope"), str) else "budget",
                reset_interval=budget.get("reset_interval")
                if isinstance(budget.get("reset_interval"), str)
                else None,
                stale_after=stale_after,
            )
        )
    free = data.get("free_model_daily_requests")
    if (
        isinstance(free, dict)
        and _num(free.get("limit")) is not None
        and _num(free.get("used")) is not None
    ):
        windows.append(
            make_window(
                source=source,
                window="free_daily_requests",
                now=now,
                as_of=now,
                unit="requests",
                used=_num(free.get("used")),
                limit=_num(free.get("limit")),
                used_percent=None,
                resets_at=_next_utc_midnight(now),
                resets_at_basis="documented_utc_day",
                window_minutes=1440,
                limit_id="free_model_daily_requests",
                reset_interval="daily",
                stale_after=stale_after,
            )
        )
    if not windows:
        return _provider(
            "openrouter",
            source,
            NO_DATA,
            location=OPENROUTER_KEY_URL,
            detail="key reports no credit limit, budget, or free-model quota",
        )
    return _provider(
        "openrouter",
        source,
        OK,
        location=OPENROUTER_KEY_URL,
        windows=windows,
        detail="live read of /api/v1/key",
    )


# ----------------------------------------------------------------- report


def build_report(
    now: float | None = None,
    *,
    codex_root: Path | None = None,
    claude_file: Path | None = None,
    offline: bool = False,
    env: Mapping[str, str] | None = None,
    fetch: Fetcher | None = None,
    stale_after: float | None = None,
    max_codex_files: int = DEFAULT_MAX_CODEX_FILES,
) -> dict:
    """The full headroom document (schema in README). ``now`` is injectable."""
    now = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    readers = (
        (
            "codex",
            "codex-rollout",
            lambda: read_codex(
                now, root=codex_root, max_files=max_codex_files, stale_after=stale_after
            ),
        ),
        (
            "claude",
            "claude-statusline",
            lambda: read_claude(now, path=claude_file, stale_after=stale_after),
        ),
        (
            "openrouter",
            "openrouter-key",
            lambda: read_openrouter(
                now, env=env, offline=offline, fetch=fetch, stale_after=stale_after
            ),
        ),
    )
    providers = []
    for name, source, reader in readers:
        try:
            providers.append(reader())
        except Exception as exc:  # one broken source must not hide the others
            providers.append(_provider(name, source, ERROR, detail=f"{type(exc).__name__}: {exc}"))
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso_utc(now),
        "thresholds": {"warn_percent": WARN_AT, "critical_percent": CRITICAL_AT},
        "providers": providers,
    }


# ------------------------------------------------------------- human view


def _local(iso: str | None, tz: timezone | None) -> str:
    if not iso:
        return "-"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _age(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _used_text(w: dict) -> str:
    if w["unit"] == "percent":
        return "-" if w["used_percent"] is None else f"{w['used_percent']:.1f}%"
    used = "?" if w["used"] is None else f"{w['used']:g}"
    limit = "?" if w["limit"] is None else f"{w['limit']:g}"
    prefix = "$" if w["unit"] == "usd" else ""
    text = f"{prefix}{used}/{prefix}{limit}"
    if w["unit"] == "requests":
        text += " req"
    return text


def render_table(report: dict, tz: timezone | None = None) -> str:
    """Short human view; times in the local zone unless ``tz`` is given."""
    rows = [("PROVIDER", "WINDOW", "USED", "RESETS (local)", "AS OF", "STATE")]
    notes = []
    for p in report["providers"]:
        if not p["windows"]:
            rows.append((p["provider"], "-", "-", "-", "-", p["status"].replace("_", " ")))
            if p["detail"]:
                notes.append(f"{p['provider']}: {p['detail']}")
        for w in p["windows"]:
            name = (
                w["window"]
                if w["limit_id"] in (None, "codex") or p["provider"] != "codex"
                else f"{w['window']} ({w['limit_id']})"
            )
            state = w["state"] if w["freshness"] == "fresh" else f"{w['state']} ({w['freshness']})"
            rows.append(
                (
                    p["provider"],
                    name,
                    _used_text(w),
                    _local(w["resets_at"], tz),
                    f"{_age(w['age_seconds'])} ago" if w["age_seconds"] is not None else "-",
                    state,
                )
            )
        for warning in p["warnings"]:
            notes.append(f"{p['provider']}: {warning}")
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip() for r in rows]
    if notes:
        lines.append("")
        lines.extend(notes)
    return "\n".join(lines) + "\n"


def write_json_atomic(report: dict, path: Path) -> None:
    """Write the document so a reader never sees a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


__all__ = [
    "SCHEMA",
    "SCHEMA_VERSION",
    "build_report",
    "make_window",
    "read_claude",
    "read_codex",
    "read_openrouter",
    "render_table",
    "window_label",
    "write_json_atomic",
    "iso_utc",
]
