import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentops.git import GitError
from agentops.gui_controller import AgentOpsController


class OperationIdentityTests(unittest.TestCase):
    def test_stale_end_does_not_clear_newer_operation(self):
        controller = AgentOpsController()
        first = controller._begin_operation()
        controller._end_operation(first)
        second = controller._begin_operation()
        self.assertIsNot(first, second)
        self.assertIs(second, controller._cancel_event)
        # A stale worker finishing after a newer operation began must not
        # clear the newer operation's active state.
        controller._end_operation(first)
        self.assertTrue(controller._active)
        controller._end_operation(second)
        self.assertFalse(controller._active)

    def test_cancel_targets_current_operation_event(self):
        controller = AgentOpsController()
        first = controller._begin_operation()
        controller._end_operation(first)
        second = controller._begin_operation()
        controller.cancel()
        self.assertTrue(second.is_set())
        self.assertFalse(first.is_set())

    def test_second_begin_while_active_still_raises(self):
        controller = AgentOpsController()
        controller._begin_operation()
        with self.assertRaises(RuntimeError):
            controller._begin_operation()
        self.assertTrue(controller._active)


class RootCacheSingleFlightTests(unittest.TestCase):
    def test_concurrent_misses_spawn_one_git_lookup(self):
        controller = AgentOpsController()
        entered = threading.Event()
        release = threading.Event()
        started = threading.Event()

        def slow_root(directory):
            entered.set()
            release.wait(5)
            return Path("C:/repo/rt")

        manager = MagicMock()
        manager.repository_root.side_effect = slow_root
        results: dict[str, Path] = {}

        with patch("agentops.gui_controller.GitWorktreeManager", return_value=manager):
            thread_a = threading.Thread(
                target=lambda: results.__setitem__(
                    "a", controller._operation_root("C:/ws")))
            thread_a.start()
            self.assertTrue(entered.wait(5))

            thread_b = threading.Thread(
                target=lambda: (started.set(),
                                results.__setitem__(
                                    "b", controller._operation_root("C:/ws"))))
            thread_b.start()
            self.assertTrue(started.wait(5))
            release.set()
            thread_a.join(5)
            thread_b.join(5)
            self.assertFalse(thread_a.is_alive())
            self.assertFalse(thread_b.is_alive())

        self.assertEqual(manager.repository_root.call_count, 1)
        self.assertEqual(results.get("a"), Path("C:/repo/rt"))
        self.assertEqual(results.get("b"), Path("C:/repo/rt"))

    def test_git_failure_returns_directory_uncached(self):
        controller = AgentOpsController()
        manager = MagicMock()
        manager.repository_root.side_effect = GitError("nope")
        with patch("agentops.gui_controller.GitWorktreeManager", return_value=manager):
            self.assertEqual(controller._operation_root("C:/ws"), Path("C:/ws"))
            self.assertEqual(controller._operation_root("C:/ws"), Path("C:/ws"))
        self.assertEqual(manager.repository_root.call_count, 2)


class GuiMergePathTests(unittest.TestCase):
    """The GUI's merge branch had NO test before commit 98ea451.

    That is how the CLI and the desktop client came to disagree: the CLI's gate
    was covered and the GUI's was not, so `if result.ready:` was free to drift.

    These drive the real `_run_task_operation` against a real scratch
    repository. The assertion is on GIT STATE, not on a mocked return value,
    because the observable difference between the two implementations is
    whether the work got committed to the agentops branch -- not the `merged`
    flag, which is False either way when nothing merged.
    """

    def _repo(self):
        import subprocess as sp
        import tempfile as tf
        import shutil
        root = Path(tf.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t.local"],
                    ["git", "config", "user.name", "T"]):
            sp.run(cmd, cwd=root, check=True, capture_output=True)
        (root / "base.txt").write_text("base", encoding="utf-8")
        sp.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        sp.run(["git", "commit", "-qm", "base"], cwd=root, check=True, capture_output=True)
        return root

    def _run_not_ready(self, root):
        """A not-READY workflow whose delegate DID change files.

        The change matters: with an empty worktree both the old and new code
        skip the interesting path, which is why the first two versions of this
        test passed against unfixed code.
        """
        import subprocess as sp
        from agentops.workflow import WorkflowResult
        from agentops.state import StateStore
        from agentops.tasks import Task, TaskStatus
        from agentops.config import AppConfig, AgentConfig
        import agentops.gui_controller as gc

        (root / ".agentops").mkdir(exist_ok=True)
        state_path = root / ".agentops" / "state.sqlite"
        seeded = StateStore(state_path)
        wf_id = seeded.create_workflow("task")
        seeded.add_task(Task("impl", "implementation", wf_id,
                             status=TaskStatus.PASSED))
        seeded.add_task(Task("verify", "verification", wf_id,
                             status=TaskStatus.BLOCKED))
        seeded.close()

        config = AppConfig(
            {"fb": AgentConfig("fb", "fake", ("{prompt}",),
                               ("architecture", "implementation",
                                "review", "debugging"))},
            {r: ("fb",) for r in ("architecture", "implementation",
                                  "review", "debugging")},
            (), max_attempts=2)

        events, worktrees = [], []
        real_create = gc.GitWorktreeManager.create

        def _create(self_self, directory, name):
            wt = real_create(self_self, directory, name)
            worktrees.append(wt)
            return wt

        def _fake_run(*args, **kwargs):
            # The delegate writes into its worktree, as a real one would.
            (Path(worktrees[0].path) / "PROOF.md").write_text(
                "agentops-proof", encoding="utf-8")
            return WorkflowResult(wf_id, False, "not ready")

        controller = AgentOpsController()
        controller._load_config = lambda: config
        controller._state_path = lambda r: state_path
        with patch("agentops.gui_controller.asyncio.run", side_effect=_fake_run), \
             patch("agentops.gui_controller.WorkflowEngine") as engine_cls, \
             patch.object(gc.GitWorktreeManager, "create", _create):
            engine_cls.return_value.degradation = MagicMock()
            controller._run_task_operation("task", root, threading.Event(),
                                           events.append)
        return events, worktrees, wf_id

    def test_not_ready_run_commits_but_never_merges(self):
        import subprocess as sp
        root = self._repo()
        events, worktrees, _ = self._run_not_ready(root)

        final = [e for e in events if e["kind"] == "workflow-result"][-1]
        self.assertFalse(final["merged"],
                         "GUI must not merge a workflow that is not READY")

        # Nothing may reach the base branch.
        self.assertFalse((root / "PROOF.md").exists(),
                         "PROOF.md must not appear on the base branch")

        # But the work must be committed to the agentops branch -- this is the
        # observable difference from the old code, which skipped finalization
        # entirely and left the delegate's work uncommitted.
        self.assertTrue(worktrees, "a worktree should have been created")
        branch = worktrees[0].branch
        log = sp.run(["git", "log", "--oneline", branch], cwd=root,
                     capture_output=True, text=True).stdout
        self.assertIn("agentops:", log,
                      "not-READY work must still be committed on its branch")
        self.assertTrue(Path(worktrees[0].path).exists(),
                        "the worktree must be preserved for later merging")

    def test_gui_and_cli_share_one_rule(self):
        import subprocess as sp
        root = self._repo()
        _events, worktrees, _ = self._run_not_ready(root)
        base_log = sp.run(["git", "log", "--oneline"], cwd=root,
                          capture_output=True, text=True).stdout
        agentops_commits = [l for l in base_log.splitlines()
                            if "agentops:" in l]
        self.assertEqual(agentops_commits, [],
                         "the base branch must carry no agentops commits")


if __name__ == "__main__":
    unittest.main()