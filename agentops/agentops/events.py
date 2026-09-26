"""Versioned durable execution-timeline events for AgentOps.

This leaf module (no I/O, no SQLite imports) defines the event schema, a
tolerant versioned codec, and a lightweight in-process subscription
abstraction.  SQLite persistence lives in :mod:`state`, which notifies
subscribers on write.  The Tk GUI reads the timeline today through
controller queries; the bus subscriber protocol is shared so a future REST
API (or live GUI updates) can consume the same stream.

Threading contract: subscribers run synchronously on the publisher thread.
Tk consumers must marshal into the UI thread (``root.after``) and must
never block; a slow subscriber stalls the publisher, never the store
(publishers notify outside the store lock).
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from .tasks import utc_now

EVENT_SCHEMA_VERSION = 1
SUPPORTED_EVENT_VERSIONS = frozenset({1})

_MESSAGE_MAX_LENGTH = 2000


class EventType(StrEnum):
    """Durable timeline event types (schema v1)."""

    WORKFLOW_CREATED = "workflow.created"
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_CANCELLED = "workflow.cancelled"
    TASK_CLAIMED = "task.claimed"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    AGENT_SELECTED = "agent.selected"
    ROUTING_DECISION = "routing.decision"
    AGENT_STARTED = "agent.started"
    AGENT_FINISHED = "agent.finished"
    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_COMPLETED = "verification.completed"
    VERIFICATION_FAILED = "verification.failed"
    FAILURE_CLASSIFIED = "failure.classified"
    REPAIR_STARTED = "repair.started"
    REPAIR_COMPLETED = "repair.completed"
    REVIEW_STARTED = "review.started"
    REVIEW_COMPLETED = "review.completed"
    POLICY_EVALUATED = "policy.evaluated"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_COMPLETED = "approval.completed"
    WORKTREE_CREATED = "worktree.created"
    WORKTREE_CLEANED = "worktree.cleaned"
    MERGE_STARTED = "merge.started"
    MERGE_COMPLETED = "merge.completed"
    MERGE_CONFLICT = "merge.conflict"
    PERSISTENCE_DEGRADED = "persistence.degraded"
    NOTE = "note"


class EventSeverity(StrEnum):
    """Severity of a timeline event."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Event:
    """One versioned entry in the durable execution timeline."""

    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=utc_now)
    workflow_id: str | None = None
    task_id: str | None = None
    agent_run_id: str | None = None
    type: EventType = EventType.NOTE
    severity: EventSeverity = EventSeverity.INFO
    message: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: int = EVENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type.value if isinstance(self.type, EventType) else str(self.type)
        data["severity"] = (
            self.severity.value if isinstance(self.severity, EventSeverity) else str(self.severity)
        )
        return data


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        return "<unrepresentable>"


def _coerce_type(value: object) -> tuple[EventType, tuple[str, ...]]:
    if isinstance(value, EventType):
        return value, ()
    if isinstance(value, str):
        normalized = value.strip()
        for member in EventType:
            if normalized == member.value or normalized == member.name:
                return member, ()
        # Legacy free-text kinds (e.g. "task-claimed") map to NOTE with detail.
        return EventType.NOTE, (f"unrecognized event type {_safe_repr(value)}; stored as note",)
    return EventType.NOTE, ("missing event type; stored as note",)


def _coerce_severity(value: object) -> EventSeverity:
    if isinstance(value, EventSeverity):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        for member in EventSeverity:
            if normalized == member.value:
                return member
    return EventSeverity.INFO


