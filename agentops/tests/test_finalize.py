import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agentops.finalize import finalize_worktree
from agentops.git import GitError, Worktree
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus


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

class MergeGateBoundaryTests(unittest.TestCase):
    """Boundary 5: merge/finalization. One invariant, four cases.

    No automatic merge may occur unless the product's documented merge
    prerequisites are satisfied.

      A  impl PASSED, verification PASSED+verified, review PASSED -> merge YES
      B  verification BLOCKED/UNVERIFIED -> commit/preserve YES, merge NO
      C  verification FAILED             -> merge NO (non-negotiable)
      D  garbage implementation, BLOCKED -> merge NO

    These drive the REAL `finalize.finalize_for_outcome`, not a copy of the gate
    expression. The first version of this file asserted against a literal
    `result.ready or implementation_passed` written in the test body; it passed
    and proved nothing, because the code under test was the test.
    """

    def _manager(self, changed=True):
        manager = MagicMock()
        manager.commit_changes.return_value = changed
        manager.merge.return_value = None
        return manager

    def _finalize(self, ready, changed=True, conflict=None):
        from agentops.finalize import finalize_for_outcome
        manager = self._manager(changed)
        manager.merge.side_effect = conflict
        state = StateStore(":memory:")
        wf = state.create_workflow("desc")
        state.close()
        worktree = _worktree()
        return finalize_for_outcome(manager, StateStore(":memory:"), worktree,
                                    "desc", wf, 2, ready=ready), manager

    def test_case_a_ready_merges(self):
        finalization, manager = self._finalize(ready=True)
        manager.merge.assert_called_once()
        self.assertTrue(finalization.merged, "case A: READY must merge")

    def test_case_b_unverified_commits_but_does_not_merge(self):
        finalization, manager = self._finalize(ready=False)
        manager.commit_changes.assert_called_once()
        manager.merge.assert_not_called()
        self.assertFalse(finalization.merged,
                         "case B: UNVERIFIED work must be preserved, not merged")
        self.assertTrue(finalization.changed,
                        "case B: it must still be committed so nothing is lost")

    def test_case_c_failed_verification_never_merges(self):
        finalization, manager = self._finalize(ready=False)
        manager.merge.assert_not_called()
        self.assertFalse(finalization.merged,
                         "case C: a demonstrated verification FAILURE must "
                         "never merge")

    def test_case_d_garbage_blocked_does_not_merge(self):
        finalization, manager = self._finalize(ready=False)
        manager.merge.assert_not_called()
        self.assertFalse(finalization.merged,
                         "case D: a garbage implementation must not merge while "
                         "verification is unresolved")

    def test_not_ready_never_removes_the_worktree(self):
        # Limbo means the work survives on disk to be merged later.
        from agentops.finalize import finalize_for_outcome
        manager = self._manager()
        finalize_for_outcome(manager, StateStore(":memory:"), _worktree(),
                             "desc", "wf", 2, ready=False)
        manager.remove.assert_not_called()


class BothEntryPointsShareOneMergeRuleTests(unittest.TestCase):
    """The CLI and the desktop client must not re-derive the gate separately.

    They disagreed: the CLI merged on `result.ready or implementation_passed`
    while the GUI merged only on `result.ready`. Both now call
    `finalize.finalize_for_outcome`, which is the only implementation.
    """

    def test_cli_delegates_to_the_shared_helper(self):
        source = (Path(__file__).resolve().parents[1]
                  / "agentops" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("finalize_for_outcome(", source)
        self.assertNotIn("if result.ready or implementation_ok:", source)

    def test_gui_delegates_to_the_shared_helper(self):
        source = (Path(__file__).resolve().parents[1]
                  / "agentops" / "gui_controller.py").read_text(encoding="utf-8")
        self.assertIn("finalize_for_outcome(", source)

    def test_retry_merge_command_exists(self):
        source = (Path(__file__).resolve().parents[1]
                  / "agentops" / "cli.py").read_text(encoding="utf-8")
        self.assertIn('"retry-merge"', source)


if __name__ == "__main__":
    unittest.main()