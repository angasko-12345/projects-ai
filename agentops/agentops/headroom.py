"""Headroom gate: route away from agents whose usage limit is used up.

Reads the versioned headroom document that ai-token-tracker writes
(``python -m token_tracker headroom --json``, schema
``ai-token-tracker/headroom`` version 1) and turns it into per-agent routing
verdicts: ``reject`` (do not route there), ``deprioritize`` (route there only
when nothing better is eligible), or nothing at all.

This module is a leaf.  It parses text it is handed and evaluates it against
an injected clock; reading the file or running the command lives in
:mod:`agentops.headroom_source`.  The tracker package is never imported: the
JSON document is the whole contract.

Fail-open rule: a missing, unreadable, malformed, stale, or unknown-schema
document produces no verdicts at all, so routing behaves exactly as it does
without the gate.  The snapshot's ``status``/``detail`` say why, for the log.
A document that parses but has no trustworthy reading for one agent is a
different case: that agent gets the configured ``unknown_policy``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

SCHEMA = "ai-token-tracker/headroom"
SCHEMA_VERSION = 1

# Verdict actions.
ALLOW = "allow"
DEPRIORITIZE = "deprioritize"
REJECT = "reject"
UNKNOWN_POLICIES = (ALLOW, DEPRIORITIZE, REJECT)

# Snapshot statuses.  Only OK yields verdicts.
SNAPSHOT_OK = "ok"
SNAPSHOT_MISSING = "missing"
SNAPSHOT_UNREADABLE = "unreadable"
SNAPSHOT_INVALID = "invalid"
SNAPSHOT_SCHEMA_MISMATCH = "schema_mismatch"
SNAPSHOT_STALE = "stale"

_ACTION_RANK = {ALLOW: 0, DEPRIORITIZE: 1, REJECT: 2}


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def parse_utc(value: object) -> float | None:
    """Epoch seconds from an ISO-8601 string (``Z`` or offset); None otherwise."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def format_utc(epoch: float | None) -> str:
    """Deterministic display form used in rejection reasons."""
    if epoch is None:
        return "unknown"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@dataclass(frozen=True)
class HeadroomBinding:
    """Which tracker provider (and optionally which windows) an agent draws on.

    Empty ``windows``/``limit_ids`` mean every window the provider reports.
    """

    provider: str
    windows: tuple[str, ...] = ()
    limit_ids: tuple[str, ...] = ()

    def selects(self, window: Mapping[str, object]) -> bool:
        if self.windows and str(window.get("window")) not in self.windows:
            return False
        if self.limit_ids and str(window.get("limit_id")) not in self.limit_ids:
            return False
        return True


@dataclass(frozen=True)
class HeadroomPolicy:
    reject_at_percent: float = 95.0
    deprioritize_at_percent: float | None = 80.0
    unknown_policy: str = DEPRIORITIZE
    max_age_seconds: float = 900.0


@dataclass(frozen=True)
class HeadroomSnapshot:
    status: str
    detail: str
    document: dict[str, object] | None = None
    generated_at: float | None = None

    @property
    def usable(self) -> bool:
        return self.status == SNAPSHOT_OK and self.document is not None


@dataclass(frozen=True)
class GateVerdict:
    action: str
    reasons: tuple[str, ...] = ()

    @property
    def rejects(self) -> bool:
        return self.action == REJECT

    @property
    def deprioritizes(self) -> bool:
        return self.action == DEPRIORITIZE


def merge_verdicts(*sources: Mapping[str, GateVerdict] | None) -> dict[str, GateVerdict]:
    """Combine verdict maps: the strongest action wins; reasons accumulate."""
    merged: dict[str, GateVerdict] = {}
    for source in sources:
        for agent, verdict in (source or {}).items():
            held = merged.get(agent)
            if held is None:
                merged[agent] = verdict
                continue
            action = verdict.action if _ACTION_RANK[verdict.action] > _ACTION_RANK[held.action] else held.action
            merged[agent] = GateVerdict(action, tuple(dict.fromkeys((*held.reasons, *verdict.reasons))))
    return merged


