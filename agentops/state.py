"""SQLite persistence for workflows, tasks, and execution events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from .tasks import Task, TaskStatus, utc_now


class StateStore:
    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
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
                max_attempts INTEGER NOT NULL, result TEXT, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, task_id TEXT,
                kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def create_workflow(self, description: str) -> str:
        workflow_id = str(uuid4())
        now = utc_now()
        self.connection.execute(
            "INSERT INTO workflows VALUES (?, ?, ?, ?, ?)",
            (workflow_id, description, TaskStatus.PENDING, now, now),
        )
        self.connection.commit()
        return workflow_id

    def add_task(self, task: Task) -> Task:
        self.connection.execute(
            """INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.id, task.workflow_id, task.description, task.role, task.assigned_agent,
             json.dumps(task.dependencies), task.status, task.attempts, task.max_attempts,
             task.result, task.created_at, task.started_at, task.finished_at, task.updated_at),
        )
        self.event(task.workflow_id, task.id, "task-created", task.description)
        self.connection.commit()
        return task

    def _task_from_row(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"], workflow_id=row["workflow_id"], description=row["description"], role=row["role"],
            assigned_agent=row["assigned_agent"], dependencies=tuple(json.loads(row["dependencies"])),
            status=TaskStatus(row["status"]), attempts=row["attempts"], max_attempts=row["max_attempts"],
            result=row["result"], created_at=row["created_at"], started_at=row["started_at"],
            finished_at=row["finished_at"], updated_at=row["updated_at"],
        )

    def get_task(self, task_id: str) -> Task:
        row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        return self._task_from_row(row)

    def list_tasks(self, workflow_id: str | None = None) -> list[Task]:
        query = "SELECT * FROM tasks" + (" WHERE workflow_id = ?" if workflow_id else "") + " ORDER BY created_at"
        rows = self.connection.execute(query, (workflow_id,) if workflow_id else ()).fetchall()
        return [self._task_from_row(row) for row in rows]

    def latest_workflow(self) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM workflows ORDER BY created_at DESC LIMIT 1").fetchone()

    def update_task(self, task: Task) -> None:
        task.updated_at = utc_now()
        self.connection.execute(
            """UPDATE tasks SET assigned_agent=?, status=?, attempts=?, result=?, started_at=?,
               finished_at=?, updated_at=? WHERE id=?""",
            (task.assigned_agent, task.status, task.attempts, task.result, task.started_at,
             task.finished_at, task.updated_at, task.id),
        )
        self.event(task.workflow_id, task.id, "task-updated", task.status)
        self.connection.commit()

    def event(self, workflow_id: str, task_id: str | None, kind: str, detail: str) -> None:
        self.connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid4()), workflow_id, task_id, kind, detail, utc_now()),
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
        self.connection.execute("UPDATE workflows SET status=?, updated_at=? WHERE id=?", (status, utc_now(), workflow_id))
        self.connection.commit()
        return status