def _coerce_payload(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        try:
            return dict(value)
        except Exception:
            return {}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {"detail": value[:_MESSAGE_MAX_LENGTH]}
        return _coerce_payload(decoded)
    return {}


def event_from_dict(data: dict[str, Any]) -> tuple[Event, tuple[str, ...]]:
    """Build an Event from a raw mapping. Never raises for hostile payloads.

    Version tolerance is reader-side: unknown ``schema_version`` values are
    coerced into the current shape with a warning (the original version is
    not preserved — only one Event shape exists per schema generation).
    """
    try:
        payload: Any = dict(data)
    except Exception:
        return Event(message="unparseable event payload"), ("payload was not a mapping",)
    warnings: list[str] = []

    def get(key: str) -> Any:
        try:
            return payload.get(key)
        except Exception:
            return None

    version = get("schema_version")
    try:
        version_int = EVENT_SCHEMA_VERSION if version is None else int(version)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        warnings.append(f"invalid schema_version {_safe_repr(version)}; defaulting")
        version_int = EVENT_SCHEMA_VERSION
    if version_int not in SUPPORTED_EVENT_VERSIONS:
        warnings.append(f"unsupported schema_version {version_int}; coerced as v{EVENT_SCHEMA_VERSION}")

    try:
        event_type, type_warnings = _coerce_type(get("type"))
    except Exception:
        event_type, type_warnings = EventType.NOTE, ("unreadable event type; stored as note",)
    warnings.extend(type_warnings)
    raw_type = get("type")
    try:
        raw_message = get("message")
        message_str = None if raw_message is None else str(raw_message)[:_MESSAGE_MAX_LENGTH]
    except Exception:
        message_str = None
        warnings.append("unreadable message; dropped")
    try:
        coerced_payload = _coerce_payload(get("payload"))
    except Exception:
        coerced_payload = {}
        warnings.append("unreadable payload; dropped")
    if raw_type is not None and event_type is EventType.NOTE and type_warnings:
        legacy = raw_type[:256] if isinstance(raw_type, str) else _safe_repr(raw_type)[:256]
        coerced_payload = {"legacy_type": legacy, **coerced_payload}
    try:
        event = Event(
            id=str(get("id") or str(uuid4())),
            timestamp=str(get("timestamp") or utc_now()),
            workflow_id=None if get("workflow_id") is None else str(get("workflow_id")),
            task_id=None if get("task_id") is None else str(get("task_id")),
            agent_run_id=None if get("agent_run_id") is None else str(get("agent_run_id")),
            type=event_type,
            severity=_coerce_severity(get("severity")),
            message=message_str,
            payload=coerced_payload,
            schema_version=EVENT_SCHEMA_VERSION,
        )
    except Exception:
        return Event(message="event coercion fallback"), tuple(warnings + ["event coercion fallback"])
    return event, tuple(warnings)


def coerce_event(value: object) -> Event | None:
    """Backward-compatible loader: dicts, JSON text, or Event. Never raises."""
    try:
        if value is None:
            return None
        if isinstance(value, Event):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                decoded: object = json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
            return coerce_event(decoded)
        if isinstance(value, dict):
            event, _ = event_from_dict(value)
            return event
        return None
    except Exception:
        return None


class EventSubscriber:
    """Sink for timeline events (GUI, API, tests).

    Called synchronously on the publisher thread.  Implementations must be
    fast, non-blocking, and thread-safe; Tk implementations must marshal
    widget updates via ``root.after`` instead of touching widgets here.
    Reentrant ``emit`` calls from inside a subscriber are deferred and
    drained by the outermost ``emit`` (no unbounded recursion).
    """

    def on_event(self, event: Event) -> None: ...


SubscriberFn = Callable[[Event], None]


class EventBus:
    """Thread-safe in-process fan-out. Delivery never breaks publishers."""

    def __init__(self, error_handler: Callable[[BaseException], None] | None = None) -> None:
        self._lock = threading.RLock()
        self._local = threading.local()
        self._error_handler = error_handler
        self._subscribers: dict[str, SubscriberFn] = {}
        self._type_subscribers: dict[str, dict[str, SubscriberFn]] = defaultdict(dict)

    def subscribe(self, subscriber: SubscriberFn, event_types: Iterable[EventType | str] | None = None) -> str:
        key = str(uuid4())
        with self._lock:
            if event_types is None:
                self._subscribers[key] = subscriber
            else:
                for event_type in event_types:
                    name = event_type.value if isinstance(event_type, EventType) else str(event_type)
                    self._type_subscribers[name][key] = subscriber
        return key

    def unsubscribe(self, key: str) -> bool:
        with self._lock:
            removed = self._subscribers.pop(key, None) is not None
            for subscribers in self._type_subscribers.values():
                if subscribers.pop(key, None) is not None:
                    removed = True
            return removed

    def _report(self, error: BaseException) -> None:
        handler = self._error_handler
        if handler is None:
            return
        try:
            handler(error)
        except Exception:
            pass

    def emit(self, event: Event) -> int:
        """Deliver to matching subscribers; returns delivery count. Never raises.

        Nested ``emit`` calls made from inside a subscriber are queued and
        drained iteratively by the outermost call, so subscriber reentrancy
        cannot recurse without bound.
        """
        try:
            with self._lock:
                targets = list(self._subscribers.values())
                type_name = event.type.value if isinstance(event.type, EventType) else str(event.type)
                targets.extend(self._type_subscribers.get(type_name, {}).values())
        except Exception:
            return 0
        if getattr(self._local, "depth", 0) > 0:
            self._local.queue.append((targets, event))
            return 0
        self._local.depth = 1
        self._local.queue = []
        delivered = 0
        try:
            delivered += self._send(targets, event)
            while self._local.queue:
                pending_targets, pending_event = self._local.queue.pop(0)
                delivered += self._send(pending_targets, pending_event)
        finally:
            self._local.depth = 0
            self._local.queue = []
        return delivered

    def _send(self, targets: list[SubscriberFn], event: Event) -> int:
        delivered = 0
        for target in targets:
            try:
                target(event)
                delivered += 1
            except Exception as error:
                self._report(error)
                continue
        return delivered


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "SUPPORTED_EVENT_VERSIONS",
    "Event",
    "EventSeverity",
    "EventSubscriber",
    "EventBus",
    "EventType",
    "coerce_event",
    "event_from_dict",
]
