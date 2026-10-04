"""Qt desktop-client tests.

The Tk widget tests were replaced when the client moved to PySide6. Widget tests
run on the offscreen platform through ``tests/qt_display.py``, so they behave
identically on a developer machine and on headless CI. Controller-level GUI
logic stays display-free in ``test_gui.py`` and ``test_gui_operation_state`` and
never imports Qt.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agentops.cli import build_parser

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, qt_app, requires_qt, wait, wait_until
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, requires_qt, wait, wait_until

REPOSITORY = "D:/repo"

WORKFLOW = {
    "id": "wf-1", "description": "workflow under test", "status": "running",
    "created_at": "2026-10-03T12:00:00Z", "updated_at": "2026-10-03T12:34:56Z",
    "task_count": 2, "task_counts": {"passed": 1, "running": 1},
    "run_count": 2, "run_counts": {"completed": 1, "failed": 1},
}

TASKS = [
    {"id": "t-1", "workflow_id": "wf-1", "role": "architecture", "status": "passed",
     "description": "plan the work", "assigned_agent": "codex", "attempts": 1,
     "max_attempts": 3, "result": "planned", "verified": False,
     "dependencies": [], "created_at": "2026-10-03T12:00:01Z",
     "started_at": "2026-10-03T12:00:01Z", "finished_at": "2026-10-03T12:04:00Z"},
    {"id": "t-2", "workflow_id": "wf-1", "role": "implementation", "status": "running",
     "description": "do the work", "assigned_agent": "codex", "attempts": 1,
     "max_attempts": 3, "result": None, "verified": False,
     "dependencies": ["t-1"], "created_at": "2026-10-03T12:00:02Z",
     "started_at": "2026-10-03T12:10:00Z", "finished_at": None},
    {"id": "t-3", "workflow_id": "wf-1", "role": "verification", "status": "pending",
     "description": "verify the work", "assigned_agent": "codex", "attempts": 0,
     "max_attempts": 3, "result": None, "verified": False,
     "dependencies": ["t-2"], "created_at": "2026-10-03T12:00:03Z"},
    {"id": "t-4", "workflow_id": "wf-1", "role": "review", "status": "pending",
     "description": "review the change", "assigned_agent": None, "attempts": 0,
     "max_attempts": 3, "result": None, "verified": False,
     "dependencies": ["t-3"], "created_at": "2026-10-03T12:00:04Z"},
]

RUNS = [
    {"id": "r-1", "workflow_id": "wf-1", "task_id": "t-2", "attempt": 1,
     "agent": "codex", "status": "completed", "exit_code": 0, "duration_seconds": 12.5,
     "model": "gpt-5-codex", "role": "implementation", "started_at": "2026-10-03T12:10:00Z",
     "ended_at": "2026-10-03T12:10:12Z", "log_path": "a1.log",
     "worktree": "D:/repo/.agentops/worktrees/wf-1", "files_changed": ["src/app.py"],
     "diff_stat": "src/app.py | 12 +", "error": None,
     "structured_result": {"changed": ["src/app.py"]}},
    {"id": "r-2", "workflow_id": "wf-1", "task_id": "t-3", "attempt": 2,
     "agent": "claude", "status": "failed", "exit_code": 1, "duration_seconds": 3.0,
     "model": "sonnet", "role": "verification", "started_at": "2026-10-03T12:11:00Z",
     "log_path": "a2.log"},
    {"id": "r-3", "workflow_id": "wf-1", "task_id": "t-2", "attempt": 1,
     "agent": "codex", "status": "running", "exit_code": None, "duration_seconds": None,
     "model": "gpt-5-codex", "role": "implementation", "started_at": "2026-10-03T12:12:00Z",
     "log_path": "a3.log"},
]

VERIFICATIONS = [
    {"id": "v-1", "workflow_id": "wf-1", "task_id": "t-1", "overall_status": "passed",
     "profile_name": "default", "passed_checks": 2, "total_checks": 2, "failed_checks": 0},
    {"id": "v-2", "workflow_id": "wf-1", "task_id": "t-2", "overall_status": "failed",
     "profile_name": "default", "passed_checks": 1, "total_checks": 2, "failed_checks": 1},
]

FAILURES = [
    {"id": "f-1", "workflow_id": "wf-1", "task_id": "t-2", "agent_run_id": "r-3",
     "category": "test_failure", "source": "verification", "severity": "high",
     "recommended_action": "retry_same_agent", "retryable": True,
     "repairable": True, "primary_error": "assert 1 == 2", "evidence": None,
     "created_at": "2026-10-03T12:08:00Z"},
]

WORKTREES = [
    {"repository": REPOSITORY, "path": REPOSITORY + "/.agentops/worktrees/wf-1",
     "branch": "agentops/wf-1", "head": "abcdef1234567890", "managed": True, "status": ""},
]

ARTIFACTS = [
    {"id": "a-1", "workflow_id": "wf-1", "task_id": "t-1", "kind": "plans",
     "name": "plan.md", "size_bytes": 128, "created_at": "2026-10-03T12:05:00Z"},
]

PROFILES = [
    {"identifier": "codex", "display_name": "Codex", "executable_path": "/bin/codex",
     "availability": True, "roles": ["architecture", "implementation"], "priority": 10},
    {"identifier": "claude", "display_name": "Claude", "executable_path": "/bin/claude",
     "availability": False, "roles": ["review"], "priority": 5},
]

EVENTS = [
    {"id": "e-1", "timestamp": "2026-10-03T12:34:56Z", "workflow_id": "wf-1",
     "task_id": "t-2", "agent_run_id": "r-1", "type": "task.finished",
     "severity": "info", "message": "task finished"},
]



class FakeController:
    """Canned payloads mirroring the real controller read contracts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.cancelled = False
        self.task_callback = None
        # Recovery probe state (shell banner / recover action).
        self.interrupted: dict[str, int] = {"agent_runs": 0, "verification_runs": 0,
                                            "tasks": 0}
        # Workflow-result outcome knobs; defaults mirror a healthy finish.
        self.result_ready = True
        self.verification_ok = True
        self.readiness_reasons = ["no passed review task"]

    def _rec(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))

    # -- reads ---------------------------------------------------------
    def list_workflows(self, directory, limit=25, offset=0, status=None):
        self._rec("list_workflows", directory, limit=limit)
        return {"total": 1, "limit": limit, "offset": offset, "workflows": [WORKFLOW]}

    def get_workflow(self, directory, workflow_id):
        self._rec("get_workflow", directory, workflow_id)
        return {
            "workflow": WORKFLOW, "tasks": TASKS, "runs": RUNS,
            "verifications": VERIFICATIONS, "failures": FAILURES,
            "worktree_ref": {"path": WORKTREES[0]["path"], "branch": "agentops/wf-1",
                             "base_branch": "main", "base_commit": "1234567"},
        }

    def list_tasks(self, directory, workflow_id=None, status=None, limit=100, offset=0):
        self._rec("list_tasks", directory, workflow_id=workflow_id)
        return TASKS

    def dashboard_summary(self, directory, workflow_limit=8, event_limit=15,
                          failure_limit=10, verification_limit=10):
        self._rec("dashboard_summary", directory)
        return {
            "repository": directory, "total_workflows": 1,
            "active_workflows": {"pending": 0, "running": 1},
            "task_counts": {"passed": 1, "running": 1},
            "recent_workflows": [WORKFLOW], "latest_workflow_id": "wf-1",
            "verification_status": {"passed": 1, "failed": 1},
            "recent_verifications": VERIFICATIONS, "failure_count": 1,
            "needs_attention": FAILURES, "recent_failures": FAILURES,
            "recent_events": EVENTS,
        }

    def list_recent_agent_runs(self, directory, limit=100, offset=0):
        self._rec("list_recent_agent_runs", directory, limit=limit)
        return RUNS

    def get_agent_run(self, directory, run_id):
        self._rec("get_agent_run", directory, run_id)
        return next((row for row in RUNS if row["id"] == run_id), None)

    def list_recent_verification_runs(self, directory, limit=100, offset=0):
        self._rec("list_recent_verification_runs", directory)
        return VERIFICATIONS

    def get_verification_run(self, directory, run_id):
        self._rec("get_verification_run", directory, run_id)
        return {
            "run": VERIFICATIONS[0],
            "report": {"overall_status": "passed", "summary": "2 checks passed"},
            "checks": [{"name": "unit", "check_class": "tests", "status": "passed",
                        "required": True, "exit_code": 0, "duration_seconds": 1.0}],
        }

    def list_recent_failures(self, directory, limit=100, offset=0):
        self._rec("list_recent_failures", directory)
        return FAILURES

    def query_events(self, directory, workflow_id=None, task_id=None,
                     agent_run_id=None, event_type=None, limit=100, offset=0):
        self._rec("query_events", directory, workflow_id=workflow_id)
        return [event for event in EVENTS
                if workflow_id is None or event.get("workflow_id") == workflow_id]

    def workflow_readiness(self, directory, workflow_id):
        self._rec("workflow_readiness", directory, workflow_id)
        return {"workflow_id": workflow_id, "ready": False,
                "reasons": list(self.readiness_reasons),
                "verification_ok": self.verification_ok,
                "review_ok": False, "evidence_present": True}

    def interrupted_work(self, directory):
        self._rec("interrupted_work", directory)
        counts = dict(self.interrupted)
        return {**counts, "total": sum(counts.values())}

    def recover_interrupted(self, directory, workflow_id=None):
        self._rec("recover_interrupted", directory, workflow_id=workflow_id)
        summary = dict(self.interrupted)
        self.interrupted = {"agent_runs": 0, "verification_runs": 0, "tasks": 0}
        return summary

    def list_worktrees(self, directory):
        self._rec("list_worktrees", directory)
        return WORKTREES

    def inspect_worktree(self, directory, path):
        self._rec("inspect_worktree", directory, path)
        return {**WORKTREES[0], "status": "", "diff_stat": " file.txt | 1 +"}

    def cleanup_worktree(self, directory, path, delete_unmerged_branch=False):
        self._rec("cleanup_worktree", directory, path,
                  delete_unmerged_branch=delete_unmerged_branch)
        return {"removed_worktree": True, "deleted_branch": False,
                "branch": "agentops/wf-1", "detail": "Unmerged branch was preserved."}

    def retry_merge(self, directory, path):
        self._rec("retry_merge", directory, path)
        return {"merged": True, "path": path, "branch": "agentops/wf-1",
                "base_branch": "main"}

    def list_artifacts(self, directory, workflow_id=None, task_id=None,
                       kind=None, limit=100, offset=0):
        self._rec("list_artifacts", directory, workflow_id=workflow_id)
        return ARTIFACTS

    def read_artifact(self, directory, artifact_id, max_bytes=65536):
        self._rec("read_artifact", directory, artifact_id)
        return {"artifact": ARTIFACTS[0], "text": "# Plan\n\nartifact body\n"}

    def list_agent_profiles(self):
        self._rec("list_agent_profiles")
        return PROFILES

    def task_options(self):
        self._rec("task_options")
        return {"routing_enabled": True, "verification_profiles": ["default", "strict"],
                "default_verification_profile": "default"}

    # -- operations ----------------------------------------------------
    def cancel(self):
        self._rec("cancel")
        self.cancelled = True

    def run_task(self, description, directory, callback, *, agent=None,
                 verification_profile=None):
        self._rec("run_task", description, directory, agent=agent,
                  verification_profile=verification_profile)
        self.task_callback = callback
        callback({"kind": "workflow-started", "workflow_id": "wf-2",
                  "worktree": WORKTREES[0]["path"]})
        callback({"kind": "workflow-result", "workflow_id": "wf-2",
                  "merged": self.result_ready, "ready": self.result_ready})

    def finish_task(self) -> None:
        """Release the operation the way the real controller does on exit."""
        if self.task_callback is not None:
            self.task_callback({"kind": "thread-finished"})



