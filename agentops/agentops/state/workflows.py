"""Workflow and task persistence: CRUD, claiming, readiness, status, recovery."""

from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from ..execution_model import (
    assert_no_fabricated_success,
)
from ..tasks import Task, TaskStatus, Workflow, utc_now


class WorkflowTaskMixin:
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

    def _task_from_row(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            workflow_id=row["workflow_id"],
            description=row["description"],
            role=row["role"],
            assigned_agent=row["assigned_agent"],
            dependencies=tuple(json.loads(row["dependencies"])),
            status=TaskStatus(row["status"]),
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            result=row["result"],
            verified=bool(row["verified"]),
            verification_run_id=row["verification_run_id"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
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
                (
                    task.id,
                    task.workflow_id,
                    task.description,
                    task.role,
                    task.assigned_agent,
                    json.dumps(task.dependencies),
                    task.status,
                    task.attempts,
                    task.max_attempts,
                    task.result,
                    int(task.verified),
                    task.verification_run_id,
                    task.created_at,
                    task.started_at,
                    task.finished_at,
                    task.updated_at,
                ),
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
        query = (
            "SELECT * FROM tasks"
            + (" WHERE workflow_id = ?" if workflow_id else "")
            + " ORDER BY created_at"
        )
        with self._lock:
            rows = self.connection.execute(query, (workflow_id,) if workflow_id else ()).fetchall()
        return [self._task_from_row(row) for row in rows]

    def latest_workflow(self) -> Workflow | None:
        """Newest workflow as a DTO (Phase 1: no Row leakage)."""
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM workflows ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
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

    def list_workflows(
        self, limit: int = 50, offset: int = 0, status: str | None = None
    ) -> list[Workflow]:
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
            row = self.connection.execute(
                "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
        return self._workflow_from_row(row) if row is not None else None

    def update_task(self, task: Task) -> None:
        task.updated_at = utc_now()
        with self._lock:
            self.connection.execute(
                """UPDATE tasks SET description=?, role=?, assigned_agent=?, status=?, attempts=?, result=?, verified=?,
                   verification_run_id=?, started_at=?, finished_at=?, updated_at=? WHERE id=?""",
                (
                    task.description,
                    task.role,
                    task.assigned_agent,
                    task.status,
                    task.attempts,
                    task.result,
                    int(task.verified),
                    task.verification_run_id,
                    task.started_at,
                    task.finished_at,
                    task.updated_at,
                    task.id,
                ),
            )
            self._event(task.workflow_id, task.id, "task-updated", task.status)
            self.connection.commit()

    def delete_task(self, task_id: str) -> bool:
        """Remove one task row; related runs keep their ``task_id`` text.

        Runs, verifications, failures, and events reference tasks by plain
        text (no foreign keys), so deleting a task never cascades: history
        recorded against it stays readable. A task that another task depends
        on cannot be deleted because doing so would make the dependent task
        permanently blocked. The controller guards liveness and workflow size.
        """
        with self._lock:
            target = self.connection.execute(
                "SELECT workflow_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if target is None:
                return False
            rows = self.connection.execute(
                "SELECT * FROM tasks WHERE id != ?", (task_id,)
            ).fetchall()
            for row in rows:
                dependent = self._task_from_row(row)
                if task_id in dependent.dependencies:
                    raise ValueError(
                        f"Task {task_id!r} cannot be deleted because task "
                        f"{dependent.id!r} has a dependency on it."
                    )
            cursor = self.connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self.connection.commit()
            return cursor.rowcount == 1

    def rename_workflow(self, workflow_id: str, description: str) -> bool:
        """Replace a workflow header's description; True when a row changed."""
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE workflows SET description=?, updated_at=? WHERE id=?",
                (description, utc_now(), workflow_id),
            )
            self.connection.commit()
            return cursor.rowcount == 1

    def delete_workflow(self, workflow_id: str) -> dict[str, int]:
        """Remove a workflow and every row that belongs to it.

        Ordered for enforced foreign keys (checks before runs, childless
        agent runs before their parents). Timeline events go with the
        workflow; on-disk worktrees and their branches are left alone for
        the Worktrees surface to clean up. Returns per-table row counts.
        """
        removed: dict[str, int] = {}
        with self._lock:
            run_ids = [
                row["id"]
                for row in self.connection.execute(
                    "SELECT id FROM verification_runs WHERE workflow_id = ?",
                    (workflow_id,),
                ).fetchall()
            ]
            checks = 0
            for verification_id in run_ids:
                cursor = self.connection.execute(
                    "DELETE FROM verification_checks WHERE run_id = ?",
                    (verification_id,),
                )
                checks += cursor.rowcount
            removed["checks"] = checks
            cursor = self.connection.execute(
                "DELETE FROM verification_reports WHERE run_id IN "
                "(SELECT id FROM verification_runs WHERE workflow_id = ?)",
                (workflow_id,),
            )
            removed["reports"] = cursor.rowcount
            cursor = self.connection.execute(
                "DELETE FROM verification_runs WHERE workflow_id = ?", (workflow_id,)
            )
            removed["verification_runs"] = cursor.rowcount
            runs = 0
            run_total = self.connection.execute(
                "SELECT COUNT(*) AS total FROM agent_runs WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()["total"]
            for _ in range(int(run_total) + 1):
                cursor = self.connection.execute(
                    """DELETE FROM agent_runs WHERE workflow_id = ? AND id NOT IN (
                           SELECT parent_run_id FROM agent_runs WHERE parent_run_id IS NOT NULL
                           UNION SELECT retry_of FROM agent_runs WHERE retry_of IS NOT NULL
                           UNION SELECT repair_of FROM agent_runs WHERE repair_of IS NOT NULL
                       )""",
                    (workflow_id,),
                )
                runs += cursor.rowcount
                if cursor.rowcount == 0:
                    break
            removed["agent_runs"] = runs
            for table in (
                "failures",
                "tasks",
                "artifacts",
                "worktree_refs",
                "events",
                "typed_events",
            ):
                cursor = self.connection.execute(
                    f"DELETE FROM {table} WHERE workflow_id = ?", (workflow_id,)
                )
                removed[table] = cursor.rowcount
            cursor = self.connection.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
            removed["workflows"] = cursor.rowcount
            self.connection.commit()
            return removed

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

    def ready_tasks(self, workflow_id: str) -> list[Task]:
        tasks = self.list_tasks(workflow_id)
        by_id = {task.id: task for task in tasks}
        ready: list[Task] = []
        for task in tasks:
            if task.status is not TaskStatus.PENDING:
                continue
            dependencies = [by_id.get(task_id) for task_id in task.dependencies]
            if any(
                dependency is None or dependency.status in {TaskStatus.FAILED, TaskStatus.BLOCKED}
                for dependency in dependencies
            ):
                task.status = TaskStatus.BLOCKED
                task.result = "Blocked by an unsuccessful dependency."
                task.finished_at = utc_now()
                self.update_task(task)
            elif all(dependency.status is TaskStatus.PASSED for dependency in dependencies):
                ready.append(task)
        return ready

    def refresh_workflow_status(self, workflow_id: str, *, cancelled: bool = False) -> TaskStatus:
        """Derive and persist a workflow's header status from its task rows.

        ``cancelled`` records that execution ended by cancellation: when the
        task pool derives only PENDING or RUNNING (no task ever started, or
        one was cut off mid-run), the workflow is terminal FAILED anyway,
        because a stopped operation must not read as active forever. Task
        rows are never rewritten here -- unstarted tasks stay pending. A
        derived PASSED, FAILED, or BLOCKED is never downgraded by the flag.
        """
        tasks = self.list_tasks(workflow_id)
        statuses = {task.status for task in tasks}
        status = (
            TaskStatus.PASSED
            if tasks and statuses == {TaskStatus.PASSED}
            else (
                TaskStatus.FAILED
                if TaskStatus.FAILED in statuses
                else TaskStatus.BLOCKED
                if TaskStatus.BLOCKED in statuses and TaskStatus.PENDING not in statuses
                else TaskStatus.RUNNING
                if TaskStatus.RUNNING in statuses
                else TaskStatus.PENDING
            )
        )
        if cancelled and status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            status = TaskStatus.FAILED
        with self._lock:
            self.connection.execute(
                "UPDATE workflows SET status=?, updated_at=? WHERE id=?",
                (status, utc_now(), workflow_id),
            )
            self.connection.commit()
        return status

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
                        now,
                        now,
                        task_id,
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
        # A1 honesty post-condition: recovery must never mint success.
        for task in recovered:
            assert_no_fabricated_success(task.status.value, False, f"recover_tasks({task.id})")
        for task in recovered:
            self.refresh_workflow_status(task.workflow_id)
        return recovered
