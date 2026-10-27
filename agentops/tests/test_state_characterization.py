"""Characterization tests for ``agentops.state``.

These pin down behaviour that the rest of the suite only reaches indirectly
(or not at all), so that moving ``StateStore`` around can be checked for "no
behaviour change": the exact schema a fresh database gets, the additive
migrations applied to an old-schema database, the JSON helpers, and the
public methods that no other test calls directly.

If a deliberate schema change makes ``test_fresh_database_schema_matches_snapshot``
fail, regenerate ``tests/fixtures/state_schema.json`` from ``agentops/`` with
``PYTHONPATH=. python tests/test_state_characterization.py --write-schema-snapshot``
(PowerShell: set ``$env:PYTHONPATH = "."`` first) and review the diff.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from agentops.agent_run import AgentRunContext, AgentRunOutcome, AgentRunStatus
from agentops.events import Event, EventType
from agentops.failure import (
    Failure,
    FailureCategory,
    FailureSeverity,
    FailureSource,
    RepairAction,
)
from agentops.state import (
    StateAgentRunObserver,
    StateStore,
    _json,
    _jsonable,
    _loads,
)
from agentops.tasks import Task, TaskStatus
from agentops.verification_model import (
    VerificationCheckClass,
    VerificationCheckSpec,
    VerificationCheckStatus,
    VerificationProfile,
    VerificationProfileMode,
)

SCHEMA_SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "state_schema.json"
CURRENT_SCHEMA_VERSIONS = list(range(1, 10))


def _schema(connection: sqlite3.Connection) -> list[list[str]]:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return [[row[0], row[1], row[2], " ".join((row[3] or "").split())] for row in rows]


def _columns(connection: sqlite3.Connection) -> dict[str, set[tuple[object, ...]]]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    return {
        table: {
            (row[1], row[2], row[3], row[4], row[5])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for table in tables
    }


def _index_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _versions(connection: sqlite3.Connection) -> list[int]:
    return [
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    ]


def _profile() -> VerificationProfile:
    return VerificationProfile(
        name="unit",
        mode=VerificationProfileMode.FAIL_FAST,
        concurrency=1,
        default_timeout_seconds=300,
        checks=(VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),),
    )


# The pre-verification (v0) layout: the three original tables, with no
# verified/verification_run_id columns on tasks and no schema_migrations.
V0_SCHEMA = """
CREATE TABLE workflows (
    id TEXT PRIMARY KEY, description TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
    description TEXT NOT NULL, role TEXT NOT NULL, assigned_agent TEXT,
    dependencies TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL, result TEXT, created_at TEXT NOT NULL,
    started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE events (
    id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, task_id TEXT,
    kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
INSERT INTO workflows VALUES ('wf-old', 'old workflow', 'running', 't0', 't0');
INSERT INTO tasks VALUES (
    'task-old', 'wf-old', 'old task', 'implementation', 'demo', '[]', 'passed',
    1, 3, 'done', 't0', 't0', 't1', 't1'
);
INSERT INTO tasks VALUES (
    'task-dep', 'wf-old', 'dependent task', 'review', NULL, '["task-old"]', 'pending',
    0, 3, NULL, 't0', NULL, NULL, 't0'
);
INSERT INTO events VALUES ('ev-old', 'wf-old', 'task-old', 'task-claimed', 'demo', 't0');
"""

# An interrupted early AgentRun migration left a partial agent_runs table.
# This one keeps the columns the agent_runs indexes name (workflow_id,
# task_id, created_at, status); the cases that lack one of them are built by
# ``_partial_agent_runs`` below.
PARTIAL_AGENT_RUNS = """
CREATE TABLE agent_runs (
    id TEXT PRIMARY KEY, workflow_id TEXT, task_id TEXT, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT INTO agent_runs VALUES ('run-old', 'wf-old', 'task-old', 'completed', 't0', 't1');
"""

AGENT_RUNS_INDEXES = {
    "idx_agent_runs_workflow",
    "idx_agent_runs_task",
    "idx_agent_runs_status",
}

# The partial table's columns, each with the legacy row's value. The
# migration repairs columns before it creates the agent_runs indexes, so a
# table missing any indexed column now opens and is upgraded.
_PARTIAL_COLUMNS = {
    "id": ("TEXT PRIMARY KEY", "'run-old'"),
    "workflow_id": ("TEXT", "'wf-old'"),
    "task_id": ("TEXT", "'task-old'"),
    "status": ("TEXT NOT NULL", "'completed'"),
    "created_at": ("TEXT NOT NULL", "'t0'"),
    "updated_at": ("TEXT NOT NULL", "'t1'"),
}


def _partial_agent_runs(missing: str, with_row: bool) -> str:
    kept = {name: spec for name, spec in _PARTIAL_COLUMNS.items() if name != missing}
    columns = ", ".join(f"{name} {spec[0]}" for name, spec in kept.items())
    script = f"CREATE TABLE agent_runs ({columns});\n"
    if with_row:
        names = ", ".join(kept)
        values = ", ".join(spec[1] for spec in kept.values())
        script += f"INSERT INTO agent_runs ({names}) VALUES ({values});\n"
    return script


class SchemaCharacterizationTests(unittest.TestCase):
    def test_fresh_database_schema_matches_snapshot(self):
        state = StateStore(":memory:")
        try:
            actual = {
                "versions": _versions(state.connection),
                "schema": _schema(state.connection),
            }
        finally:
            state.close()
        expected = json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(actual["versions"], CURRENT_SCHEMA_VERSIONS)
        self.assertEqual(actual, expected)

    def test_file_database_gets_the_same_schema_as_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory) / "state.sqlite")
            try:
                on_disk = _schema(state.connection)
                journal = state.connection.execute("PRAGMA journal_mode").fetchone()[0]
            finally:
                state.close()
        memory = StateStore(":memory:")
        try:
            self.assertEqual(on_disk, _schema(memory.connection))
        finally:
            memory.close()
        self.assertEqual(journal.lower(), "wal")


class OldSchemaMigrationTests(unittest.TestCase):
    def _build(self, directory: str, script: str) -> str:
        database = str(Path(directory) / "old.sqlite")
        connection = sqlite3.connect(database)
        try:
            connection.executescript(script)
            connection.commit()
        finally:
            connection.close()
        return database

    def _fresh_columns_and_indexes(self) -> tuple[dict[str, set[tuple[object, ...]]], set[str]]:
        fresh = StateStore(":memory:")
        try:
            return _columns(fresh.connection), _index_names(fresh.connection)
        finally:
            fresh.close()

    def test_v0_database_is_upgraded_additively_and_keeps_its_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._build(directory, V0_SCHEMA)
            state = StateStore(database)
            try:
                columns = _columns(state.connection)
                fresh_columns, fresh_indexes = self._fresh_columns_and_indexes()
                # Column ensure: the verification columns were appended to tasks.
                task_columns = {column[0] for column in columns["tasks"]}
                self.assertIn("verified", task_columns)
                self.assertIn("verification_run_id", task_columns)
                # Every table and column a fresh store has, the upgraded one has.
                self.assertEqual(columns, fresh_columns)
                self.assertEqual(_index_names(state.connection), fresh_indexes)
                self.assertEqual(_versions(state.connection), CURRENT_SCHEMA_VERSIONS)

                workflow = state.get_workflow("wf-old")
                self.assertIsNotNone(workflow)
                self.assertEqual(workflow.status, TaskStatus.RUNNING)
                old = state.get_task("task-old")
                self.assertEqual(old.status, TaskStatus.PASSED)
                self.assertEqual(old.result, "done")
                self.assertFalse(old.verified)
                self.assertIsNone(old.verification_run_id)
                dependent = state.get_task("task-dep")
                self.assertEqual(dependent.dependencies, ("task-old",))
                self.assertEqual([task.id for task in state.ready_tasks("wf-old")], ["task-dep"])
                self.assertEqual(
                    [event["kind"] for event in state.list_events("wf-old")], ["task-claimed"]
                )
                self.assertEqual(state.count_agent_runs(), 0)
                self.assertEqual(state.count_verification_runs(), 0)
                self.assertEqual(state.count_failures(), 0)
            finally:
                state.close()

            reopened = StateStore(database)
            try:
                self.assertEqual(_versions(reopened.connection), CURRENT_SCHEMA_VERSIONS)
                self.assertEqual(
                    reopened.connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version = 1"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(len(reopened.list_tasks("wf-old")), 2)
            finally:
                reopened.close()

    def test_partial_agent_runs_table_is_repaired_column_by_column(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._build(directory, V0_SCHEMA + PARTIAL_AGENT_RUNS)
            state = StateStore(database)
            try:
                fresh_columns, _ = self._fresh_columns_and_indexes()
                repaired = {column[0] for column in _columns(state.connection)["agent_runs"]}
                expected = {column[0] for column in fresh_columns["agent_runs"]}
                self.assertEqual(repaired, expected)
                run = state.get_agent_run("run-old")
                self.assertEqual(run.status, AgentRunStatus.COMPLETED)
                self.assertEqual(run.attempt, 1)
                self.assertFalse(run.cancelled)
                self.assertEqual(run.command_metadata, {})
                self.assertEqual(run.files_changed, ())
                self.assertEqual(state.count_agent_runs(), 1)
            finally:
                state.close()

    def _assert_upgraded_and_idempotent(self, database: str) -> None:
        fresh_columns, fresh_indexes = self._fresh_columns_and_indexes()
        expected = {column[0] for column in fresh_columns["agent_runs"]}
        for _ in range(2):  # the second open must be a no-op
            state = StateStore(database)
            try:
                journal = state.connection.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(journal.lower(), "wal")
                columns = _columns(state.connection)["agent_runs"]
                self.assertEqual({column[0] for column in columns}, expected)
                self.assertEqual(len(columns), len(expected))
                self.assertEqual(_index_names(state.connection), fresh_indexes)
                self.assertLessEqual(AGENT_RUNS_INDEXES, fresh_indexes)
                self.assertEqual(_versions(state.connection), CURRENT_SCHEMA_VERSIONS)
                self.assertEqual(
                    state.connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version = 1"
                    ).fetchone()[0],
                    1,
                )
            finally:
                state.close()

    def test_partial_agent_runs_missing_a_nullable_indexed_column_opens_and_keeps_rows(self):
        for missing in ("workflow_id", "task_id"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                database = self._build(
                    directory, V0_SCHEMA + _partial_agent_runs(missing, with_row=True)
                )
                self._assert_upgraded_and_idempotent(database)
                state = StateStore(database)
                try:
                    self.assertEqual(state.count_agent_runs(), 1)
                    run = state.get_agent_run("run-old")
                    self.assertEqual(run.status, AgentRunStatus.COMPLETED)
                    self.assertEqual(run.created_at, "t0")
                    self.assertEqual(run.attempt, 1)
                    self.assertFalse(run.cancelled)
                    self.assertEqual(run.relationship.value, "root")
                    # The repaired column is NULL for the legacy row; the
                    # surviving one keeps its value.
                    kept = "task_id" if missing == "workflow_id" else "workflow_id"
                    self.assertIsNone(getattr(run, missing))
                    self.assertEqual(getattr(run, kept), _PARTIAL_COLUMNS[kept][1].strip("'"))
                finally:
                    state.close()

    def test_partial_agent_runs_missing_a_not_null_indexed_column_opens_when_empty(self):
        # status and created_at are TEXT NOT NULL with no default in both the
        # fresh DDL and the repair map, so SQLite can add them only while the
        # table has no rows. An empty partial table now opens and upgrades.
        for missing in ("status", "created_at"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                database = self._build(
                    directory, V0_SCHEMA + _partial_agent_runs(missing, with_row=False)
                )
                self._assert_upgraded_and_idempotent(database)
                state = StateStore(database)
                try:
                    run = state.create_agent_run(
                        AgentRunContext(agent="demo", workflow_id="wf-old", task_id="task-old")
                    )
                    self.assertEqual(state.get_agent_run(run.id).status, AgentRunStatus.PENDING)
                    self.assertEqual(state.count_agent_runs(), 1)
                finally:
                    state.close()

    def test_partial_agent_runs_missing_a_not_null_column_with_rows_still_fails(self):
        # Pinned limitation: the repair map has no default for status or
        # created_at, and SQLite refuses ADD COLUMN ... NOT NULL without a
        # default on a table with rows. The legacy row is left in place.
        for missing in ("status", "created_at"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                database = self._build(
                    directory, V0_SCHEMA + _partial_agent_runs(missing, with_row=True)
                )
                with (
                    unittest.mock.patch("agentops.state.time.sleep"),
                    self.assertRaisesRegex(sqlite3.OperationalError, "NOT NULL column"),
                ):
                    StateStore(database)
                connection = sqlite3.connect(database)
                try:
                    self.assertEqual(
                        connection.execute("SELECT id FROM agent_runs").fetchall(),
                        [("run-old",)],
                    )
                finally:
                    connection.close()

    def test_partial_agent_runs_repair_works_on_an_in_memory_database(self):
        state = StateStore(":memory:")
        try:
            fresh_indexes = _index_names(state.connection)
            expected = {column[0] for column in _columns(state.connection)["agent_runs"]}
            state.connection.execute("PRAGMA foreign_keys = OFF")
            state.connection.executescript(
                "DROP TABLE agent_runs;\n" + _partial_agent_runs("task_id", with_row=True)
            )
            state.connection.execute("PRAGMA foreign_keys = ON")
            for _ in range(2):  # idempotent
                state._migrate_agent_runs()
                state.connection.commit()
                columns = {column[0] for column in _columns(state.connection)["agent_runs"]}
                self.assertEqual(columns, expected)
                self.assertEqual(_index_names(state.connection), fresh_indexes)
            run = state.get_agent_run("run-old")
            self.assertEqual(run.status, AgentRunStatus.COMPLETED)
            self.assertIsNone(run.task_id)
        finally:
            state.close()

    def test_verification_columns_are_not_duplicated_when_already_present(self):
        state = StateStore(":memory:")
        try:
            before = _columns(state.connection)["tasks"]
            state._ensure_task_verification_columns()
            state._migrate_agent_runs()
            state._migrate_verification()
            state.connection.commit()
            self.assertEqual(_columns(state.connection)["tasks"], before)
            self.assertEqual(_versions(state.connection), CURRENT_SCHEMA_VERSIONS)
        finally:
            state.close()


class JsonHelperTests(unittest.TestCase):
    def test_jsonable_coerces_unserializable_values(self):
        class Opaque:
            def __repr__(self) -> str:
                return "Opaque()"

        value = _jsonable({1: [math.nan, math.inf, (True, None)], "o": Opaque(), "f": 1.5})
        self.assertEqual(value, {"1": ["nan", "inf", [True, None]], "o": "Opaque()", "f": 1.5})
        json.dumps(value)

    def test_jsonable_survives_a_broken_repr(self):
        class Broken:
            def __repr__(self) -> str:
                raise RuntimeError("no repr")

        self.assertEqual(_jsonable(Broken()), "<unrepresentable Broken>")

    def test_json_is_compact_sorted_ascii(self):
        self.assertIsNone(_json(None))
        self.assertEqual(_json({"b": 1, "a": "é"}), '{"a":"\\u00e9","b":1}')

    def test_loads_falls_back_to_default(self):
        self.assertEqual(_loads(None, ()), ())
        self.assertEqual(_loads("not json", {}), {})
        self.assertEqual(_loads('{"a": 1}', None), {"a": 1})


class StoreMethodTests(unittest.TestCase):
    def setUp(self):
        self.state = StateStore(":memory:")
        self.workflow_id = self.state.create_workflow("characterization")
        self.task = self.state.add_task(Task("t", "implementation", self.workflow_id))

    def tearDown(self):
        self.state.close()

    def _run(self, **context: object):
        run = self.state.create_agent_run(
            AgentRunContext(
                agent="demo", workflow_id=self.workflow_id, task_id=self.task.id, **context
            )
        )
        self.state.start_agent_run(run.id)
        self.state.mark_agent_run_running(run.id)
        return run

    def test_counts_track_rows(self):
        self.assertEqual(
            (
                self.state.count_events(),
                self.state.count_agent_runs(),
                self.state.count_verification_runs(),
                self.state.count_failures(),
            ),
            (0, 0, 0, 0),
        )
        self.state.record_typed_event(
            Event(workflow_id=self.workflow_id, type=EventType.WORKFLOW_STARTED)
        )
        self._run()
        self.state.create_verification_run(self.workflow_id, self.task.id, _profile())
        self.state.create_failure(
            Failure(
                id="failure-1",
                workflow_id=self.workflow_id,
                task_id=self.task.id,
                agent_run_id=None,
                source=FailureSource.AGENT,
                category=FailureCategory.AGENT_ERROR,
                severity=FailureSeverity.HIGH,
                retryable=True,
                repairable=False,
                recommended_action=RepairAction.STOP,
            )
        )
        self.assertEqual(self.state.count_events(), 1)
        self.assertEqual(self.state.count_agent_runs(), 1)
        self.assertEqual(self.state.count_verification_runs(), 1)
        self.assertEqual(self.state.count_failures(), 1)

    def test_terminal_helpers_set_status_flags_and_classification(self):
        cases = [
            (self.state.cancel_agent_run, AgentRunStatus.CANCELLED, "cancelled", "cancelled"),
            (self.state.timeout_agent_run, AgentRunStatus.TIMED_OUT, "timed_out", "timeout"),
            (self.state.terminate_agent_run, AgentRunStatus.TERMINATED, "terminated", "terminated"),
        ]
        for method, status, flag, classification in cases:
            with self.subTest(status=status):
                run = self._run()
                finished = method(run.id, "why")
                self.assertIs(finished.status, status)
                self.assertTrue(getattr(finished, flag))
                self.assertEqual(finished.error, "why")
                self.assertEqual(finished.failure_classification, classification)
                self.assertIsNotNone(finished.ended_at)

    def test_fail_agent_run_maps_classification_to_status(self):
        cases = [
            ("execution_error", AgentRunStatus.FAILED),
            ("cancelled", AgentRunStatus.CANCELLED),
            ("timeout", AgentRunStatus.TIMED_OUT),
            ("terminated", AgentRunStatus.TERMINATED),
        ]
        for classification, status in cases:
            with self.subTest(classification=classification):
                run = self._run()
                failed = self.state.fail_agent_run(run.id, RuntimeError("boom"), classification)
                self.assertIs(failed.status, status)
                self.assertEqual(failed.error, "boom")
                self.assertEqual(failed.failure_classification, classification)

    def test_finished_run_keeps_its_terminal_status(self):
        run = self._run()
        self.state.finish_agent_run(run.id, AgentRunOutcome(status=AgentRunStatus.COMPLETED))
        again = self.state.cancel_agent_run(run.id, "late")
        self.assertIs(again.status, AgentRunStatus.COMPLETED)

    def test_update_agent_run_only_touches_allowed_metadata(self):
        run = self._run()
        updated = self.state.update_agent_run(
            run.id, files_changed=["a.py"], structured_result={"ok": True}, diff_stat="1 file"
        )
        self.assertEqual(updated.files_changed, ("a.py",))
        self.assertEqual(updated.structured_result, {"ok": True})
        self.assertEqual(updated.diff_stat, "1 file")
        self.assertIs(updated.status, AgentRunStatus.RUNNING)
        with self.assertRaises(ValueError):
            self.state.update_agent_run(run.id, status="succeeded")
        with self.assertRaises(KeyError):
            self.state.update_agent_run("missing", error="x")

    def test_latest_agent_run_filters(self):
        self.assertIsNone(self.state.latest_agent_run(self.workflow_id))
        first = self._run()
        self.state.finish_agent_run(first.id, AgentRunOutcome(status=AgentRunStatus.COMPLETED))
        second = self._run(attempt=2)
        self.assertEqual(self.state.latest_agent_run(self.workflow_id).id, second.id)
        self.assertEqual(
            self.state.latest_agent_run(
                self.workflow_id, self.task.id, statuses=(AgentRunStatus.COMPLETED,)
            ).id,
            first.id,
        )
        self.assertIsNone(self.state.latest_agent_run("other-workflow"))

    def test_get_verification_check_round_trips(self):
        run = self.state.create_verification_run(self.workflow_id, self.task.id, _profile())
        check = self.state.create_verification_check(
            run.id,
            self.workflow_id,
            self.task.id,
            "unit",
            "unit",
            "tests",
            ("python", "-m", "unittest"),
            None,
            60,
            True,
            "sequential",
        )
        fetched = self.state.get_verification_check(check.id)
        self.assertEqual(fetched, check)
        self.assertIs(fetched.status, VerificationCheckStatus.PENDING)
        self.assertEqual(fetched.command, ("python", "-m", "unittest"))
        self.assertEqual(self.state.list_verification_checks(run.id), [check])
        with self.assertRaises(KeyError):
            self.state.get_verification_check("missing")

    def test_agent_cooldowns_set_expire_replace_and_clear(self):
        self.assertEqual(self.state.active_agent_cooldowns(0.0), {})
        self.state.set_agent_cooldown("demo", 100.0, "limit", workflow_id=self.workflow_id)
        self.state.set_agent_cooldown("other", 50.0)
        active = self.state.active_agent_cooldowns(75.0)
        self.assertEqual(list(active), ["demo"])
        self.assertEqual(active["demo"]["until_epoch"], 100.0)
        self.assertEqual(active["demo"]["until_at"], "1970-01-01T00:01:40+00:00")
        self.assertEqual(active["demo"]["reason"], "limit")
        self.state.set_agent_cooldown("demo", 200.0, "again")
        self.assertEqual(self.state.active_agent_cooldowns(150.0)["demo"]["reason"], "again")
        self.state.clear_agent_cooldown("demo")
        self.assertEqual(self.state.active_agent_cooldowns(0.0), {"other": unittest.mock.ANY})

    def test_observer_drives_the_store(self):
        observer = self.state.agent_run_observer()
        self.assertIsInstance(observer, StateAgentRunObserver)
        self.assertIs(observer.state, self.state)
        run = observer.create_run(
            AgentRunContext(agent="demo", workflow_id=self.workflow_id, task_id=self.task.id)
        )
        observer.mark_starting(run.id)
        self.assertIs(self.state.get_agent_run(run.id).status, AgentRunStatus.STARTING)
        observer.mark_running(run.id)
        self.assertIs(self.state.get_agent_run(run.id).status, AgentRunStatus.RUNNING)
        observer.finish_run(run.id, AgentRunOutcome(status=AgentRunStatus.COMPLETED, exit_code=0))
        self.assertIs(self.state.get_agent_run(run.id).status, AgentRunStatus.COMPLETED)

        failing = observer.create_run(AgentRunContext(agent="demo", workflow_id=self.workflow_id))
        observer.mark_starting(failing.id)
        observer.fail_run(failing.id, RuntimeError("bad"), "timeout")
        failed = self.state.get_agent_run(failing.id)
        self.assertIs(failed.status, AgentRunStatus.TIMED_OUT)
        self.assertEqual(failed.error, "bad")


# Every attribute StateStore had before the package split, private helpers
# included: callers and tests reach for some of them directly.
STATE_STORE_ATTRIBUTES = {
    "_agent_run_from_row",
    "_artifact_from_row",
    "_ensure_task_verification_columns",
    "_event",
    "_failure_from_row",
    "_initialize",
    "_initialize_once",
    "_loads_structured_evidence",
    "_migrate_agent_cooldowns",
    "_migrate_agent_runs",
    "_migrate_artifacts",
    "_migrate_failure_evidence",
    "_migrate_failures",
    "_migrate_hot_path_indexes",
    "_migrate_typed_events",
    "_migrate_verification",
    "_migrate_worktree_refs",
    "_notify_bus",
    "_open_with_retry",
    "_status_value",
    "_task_from_row",
    "_transition",
    "_typed_event_from_row",
    "_verification_check_from_row",
    "_verification_report_from_row",
    "_verification_run_from_row",
    "_workflow_from_row",
    "_worktree_ref_from_row",
    "active_agent_cooldowns",
    "add_task",
    "agent_run_observer",
    "cancel_agent_run",
    "claim_task",
    "clear_agent_cooldown",
    "close",
    "count_agent_runs",
    "count_events",
    "count_failures",
    "count_interrupted_work",
    "count_verification_runs",
    "count_workflows",
    "create_agent_run",
    "create_artifact_record",
    "create_failure",
    "create_verification_check",
    "create_verification_report",
    "create_verification_run",
    "create_workflow",
    "delete_agent_run",
    "delete_artifact_record",
    "delete_failure",
    "delete_task",
    "delete_verification_run",
    "delete_workflow",
    "event",
    "fail_agent_run",
    "find_worktree_ref_by_path",
    "finish_agent_run",
    "finish_verification_check",
    "finish_verification_run",
    "get_agent_run",
    "get_artifact",
    "get_failure",
    "get_task",
    "get_verification_check",
    "get_verification_report_by_run",
    "get_verification_run",
    "get_workflow",
    "get_worktree_ref",
    "latest_agent_run",
    "latest_workflow",
    "list_agent_runs",
    "list_artifacts",
    "list_events",
    "list_failures",
    "list_tasks",
    "list_verification_checks",
    "list_verification_runs",
    "list_workflows",
    "list_worktree_refs",
    "mark_agent_run_running",
    "query_events",
    "ready_tasks",
    "record_typed_event",
    "record_worktree_ref",
    "recover_agent_runs",
    "recover_all",
    "recover_tasks",
    "recover_verification_runs",
    "refresh_workflow_status",
    "rename_workflow",
    "set_agent_cooldown",
    "start_agent_run",
    "start_verification_check",
    "start_verification_run",
    "terminate_agent_run",
    "timeout_agent_run",
    "update_agent_run",
    "update_task",
}

# Every name importable from ``agentops.state`` before the split. Code and
# tests import StateStore and the JSON helpers; the rest are re-exports that
# ``from agentops.state import X`` has always resolved, and
# ``agentops.state.tempfile`` is a patch target in test_state.
MODULE_NAMES = {
    "AgentRun",
    "AgentRunContext",
    "AgentRunOutcome",
    "AgentRunRelationship",
    "AgentRunStatus",
    "Artifact",
    "ArtifactKind",
    "ArtifactMetadata",
    "Event",
    "EventBus",
    "EventSeverity",
    "EventType",
    "Failure",
    "FailureCategory",
    "FailureSeverity",
    "FailureSource",
    "Path",
    "RecoveryState",
    "RepairAction",
    "StateAgentRunObserver",
    "StateStore",
    "StateTransitionError",
    "TERMINAL_STATUSES",
    "Task",
    "TaskStatus",
    "VerificationCheck",
    "VerificationCheckClass",
    "VerificationCheckStatus",
    "VerificationExecutionPolicy",
    "VerificationProfile",
    "VerificationProfileMode",
    "VerificationReport",
    "VerificationReportStatus",
    "VerificationRun",
    "VerificationRunStatus",
    "Workflow",
    "WorktreeRef",
    "_json",
    "_jsonable",
    "_jsonable_key",
    "_loads",
    "_safe_json_repr",
    "annotations",
    "assert_agent_run_transition",
    "assert_no_fabricated_success",
    "coerce_event",
    "datetime",
    "json",
    "profile_to_dict",
    "sqlite3",
    "tempfile",
    "threading",
    "time",
    "timezone",
    "utc_now",
    "uuid4",
}


class ImportSurfaceTests(unittest.TestCase):
    def test_state_store_keeps_every_attribute(self):
        attributes = {name for name in dir(StateStore) if name not in dir(object)}
        self.assertEqual(STATE_STORE_ATTRIBUTES - attributes, set())
        self.assertEqual(
            {name for name in attributes if not name.startswith("__")} - STATE_STORE_ATTRIBUTES,
            set(),
        )

    def test_module_keeps_every_importable_name(self):
        import agentops.state as state_module

        self.assertEqual(MODULE_NAMES - set(vars(state_module)), set())
        self.assertEqual(state_module.__all__, ["StateAgentRunObserver", "StateStore"])


def _write_schema_snapshot() -> None:
    state = StateStore(":memory:")
    try:
        payload = {"versions": _versions(state.connection), "schema": _schema(state.connection)}
    finally:
        state.close()
    SCHEMA_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_SNAPSHOT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if "--write-schema-snapshot" in sys.argv:
        _write_schema_snapshot()
    else:
        unittest.main()
