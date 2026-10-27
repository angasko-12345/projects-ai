"""SQLite persistence for workflows, tasks, execution events, and agent runs."""

# ruff: noqa: F401

# Package layout (pure move from the former single-module state.py):
#   _json.py        JSON helpers (_jsonable, _json, _loads)
#   migrations.py   SchemaMixin: schema creation + additive migrations
#   workflows.py    WorkflowTaskMixin: workflows and tasks
#   events.py       EventMixin: legacy + typed events
#   artifacts.py    ArtifactMixin
#   agent_runs.py   AgentRunMixin (+ cooldowns) and StateAgentRunObserver
#   verification.py VerificationMixin: runs, checks, reports
#   failures.py     FailureMixin
#   worktrees.py    WorktreeRefMixin
# StateStore keeps the connection lifecycle and cross-domain recovery.

# The imports below are deliberately broader than this module needs: they
# keep every name that `from agentops.state import X` resolved before the
# split importable (and `agentops.state.tempfile` patchable).
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..agent_run import (
    AgentRun,
    AgentRunContext,
    AgentRunOutcome,
    AgentRunRelationship,
    AgentRunStatus,
    TERMINAL_STATUSES,
)
from ..artifacts import Artifact, ArtifactKind, ArtifactMetadata
from ..events import Event, EventBus, EventSeverity, EventType, coerce_event
from ..failure import (
    Failure,
    FailureCategory,
    FailureSeverity,
    FailureSource,
    RecoveryState,
    RepairAction,
)
from ..execution_model import (
    StateTransitionError,
    assert_agent_run_transition,
    assert_no_fabricated_success,
)
from ..git import WorktreeRef
from ..tasks import Task, TaskStatus, Workflow, utc_now
from ..verification_model import (
    VerificationCheck,
    VerificationCheckClass,
    VerificationCheckStatus,
    VerificationExecutionPolicy,
    VerificationProfile,
    VerificationProfileMode,
    VerificationReport,
    VerificationReportStatus,
    VerificationRun,
    VerificationRunStatus,
    profile_to_dict,
)

from ._json import _json, _jsonable, _jsonable_key, _loads, _safe_json_repr
from .agent_runs import AgentRunMixin, StateAgentRunObserver
from .artifacts import ArtifactMixin
from .events import EventMixin
from .failures import FailureMixin
from .migrations import SchemaMixin
from .verification import VerificationMixin
from .workflows import WorkflowTaskMixin
from .worktrees import WorktreeRefMixin


class StateStore(
    SchemaMixin,
    WorkflowTaskMixin,
    EventMixin,
    ArtifactMixin,
    AgentRunMixin,
    VerificationMixin,
    FailureMixin,
    WorktreeRefMixin,
):
    def __init__(self, database_path: str | Path, event_bus: EventBus | None = None):
        self.database_path = str(database_path)
        self._event_bus = event_bus
        if self.database_path != ":memory:":
            parent = Path(self.database_path).parent
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise OSError(
                    f"AgentOps cannot use state directory {parent}: {error.strerror or error}"
                ) from error
            # SQLite opens with WAL, which needs directory write access
            # even for reads. Probe first so an unusable location fails
            # here with the path in the message instead of surfacing as
            # a raw "unable to open database file" on first load.
            try:
                with tempfile.NamedTemporaryFile(
                    dir=str(parent), prefix=".agentops-write-probe-", delete=True
                ):
                    pass
            except OSError as error:
                raise OSError(
                    f"AgentOps cannot write to state directory {parent}: {error.strerror or error}"
                ) from error
        # RLock (not Lock): public methods nest (e.g. ready_tasks -> update_task).
        # check_same_thread=False + the lock make one store safe to share
        # across threads; across processes the conditional UPDATE in
        # claim_task is the atomicity guarantee (serialized by SQLite).
        self._lock = threading.RLock()
        self.connection = self._open_with_retry()

    def _open_with_retry(self) -> sqlite3.Connection:
        # Concurrent first-open of one database file can hit SQLITE_LOCKED
        # (journal-mode change, DDL snapshot conflicts), which busy_timeout
        # does not cover. Everything below is idempotent (IF NOT EXISTS +
        # OR IGNORE), so retry the whole open; failed attempts are closed
        # to avoid leaking file handles.
        attempts = 8
        delay = 0.05
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(attempts):
            connection = sqlite3.connect(self.database_path, timeout=30, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            self.connection = connection
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 30000")
                if self.database_path != ":memory:":
                    connection.execute("PRAGMA journal_mode = WAL")
                self._initialize()
                return connection
            except sqlite3.OperationalError as error:
                last_error = error
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
                if attempt == attempts - 1:
                    break
                time.sleep(delay)
                delay *= 2
        raise last_error  # type: ignore[misc]

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def count_interrupted_work(self) -> dict[str, int]:
        """Read-only mirror of the ``recover_all()`` predicates.

        Counts exactly the rows each recovery pass would reap (agent runs
        PENDING/STARTING/RUNNING, verification runs PENDING/RUNNING, tasks
        RUNNING) without mutating anything, so surfaces can advertise
        interrupted work before an operator confirms recovery. Keep the
        predicates identical to the ``recover_*`` queries above;
        ``tests/test_state.py`` guards the parity.
        """
        with self._lock:
            runs = self.connection.execute(
                "SELECT COUNT(*) AS total FROM agent_runs WHERE status IN (?, ?, ?)",
                (AgentRunStatus.PENDING, AgentRunStatus.STARTING, AgentRunStatus.RUNNING),
            ).fetchone()
            verifications = self.connection.execute(
                "SELECT COUNT(*) AS total FROM verification_runs WHERE status IN (?, ?)",
                (VerificationRunStatus.PENDING, VerificationRunStatus.RUNNING),
            ).fetchone()
            tasks = self.connection.execute(
                "SELECT COUNT(*) AS total FROM tasks WHERE status = ?",
                (TaskStatus.RUNNING,),
            ).fetchone()
        return {
            "agent_runs": int(runs["total"]),
            "verification_runs": int(verifications["total"]),
            "tasks": int(tasks["total"]),
        }

    def recover_all(self) -> dict[str, int]:
        """Run every crash-recovery pass and return counts.

        Covers agent execution, verification, and task-level interruption
        (which subsumes review-role tasks). Worktree creation, Git
        operation, and merge interruptions surface as task-level recoveries
        with preserved worktrees; callers record Failure rows with the
        matching InterruptionContext for precise RecoveryState accounting.
        """

        agent_runs = self.recover_agent_runs()
        verifications = self.recover_verification_runs()
        tasks = self.recover_tasks()
        return {
            "agent_runs": len(agent_runs),
            "verification_runs": len(verifications),
            "tasks": len(tasks),
        }


__all__ = ["StateAgentRunObserver", "StateStore"]
