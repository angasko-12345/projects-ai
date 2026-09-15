import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agentops.finalize import finalize_worktree
from agentops.git import GitError, Worktree
from agentops.state import StateStore
from agentops.tasks import TaskStatus


def _worktree() -> Worktree:
    return Worktree(Path("C:/repo"), Path("C:/wt"), "agentops/demo-12345678", "main", "abc123")


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.state = StateStore(":memory:")
        self.workflow_id = self.state.create_workflow("finalize me")

    def tearDown(self):
        self.state.close()

    def test_no_changes_skips_merge(self):
        manager = MagicMock()
        manager.commit_changes.return_value = False
        result = finalize_worktree(manager, self.state, _worktree(), "do thing", self.workflow_id, 2)
        self.assertFalse(result.changed)
        self.assertFalse(result.merged)
        self.assertIsNone(result.conflict_error)
        manager.merge.assert_not_called()

    def test_changes_merge_cleanly(self):
        manager = MagicMock()
        manager.commit_changes.return_value = True
        result = finalize_worktree(manager, self.state, _worktree(), "do thing", self.workflow_id, 2)
        self.assertTrue(result.changed)
        self.assertTrue(result.merged)
        manager.merge.assert_called_once()

    def test_conflict_preserves_worktree_and_records_debugging_task(self):
        manager = MagicMock()
        manager.commit_changes.return_value = True
        manager.merge.side_effect = GitError("boom")
        result = finalize_worktree(manager, self.state, _worktree(), "do thing", self.workflow_id, 2)
        self.assertTrue(result.changed)
        self.assertFalse(result.merged)
        self.assertIn("boom", result.conflict_error or "")
        debugging = [task for task in self.state.list_tasks(self.workflow_id) if task.role == "debugging"]
        self.assertEqual(len(debugging), 1)
        self.assertEqual(debugging[0].status, TaskStatus.PENDING)