@requires_qt
class MainWindowShellTests(unittest.TestCase):
    """The real shell must build, navigate, and load every view offscreen."""

    def setUp(self):
        from agentops.gui.settings import AppSettings
        from agentops.gui.shell import MainWindow

        qt_app()  # widgets require an application before construction
        self.controller = FakeController()
        settings = AppSettings()
        settings.touch_repository(REPOSITORY)
        self.window = MainWindow(self.controller, settings=settings)
        self.addCleanup(self._teardown)
        self.window.show()
        wait(50)

    def _teardown(self):
        # A still-running operation would raise a modal quit dialog, which no
        # test may block on; the offscreen session must always close cleanly.
        from PySide6.QtWidgets import QMessageBox

        with patch("agentops.gui.shell.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            destroy(self.window)

    def _activate(self, view_id: str) -> None:
        self.window.navigate(view_id)
        wait(50)

    def test_gui_command_is_available(self):
        self.assertEqual(build_parser().parse_args(["gui"]).command, "gui")

    def test_every_registered_view_loads_without_request_failures(self):
        from agentops.gui.views import VIEW_SPECS

        recorded: list[tuple[str, str, str]] = []
        original = self.window.toast

        def record(title, message="", kind="info"):
            recorded.append((str(title), str(message), str(kind)))
            original(title, message, kind)

        # Views captured `window.toast` when their context was built, so swap
        # the recorder into every context they hold.
        context = replace(self.window._views_ctx, toast=record)
        self.window._views_ctx = context
        for view in self.window._views.values():
            view.ctx = context
        for view_id, _title, _factory in VIEW_SPECS:
            self._activate(view_id)
            view = self.window._views[view_id]
            table = getattr(view, "_table", None) or getattr(view, "_list", None)
            if table is not None and table.proxy.rowCount():
                table.select_row_index(0)
                wait(50)
        wait(50)
        self.assertEqual(
            [entry for entry in recorded
             if entry[0] in ("Request failed", "Load failed")],
            [], "a view raised a silent load failure",
        )

    def test_list_views_render_their_rows(self):
        expected = {
            "tasks": 4, "runs": 3, "verification": 2, "failures": 1,
            "worktrees": 1, "artifacts": 1, "agents": 2,
        }
        for view_id, rows in expected.items():
            self._activate(view_id)
            table = self.window._views[view_id]._table
            with self.subTest(view=view_id):
                self.assertTrue(wait_until(lambda: table.proxy.rowCount() == rows))
                self.assertEqual(table.proxy.rowCount(), rows)

    def test_workflow_selection_fills_tasks_runs_and_artifacts(self):
        view = self.window._views["workflows"]
        self._activate("workflows")
        self.assertTrue(wait_until(lambda: view._list.proxy.rowCount() == 1))
        view._list.select_row_index(0)
        self.assertTrue(wait_until(lambda: view._tasks.proxy.rowCount() == 4))
        self.assertEqual(view._runs.proxy.rowCount(), 3)
        self.assertTrue(wait_until(lambda: view._artifacts.proxy.rowCount() == 1))

    def test_run_filters_narrow_the_visible_rows(self):
        view = self.window._views["runs"]
        self._activate("runs")
        self.assertTrue(wait_until(lambda: view._table.proxy.rowCount() == 3))
        failed_index = view._status.findText("Failed")
        self.assertGreater(failed_index, 0)
        view._status.setCurrentIndex(failed_index)
        self.assertEqual(view._table.proxy.rowCount(), 1)
        view._status.setCurrentIndex(0)
        view._search.setText("codex")
        self.assertEqual(view._table.proxy.rowCount(), 2)
        view._search.setText("")
        self.assertEqual(view._table.proxy.rowCount(), 3)

    def test_deep_links_select_the_recorded_row(self):
        self._activate("workflows")
        self.window.open_task("wf-1", "t-2")
        self.assertTrue(wait_until(
            lambda: self.window._views["workflows"]._selected_task_id == "t-2"))
        self.window.open_run("r-3")
        self.assertTrue(wait_until(
            lambda: self.window._views["runs"]._selected_id == "r-3"))

    def test_start_task_forwards_pinned_options_and_cancels(self):
        started = self.window.start_task("Ship the Qt client", repository=REPOSITORY,
                                         agent="codex", verification_profile="strict")
        self.assertTrue(started)
        self.assertTrue(wait_until(
            lambda: any(call[0] == "run_task" for call in self.controller.calls)))
        call = next(call for call in self.controller.calls if call[0] == "run_task")
        self.assertEqual(call[2]["agent"], "codex")
        self.assertEqual(call[2]["verification_profile"], "strict")
        self.assertTrue(wait_until(lambda: self.window._active))

        self.window.cancel_operation()
        wait(50)
        self.assertTrue(self.controller.cancelled)

        # The real controller always reports thread-finished; leaving the
        # operation active would block closeEvent on a modal dialog.
        self.controller.finish_task()
        self.assertTrue(wait_until(lambda: not self.window._active))

    def test_start_task_without_repository_is_refused(self):
        self.window._repository = ""
        self.assertFalse(self.window.start_task("nowhere to run"))

    def test_settings_updates_persist_to_the_given_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.window._settings_file = path
            self.window.update_settings({"sidebar_collapsed": True})
            self.assertTrue(self.window._settings.sidebar_collapsed)
            self.assertTrue(path.exists())

    def test_command_palette_opens_and_closes(self):
        self.window.open_palette()
        wait(50)
        self.assertTrue(self.window._palette.isVisible())
        self.window._palette.close()
        wait(20)
        self.assertFalse(self.window._palette.isVisible())

    def test_command_palette_exposes_the_desktop_commands(self):
        expected = [
            "New Task...", "Open Dashboard", "Open Tasks", "Open Workflows",
            "Open Agents", "Open Runs", "Open Verification", "Open Failures",
            "Open Worktrees", "Open Artifacts", "Recover Interrupted Work",
            "Refresh", "Settings",
        ]
        self.window.open_palette()
        wait(20)
        titles = [command.title
                  for command in self.window._palette.visible_commands()]
        for title in expected:
            with self.subTest(command=title):
                self.assertIn(title, titles)
        self.window._palette.close()

    def test_palette_commands_run_real_shell_actions(self):
        self.window.open_palette()
        wait(20)
        commands = {command.title: command
                    for command in self.window._palette.visible_commands()}
        # Actions are wired to live shell methods, not placeholders.
        self.assertEqual(commands["New Task..."].run, self.window.open_new_task)
        self.assertEqual(commands["Recover Interrupted Work"].run,
                         self.window.recover_interrupted_work)
        self.assertEqual(commands["Refresh"].run, self.window.refresh_active)
        # Navigation commands actually switch the active view.
        commands["Open Runs"].run()
        self.assertIs(self.window.active_view(), self.window._views["runs"])
        commands["Settings"].run()
        self.assertIs(self.window.active_view(), self.window._views["settings"])
        self.window._palette.close()

    def test_shortcuts_cover_the_desktop_contract(self):
        for sequence in ("Ctrl+K", "Ctrl+N", "F5", "Ctrl+R", "Ctrl+B"):
            with self.subTest(sequence=sequence):
                self.assertIn(sequence, self.window._shortcuts)
                self.assertTrue(self.window._shortcuts[sequence].isEnabled())
        # Esc is contextual: armed only while the recovery banner is shown.
        self.assertFalse(self.window._banner_escape.isEnabled())

    def test_recovery_banner_surfaces_recovers_and_notifies_once(self):
        from PySide6.QtWidgets import QMessageBox

        recorded: list[tuple[str, str, str]] = []
        original = self.window.toast

        def record(title, message="", kind="info"):
            recorded.append((str(title), str(message), str(kind)))
            original(title, message, kind)

        self.window.toast = record  # type: ignore[method-assign]

        self.controller.interrupted = {"agent_runs": 2, "verification_runs": 0,
                                       "tasks": 0}
        self.window._check_interrupted_work()
        self.assertTrue(wait_until(lambda: self.window._recovery_banner.isVisible()))
        self.assertEqual(self.window._recovery_detail.text(),
                         "2 runs require recovery")
        self.assertTrue(self.window._banner_escape.isEnabled())

        # Repeated refreshes do not repeat the notification.
        self.window.refresh_active()
        wait(50)
        self.assertEqual(
            len([entry for entry in recorded
                 if entry[0] == "Interrupted work detected"]), 1)

        with patch("agentops.gui.shell.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes) as question:
            self.window.recover_interrupted_work()
            self.assertTrue(wait_until(
                lambda: any(call[0] == "recover_interrupted"
                            for call in self.controller.calls)))
            self.assertTrue(wait_until(
                lambda: not self.window._recovery_banner.isVisible()))
        self.assertEqual(question.call_count, 1)
        self.assertFalse(self.window._banner_escape.isEnabled())
        applied = [entry for entry in recorded if entry[0] == "Recovery applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0][1], "2 runs require recovery")
        self.assertEqual(applied[0][2], "success")

    def test_recovery_banner_dismissal_sticks_until_counts_change(self):
        self.controller.interrupted = {"agent_runs": 1, "verification_runs": 1,
                                       "tasks": 0}
        self.window._check_interrupted_work()
        self.assertTrue(wait_until(lambda: self.window._recovery_banner.isVisible()))
        self.assertEqual(self.window._recovery_detail.text(),
                         "1 run and 1 verification run require recovery")

        self.window._dismiss_recovery_banner()
        self.assertFalse(self.window._recovery_banner.isVisible())
        self.window.refresh_active()
        wait(50)
        self.assertFalse(self.window._recovery_banner.isVisible())

        # A fresh interruption (different counts) re-surfaces the banner.
        self.controller.interrupted = {"agent_runs": 3, "verification_runs": 0,
                                       "tasks": 0}
        self.window.refresh_active()
        self.assertTrue(wait_until(lambda: self.window._recovery_banner.isVisible()))
        self.assertEqual(self.window._recovery_detail.text(),
                         "3 runs require recovery")

    def test_verification_failure_is_named_after_an_unready_workflow(self):
        recorded: list[tuple[str, str, str]] = []
        original = self.window.toast

        def record(title, message="", kind="info"):
            recorded.append((str(title), str(message), str(kind)))
            original(title, message, kind)

        self.window.toast = record  # type: ignore[method-assign]
        self.controller.result_ready = False
        self.controller.verification_ok = False
        self.controller.readiness_reasons = ["verification not passed",
                                             "no passed review task"]
        try:
            self.assertTrue(self.window.start_task("ship it", repository=REPOSITORY))
            self.assertTrue(wait_until(
                lambda: any(entry[0] == "Verification failed" for entry in recorded)))
            failure = next(entry for entry in recorded
                           if entry[0] == "Verification failed")
            self.assertEqual(failure[2], "error")
            self.assertIn("verification not passed", failure[1])
            # The blocked path must not also fire a generic "finished" toast.
            self.assertFalse(any(entry[0] == "Workflow finished"
                                 for entry in recorded))
        finally:
            self.controller.finish_task()
        self.assertTrue(wait_until(lambda: not self.window._active))

    def test_blocked_workflow_reports_its_readiness_reasons(self):
        recorded: list[tuple[str, str, str]] = []
        original = self.window.toast

        def record(title, message="", kind="info"):
            recorded.append((str(title), str(message), str(kind)))
            original(title, message, kind)

        self.window.toast = record  # type: ignore[method-assign]
        self.controller.result_ready = False
        self.controller.verification_ok = True
        self.controller.readiness_reasons = ["no passed review task"]
        try:
            self.assertTrue(self.window.start_task("ship it", repository=REPOSITORY))
            self.assertTrue(wait_until(
                lambda: any(entry[0] == "Workflow blocked" for entry in recorded)))
            blocked = next(entry for entry in recorded
                           if entry[0] == "Workflow blocked")
            self.assertEqual(blocked[2], "warning")
            self.assertIn("no passed review task", blocked[1])
        finally:
            self.controller.finish_task()
        self.assertTrue(wait_until(lambda: not self.window._active))

    def test_tray_reports_status_and_offers_hide(self):
        self.assertEqual(self.window._tray.toolTip(), "AgentOps - Idle")
        self.window._mark_operation(True, "Task running...")
        self.assertEqual(self.window._tray.toolTip(), "AgentOps - Task running...")
        actions = [action.text()
                   for action in self.window._tray.contextMenu().actions()]
        for text in ("Show AgentOps", "Hide AgentOps", "New Task...", "Quit"):
            with self.subTest(action=text):
                self.assertIn(text, actions)
        self.window._set_status_idle()
        self.assertEqual(self.window._tray.toolTip(), "AgentOps - Idle")

    def test_window_geometry_round_trips_through_settings(self):
        from agentops.gui.settings import load_settings
        from agentops.gui.shell import MainWindow

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.window._settings_file = path
            # Sized inside the offscreen screen (1024x768): restore clamps
            # to the screen, which would mask a broken round trip.
            self.window.resize(1024, 730)
            self.window._persist_window_state()
            self.window._save_settings()
            self.assertTrue(path.exists())
            restored = load_settings(path)
            self.assertTrue(restored.geometry)

            other = MainWindow(self.controller, settings=restored,
                               settings_file=path)
            self.addCleanup(lambda: destroy(other))
            other.restore_geometry()
            self.assertEqual(other.size(), self.window.size())

    def test_worktree_cleanup_and_merge_retry_call_the_controller(self):
        view = self.window._views["worktrees"]
        self._activate("worktrees")
        self.assertTrue(wait_until(lambda: view._table.proxy.rowCount() == 1))
        view._table.select_row_index(0)
        self.assertTrue(wait_until(lambda: view._detail._entry is not None))
        panel = view._detail

        with patch("agentops.gui.detail._confirm", return_value=True):
            panel._cleanup()
            panel._retry_merge()
        wait(50)
        names = [call[0] for call in self.controller.calls]
        self.assertIn("cleanup_worktree", names)
        self.assertIn("retry_merge", names)


if __name__ == "__main__":
    unittest.main()
