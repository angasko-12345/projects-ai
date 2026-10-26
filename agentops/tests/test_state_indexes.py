import sqlite3
import tempfile
import unittest
from pathlib import Path

from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus


def _index_names(state: StateStore, table: str) -> set[str]:
    return {
        row[1]
        for row in state.connection.execute(f"PRAGMA index_list({table})").fetchall()
    }


def _plan_details(state: StateStore, query: str, params: tuple[object, ...]) -> list[str]:
    return [
        row[3]
        for row in state.connection.execute(
            "EXPLAIN QUERY PLAN " + query, params
        ).fetchall()
    ]


def _seed_store(state: StateStore) -> tuple[str, str]:
    first = state.create_workflow("first workflow")
    second = state.create_workflow("second workflow")
    state.connection.execute(
        "UPDATE workflows SET status = ? WHERE id = ?",
        (TaskStatus.RUNNING, first),
    )
    state.connection.commit()
    state.add_task(Task("first-task", "implementation", first))
    state.add_task(Task("second-task", "review", first))
    state.add_task(Task("third-task", "review", second))
    return first, second


def _query_results(
    state: StateStore, workflow_id: str
) -> tuple[list[str], list[str], tuple[int, int]]:
    task_ids = [task.id for task in state.list_tasks(workflow_id)]
    workflow_ids = [workflow.id for workflow in state.list_workflows(status=TaskStatus.RUNNING)]
    counts = (state.count_workflows(), state.count_workflows(status=TaskStatus.RUNNING))
    return task_ids, workflow_ids, counts


class StateIndexMigrationTests(unittest.TestCase):
    def test_clean_database_creates_only_the_audited_hot_path_indexes(self):
        state = StateStore(":memory:")
        try:
            self.assertIn("idx_tasks_workflow_created", _index_names(state, "tasks"))
            self.assertIn("idx_workflows_status", _index_names(state, "workflows"))
            versions = [
                row[0]
                for row in state.connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            self.assertEqual(versions, list(range(1, 10)))
            self.assertIn(
                "USING INDEX idx_tasks_workflow_created",
                " ".join(
                    _plan_details(
                        state,
                        "SELECT * FROM tasks WHERE workflow_id = ? ORDER BY created_at",
                        ("workflow",),
                    )
                ),
            )
            self.assertIn(
                "USING INDEX idx_workflows_status",
                " ".join(
                    _plan_details(
                        state,
                        "SELECT * FROM workflows WHERE status = ? ORDER BY rowid DESC LIMIT ? OFFSET ?",
                        (TaskStatus.RUNNING, 50, 0),
                    )
                ),
            )
            self.assertIn(
                "USING COVERING INDEX idx_workflows_status",
                " ".join(
                    _plan_details(
                        state,
                        "SELECT COUNT(*) AS total FROM workflows WHERE status = ?",
                        (TaskStatus.RUNNING,),
                    )
                ),
            )
        finally:
            state.close()

    def test_upgrade_from_every_supported_version_preserves_query_results(self):
        for version in range(9):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                database = str(Path(directory) / "state.sqlite")
                state = StateStore(database)
                try:
                    workflow_id, _ = _seed_store(state)
                    expected = _query_results(state, workflow_id)
                    state.connection.execute("DROP INDEX IF EXISTS idx_tasks_workflow_created")
                    state.connection.execute("DROP INDEX IF EXISTS idx_workflows_status")
                    state.connection.execute(
                        "DELETE FROM schema_migrations WHERE version > ?", (version,)
                    )
                    state.connection.commit()
                finally:
                    state.close()

                upgraded = StateStore(database)
                try:
                    self.assertEqual(_query_results(upgraded, workflow_id), expected)
                    self.assertIn("idx_tasks_workflow_created", _index_names(upgraded, "tasks"))
                    self.assertIn("idx_workflows_status", _index_names(upgraded, "workflows"))
                    versions = [
                        row[0]
                        for row in upgraded.connection.execute(
                            "SELECT version FROM schema_migrations ORDER BY version"
                        ).fetchall()
                    ]
                    self.assertEqual(versions, list(range(1, 10)))
                finally:
                    upgraded.close()

    def test_repeated_migration_is_idempotent_and_preserves_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "state.sqlite")
            first = StateStore(database)
            try:
                workflow_id, _ = _seed_store(first)
                expected = _query_results(first, workflow_id)
            finally:
                first.close()

            second = StateStore(database)
            try:
                self.assertEqual(_query_results(second, workflow_id), expected)
                self.assertEqual(
                    second.connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version = 8"
                    ).fetchone()[0],
                    1,
                )
            finally:
                second.close()

            third = StateStore(database)
            try:
                self.assertEqual(_query_results(third, workflow_id), expected)
                self.assertEqual(
                    third.connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version = 8"
                    ).fetchone()[0],
                    1,
                )
            finally:
                third.close()

    def test_legacy_database_without_hot_path_indexes_keeps_results_after_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "legacy.sqlite")
            state = StateStore(database)
            try:
                workflow_id, _ = _seed_store(state)
                expected = _query_results(state, workflow_id)
                state.connection.execute("DROP INDEX IF EXISTS idx_tasks_workflow_created")
                state.connection.execute("DROP INDEX IF EXISTS idx_workflows_status")
                state.connection.commit()
            finally:
                state.close()

            legacy = sqlite3.connect(database)
            try:
                self.assertEqual(
                    legacy.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index' "
                        "AND name IN ('idx_tasks_workflow_created', 'idx_workflows_status')"
                    ).fetchall(),
                    [],
                )
            finally:
                legacy.close()

            upgraded = StateStore(database)
            try:
                self.assertEqual(_query_results(upgraded, workflow_id), expected)
                self.assertIn("idx_tasks_workflow_created", _index_names(upgraded, "tasks"))
                self.assertIn("idx_workflows_status", _index_names(upgraded, "workflows"))
            finally:
                upgraded.close()


if __name__ == "__main__":
    unittest.main()
