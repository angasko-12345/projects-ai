"""Task data model used by the persistent workflow engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class Workflow:
    """Persistent workflow header (Phase 1 Storage DTO).

    Returned by StateStore instead of raw sqlite3.Row so presentation layers
    never touch database rows directly.
    """

    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass
class Task:
    description: str
    role: str
    workflow_id: str
    id: str = field(default_factory=lambda: str(uuid4()))
    assigned_agent: str | None = None
    dependencies: tuple[str, ...] = ()
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    max_attempts: int = 2
    result: str | None = None
    verified: bool = False
    verification_run_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str = field(default_factory=utc_now)
