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


if __name__ == "__main__":
    unittest.main()