def parse_snapshot(text: str | bytes | None, now: float, max_age_seconds: float) -> HeadroomSnapshot:
    """Validate one headroom document.  Anything but a fresh v1 doc is unusable."""
    if text is None:
        return HeadroomSnapshot(SNAPSHOT_MISSING, "no headroom document")
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    try:
        document = json.loads(text)
    except ValueError as error:
        return HeadroomSnapshot(SNAPSHOT_INVALID, f"headroom document is not JSON ({error.msg})")
    if not isinstance(document, dict):
        return HeadroomSnapshot(SNAPSHOT_INVALID, "headroom document root is not an object")
    schema = document.get("schema")
    version = document.get("schema_version")
    if schema != SCHEMA or isinstance(version, bool) or version != SCHEMA_VERSION:
        return HeadroomSnapshot(
            SNAPSHOT_SCHEMA_MISMATCH,
            f"unsupported headroom schema {schema!r} version {version!r} "
            f"(this AgentOps reads {SCHEMA!r} version {SCHEMA_VERSION})",
        )
    providers = document.get("providers")
    if not isinstance(providers, list):
        return HeadroomSnapshot(SNAPSHOT_INVALID, "headroom document has no providers list")
    generated = parse_utc(document.get("generated_at"))
    if generated is None:
        return HeadroomSnapshot(SNAPSHOT_INVALID, "headroom document has no valid generated_at")
    age = now - generated
    if age > max_age_seconds:
        return HeadroomSnapshot(
            SNAPSHOT_STALE,
            f"headroom document generated {format_utc(generated)} is {int(age)}s old "
            f"(max {int(max_age_seconds)}s)",
            None, generated,
        )
    return HeadroomSnapshot(SNAPSHOT_OK, f"headroom document generated {format_utc(generated)}",
                            document, generated)


def _provider_entry(document: Mapping[str, object], provider: str) -> Mapping[str, object] | None:
    for entry in document.get("providers") or ():
        if isinstance(entry, Mapping) and entry.get("provider") == provider:
            return entry
    return None


def _usage_text(window: Mapping[str, object]) -> str:
    pct = _num(window.get("used_percent"))
    if pct is not None:
        return f"{pct:g}% used"
    used, limit = _num(window.get("used")), _num(window.get("limit"))
    if used is not None and limit is not None:
        return f"{used:g}/{limit:g} {window.get('unit') or ''}".rstrip() + " used"
    return "usage not reported"


def _window_name(provider: str, window: Mapping[str, object]) -> str:
    name = str(window.get("window") or "window")
    limit_id = window.get("limit_id")
    if provider == "codex" and isinstance(limit_id, str) and limit_id not in ("", "codex"):
        name = f"{name} ({limit_id})"
    return f"{provider} {name}"


def evaluate_agent(
    agent: str,
    binding: HeadroomBinding,
    document: Mapping[str, object],
    policy: HeadroomPolicy,
) -> GateVerdict:
    """One agent's verdict from a usable document."""
    entry = _provider_entry(document, binding.provider)
    if entry is None:
        return _unknown(policy, f"headroom: provider {binding.provider} not in the document")
    status = entry.get("status")
    windows = [w for w in (entry.get("windows") or ()) if isinstance(w, Mapping) and binding.selects(w)]
    if status != "ok" or not windows:
        detail = entry.get("detail")
        why = f"status {status}" + (f": {detail}" if isinstance(detail, str) and detail else "")
        if status == "ok":
            why = "no matching window"
        return _unknown(policy, f"headroom: {binding.provider} has no reading ({why})")

    rejects: list[str] = []
    lows: list[str] = []
    unknowns: list[str] = []
    trusted = False
    for window in windows:
        name = _window_name(binding.provider, window)
        state = window.get("state")
        freshness = window.get("freshness")
        pct = _num(window.get("used_percent"))
        resets = parse_utc(window.get("resets_at"))
        reset_text = f", resets {format_utc(resets)}" if resets is not None else ", reset time not reported"
        # usage inside a window only rises until the reset, so a stale high
        # reading is still a valid lower bound; an expired one is not.
        high = state == "exhausted" or bool(window.get("reached")) or (
            pct is not None and pct >= policy.reject_at_percent
        )
        if high and state != "unknown" and freshness != "expired":
            label = "exhausted" if state == "exhausted" or window.get("reached") else state
            rejects.append(f"headroom: {name} {_usage_text(window)} ({label}){reset_text}")
            continue
        if state == "unknown" or freshness in ("expired", "unknown") or freshness == "stale":
            unknowns.append(f"headroom: {name} reading is {freshness or 'unknown'} ({_usage_text(window)})")
            continue
        trusted = True
        if policy.deprioritize_at_percent is not None and pct is not None and pct >= policy.deprioritize_at_percent:
            lows.append(f"headroom: {name} {_usage_text(window)} ({state}){reset_text}")
    if rejects:
        return GateVerdict(REJECT, tuple(rejects))
    if unknowns and not trusted:
        return _unknown(policy, *unknowns)
    if lows:
        return GateVerdict(DEPRIORITIZE, tuple(lows))
    if unknowns:
        # some windows trusted and fine, others unknown: still no full signal
        return _unknown(policy, *unknowns)
    return GateVerdict(ALLOW, ())


