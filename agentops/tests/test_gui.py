import asyncio
import sys
import tempfile
import threading
import tkinter as tk
import unittest
from tkinter import ttk
from contextlib import suppress
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentops.cli import build_parser
from agentops.config import AgentConfig, AppConfig
from agentops.gui import AgentOpsApp
from agentops.gui_controller import AgentOpsController
from agentops.logging import LogManager
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner, OperationCancelled
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.workflow import WorkflowEngine


class RunnerCancellationTests(unittest.TestCase):
    def test_run_agent_honors_cancel_event(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = AgentRunner(LogManager(Path(directory) / "logs"))
            config = AgentConfig("python", sys.executable, ("-c", "import time; time.sleep(10)"))
            agent = DetectedAgent(config, True, sys.executable)
            cancel_event = threading.Event()
            timer = threading.Timer(0.2, cancel_event.set)
            timer.start()
            try:
                with self.assertRaises(OperationCancelled):
                    asyncio.run(runner.run_agent(agent, "cancel me", Path(directory),
                                                 timeout_seconds=30, cancel_event=cancel_event))
            finally:
                timer.cancel()
                timer.join(timeout=5)


class StatusPollEfficiencyTests(unittest.TestCase):
    def test_operation_root_is_cached(self):
        from agentops.git import GitWorktreeManager
        with tempfile.TemporaryDirectory() as directory:
            controller = AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            with patch.object(GitWorktreeManager, "repository_root",
                              return_value=Path(directory)) as root:
                first = controller._operation_root(directory)
                second = controller._operation_root(directory)
            self.assertEqual(first, second)
            self.assertEqual(root.call_count, 1)

    def test_operation_root_failures_are_not_cached(self):
        from agentops.git import GitError, GitWorktreeManager
        with tempfile.TemporaryDirectory() as directory:
            controller = AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            with patch.object(GitWorktreeManager, "repository_root",
                              side_effect=[GitError("nope"), Path(directory)]) as root:
                controller._operation_root(directory)
                controller._operation_root(directory)
            self.assertEqual(root.call_count, 2)

    def test_update_tasks_skips_unchanged_payloads(self):
        app = AgentOpsApp.__new__(AgentOpsApp)
        app.task_tree = MagicMock()
        app._current_tasks = []
        if hasattr(app, "_task_signature_cache"):
            del app._task_signature_cache
        tasks = [Task("work", "implementation", "w", max_attempts=1)]
        app._update_tasks(tasks)
        first_deletes = app.task_tree.delete.call_count
        first_inserts = app.task_tree.insert.call_count
        self.assertGreater(first_inserts, 0)
        # Same object again, then an equal-valued copy: both skip the rebuild.
        app._update_tasks(tasks)
        app._update_tasks([Task("work", "implementation", "w", id=tasks[0].id, max_attempts=1)])
        self.assertEqual(app.task_tree.delete.call_count, first_deletes)
        self.assertEqual(app.task_tree.insert.call_count, first_inserts)
        changed = Task("work", "implementation", "w", max_attempts=1)
        changed.status = TaskStatus.FAILED
        app._update_tasks([changed])
        self.assertGreater(app.task_tree.insert.call_count, first_inserts)


class ControllerObservabilityTests(unittest.TestCase):
    def test_history_and_logs_use_canonical_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("observable workflow")
                state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
            finally:
                state.close()
            page = controller.list_workflows(root)
            self.assertEqual(page["total"], 1)
            detail = controller.get_workflow(root, workflow_id)
            assert detail is not None
            self.assertEqual(len(detail["tasks"]), 1)
            log_path = LogManager(root / ".agentops" / "logs").write_run("task-1", "demo", "hi", "", "meta")
            name = str(log_path.parent.name + "/" + log_path.name.replace(".meta.log", ".stdout.log"))
            entries = controller.list_logs(root)
            self.assertTrue(any(entry["name"] == name for entry in entries))
            content = controller.read_log(root, name)
            self.assertIn("hi", str(content["text"]))


class WorkflowCancellationTests(unittest.TestCase):
    def test_execute_checks_cancel_event_before_starting(self):
        config = AppConfig({}, {}, (), max_attempts=1, concurrency=1)
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("cancelled workflow")
            task = state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
            engine = WorkflowEngine(config, state, MagicMock(), MagicMock(), MagicMock())
            cancel_event = threading.Event()
            cancel_event.set()
            with self.assertRaises(OperationCancelled):
                asyncio.run(engine.execute(workflow_id, Path.cwd(), cancel_event))
            self.assertEqual(state.get_task(task.id).status, TaskStatus.PENDING)
        finally:
            state.close()


class FakeController:
    def __init__(self):
        self.starts: list[tuple[str, tuple[object, ...]]] = []
        self.cancels = 0

    def detect_agents(self):
        return {"demo": object()}

    def run_agent(self, agent, prompt, directory, callback):
        self.starts.append(("agent", (agent, prompt, directory)))

    def run_task(self, description, directory, callback):
        self.starts.append(("task", (description, directory)))

    def cancel(self):
        self.cancels += 1

    def list_workflows(self, directory, limit=25, offset=0, status=None):
        return {"total": 1, "limit": limit, "offset": offset, "workflows": [{
            "id": "w1", "status": "passed", "description": "history workflow",
            "created_at": "now", "updated_at": "now", "task_count": 1, "task_counts": {"passed": 1},
        }]}

    def get_workflow(self, directory, workflow_id):
        return {"id": workflow_id, "status": "passed", "description": "history workflow", "tasks": [{
            "id": "t1", "status": "passed", "role": "review", "assigned_agent": "demo",
            "attempts": 1, "dependencies": (), "description": "check history", "result": "history ok",
        }]}

    def list_logs(self, directory, task_id=None, limit=200):
        return [{"path": "t1/demo.stdout.log", "name": "t1/demo.stdout.log", "size": 5, "modified": 0.0}]

    def read_log(self, directory, name, max_bytes=65536):
        return {"name": str(name), "path": str(name), "truncated": False, "text": "hello log"}

    def list_worktrees(self, directory):
        return [{"repository": "repo", "path": "/tmp/wt", "branch": "agentops/demo-12345678",
                 "head": "abc123", "managed": True, "status": ""}]

    def inspect_worktree(self, directory, path):
        return {"repository": "repo", "path": path, "branch": "agentops/demo-12345678",
                "head": "abc123", "managed": True, "status": "", "diff_stat": " file.txt | 1 +"}

    def cleanup_worktree(self, directory, path, delete_unmerged_branch=False):
        self.starts.append(("cleanup", (path, delete_unmerged_branch)))
        return {"removed_worktree": True, "deleted_branch": False, "branch": "agentops/demo-12345678",
                "detail": "Unmerged branch was preserved."}

    def retry_merge(self, directory, path):
        self.starts.append(("retry", (path,)))
        return {"merged": True, "path": path, "branch": "agentops/demo-12345678", "base_branch": "main"}

    def latest_workflow(self, directory):
        return None


class LayoutScrollbarTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = AgentOpsApp(self.root, controller=FakeController())
        self.root.update_idletasks()

    def tearDown(self):
        with suppress(tk.TclError):
            self.root.destroy()

    def test_text_boxes_have_vertical_scrollbars(self):
        for text_name, scroll_name in (("prompt", "prompt_yscroll"),
                                       ("description", "description_yscroll"),
                                       ("output", None)):
            text = getattr(self.app, text_name)
            self.assertIsInstance(text, tk.Text)
            self.assertTrue(text.grid_info(), text_name)
            if scroll_name is not None:
                scroll = getattr(self.app, scroll_name)
                self.assertIsInstance(scroll, ttk.Scrollbar)
                self.assertTrue(scroll.grid_info(), scroll_name)
                self.assertIn("yview", str(scroll.cget("command")))

    def test_trees_and_lists_scroll_both_directions(self):
        for widget_name, vscroll_name, hscroll_name in (
                ("history_tree", "history_vscroll", "history_hscroll"),
                ("worktree_tree", "worktree_vscroll", "worktree_hscroll"),
                ("task_tree", "task_vscroll", "task_hscroll"),
                ("log_list", None, "logs_hscroll"),
                ("artifact_list", None, "artifacts_hscroll")):
            widget = getattr(self.app, widget_name)
            self.assertTrue(widget.grid_info(), widget_name)
            hscroll = getattr(self.app, hscroll_name)
            self.assertIsInstance(hscroll, ttk.Scrollbar)
            self.assertTrue(hscroll.grid_info(), hscroll_name)
            if vscroll_name is not None:
                vscroll = getattr(self.app, vscroll_name)
                self.assertIsInstance(vscroll, ttk.Scrollbar)
                self.assertTrue(vscroll.grid_info(), vscroll_name)

    def test_tree_columns_share_extra_width(self):
        for tree_name in ("history_tree", "worktree_tree", "task_tree"):
            tree = getattr(self.app, tree_name)
            for column in tree["columns"]:
                self.assertTrue(bool(tree.column(column, "stretch")), (tree_name, column))


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.controller = FakeController()
        self.app = AgentOpsApp(self.root, controller=self.controller)

    def tearDown(self):
        with suppress(tk.TclError):
            self.root.destroy()

    def test_gui_command_is_available(self):
        self.assertEqual(build_parser().parse_args(["gui"]).command, "gui")

    def test_missing_run_input_warns(self):
        self.app.agent_var.set("")
        self.app.prompt.insert("1.0", "hello")
        with patch("agentops.gui.messagebox.showwarning") as warning:
            self.app.start_run()
        warning.assert_called_once()
        self.assertEqual(self.controller.starts, [])

    def test_missing_task_input_warns(self):
        with patch("agentops.gui.messagebox.showwarning") as warning:
            self.app.start_task()
        warning.assert_called_once()
        self.assertEqual(self.controller.starts, [])

    def test_history_browser_loads_selected_workflow(self):
        self.app.refresh_history()
        self.assertIn("history-0", self.app.history_tree.get_children())
        self.assertIn("1 of 1", self.app.history_nav_var.get())
        self.app.history_tree.selection_set("history-0")
        self.app.load_selected_workflow()
        self.assertIn("task-0", self.app.task_tree.get_children())
        self.assertIn("history workflow", self.app.output.get("1.0", "end"))

    def test_logs_browser_views_selected_log(self):
        self.app.refresh_logs()
        self.assertEqual(self.app.log_list.size(), 1)
        self.app.log_list.selection_set(0)
        self.app.view_selected_log()
        self.assertIn("hello log", self.app.output.get("1.0", "end"))

    def test_worktrees_browser_inspects_and_cleans_up(self):
        self.app.refresh_worktrees()
        self.assertIn("worktree-0", self.app.worktree_tree.get_children())
        self.app.worktree_tree.selection_set("worktree-0")
        self.app.inspect_selected_worktree()
        with patch("agentops.gui.messagebox.askyesno", return_value=True):
            self.app.cleanup_selected_worktree()
            self.app.worktree_tree.selection_set("worktree-0")
            self.app.retry_selected_merge()
        output = self.app.output.get("1.0", "end")
        self.assertIn("Changed files:", output)
        self.assertIn("preserved branch", output)
        self.assertIn("Merged agentops/demo-12345678 into main.", output)
        self.assertIn(("cleanup", ("/tmp/wt", False)), self.controller.starts)
        self.assertIn(("retry", ("/tmp/wt",)), self.controller.starts)

    def test_stale_events_do_not_finish_new_operation(self):
        self.app._set_running(True)
        first_id = self.app._operation_id
        self.app._operation_seq += 1
        self.app._operation_id = self.app._operation_seq
        self.app._handle_event({"kind": "thread-finished", "operation_id": first_id})
        self.assertTrue(self.app._running)
        self.app._handle_event({"kind": "thread-finished", "operation_id": self.app._operation_id})
        self.assertFalse(self.app._running)

    def test_history_offset_clamps_when_total_shrinks(self):
        self.app._history_offset = 50
        self.app.refresh_history()
        self.assertEqual(self.app._history_offset, 0)
        self.assertIn("1 of 1", self.app.history_nav_var.get())

    def test_history_and_log_errors_are_reported(self):
        with patch("agentops.gui.messagebox.showerror") as errorbox:
            with patch.object(FakeController, "list_workflows", side_effect=RuntimeError("boom")):
                self.app.refresh_history()
            with patch.object(FakeController, "list_logs", side_effect=RuntimeError("boom")):
                self.app.refresh_logs()
            with patch.object(FakeController, "list_worktrees", side_effect=RuntimeError("boom")):
                self.app.refresh_worktrees()
        self.assertEqual(errorbox.call_count, 3)

    def test_empty_selections_warn_without_crashing(self):
        with patch("agentops.gui.messagebox.showwarning") as warning:
            self.app.show_selected_task()
            self.app.load_selected_workflow()
            self.app.view_selected_log()
            self.app.inspect_selected_worktree()
        self.assertEqual(warning.call_count, 4)

    def test_task_details_show_full_result(self):
        self.app.refresh_history()
        self.app.history_tree.selection_set("history-0")
        self.app.load_selected_workflow()
        self.app.task_tree.selection_set("task-0")
        self.app.show_selected_task()
        self.assertIn("history ok", self.app.output.get("1.0", "end"))

    def test_result_event_returns_gui_to_idle(self):
        self.app._set_running(True)
        self.app._handle_event({
            "kind": "result", "agent": "demo", "succeeded": True, "duration_seconds": 1.25,
            "stdout": "done", "stderr": "", "log_path": "demo.log",
            "operation_id": self.app._operation_id,
        })
        self.assertFalse(self.app._running)
        self.assertEqual(str(self.app.run_cancel_button["state"]), "disabled")
        self.assertIn("demo passed", self.app.output.get("1.0", "end"))
