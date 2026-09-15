"""Phase 1 (Storage DTOs) + Phase 3 (persisted worktree refs) tests."""

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from agentops.git import WorktreeRef
from agentops.gui_controller import (
    serialize_task,
    serialize_workflow,
    serialize_worktree_ref,
)
from agentops.state import StateStore
from agentops.tasks import Task, Workflow


class WorkflowDtoTests(unittest.TestCase):
    def setUp(self):
        self.state = StateStore(":memory:")

    def tearDown(self):
        self.state.close()

    def test_workflow_getters_return_dtos_not_rows(self):
        wid = self.state.create_workflow("dto workflow")
        fetched = self.state.get_workflow(wid)
        self.assertIsInstance(fetched, Workflow)
        assert fetched is not None
        self.assertEqual(fetched.id, wid)
        self.assertEqual(fetched.description, "dto workflow")
        latest = self.state.latest_workflow()
        self.assertIsInstance(latest, Workflow)
        listed = self.state.list_workflows()
        self.assertTrue(all(isinstance(w, Workflow) for w in listed))

    def test_controller_serializers_emit_plain_dicts(self):
        workflow = Workflow(id="w1", description="d")
        payload = serialize_workflow(workflow)
        self.assertEqual(payload["id"], "w1")
        self.assertIsInstance(payload, dict)
        task = Task("do", "implementation", "w1")
        serialized = serialize_task(task)
        self.assertEqual(serialized["workflow_id"], "w1")
        self.assertIsInstance(serialized["dependencies"], list)
        ref = WorktreeRef(id="r1", workflow_id="w1", path="/tmp/x",
                          branch="agentops/a", base_branch="main", base_commit="abc")
        ref_payload = serialize_worktree_ref(ref)
        self.assertEqual(ref_payload["base_branch"], "main")

    def test_controller_workflow_payload_has_serialized_tasks_and_ref(self):
        import tempfile
        from agentops.config import AppConfig
        from agentops.gui_controller import AgentOpsController
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            state = StateStore(controller._state_path(root))
            try:
                wid = state.create_workflow("payload check")
                state.add_task(Task("work", "implementation", wid, max_attempts=1))
                state.record_worktree_ref(WorktreeRef(
                    id=str(uuid4()), workflow_id=wid, path=str(root / "wt"),
                    branch="agentops/x", base_branch="main", base_commit="deadbeef",
                ))
            finally:
                state.close()
            detail = controller.get_workflow(root, wid)
            assert detail is not None
            self.assertIsInstance(detail["tasks"][0], dict)
            self.assertIsNotNone(detail["worktree_ref"])
            latest = controller.latest_workflow(root)
            assert latest is not None
            self.assertIsInstance(latest["tasks"][0], dict)


class WorktreeRefTests(unittest.TestCase):
    def setUp(self):
        self.state = StateStore(":memory:")
        self.workflow_id = self.state.create_workflow("wt workflow")

    def tearDown(self):
        self.state.close()

    def test_record_and_fetch_by_workflow(self):
        ref = self.state.record_worktree_ref(WorktreeRef(
            id=str(uuid4()), workflow_id=self.workflow_id, path="/tmp/wt-1",
            branch="agentops/slug-1234", base_branch="main", base_commit="abc123",
        ))
        self.assertEqual(ref.workflow_id, self.workflow_id)
        fetched = self.state.get_worktree_ref(self.workflow_id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.branch, "agentops/slug-1234")
        self.assertEqual(fetched.base_commit, "abc123")

    def test_find_by_path_powers_retry(self):
        self.state.record_worktree_ref(WorktreeRef(
            id=str(uuid4()), workflow_id=self.workflow_id, path="/tmp/wt-retry",
            branch="agentops/retry-1", base_branch="main", base_commit="base000",
        ))
        found = self.state.find_worktree_ref_by_path("/tmp/wt-retry")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.base_branch, "main")
        self.assertIsNone(self.state.find_worktree_ref_by_path("/tmp/missing"))

    def test_missing_workflow_has_no_ref(self):
        self.assertIsNone(self.state.get_worktree_ref("no-such-workflow"))
        self.assertEqual(self.state.list_worktree_refs("no-such-workflow"), [])

    def test_migration_is_idempotent_and_versioned(self):
        self.state._migrate_worktree_refs()
        self.state._migrate_worktree_refs()
        row = self.state.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 6").fetchone()
        self.assertIsNotNone(row)
        # Round-trip survives close/reopen.
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "s.sqlite"
            store = StateStore(db)
            try:
                wid = store.create_workflow("persist me")
                store.record_worktree_ref(WorktreeRef(
                    id="r-persist", workflow_id=wid, path="/tmp/p",
                    branch="agentops/p", base_branch="main", base_commit="c0ffee",
                ))
            finally:
                store.close()
            reopened = StateStore(db)
            try:
                ref = reopened.get_worktree_ref(wid)
                self.assertIsNotNone(ref)
                assert ref is not None
                self.assertEqual(ref.base_commit, "c0ffee")
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
