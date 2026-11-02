"""AgentRun persistence and lifecycle transitions, plus per-agent cooldowns."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from ..agent_run import (
    AgentRun,
    AgentRunContext,
    AgentRunOutcome,
    AgentRunRelationship,
    AgentRunStatus,
    TERMINAL_STATUSES,
)
from ..execution_model import (
    StateTransitionError,
    assert_agent_run_transition,
    assert_no_fabricated_success,
)
from ..tasks import utc_now

from ._json import _json, _loads

if TYPE_CHECKING:
    from . import StateStore


class AgentRunMixin:
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

    def count_agent_runs(self) -> int:
        """Total agent-run rows; lets callers window the newest runs."""
        with self._lock:
            row = self.connection.execute("SELECT COUNT(*) AS total FROM agent_runs").fetchone()
        return int(row["total"])

    def delete_agent_run(self, run_id: str) -> bool:
        """Remove an agent run with its linked verification runs.

        Linked verifications cascade (their checks and reports go with
        them); failure rows keep plain-text references and stay readable.
        Refuses (False) when follow-up runs still reference this one —
        deleting a retried run out from under its retry would orphan the
        chain, so callers surface that instead.
        """
        with self._lock:
            child = self.connection.execute(
                """SELECT id FROM agent_runs WHERE parent_run_id = ?
                   OR retry_of = ? OR repair_of = ? LIMIT 1""",
                (run_id, run_id, run_id),
            ).fetchone()
            if child is not None:
                return False
            linked = [
                row["id"]
                for row in self.connection.execute(
                    "SELECT id FROM verification_runs WHERE source_agent_run_id = ?",
                    (run_id,),
                ).fetchall()
            ]
            for verification_id in linked:
                self.connection.execute(
                    "DELETE FROM verification_checks WHERE run_id = ?",
                    (verification_id,),
                )
                self.connection.execute(
                    "DELETE FROM verification_reports WHERE run_id = ?",
                    (verification_id,),
                )
                self.connection.execute(
                    "DELETE FROM verification_runs WHERE id = ?", (verification_id,)
                )
            cursor = self.connection.execute("DELETE FROM agent_runs WHERE id = ?", (run_id,))
            self.connection.commit()
            return cursor.rowcount == 1

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
            row = self.connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
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
            row = self.connection.execute(
                "SELECT status FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown AgentRun: {run_id}")
            current = AgentRunStatus(row["status"])
            if current is target:
                # Same-state is a documented no-op: no write, no event.
                return self.get_agent_run(run_id)
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
            row = self.connection.execute(
                "SELECT status, started_at FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
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

    def fail_agent_run(
        self, run_id: str, error: BaseException, classification: str = "execution_error"
    ) -> AgentRun:
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
            "stdout_path",
            "stderr_path",
            "log_path",
            "files_changed",
            "diff_stat",
            "error",
            "failure_classification",
            "structured_result",
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
                    (
                        AgentRunStatus.PENDING,
                        AgentRunStatus.STARTING,
                        AgentRunStatus.RUNNING,
                        workflow_id,
                    ),
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
        # A1 honesty post-condition: recovery must never mint success.
        for run in recovered:
            assert_no_fabricated_success(run.status.value, False, f"recover_agent_runs({run.id})")
        return recovered

    def agent_run_observer(self) -> StateAgentRunObserver:
        return StateAgentRunObserver(self)

    def set_agent_cooldown(
        self,
        agent: str,
        until_epoch: float,
        reason: str | None = None,
        *,
        failure_id: str | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Record that ``agent`` must not be routed to before ``until_epoch``."""
        until_at = datetime.fromtimestamp(float(until_epoch), tz=timezone.utc).isoformat()
        with self._lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO agent_cooldowns (
                    agent, until_epoch, until_at, reason, failure_id, workflow_id, task_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(agent),
                    float(until_epoch),
                    until_at,
                    reason,
                    failure_id,
                    workflow_id,
                    task_id,
                    utc_now(),
                ),
            )
            self.connection.commit()

    def active_agent_cooldowns(self, now_epoch: float) -> dict[str, dict[str, object]]:
        """Cooldowns still in force at ``now_epoch``, keyed by agent."""
        with self._lock:
            rows = self.connection.execute(
                "SELECT agent, until_epoch, until_at, reason FROM agent_cooldowns "
                "WHERE until_epoch > ? ORDER BY agent",
                (float(now_epoch),),
            ).fetchall()
        return {
            row["agent"]: {
                "until_epoch": float(row["until_epoch"]),
                "until_at": row["until_at"],
                "reason": row["reason"],
            }
            for row in rows
        }

    def clear_agent_cooldown(self, agent: str) -> None:
        with self._lock:
            self.connection.execute("DELETE FROM agent_cooldowns WHERE agent = ?", (str(agent),))
            self.connection.commit()


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
