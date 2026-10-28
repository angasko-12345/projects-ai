"""Phase C3 right pane: Diff / Logs / Records, its toggle, and the narrow
window rule (below 1280px an open pane collapses the thread list)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from tests.test_gui_qt import REPOSITORY, FakeController
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from test_gui_qt import REPOSITORY, FakeController

DIFF = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,3 @@
 import os
-print(1)
+print(2)
+print(3)
"""


class PaneController(FakeController):
    def workflow_diff(self, directory, workflow_id, max_bytes=262144):
        self._rec("workflow_diff", directory, workflow_id)
        return {"source": "worktree", "text": DIFF, "truncated": False}

    def list_logs(self, directory, task_id=None, limit=200):
        self._rec("list_logs", directory, task_id)
        return [{"name": f"{task_id}/run.stdout.log", "path": "", "size": 5, "modified": 0}]

    def read_log(self, directory, name, max_bytes=65536):
        self._rec("read_log", directory, name)
        return {"name": name, "text": "tail of the log", "truncated": False}


@requires_qt
class RightPaneTest(unittest.TestCase):
    def _window(self, width: int, height: int):
        from agentops.gui.settings import AppSettings
        from agentops.gui.shell import MainWindow

        qt_app()
        settings = AppSettings()
        settings.touch_repository(REPOSITORY)
        self.controller = PaneController()
        window = MainWindow(
            self.controller, settings=settings, settings_file=Path(tempfile.mkdtemp()) / "s.json"
        )
        self.addCleanup(self._teardown, window)
        window.resize(width, height)
        window.show()
        window.navigate("workflows")
        view = window._views["workflows"]
        self.assertTrue(wait_until(lambda: bool(view._transcript.model().get("blocks"))))
        wait(50)
        return window, view

    def _teardown(self, window):
        from PySide6.QtWidgets import QMessageBox

        with patch(
            "agentops.gui.shell.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            destroy(window)

    def called(self, name):
        return [call[1] for call in self.controller.calls if call[0] == name]

    def test_three_panes_at_1440_without_horizontal_overflow(self):
        window, view = self._window(1440, 900)
        pane = view._records
        self.assertTrue(pane.isVisible() and window._sidebar.isVisible())
        self.assertEqual((pane.width(), pane.tab()), (480, "diff"))
        self.assertEqual(view._body_scroll.horizontalScrollBar().maximum(), 0)
        self.assertTrue(wait_until(lambda: bool(pane._diff.files())))
        self.assertEqual(self.called("workflow_diff"), [(REPOSITORY, "wf-1")])
        self.assertEqual(pane._attempt.currentText(), "Attempt 1 · live worktree")
        self.assertEqual((pane._added.text(), pane._removed.text()), ("+2", "\u22121"))

    def test_narrow_window_opens_the_pane_over_the_thread_list(self):
        window, view = self._window(1100, 700)
        pane = view._records
        self.assertFalse(pane.isVisible())  # closed by default below 1280px
        self.assertTrue(window._sidebar.isVisible())
        self.assertEqual(self.called("workflow_diff"), [])  # no read while hidden
        view._pane_toggle.click()
        self.assertTrue(pane.isVisible())
        self.assertFalse(window._sidebar.isVisible())
        self.assertEqual(pane.width(), 420)
        self.assertEqual(window._sidebar_toggle.toolTip(), "Show sidebar  Ctrl+B")
        window.toggle_sidebar()  # bringing the list back closes the pane
        self.assertFalse(pane.isVisible())
        self.assertTrue(window._sidebar.isVisible())
        self.assertFalse(window._settings.sidebar_collapsed)
        pane.closed.emit()  # the pane's X keeps the toggle in step
        self.assertFalse(view._pane_toggle.isChecked())

    def test_open_diff_and_logs_follow_the_picked_attempt_records_hold_the_tabs(self):
        window, view = self._window(1100, 700)
        pane = view._records
        view._transcript.open_diff_requested.emit("r-1")
        self.assertTrue(pane.isVisible())
        self.assertEqual((pane.tab(), pane._attempt.currentData()), ("diff", "r-1"))
        self.assertTrue(wait_until(lambda: bool(self.called("workflow_diff"))))
        wait(100)
        self.assertEqual(pane._diff.files(), [])  # an older attempt: its recorded stat
        pane.set_tab("logs")
        self.assertTrue(wait_until(lambda: pane._log.toPlainText() == "tail of the log"))
        self.assertEqual(self.called("list_logs"), [(REPOSITORY, "t-2")])
        self.assertEqual(self.called("read_log"), [(REPOSITORY, "t-2/run.stdout.log")])
        pane.set_tab("records")
        self.assertTrue(pane.isAncestorOf(view._tabs))
        self.assertTrue(view._tabs.isVisible())


if __name__ == "__main__":
    unittest.main()
