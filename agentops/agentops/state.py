"""SQLite persistence for workflows, tasks, execution events, and agent runs."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

from .agent_run import (
    AgentRun,
    AgentRunContext,
    AgentRunOutcome,
    AgentRunRelationship,
    AgentRunStatus,
    TERMINAL_STATUSES,
)
from .artifacts import Artifact, ArtifactKind, ArtifactMetadata
from .events import Event, EventBus, EventSeverity, EventType, coerce_event
from .failure import (
    Failure,
    FailureCategory,
    FailureSeverity,
    FailureSource,
    InterruptionContext,
    RecoveryState,
    RepairAction,
)
from .execution_model import StateTransitionError, assert_agent_run_transition
from .git import WorktreeRef
from .tasks import Task, TaskStatus, Workflow, utc_now
from .verification_model import (
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


def _jsonable(value: object) -> object:
    """Deep-coerce a value into JSON-serializable data (never raises)."""
    try:
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            return value if value == value and value not in (float("inf"), float("-inf")) else str(value)
        if isinstance(value, dict):
            return {str(_jsonable_key(key)): _jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        return _safe_json_repr(value)
    except Exception:
        return _safe_json_repr(value)


def _jsonable_key(key: object) -> str:
    if isinstance(key, str):
        return key
    try:
        return str(key)
    except Exception:
        return "<unrepresentable>"


def _safe_json_repr(value: object) -> str:
    try:
        return repr(value)
    except Exception:
        try:
            return f"<unrepresentable {type(value).__name__}>"
        except Exception:
            return "<unrepresentable>"


def _json(value: object | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _loads(value: str | None, default: object) -> object:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class StateStore:
    def __init__(self, database_path: str | Path, event_bus: EventBus | None = None):
        self.database_path = str(database_path)
        self._event_bus = event_bus
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
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

    def _initialize(self) -> None:
        self._initialize_once()

    def _initialize_once(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY, description TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
                description TEXT NOT NULL, role TEXT NOT NULL, assigned_agent TEXT,
                dependencies TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL, result TEXT, verified INTEGER NOT NULL DEFAULT 0,
                verification_run_id TEXT, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, task_id TEXT,
                kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )
        self._migrate_agent_runs()
        self._ensure_task_verification_columns()
        self._migrate_verification()
        self._migrate_failures()
        self._migrate_typed_events()
        self._migrate_artifacts()
        self._migrate_worktree_refs()
        self.connection.commit()

    def _migrate_agent_runs(self) -> None:
        """Apply additive AgentRun migrations without rewriting legacy tables."""

        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT REFERENCES workflows(id),
                task_id TEXT,
                attempt INTEGER NOT NULL DEFAULT 1,
                agent TEXT,
                executable TEXT,
                role TEXT,
                model TEXT,
                started_at TEXT,
                ended_at TEXT,
                duration_seconds REAL,
                status TEXT NOT NULL,
                exit_code INTEGER,
                cancelled INTEGER NOT NULL DEFAULT 0,
                timed_out INTEGER NOT NULL DEFAULT 0,
                terminated INTEGER NOT NULL DEFAULT 0,
                command TEXT,
                command_metadata TEXT,
                working_directory TEXT,
                worktree TEXT,
                prompt_metadata TEXT,
                stdout_path TEXT,
                stderr_path TEXT,
                log_path TEXT,
                files_changed TEXT,
                diff_stat TEXT,
                error TEXT,
                failure_classification TEXT,
                relationship TEXT NOT NULL DEFAULT 'root',
                parent_run_id TEXT REFERENCES agent_runs(id),
                retry_of TEXT REFERENCES agent_runs(id),
                repair_of TEXT REFERENCES agent_runs(id),
                structured_result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_runs_workflow
                ON agent_runs(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_task
                ON agent_runs(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_status
                ON agent_runs(status);
            """
        )

        # This branch also repairs databases created by an interrupted early
        # version of the migration.  All operations are additive.
        existing = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(agent_runs)").fetchall()
        }
        columns = {
            "id": "TEXT PRIMARY KEY",
            "workflow_id": "TEXT REFERENCES workflows(id)",
            "task_id": "TEXT",
            "attempt": "INTEGER NOT NULL DEFAULT 1",
            "agent": "TEXT",
            "executable": "TEXT",
            "role": "TEXT",
            "model": "TEXT",
            "started_at": "TEXT",
            "ended_at": "TEXT",
            "duration_seconds": "REAL",
            "status": "TEXT NOT NULL",
            "exit_code": "INTEGER",
            "cancelled": "INTEGER NOT NULL DEFAULT 0",
            "timed_out": "INTEGER NOT NULL DEFAULT 0",
            "terminated": "INTEGER NOT NULL DEFAULT 0",
            "command": "TEXT",
            "command_metadata": "TEXT",
            "working_directory": "TEXT",
            "worktree": "TEXT",
            "prompt_metadata": "TEXT",
            "stdout_path": "TEXT",
            "stderr_path": "TEXT",
            "log_path": "TEXT",
            "files_changed": "TEXT",
            "diff_stat": "TEXT",
            "error": "TEXT",
            "failure_classification": "TEXT",
            "relationship": "TEXT NOT NULL DEFAULT 'root'",
            "parent_run_id": "TEXT REFERENCES agent_runs(id)",
            "retry_of": "TEXT REFERENCES agent_runs(id)",
            "repair_of": "TEXT REFERENCES agent_runs(id)",
            "structured_result": "TEXT",
            "created_at": "TEXT NOT NULL",
            "updated_at": "TEXT NOT NULL",
        }
        for name, definition in columns.items():
            if name not in existing:
                self.connection.execute(f"ALTER TABLE agent_runs ADD COLUMN {name} {definition}")

        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 1"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (1, utc_now()),
            )

    def _migrate_verification(self) -> None:
        """Apply additive Verification Kernel migrations without rewriting legacy tables."""

        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS verification_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT REFERENCES workflows(id),
                task_id TEXT,
                profile_name TEXT NOT NULL,
                profile_snapshot TEXT,
                mode TEXT NOT NULL,
                concurrency INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                source_agent_run_id TEXT REFERENCES agent_runs(id),
                started_at TEXT,
                ended_at TEXT,
                duration_seconds REAL,
                total_checks INTEGER NOT NULL DEFAULT 0,
                passed_checks INTEGER NOT NULL DEFAULT 0,
                failed_checks INTEGER NOT NULL DEFAULT 0,
                skipped_checks INTEGER NOT NULL DEFAULT 0,
                required_failures INTEGER NOT NULL DEFAULT 0,
                overall_status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS verification_checks (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES verification_runs(id),
                workflow_id TEXT REFERENCES workflows(id),
                task_id TEXT,
                profile_name TEXT NOT NULL,
                name TEXT NOT NULL,
                check_class TEXT NOT NULL,
                command TEXT,
                working_directory TEXT,
                timeout_seconds INTEGER,
                required INTEGER NOT NULL DEFAULT 1,
                policy TEXT NOT NULL DEFAULT 'sequential',
                status TEXT NOT NULL,
                exit_code INTEGER,
                started_at TEXT,
                ended_at TEXT,
                duration_seconds REAL,
                stdout_path TEXT,
                stderr_path TEXT,
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, name)
            );
            CREATE TABLE IF NOT EXISTS verification_reports (
                id TEXT PRIMARY KEY,
                run_id TEXT UNIQUE NOT NULL REFERENCES verification_runs(id),
                workflow_id TEXT REFERENCES workflows(id),
                task_id TEXT,
                profile_name TEXT NOT NULL,
                total_checks INTEGER NOT NULL DEFAULT 0,
                passed_checks INTEGER NOT NULL DEFAULT 0,
                failed_checks INTEGER NOT NULL DEFAULT 0,
                skipped_checks INTEGER NOT NULL DEFAULT 0,
                required_failures INTEGER NOT NULL DEFAULT 0,
                duration_seconds REAL,
                overall_status TEXT NOT NULL,
                generated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_verification_runs_workflow
                ON verification_runs(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_verification_checks_run
                ON verification_checks(run_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_verification_reports_workflow
                ON verification_reports(workflow_id, generated_at);
            """
        )
        # Repair loop mirroring the agent_runs migration: databases created
        # by an interrupted early version of this migration gain any missing
        # columns additively instead of failing.
        expected_columns: dict[str, dict[str, str]] = {
            "verification_runs": {
                "profile_snapshot": "TEXT", "mode": "TEXT NOT NULL",
                "concurrency": "INTEGER NOT NULL DEFAULT 1",
                "source_agent_run_id": "TEXT REFERENCES agent_runs(id)",
                "started_at": "TEXT", "ended_at": "TEXT", "duration_seconds": "REAL",
                "total_checks": "INTEGER NOT NULL DEFAULT 0",
                "passed_checks": "INTEGER NOT NULL DEFAULT 0",
                "failed_checks": "INTEGER NOT NULL DEFAULT 0",
                "skipped_checks": "INTEGER NOT NULL DEFAULT 0",
                "required_failures": "INTEGER NOT NULL DEFAULT 0",
                "overall_status": "TEXT",
            },
            "verification_checks": {
                "working_directory": "TEXT", "timeout_seconds": "INTEGER",
                "required": "INTEGER NOT NULL DEFAULT 1",
                "policy": "TEXT NOT NULL DEFAULT 'sequential'",
                "started_at": "TEXT", "ended_at": "TEXT", "duration_seconds": "REAL",
                "stdout_path": "TEXT", "stderr_path": "TEXT", "failure_reason": "TEXT",
            },
        }
        for table, columns in expected_columns.items():
            existing = {
                row["name"]
                for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 2"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (2, utc_now()),
            )

    def _ensure_task_verification_columns(self) -> None:
        existing = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "verified" not in existing:
            self.connection.execute("ALTER TABLE tasks ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
        if "verification_run_id" not in existing:
            self.connection.execute("ALTER TABLE tasks ADD COLUMN verification_run_id TEXT")

    def _workflow_from_row(self, row: sqlite3.Row) -> Workflow:
        try:
            status = TaskStatus(row["status"])
        except (ValueError, KeyError, TypeError):
            status = TaskStatus.PENDING
        return Workflow(
            id=row["id"],
            description=row["description"],
            status=status,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _worktree_ref_from_row(self, row: sqlite3.Row) -> WorktreeRef:
        return WorktreeRef(
            id=row["id"],
            workflow_id=row["workflow_id"],
            path=row["path"],
            branch=row["branch"],
            base_branch=row["base_branch"],
            base_commit=row["base_commit"],
            created_at=row["created_at"],
        )

    def _task_from_row(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"], workflow_id=row["workflow_id"], description=row["description"], role=row["role"],
            assigned_agent=row["assigned_agent"], dependencies=tuple(json.loads(row["dependencies"])),
            status=TaskStatus(row["status"]), attempts=row["attempts"], max_attempts=row["max_attempts"],
            result=row["result"], verified=bool(row["verified"]),
            verification_run_id=row["verification_run_id"],
            created_at=row["created_at"], started_at=row["started_at"],
            finished_at=row["finished_at"], updated_at=row["updated_at"],
        )

    def _agent_run_from_row(self, row: sqlite3.Row) -> AgentRun:
        command = _loads(row["command"], ())
        files = _loads(row["files_changed"], ())
        command_metadata = _loads(row["command_metadata"], {})
        prompt_metadata = _loads(row["prompt_metadata"], {})
        structured_result = _loads(row["structured_result"], None)
        return AgentRun(
            id=row["id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            attempt=row["attempt"],
            agent=row["agent"],
            executable=row["executable"],
            role=row["role"],
            model=row["model"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            duration_seconds=row["duration_seconds"],
            status=AgentRunStatus(row["status"]),
            exit_code=row["exit_code"],
            cancelled=bool(row["cancelled"]),
            timed_out=bool(row["timed_out"]),
            terminated=bool(row["terminated"]),
            command=tuple(command) if isinstance(command, list) else None,
            command_metadata=command_metadata if isinstance(command_metadata, dict) else {},
            working_directory=row["working_directory"],
            worktree=row["worktree"],
            prompt_metadata=prompt_metadata if isinstance(prompt_metadata, dict) else {},
            stdout_path=row["stdout_path"],
            stderr_path=row["stderr_path"],
            log_path=row["log_path"],
            files_changed=tuple(files) if isinstance(files, list) else (),
            diff_stat=row["diff_stat"],
            error=row["error"],
            failure_classification=row["failure_classification"],
            relationship=AgentRunRelationship(row["relationship"] or AgentRunRelationship.ROOT),
            parent_run_id=row["parent_run_id"],
            retry_of=row["retry_of"],
            repair_of=row["repair_of"],
            structured_result=structured_result,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_workflow(self, description: str) -> str:
        workflow_id = str(uuid4())
        now = utc_now()
        with self._lock:
            self.connection.execute(
                "INSERT INTO workflows VALUES (?, ?, ?, ?, ?)",
                (workflow_id, description, TaskStatus.PENDING, now, now),
            )
            self.connection.commit()
        return workflow_id

    def add_task(self, task: Task) -> Task:
        with self._lock:
            # Explicit columns: ALTER TABLE appends migrated columns after
            # updated_at on legacy databases, so positional inserts are unsafe.
            self.connection.execute(
                """INSERT INTO tasks (
                    id, workflow_id, description, role, assigned_agent, dependencies,
                    status, attempts, max_attempts, result, verified, verification_run_id,
                    created_at, started_at, finished_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task.id, task.workflow_id, task.description, task.role, task.assigned_agent,
                 json.dumps(task.dependencies), task.status, task.attempts, task.max_attempts,
                 task.result, int(task.verified), task.verification_run_id,
                 task.created_at, task.started_at, task.finished_at, task.updated_at),
            )
            self._event(task.workflow_id, task.id, "task-created", task.description)
            self.connection.commit()
        return task

    def get_task(self, task_id: str) -> Task:
        with self._lock:
            row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        return self._task_from_row(row)

    def list_tasks(self, workflow_id: str | None = None) -> list[Task]:
        query = "SELECT * FROM tasks" + (" WHERE workflow_id = ?" if workflow_id else "") + " ORDER BY created_at"
        with self._lock:
            rows = self.connection.execute(query, (workflow_id,) if workflow_id else ()).fetchall()
        return [self._task_from_row(row) for row in rows]

    def latest_workflow(self) -> Workflow | None:
        """Newest workflow as a DTO (Phase 1: no Row leakage)."""
        with self._lock:
            row = self.connection.execute("SELECT * FROM workflows ORDER BY rowid DESC LIMIT 1").fetchone()
        return self._workflow_from_row(row) if row is not None else None

    def count_workflows(self, status: str | None = None) -> int:
        query = "SELECT COUNT(*) AS total FROM workflows"
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        with self._lock:
            row = self.connection.execute(query, params).fetchone()
        return int(row["total"])

    def list_workflows(self, limit: int = 50, offset: int = 0, status: str | None = None) -> list[Workflow]:
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("Workflow list limit and offset must be non-negative integers.")
        query = "SELECT * FROM workflows"
        params: tuple[str | int, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY rowid DESC LIMIT ? OFFSET ?"
        with self._lock:
            rows = self.connection.execute(query, (*params, limit, offset)).fetchall()
        return [self._workflow_from_row(row) for row in rows]

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        """Fetch one workflow header as a DTO (Phase 1: no Row leakage)."""
        with self._lock:
            row = self.connection.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        return self._workflow_from_row(row) if row is not None else None

    def update_task(self, task: Task) -> None:
        task.updated_at = utc_now()
        with self._lock:
            self.connection.execute(
                """UPDATE tasks SET assigned_agent=?, status=?, attempts=?, result=?, verified=?,
                   verification_run_id=?, started_at=?, finished_at=?, updated_at=? WHERE id=?""",
                (task.assigned_agent, task.status, task.attempts, task.result, int(task.verified),
                 task.verification_run_id, task.started_at, task.finished_at, task.updated_at, task.id),
            )
            self._event(task.workflow_id, task.id, "task-updated", task.status)
            self.connection.commit()

    def claim_task(self, task_id: str) -> Task | None:
        """Atomically claim a PENDING task for execution."""
        now = utc_now()
        with self._lock:
            cursor = self.connection.execute(
                """UPDATE tasks SET status=?, attempts=attempts+1, started_at=?, updated_at=?
                   WHERE id=? AND status=?""",
                (TaskStatus.RUNNING, now, now, task_id, TaskStatus.PENDING),
            )
            self.connection.commit()
            if cursor.rowcount != 1:
                return None
            row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            task = self._task_from_row(row)
            self._event(task.workflow_id, task.id, "task-claimed", task.status)
            self.connection.commit()
            return task

    def event(self, workflow_id: str, task_id: str | None, kind: str, detail: str) -> None:
        with self._lock:
            self._event(workflow_id, task_id, kind, detail)
            self.connection.commit()
        # Notify outside the lock: subscribers run on the publisher thread
        # and must never stall other store users (see EventBus docs).
        self._notify_bus(Event(
            workflow_id=workflow_id,
            task_id=task_id,
            type=EventType.NOTE,
            message=detail,
            payload={"legacy_kind": kind},
        ))

    def list_events(self, workflow_id: str, task_id: str | None = None) -> list[dict[str, object]]:
        """Legacy events as plain dicts (Phase 1: no Row leakage)."""
        query = "SELECT * FROM events WHERE workflow_id = ?" + (" AND task_id = ?" if task_id else "")
        query += " ORDER BY rowid"
        params: tuple[str, ...] = (workflow_id,) if not task_id else (workflow_id, task_id)
        with self._lock:
            rows = self.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def _event(self, workflow_id: str, task_id: str | None, kind: str, detail: str) -> None:
        self.connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid4()), workflow_id, task_id, kind, detail, utc_now()),
        )

    def _notify_bus(self, event: Event) -> None:
        bus = getattr(self, "_event_bus", None)
        if bus is None:
            return
        try:
            bus.emit(event)
        except Exception:
            pass

    def record_typed_event(self, event: Event) -> Event:
        """Persist a versioned timeline event and notify subscribers.

        Also mirrors the event into the legacy ``events`` table (same
        transaction) so older readers using ``list_events`` keep seeing new
        activity. Events without a workflow id cannot mirror (the legacy
        column is NOT NULL) and live only in ``typed_events``.
        """
        normalized = coerce_event(event.to_dict() if isinstance(event, Event) else event)
        if normalized is None:
            raise ValueError("cannot record an empty event")
        with self._lock:
            self.connection.execute(
                """INSERT INTO typed_events (
                    id, workflow_id, task_id, agent_run_id, type, severity,
                    message, payload, schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalized.id,
                    normalized.workflow_id,
                    normalized.task_id,
                    normalized.agent_run_id,
                    normalized.type.value if isinstance(normalized.type, EventType) else str(normalized.type),
                    normalized.severity.value if isinstance(normalized.severity, EventSeverity) else str(normalized.severity),
                    normalized.message,
                    _json(_jsonable(normalized.payload)),
                    normalized.schema_version,
                    normalized.timestamp,
                ),
            )
            if normalized.workflow_id is not None:
                self._event(
                    normalized.workflow_id,
                    normalized.task_id,
                    normalized.type.value if isinstance(normalized.type, EventType) else str(normalized.type),
                    normalized.message or "",
                )
            self.connection.commit()
        self._notify_bus(normalized)
        return normalized

    def query_events(
        self,
        workflow_id: str | None = None,
        task_id: str | None = None,
        agent_run_id: str | None = None,
        event_type: EventType | str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Event]:
        """Chronologically ordered, paginated, filterable timeline query."""
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("Event query limit and offset must be non-negative integers.")
        take = min(limit, 1000)
        skip = offset
        clauses: list[str] = []
        params: list[object] = []
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if agent_run_id is not None:
            clauses.append("agent_run_id = ?")
            params.append(agent_run_id)
        if event_type is not None:
            clauses.append("type = ?")
            params.append(event_type.value if isinstance(event_type, EventType) else str(event_type))
        query = "SELECT * FROM typed_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, rowid LIMIT ? OFFSET ?"
        params.extend([take, skip])
        with self._lock:
            rows = self.connection.execute(query, tuple(params)).fetchall()
        return [self._typed_event_from_row(row) for row in rows]

    def _typed_event_from_row(self, row: sqlite3.Row) -> Event:
        try:
            event_type = EventType(row["type"])
        except (ValueError, KeyError, TypeError):
            event_type = EventType.NOTE
        try:
            severity = EventSeverity(row["severity"])
        except (ValueError, KeyError, TypeError):
            severity = EventSeverity.INFO
        payload = _loads(row["payload"], {})
        return Event(
            id=row["id"],
            timestamp=row["created_at"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            agent_run_id=row["agent_run_id"],
            type=event_type,
            severity=severity,
            message=row["message"],
            payload=payload if isinstance(payload, dict) else {},
            schema_version=row["schema_version"] or 1,
        )

    def create_artifact_record(self, artifact: Artifact) -> Artifact:
        with self._lock:
            self.connection.execute(
                """INSERT INTO artifacts (
                    id, workflow_id, task_id, agent_run_id, kind, name,
                    rel_path, sha256, size_bytes, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact.id,
                    artifact.workflow_id,
                    artifact.task_id,
                    artifact.agent_run_id,
                    artifact.kind.value if isinstance(artifact.kind, ArtifactKind) else str(artifact.kind),
                    artifact.name,
                    artifact.rel_path,
                    artifact.sha256,
                    artifact.size_bytes,
                    _json(_jsonable(artifact.metadata.to_dict() if isinstance(artifact.metadata, ArtifactMetadata) else {})),
                    artifact.created_at,
                ),
            )
            self.connection.commit()
        return artifact

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        return self._artifact_from_row(row) if row is not None else None

    def list_artifacts(
        self,
        workflow_id: str | None = None,
        task_id: str | None = None,
        kind: ArtifactKind | str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("Artifact list limit and offset must be non-negative integers.")
        take = min(limit, 1000)
        skip = offset
        clauses: list[str] = []
        params: list[object] = []
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value if isinstance(kind, ArtifactKind) else str(kind))
        query = "SELECT * FROM artifacts"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, rowid LIMIT ? OFFSET ?"
        params.extend([take, skip])
        with self._lock:
            rows = self.connection.execute(query, tuple(params)).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def delete_artifact_record(self, artifact_id: str) -> bool:
        """Delete one artifact metadata row.

        Removes only the DB row; pair with ``ArtifactStore.delete`` to remove
        the file (the CLI ``artifacts --prune-keep`` path does both).
        """
        with self._lock:
            cursor = self.connection.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
            self.connection.commit()
            return cursor.rowcount > 0

    def _artifact_from_row(self, row: sqlite3.Row) -> Artifact:
        try:
            kind = ArtifactKind(row["kind"])
        except (ValueError, KeyError, TypeError):
            kind = ArtifactKind.CUSTOM
        metadata = _loads(row["metadata"], {})
        return Artifact(
            id=row["id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            agent_run_id=row["agent_run_id"],
            kind=kind,
            name=row["name"],
            rel_path=row["rel_path"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"] or 0,
            metadata=ArtifactMetadata.from_dict(metadata if isinstance(metadata, dict) else {}),
            created_at=row["created_at"],
        )

    def ready_tasks(self, workflow_id: str) -> list[Task]:
        tasks = self.list_tasks(workflow_id)
        by_id = {task.id: task for task in tasks}
        ready: list[Task] = []
        for task in tasks:
            if task.status is not TaskStatus.PENDING:
                continue
            dependencies = [by_id.get(task_id) for task_id in task.dependencies]
            if any(dependency is None or dependency.status in {TaskStatus.FAILED, TaskStatus.BLOCKED} for dependency in dependencies):
                task.status = TaskStatus.BLOCKED
                task.result = "Blocked by an unsuccessful dependency."
                task.finished_at = utc_now()
                self.update_task(task)
            elif all(dependency.status is TaskStatus.PASSED for dependency in dependencies):
                ready.append(task)
        return ready

    def refresh_workflow_status(self, workflow_id: str) -> TaskStatus:
        tasks = self.list_tasks(workflow_id)
        statuses = {task.status for task in tasks}
        status = TaskStatus.PASSED if tasks and statuses == {TaskStatus.PASSED} else (
            TaskStatus.FAILED if TaskStatus.FAILED in statuses else
            TaskStatus.BLOCKED if TaskStatus.BLOCKED in statuses and TaskStatus.PENDING not in statuses else
            TaskStatus.RUNNING if TaskStatus.RUNNING in statuses else TaskStatus.PENDING
        )
        with self._lock:
            self.connection.execute("UPDATE workflows SET status=?, updated_at=? WHERE id=?", (status, utc_now(), workflow_id))
            self.connection.commit()
        return status

    # ------------------------------------------------------------------
    # AgentRun persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _status_value(status: AgentRunStatus | str) -> AgentRunStatus:
        return status if isinstance(status, AgentRunStatus) else AgentRunStatus(status)

    def create_agent_run(self, context: AgentRunContext) -> AgentRun:
        run_id = str(uuid4())
        now = utc_now()
        relationship = (
            context.relationship
            if isinstance(context.relationship, AgentRunRelationship)
            else AgentRunRelationship(context.relationship)
        )
        retry_of = context.parent_run_id if relationship is AgentRunRelationship.RETRY else None
        repair_of = context.parent_run_id if relationship is AgentRunRelationship.REPAIR else None
        with self._lock:
            self.connection.execute(
                """INSERT INTO agent_runs (
                    id, workflow_id, task_id, attempt, agent, executable, role, model,
                    status, command, command_metadata, working_directory, worktree,
                    prompt_metadata, relationship, parent_run_id, retry_of, repair_of,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    context.workflow_id,
                    context.task_id,
                    max(1, int(context.attempt)),
                    context.agent,
                    context.executable,
                    context.role,
                    context.model,
                    AgentRunStatus.PENDING,
                    _json(context.safe_command),
                    _json(context.command_metadata),
                    context.working_directory,
                    context.worktree,
                    _json(context.prompt_metadata),
                    relationship,
                    context.parent_run_id,
                    retry_of,
                    repair_of,
                    now,
                    now,
                ),
            )
            if context.workflow_id is not None:
                self._event(context.workflow_id, context.task_id, "agent-run-created", run_id)
            self.connection.commit()
        return self.get_agent_run(run_id)

    def get_agent_run(self, run_id: str) -> AgentRun:
        with self._lock:
            row = self.connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown AgentRun: {run_id}")
        return self._agent_run_from_row(row)

    def list_agent_runs(
        self,
        workflow_id: str | None = None,
        task_id: str | None = None,
        status: AgentRunStatus | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AgentRun]:
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("AgentRun list limit and offset must be non-negative integers.")
        clauses: list[str] = []
        params: list[str | int] = []
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(self._status_value(status))
        query = "SELECT * FROM agent_runs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, rowid LIMIT ? OFFSET ?"
        with self._lock:
            rows = self.connection.execute(query, (*params, limit, offset)).fetchall()
        return [self._agent_run_from_row(row) for row in rows]

    def latest_agent_run(
        self,
        workflow_id: str | None = None,
        task_id: str | None = None,
        statuses: tuple[AgentRunStatus | str, ...] | None = None,
    ) -> AgentRun | None:
        clauses: list[str] = []
        params: list[str] = []
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if statuses:
            clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
            params.extend(self._status_value(item) for item in statuses)
        query = "SELECT * FROM agent_runs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with self._lock:
            row = self.connection.execute(query, params).fetchone()
        return None if row is None else self._agent_run_from_row(row)

    def _transition(self, run_id: str, target: AgentRunStatus, **changes: object) -> AgentRun:
        with self._lock:
            row = self.connection.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown AgentRun: {run_id}")
            current = AgentRunStatus(row["status"])
            # Single-sourced matrix (Track A1): execution_model owns the
            # table; this method only maps the violation to ValueError to
            # preserve the long-standing public error contract.
            try:
                assert_agent_run_transition(current, target)
            except StateTransitionError as error:
                raise ValueError(str(error)) from error
            assignments = ["status=?", "updated_at=?"]
            values: list[object] = [target, utc_now()]
            for key, value in changes.items():
                assignments.append(f"{key}=?")
                values.append(value)
            values.append(run_id)
            self.connection.execute(
                f"UPDATE agent_runs SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            )
            workflow_id, task_id = self.connection.execute(
                "SELECT workflow_id, task_id FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if workflow_id is not None:
                self._event(workflow_id, task_id, "agent-run-updated", target)
            self.connection.commit()
        return self.get_agent_run(run_id)

    def start_agent_run(self, run_id: str) -> AgentRun:
        return self._transition(run_id, AgentRunStatus.STARTING, started_at=utc_now())

    def mark_agent_run_running(self, run_id: str) -> AgentRun:
        return self._transition(run_id, AgentRunStatus.RUNNING)

    def finish_agent_run(self, run_id: str, outcome: AgentRunOutcome) -> AgentRun:
        status = self._status_value(outcome.status)
        with self._lock:
            row = self.connection.execute("SELECT status, started_at FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown AgentRun: {run_id}")
            current = AgentRunStatus(row["status"])
            if current in TERMINAL_STATUSES:
                status = current
            duration = outcome.duration_seconds
            if duration is None and row["started_at"]:
                duration = None
            values = {
                "ended_at": utc_now(),
                "duration_seconds": duration,
                "exit_code": outcome.exit_code,
                "cancelled": int(outcome.cancelled),
                "timed_out": int(outcome.timed_out),
                "terminated": int(outcome.terminated),
                "stdout_path": outcome.stdout_path,
                "stderr_path": outcome.stderr_path,
                "log_path": outcome.log_path,
                "files_changed": _json(outcome.files_changed),
                "diff_stat": outcome.diff_stat,
                "error": outcome.error,
                "failure_classification": outcome.failure_classification,
                "structured_result": _json(outcome.structured_result),
            }
        return self._transition(run_id, status, **values)

    def fail_agent_run(self, run_id: str, error: BaseException, classification: str = "execution_error") -> AgentRun:
        if classification == "cancelled":
            status = AgentRunStatus.CANCELLED
        elif classification == "timeout":
            status = AgentRunStatus.TIMED_OUT
        elif classification == "terminated":
            status = AgentRunStatus.TERMINATED
        else:
            status = AgentRunStatus.FAILED
        return self.finish_agent_run(
            run_id,
            AgentRunOutcome(
                status=status,
                error=str(error),
                failure_classification=classification,
                cancelled=status is AgentRunStatus.CANCELLED,
                timed_out=status is AgentRunStatus.TIMED_OUT,
                terminated=status is AgentRunStatus.TERMINATED,
            ),
        )

    def cancel_agent_run(self, run_id: str, error: str | None = None) -> AgentRun:
        return self.finish_agent_run(
            run_id,
            AgentRunOutcome(
                status=AgentRunStatus.CANCELLED,
                cancelled=True,
                error=error,
                failure_classification="cancelled",
            ),
        )

    def timeout_agent_run(self, run_id: str, error: str | None = None) -> AgentRun:
        return self.finish_agent_run(
            run_id,
            AgentRunOutcome(
                status=AgentRunStatus.TIMED_OUT,
                timed_out=True,
                error=error,
                failure_classification="timeout",
            ),
        )

    def terminate_agent_run(self, run_id: str, error: str | None = None) -> AgentRun:
        return self.finish_agent_run(
            run_id,
            AgentRunOutcome(
                status=AgentRunStatus.TERMINATED,
                terminated=True,
                error=error,
                failure_classification="terminated",
            ),
        )

    def update_agent_run(self, run_id: str, **changes: object) -> AgentRun:
        """Update safe post-execution metadata without changing lifecycle state."""

        allowed = {
            "stdout_path", "stderr_path", "log_path", "files_changed", "diff_stat",
            "error", "failure_classification", "structured_result",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported AgentRun fields: {', '.join(sorted(unknown))}")
        values: dict[str, object] = {}
        for key, value in changes.items():
            values[key] = _json(value) if key in {"files_changed", "structured_result"} else value
        with self._lock:
            assignments = ", ".join(f"{key}=?" for key in values) + ", updated_at=?"
            params = [*values.values(), utc_now(), run_id]
            cursor = self.connection.execute(
                f"UPDATE agent_runs SET {assignments} WHERE id = ?", params
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown AgentRun: {run_id}")
            self.connection.commit()
        return self.get_agent_run(run_id)

    def recover_agent_runs(self, workflow_id: str | None = None) -> list[AgentRun]:
        """Mark runs left non-terminal by a process restart as interrupted.

        When a workflow id is given, only that workflow's runs are touched so
        workflow-scoped recovery cannot terminate another workflow's live rows.
        """

        with self._lock:
            if workflow_id is not None:
                rows = self.connection.execute(
                    "SELECT id FROM agent_runs WHERE status IN (?, ?, ?) AND workflow_id = ?",
                    (AgentRunStatus.PENDING, AgentRunStatus.STARTING, AgentRunStatus.RUNNING, workflow_id),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT id FROM agent_runs WHERE status IN (?, ?, ?)",
                    (AgentRunStatus.PENDING, AgentRunStatus.STARTING, AgentRunStatus.RUNNING),
                ).fetchall()
            recovered: list[AgentRun] = []
            for row in rows:
                run_id = row["id"]
                self.connection.execute(
                    """UPDATE agent_runs SET status=?, ended_at=?, terminated=?,
                       error=?, failure_classification=?, updated_at=? WHERE id=?""",
                    (
                        AgentRunStatus.TERMINATED,
                        utc_now(),
                        1,
                        "AgentRun was interrupted before a terminal outcome was recorded.",
                        "interrupted",
                        utc_now(),
                        run_id,
                    ),
                )
                workflow_id, task_id = self.connection.execute(
                    "SELECT workflow_id, task_id FROM agent_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if workflow_id is not None:
                    self._event(workflow_id, task_id, "agent-run-recovered", run_id)
                recovered.append(self.get_agent_run(run_id))
            if rows:
                self.connection.commit()
        return recovered

    # ------------------------------------------------------------------
    # Verification Kernel persistence
    # ------------------------------------------------------------------

    def _verification_run_from_row(self, row: sqlite3.Row) -> VerificationRun:
        snapshot = _loads(row["profile_snapshot"], {})
        overall = row["overall_status"]
        return VerificationRun(
            id=row["id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            profile_name=row["profile_name"],
            profile_snapshot=snapshot if isinstance(snapshot, dict) else {},
            mode=VerificationProfileMode(row["mode"]),
            concurrency=row["concurrency"],
            status=VerificationRunStatus(row["status"]),
            source_agent_run_id=row["source_agent_run_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            duration_seconds=row["duration_seconds"],
            total_checks=row["total_checks"],
            passed_checks=row["passed_checks"],
            failed_checks=row["failed_checks"],
            skipped_checks=row["skipped_checks"],
            required_failures=row["required_failures"],
            overall_status=VerificationReportStatus(overall) if overall else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _verification_check_from_row(self, row: sqlite3.Row) -> VerificationCheck:
        command = _loads(row["command"], ())
        return VerificationCheck(
            id=row["id"],
            run_id=row["run_id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            profile_name=row["profile_name"],
            name=row["name"],
            check_class=VerificationCheckClass(row["check_class"]),
            command=tuple(command) if isinstance(command, list) else (),
            working_directory=row["working_directory"],
            timeout_seconds=row["timeout_seconds"],
            required=bool(row["required"]),
            policy=VerificationExecutionPolicy(row["policy"]),
            status=VerificationCheckStatus(row["status"]),
            exit_code=row["exit_code"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            duration_seconds=row["duration_seconds"],
            stdout_path=row["stdout_path"],
            stderr_path=row["stderr_path"],
            failure_reason=row["failure_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _verification_report_from_row(self, row: sqlite3.Row) -> VerificationReport:
        return VerificationReport(
            id=row["id"],
            run_id=row["run_id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            profile_name=row["profile_name"],
            total_checks=row["total_checks"],
            passed_checks=row["passed_checks"],
            failed_checks=row["failed_checks"],
            skipped_checks=row["skipped_checks"],
            required_failures=row["required_failures"],
            duration_seconds=row["duration_seconds"],
            overall_status=VerificationReportStatus(row["overall_status"]),
            generated_at=row["generated_at"],
        )

    def create_verification_run(
        self,
        workflow_id: str | None,
        task_id: str | None,
        profile: VerificationProfile,
        source_agent_run_id: str | None = None,
    ) -> VerificationRun:
        run_id = str(uuid4())
        now = utc_now()
        with self._lock:
            if source_agent_run_id is not None:
                parent = self.connection.execute(
                    "SELECT 1 FROM agent_runs WHERE id = ?", (source_agent_run_id,)
                ).fetchone()
                if parent is None:
                    raise ValueError(f"Unknown source AgentRun: {source_agent_run_id}.")
            self.connection.execute(
                """INSERT INTO verification_runs (
                    id, workflow_id, task_id, profile_name, profile_snapshot, mode,
                    concurrency, status, source_agent_run_id, started_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, workflow_id, task_id, profile.name, _json(profile_to_dict(profile)),
                    profile.mode, profile.concurrency, VerificationRunStatus.PENDING,
                    source_agent_run_id, now, now, now,
                ),
            )
            if workflow_id is not None:
                self._event(workflow_id, task_id, "verification-run-created", run_id)
            self.connection.commit()
        return self.get_verification_run(run_id)

    def get_verification_run(self, run_id: str) -> VerificationRun:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM verification_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown verification run: {run_id}")
        return self._verification_run_from_row(row)

    def list_verification_runs(
        self,
        workflow_id: str | None = None,
        task_id: str | None = None,
        status: VerificationRunStatus | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[VerificationRun]:
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("Verification run list limit and offset must be non-negative integers.")
        clauses: list[str] = []
        params: list[str | int] = []
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status if isinstance(status, VerificationRunStatus) else VerificationRunStatus(status))
        query = "SELECT * FROM verification_runs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, rowid LIMIT ? OFFSET ?"
        with self._lock:
            rows = self.connection.execute(query, (*params, limit, offset)).fetchall()
        return [self._verification_run_from_row(row) for row in rows]

    def start_verification_run(self, run_id: str) -> VerificationRun:
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE verification_runs SET status=?, started_at=?, updated_at=? WHERE id=? AND status=?",
                (VerificationRunStatus.RUNNING, utc_now(), utc_now(), run_id, VerificationRunStatus.PENDING),
            )
            if cursor.rowcount != 1:
                current = self.get_verification_run(run_id)
                if current.status is not VerificationRunStatus.RUNNING:
                    raise ValueError(f"Invalid verification run transition to running from {current.status.value}.")
                return current
            self.connection.commit()
        return self.get_verification_run(run_id)

    def finish_verification_run(
        self,
        run_id: str,
        status: VerificationRunStatus,
        overall_status: VerificationReportStatus,
        total_checks: int,
        passed_checks: int,
        failed_checks: int,
        skipped_checks: int,
        required_failures: int,
        duration_seconds: float | None,
    ) -> VerificationRun:
        now = utc_now()
        with self._lock:
            cursor = self.connection.execute(
                """UPDATE verification_runs SET status=?, ended_at=?, duration_seconds=?,
                   total_checks=?, passed_checks=?, failed_checks=?, skipped_checks=?,
                   required_failures=?, overall_status=?, updated_at=? WHERE id=?""",
                (status, now, duration_seconds, total_checks, passed_checks, failed_checks,
                 skipped_checks, required_failures, overall_status, now, run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown verification run: {run_id}")
            row = self.connection.execute(
                "SELECT workflow_id, task_id FROM verification_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row["workflow_id"] is not None:
                self._event(row["workflow_id"], row["task_id"], "verification-run-finished", status.value)
            self.connection.commit()
        return self.get_verification_run(run_id)

    def create_verification_check(
        self,
        run_id: str,
        workflow_id: str | None,
        task_id: str | None,
        profile_name: str,
        name: str,
        check_class: str,
        command: tuple[str, ...] | None,
        working_directory: str | None,
        timeout_seconds: int | None,
        required: bool,
        policy: str,
    ) -> VerificationCheck:
        check_id = str(uuid4())
        now = utc_now()
        with self._lock:
            self.connection.execute(
                """INSERT INTO verification_checks (
                    id, run_id, workflow_id, task_id, profile_name, name, check_class,
                    command, working_directory, timeout_seconds, required, policy, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (check_id, run_id, workflow_id, task_id, profile_name, name, check_class,
                 _json(command), working_directory, timeout_seconds, int(required), policy,
                 VerificationCheckStatus.PENDING, now, now),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM verification_checks WHERE id = ?", (check_id,)
            ).fetchone()
        return self._verification_check_from_row(row)

    def start_verification_check(self, check_id: str) -> VerificationCheck:
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE verification_checks SET status=?, started_at=?, updated_at=? WHERE id=? AND status=?",
                (VerificationCheckStatus.RUNNING, utc_now(), utc_now(), check_id, VerificationCheckStatus.PENDING),
            )
            if cursor.rowcount != 1:
                current = self.get_verification_check(check_id)
                if current.status is not VerificationCheckStatus.RUNNING:
                    raise ValueError(f"Invalid verification check transition to running from {current.status.value}.")
                return current
            self.connection.commit()
        return self.get_verification_check(check_id)

    def finish_verification_check(
        self,
        check_id: str,
        status: VerificationCheckStatus,
        exit_code: int | None = None,
        duration_seconds: float | None = None,
        stdout_path: str | None = None,
        stderr_path: str | None = None,
        failure_reason: str | None = None,
    ) -> VerificationCheck:
        now = utc_now()
        with self._lock:
            cursor = self.connection.execute(
                """UPDATE verification_checks SET status=?, ended_at=?, duration_seconds=?,
                   exit_code=?, stdout_path=?, stderr_path=?, failure_reason=?, updated_at=?
                   WHERE id=?""",
                (status, now, duration_seconds, exit_code, stdout_path, stderr_path,
                 failure_reason, now, check_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown verification check: {check_id}")
            self.connection.commit()
        return self.get_verification_check(check_id)

    def get_verification_check(self, check_id: str) -> VerificationCheck:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM verification_checks WHERE id = ?", (check_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown verification check: {check_id}")
        return self._verification_check_from_row(row)

    def list_verification_checks(self, run_id: str) -> list[VerificationCheck]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM verification_checks WHERE run_id = ? ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
        return [self._verification_check_from_row(row) for row in rows]

    def create_verification_report(self, report: VerificationReport) -> VerificationReport:
        with self._lock:
            self.connection.execute(
                """INSERT INTO verification_reports (
                    id, run_id, workflow_id, task_id, profile_name, total_checks,
                    passed_checks, failed_checks, skipped_checks, required_failures,
                    duration_seconds, overall_status, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (report.id, report.run_id, report.workflow_id, report.task_id, report.profile_name,
                 report.total_checks, report.passed_checks, report.failed_checks, report.skipped_checks,
                 report.required_failures, report.duration_seconds, report.overall_status, report.generated_at),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM verification_reports WHERE id = ?", (report.id,)
            ).fetchone()
        return self._verification_report_from_row(row)

    def get_verification_report_by_run(self, run_id: str) -> VerificationReport:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM verification_reports WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown verification report for run: {run_id}")
        return self._verification_report_from_row(row)

    def recover_verification_runs(self, workflow_id: str | None = None) -> list[VerificationRun]:
        """Mark verification work stranded by a restart as interrupted failures.

        When a workflow id is given, only that workflow's runs and their
        checks are touched so workflow-scoped recovery cannot mutate another
        workflow's live rows.
        """

        with self._lock:
            if workflow_id is not None:
                runs = self.connection.execute(
                    "SELECT id, workflow_id, task_id FROM verification_runs "
                    "WHERE status IN (?, ?) AND workflow_id = ?",
                    (VerificationRunStatus.PENDING, VerificationRunStatus.RUNNING, workflow_id),
                ).fetchall()
                run_ids = [run["id"] for run in runs]
                if run_ids:
                    placeholders = ",".join("?" for _ in run_ids)
                    checks = self.connection.execute(
                        f"SELECT id FROM verification_checks WHERE status IN (?, ?) AND run_id IN ({placeholders})",
                        (VerificationCheckStatus.PENDING, VerificationCheckStatus.RUNNING, *run_ids),
                    ).fetchall()
                else:
                    checks = []
            else:
                runs = self.connection.execute(
                    "SELECT id, workflow_id, task_id FROM verification_runs WHERE status IN (?, ?)",
                    (VerificationRunStatus.PENDING, VerificationRunStatus.RUNNING),
                ).fetchall()
                checks = self.connection.execute(
                    "SELECT id FROM verification_checks WHERE status IN (?, ?)",
                    (VerificationCheckStatus.PENDING, VerificationCheckStatus.RUNNING),
                ).fetchall()
            for check in checks:
                self.connection.execute(
                    "UPDATE verification_checks SET status=?, ended_at=?, failure_reason=?, updated_at=? WHERE id=?",
                    (VerificationCheckStatus.FAILED, utc_now(), "interrupted", utc_now(), check["id"]),
                )
            recovered: list[VerificationRun] = []
            for run in runs:
                # Count after marking stranded checks interrupted, so the
                # totals reflect post-crash state.  required_failures only
                # counts required checks; optional interruptions do not fail
                # the report.  Already-terminal checks (including CANCELLED
                # and TIMED_OUT recorded before the crash) are preserved.
                counts = self.connection.execute(
                    """SELECT
                       COUNT(*) AS total,
                       SUM(status = 'passed') AS passed,
                       SUM(status IN ('failed', 'timed_out')) AS failed,
                       SUM(status = 'skipped') AS skipped,
                       SUM(required AND status IN ('failed', 'timed_out', 'cancelled')) AS required_failures
                       FROM verification_checks WHERE run_id = ?""",
                    (run["id"],),
                ).fetchone()
                self.connection.execute(
                    """UPDATE verification_runs SET status=?, ended_at=?, overall_status=?,
                       total_checks=?, passed_checks=?, failed_checks=?, skipped_checks=?,
                       required_failures=?, updated_at=? WHERE id=?""",
                    (VerificationRunStatus.FAILED, utc_now(), VerificationReportStatus.FAILED,
                     counts["total"] or 0, counts["passed"] or 0, counts["failed"] or 0,
                     counts["skipped"] or 0, counts["required_failures"] or 0, utc_now(), run["id"]),
                )
                if run["workflow_id"] is not None:
                    self._event(run["workflow_id"], run["task_id"], "verification-run-recovered", run["id"])
                recovered.append(self.get_verification_run(run["id"]))
            if runs or checks:
                self.connection.commit()
        return recovered

    def agent_run_observer(self) -> StateAgentRunObserver:
        return StateAgentRunObserver(self)

    # ------------------------------------------------------------------
    # Failure / recovery persistence (schema v3, additive)
    # ------------------------------------------------------------------

    def _migrate_failures(self) -> None:
        """Apply additive Failure Kernel migrations without rewriting tables."""

        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS failures (
                id TEXT PRIMARY KEY,
                workflow_id TEXT REFERENCES workflows(id),
                task_id TEXT,
                agent_run_id TEXT,
                source TEXT NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                retryable INTEGER NOT NULL DEFAULT 0,
                repairable INTEGER NOT NULL DEFAULT 0,
                evidence TEXT,
                primary_error TEXT,
                verification_run_id TEXT,
                recommended_action TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                repair_cycle INTEGER NOT NULL DEFAULT 0,
                recovery_state TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_failures_workflow
                ON failures(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_failures_task
                ON failures(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_failures_category
                ON failures(category);
            """
        )
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 3"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (3, utc_now()),
            )

    def _migrate_typed_events(self) -> None:
        """Apply additive versioned-timeline migrations without rewriting tables."""

        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS typed_events (
                id TEXT PRIMARY KEY,
                -- Run references are plain TEXT (no REFERENCES): events must
                -- record for workflows/runs that were never persisted
                -- (crash recovery, legacy imports), mirroring failures.
                workflow_id TEXT,
                task_id TEXT,
                agent_run_id TEXT,
                type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT,
                payload TEXT,
                schema_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_typed_events_workflow
                ON typed_events(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_typed_events_task
                ON typed_events(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_typed_events_run
                ON typed_events(agent_run_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_typed_events_type
                ON typed_events(type, created_at);
            """
        )
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 4"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (4, utc_now()),
            )

    def _migrate_artifacts(self) -> None:
        """Apply additive artifact-registry migrations without rewriting tables."""

        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                -- Plain TEXT refs (no REFERENCES): artifacts may describe
                -- runs/workflows recorded elsewhere or not at all.
                workflow_id TEXT,
                task_id TEXT,
                agent_run_id TEXT,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_artifacts_workflow
                ON artifacts(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_artifacts_task
                ON artifacts(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_artifacts_kind
                ON artifacts(kind, created_at);
            """
        )
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 5"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (5, utc_now()),
            )

    def _migrate_worktree_refs(self) -> None:
        """Phase 3: persisted worktree provenance (additive, idempotent)."""
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS worktree_refs (
                id TEXT PRIMARY KEY,
                -- Plain TEXT refs (no REFERENCES): refs must survive for
                -- workflows recorded in older DBs and crash-recovery paths.
                workflow_id TEXT NOT NULL,
                path TEXT NOT NULL,
                branch TEXT NOT NULL,
                base_branch TEXT NOT NULL,
                base_commit TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_worktree_refs_workflow
                ON worktree_refs(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_worktree_refs_path
                ON worktree_refs(path);
            """
        )
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 6"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (6, utc_now()),
            )

    def record_worktree_ref(self, ref: WorktreeRef) -> WorktreeRef:
        """Persist one worktree provenance record (Phase 3)."""
        if not ref.id:
            ref.id = str(uuid4())
        if not ref.created_at:
            ref.created_at = utc_now()
        with self._lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO worktree_refs (
                    id, workflow_id, path, branch, base_branch, base_commit, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ref.id, ref.workflow_id, ref.path, ref.branch,
                 ref.base_branch, ref.base_commit, ref.created_at),
            )
            self.connection.commit()
        return ref

    def get_worktree_ref(self, workflow_id: str) -> WorktreeRef | None:
        """Latest persisted provenance for a workflow, if any."""
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM worktree_refs WHERE workflow_id = ? ORDER BY rowid DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
        return self._worktree_ref_from_row(row) if row is not None else None

    def find_worktree_ref_by_path(self, path: str | Path) -> WorktreeRef | None:
        """Look up persisted provenance by worktree path (retry/merge path)."""
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM worktree_refs WHERE path = ? ORDER BY rowid DESC LIMIT 1",
                (str(path),),
            ).fetchone()
        return self._worktree_ref_from_row(row) if row is not None else None

    def list_worktree_refs(self, workflow_id: str | None = None) -> list[WorktreeRef]:
        query = "SELECT * FROM worktree_refs"
        params: tuple[str, ...] = ()
        if workflow_id is not None:
            query += " WHERE workflow_id = ?"
            params = (workflow_id,)
        query += " ORDER BY rowid"
        with self._lock:
            rows = self.connection.execute(query, params).fetchall()
        return [self._worktree_ref_from_row(row) for row in rows]

    def _failure_from_row(self, row: sqlite3.Row) -> Failure:
        recovery = row["recovery_state"]
        return Failure(
            id=row["id"],
            workflow_id=row["workflow_id"],
            task_id=row["task_id"],
            agent_run_id=row["agent_run_id"],
            source=FailureSource(row["source"]),
            category=FailureCategory(row["category"]),
            severity=FailureSeverity(row["severity"]),
            retryable=bool(row["retryable"]),
            repairable=bool(row["repairable"]),
            evidence=row["evidence"],
            primary_error=row["primary_error"],
            verification_run_id=row["verification_run_id"],
            recommended_action=RepairAction(row["recommended_action"]),
            attempt=row["attempt"],
            repair_cycle=row["repair_cycle"],
            recovery_state=RecoveryState(recovery) if recovery else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_failure(self, failure: Failure) -> Failure:
        with self._lock:
            self.connection.execute(
                """INSERT INTO failures (
                    id, workflow_id, task_id, agent_run_id, source, category,
                    severity, retryable, repairable, evidence, primary_error,
                    verification_run_id, recommended_action, attempt, repair_cycle,
                    recovery_state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    failure.id, failure.workflow_id, failure.task_id, failure.agent_run_id,
                    failure.source.value if isinstance(failure.source, FailureSource) else str(failure.source),
                    failure.category.value if isinstance(failure.category, FailureCategory) else str(failure.category),
                    failure.severity.value if isinstance(failure.severity, FailureSeverity) else str(failure.severity),
                    int(failure.retryable), int(failure.repairable), failure.evidence,
                    failure.primary_error, failure.verification_run_id,
                    failure.recommended_action.value if isinstance(failure.recommended_action, RepairAction) else str(failure.recommended_action),
                    failure.attempt, failure.repair_cycle,
                    failure.recovery_state.value if isinstance(failure.recovery_state, RecoveryState) else failure.recovery_state,
                    failure.created_at, failure.updated_at,
                ),
            )
            if failure.workflow_id is not None:
                self._event(failure.workflow_id, failure.task_id, "failure-recorded", failure.category.value if isinstance(failure.category, FailureCategory) else str(failure.category))
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM failures WHERE id = ?", (failure.id,)
            ).fetchone()
        return self._failure_from_row(row)

    def get_failure(self, failure_id: str) -> Failure:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM failures WHERE id = ?", (failure_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown failure: {failure_id}")
        return self._failure_from_row(row)

    def list_failures(
        self,
        workflow_id: str | None = None,
        task_id: str | None = None,
        category: FailureCategory | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Failure]:
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("Failure list limit and offset must be non-negative integers.")
        clauses: list[str] = []
        params: list[str | int] = []
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if category is not None:
            clauses.append("category = ?")
            params.append(category.value if isinstance(category, FailureCategory) else str(category))
        query = "SELECT * FROM failures"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, rowid LIMIT ? OFFSET ?"
        with self._lock:
            rows = self.connection.execute(query, (*params, limit, offset)).fetchall()
        return [self._failure_from_row(row) for row in rows]

    def recover_tasks(self, workflow_id: str | None = None) -> list[Task]:
        """Crash recovery for tasks stranded RUNNING by a restart.

        Every stranded task becomes FAILED with preserved evidence — never
        PASSED — so an unknown/interrupted state cannot become success
        without verification evidence. Worktree preservation is handled by
        the caller (CLI/GUI never deletes on failure/conflict per AgentOps
        behavior).
        """

        with self._lock:
            if workflow_id is not None:
                rows = self.connection.execute(
                    "SELECT id FROM tasks WHERE status = ? AND workflow_id = ?",
                    (TaskStatus.RUNNING, workflow_id),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT id FROM tasks WHERE status = ?", (TaskStatus.RUNNING,)
                ).fetchall()
            recovered: list[Task] = []
            for row in rows:
                task_id = row["id"]
                now = utc_now()
                self.connection.execute(
                    "UPDATE tasks SET status=?, result=?, finished_at=?, updated_at=? WHERE id=?",
                    (
                        TaskStatus.FAILED,
                        "Task was interrupted before a terminal outcome was recorded; "
                        "recovered as failed (never success without evidence).",
                        now, now, task_id,
                    ),
                )
                task_row = self.connection.execute(
                    "SELECT workflow_id FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if task_row is not None:
                    self._event(task_row["workflow_id"], task_id, "task-recovered", "interrupted")
            if rows:
                self.connection.commit()
            recovered = [self.get_task(row["id"]) for row in rows]
        for task in recovered:
            self.refresh_workflow_status(task.workflow_id)
        return recovered

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


class StateAgentRunObserver:
    """SQLite adapter for :class:`agentops.runner.AgentRunner`."""

    def __init__(self, state: StateStore):
        self.state = state

    def create_run(self, context: AgentRunContext) -> AgentRun:
        return self.state.create_agent_run(context)

    def mark_starting(self, run_id: str) -> None:
        self.state.start_agent_run(run_id)

    def mark_running(self, run_id: str) -> None:
        self.state.mark_agent_run_running(run_id)

    def finish_run(self, run_id: str, outcome: AgentRunOutcome) -> None:
        self.state.finish_agent_run(run_id, outcome)

    def fail_run(self, run_id: str, error: BaseException, classification: str) -> None:
        self.state.fail_agent_run(run_id, error, classification)


__all__ = ["StateAgentRunObserver", "StateStore"]
