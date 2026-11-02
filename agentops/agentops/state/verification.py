"""Verification Kernel persistence: runs, checks, reports."""

from __future__ import annotations

import sqlite3
from uuid import uuid4

from ..execution_model import (
    assert_no_fabricated_success,
)
from ..tasks import utc_now
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

from ._json import _json, _loads


class VerificationMixin:
    def count_verification_runs(self) -> int:
        """Total verification-run rows; lets callers window the newest runs."""
        with self._lock:
            row = self.connection.execute(
                "SELECT COUNT(*) AS total FROM verification_runs"
            ).fetchone()
        return int(row["total"])

    def delete_verification_run(self, run_id: str) -> bool:
        """Remove a verification run with its checks and report."""
        with self._lock:
            self.connection.execute("DELETE FROM verification_checks WHERE run_id = ?", (run_id,))
            self.connection.execute("DELETE FROM verification_reports WHERE run_id = ?", (run_id,))
            cursor = self.connection.execute(
                "DELETE FROM verification_runs WHERE id = ?", (run_id,)
            )
            self.connection.commit()
            return cursor.rowcount == 1

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
                    run_id,
                    workflow_id,
                    task_id,
                    profile.name,
                    _json(profile_to_dict(profile)),
                    profile.mode,
                    profile.concurrency,
                    VerificationRunStatus.PENDING,
                    source_agent_run_id,
                    now,
                    now,
                    now,
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
            raise ValueError(
                "Verification run list limit and offset must be non-negative integers."
            )
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
            params.append(
                status
                if isinstance(status, VerificationRunStatus)
                else VerificationRunStatus(status)
            )
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
                (
                    VerificationRunStatus.RUNNING,
                    utc_now(),
                    utc_now(),
                    run_id,
                    VerificationRunStatus.PENDING,
                ),
            )
            if cursor.rowcount != 1:
                current = self.get_verification_run(run_id)
                if current.status is not VerificationRunStatus.RUNNING:
                    raise ValueError(
                        f"Invalid verification run transition to running from {current.status.value}."
                    )
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
                (
                    status,
                    now,
                    duration_seconds,
                    total_checks,
                    passed_checks,
                    failed_checks,
                    skipped_checks,
                    required_failures,
                    overall_status,
                    now,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown verification run: {run_id}")
            row = self.connection.execute(
                "SELECT workflow_id, task_id FROM verification_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row["workflow_id"] is not None:
                self._event(
                    row["workflow_id"], row["task_id"], "verification-run-finished", status.value
                )
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
                (
                    check_id,
                    run_id,
                    workflow_id,
                    task_id,
                    profile_name,
                    name,
                    check_class,
                    _json(command),
                    working_directory,
                    timeout_seconds,
                    int(required),
                    policy,
                    VerificationCheckStatus.PENDING,
                    now,
                    now,
                ),
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
                (
                    VerificationCheckStatus.RUNNING,
                    utc_now(),
                    utc_now(),
                    check_id,
                    VerificationCheckStatus.PENDING,
                ),
            )
            if cursor.rowcount != 1:
                current = self.get_verification_check(check_id)
                if current.status is not VerificationCheckStatus.RUNNING:
                    raise ValueError(
                        f"Invalid verification check transition to running from {current.status.value}."
                    )
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
                (
                    status,
                    now,
                    duration_seconds,
                    exit_code,
                    stdout_path,
                    stderr_path,
                    failure_reason,
                    now,
                    check_id,
                ),
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
                (
                    report.id,
                    report.run_id,
                    report.workflow_id,
                    report.task_id,
                    report.profile_name,
                    report.total_checks,
                    report.passed_checks,
                    report.failed_checks,
                    report.skipped_checks,
                    report.required_failures,
                    report.duration_seconds,
                    report.overall_status,
                    report.generated_at,
                ),
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
                        (
                            VerificationCheckStatus.PENDING,
                            VerificationCheckStatus.RUNNING,
                            *run_ids,
                        ),
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
                    (
                        VerificationCheckStatus.FAILED,
                        utc_now(),
                        "interrupted",
                        utc_now(),
                        check["id"],
                    ),
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
                    (
                        VerificationRunStatus.FAILED,
                        utc_now(),
                        VerificationReportStatus.FAILED,
                        counts["total"] or 0,
                        counts["passed"] or 0,
                        counts["failed"] or 0,
                        counts["skipped"] or 0,
                        counts["required_failures"] or 0,
                        utc_now(),
                        run["id"],
                    ),
                )
                if run["workflow_id"] is not None:
                    self._event(
                        run["workflow_id"], run["task_id"], "verification-run-recovered", run["id"]
                    )
                recovered.append(self.get_verification_run(run["id"]))
            if runs or checks:
                self.connection.commit()
        # A1 honesty post-condition: recovery must never mint success.
        for run in recovered:
            assert_no_fabricated_success(
                run.status.value, False, f"recover_verification_runs({run.id})"
            )
        return recovered
