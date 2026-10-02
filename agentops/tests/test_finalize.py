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

    def _merge_failure_task(self, message: str):
        manager = MagicMock()
        manager.commit_changes.return_value = True
        manager.merge.side_effect = GitError(message)
        result = finalize_worktree(manager, self.state, _worktree(), "do thing", self.workflow_id, 2)
        self.assertEqual(result.conflict_error, message)
        debugging = [task for task in self.state.list_tasks(self.workflow_id) if task.role == "debugging"]
        self.assertEqual(len(debugging), 1)
        return debugging[0].description

    def test_dirty_base_gets_dirty_worktree_task_not_conflict_task(self):
        text = self._merge_failure_task(
            "Base worktree has uncommitted changes; refusing to merge agent worktree.")
        self.assertNotIn("Resolve Git merge conflict", text)
        self.assertIn("dirty base worktree", text)

    def test_changed_base_gets_refused_task_not_conflict_task(self):
        text = self._merge_failure_task(
            "Base branch changed from 'main'; refusing to merge.")
        self.assertNotIn("Resolve Git merge conflict", text)
        self.assertIn("was refused", text)

    def test_changed_commit_gets_refused_task_not_conflict_task(self):
        text = self._merge_failure_task(
            "Base commit changed while the agent worked; refusing to merge.")
        self.assertNotIn("Resolve Git merge conflict", text)
        self.assertIn("was refused", text)

    def test_real_conflict_keeps_conflict_task(self):
        text = self._merge_failure_task(
            "CONFLICT (content): Merge conflict in file.txt\n"
            "Automatic merge failed; fix conflicts and then commit the result.")
        self.assertIn("Resolve Git merge conflict", text)

    def test_generic_git_failure_gets_generic_task_not_conflict_task(self):
        text = self._merge_failure_task("Could not inspect base worktree status.")
        self.assertNotIn("Resolve Git merge conflict", text)
        self.assertIn("Git merge failed", text)