def _unknown(policy: HeadroomPolicy, *reasons: str) -> GateVerdict:
    action = policy.unknown_policy if policy.unknown_policy in UNKNOWN_POLICIES else DEPRIORITIZE
    suffix = {ALLOW: "allowed by unknown_policy", DEPRIORITIZE: "deprioritized by unknown_policy",
              REJECT: "rejected by unknown_policy"}[action]
    return GateVerdict(action, tuple(f"{reason}; {suffix}" for reason in reasons))


def evaluate(
    snapshot: HeadroomSnapshot,
    bindings: Mapping[str, HeadroomBinding],
    policy: HeadroomPolicy,
    agents: Iterable[str] | None = None,
) -> dict[str, GateVerdict]:
    """Verdicts for every bound agent; empty when the snapshot is unusable."""
    if not snapshot.usable or snapshot.document is None:
        return {}
    names = set(agents) if agents is not None else None
    verdicts: dict[str, GateVerdict] = {}
    for agent, binding in bindings.items():
        if names is not None and agent not in names:
            continue
        verdict = evaluate_agent(agent, binding, snapshot.document, policy)
        if verdict.action != ALLOW:
            verdicts[agent] = verdict
    return verdicts


Reader = Callable[[float], HeadroomSnapshot]


@dataclass
class HeadroomGate:
    """Routing gate over an injected snapshot reader.

    ``reader(now)`` returns a :class:`HeadroomSnapshot`; it owns all I/O.  The
    gate never raises into routing: a reader exception is an unusable snapshot.
    """

    reader: Reader
    bindings: Mapping[str, HeadroomBinding]
    policy: HeadroomPolicy = field(default_factory=HeadroomPolicy)

    def check(self, now: float, agents: Iterable[str] | None = None) -> tuple[dict[str, GateVerdict], HeadroomSnapshot]:
        try:
            snapshot = self.reader(now)
        except Exception as error:  # noqa: BLE001 - the gate must fail open
            snapshot = HeadroomSnapshot(SNAPSHOT_UNREADABLE, f"headroom reader failed: {type(error).__name__}")
        try:
            verdicts = evaluate(snapshot, self.bindings, self.policy, agents)
        except Exception as error:  # noqa: BLE001
            snapshot = HeadroomSnapshot(SNAPSHOT_INVALID, f"headroom evaluation failed: {type(error).__name__}")
            verdicts = {}
        return verdicts, snapshot

    @staticmethod
    def note(snapshot: HeadroomSnapshot) -> str:
        """One constraint line for the routing decision."""
        if snapshot.usable:
            return f"headroom: {snapshot.detail}"
        return f"headroom: unavailable ({snapshot.status}: {snapshot.detail}); routing unchanged"


__all__ = [
    "ALLOW", "DEPRIORITIZE", "REJECT", "SCHEMA", "SCHEMA_VERSION",
    "GateVerdict", "HeadroomBinding", "HeadroomGate", "HeadroomPolicy", "HeadroomSnapshot",
    "evaluate", "evaluate_agent", "format_utc", "merge_verdicts", "parse_snapshot", "parse_utc",
]
