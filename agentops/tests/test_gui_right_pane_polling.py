"""Right pane polling: an unchanged poll re-reads and re-renders nothing, a
hidden pane does no work, a growing log keeps the reader's scroll position;
plus the picker, empty-state and gap strings."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from tests.test_gui_qt import REPOSITORY
    from tests.test_gui_right_pane import PaneController
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from test_gui_qt import REPOSITORY
    from test_gui_right_pane import PaneController


class GrowingLogController(PaneController):
    """``read_log`` returns 50 more lines on every read."""

    lines = 200

    def read_log(self, directory, name, max_bytes=65536):
        self._rec("read_log", directory, name)
        text = "\n".join(f"line {n}" for n in range(self.lines))
        self.lines += 50
        return {"name": name, "text": text, "truncated": False}


class GapLabelTest(unittest.TestCase):
    def test_gap_label_is_pluralised(self):
        try:
            from agentops.gui.diffview import gap_label
        except ImportError:  # PySide6 absent
            self.skipTest("PySide6 is not installed")
        self.assertEqual(gap_label(1), "1 unmodified line")
        self.assertEqual(gap_label(7), "7 unmodified lines")


@requires_qt
class RightPanePollingTest(unittest.TestCase):
    def _window(self, width: int, height: int, controller=None):
        from agentops.gui.settings import AppSettings
        from agentops.gui.shell import MainWindow

        qt_app()
        settings = AppSettings()
        settings.touch_repository(REPOSITORY)
        self.controller = controller or PaneController()
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

    def _poll(self, view) -> None:
        """One shell poll: the view re-reads the workflow and hands it on."""
        before = len(self.called("get_workflow"))
        view.refresh()
        self.assertTrue(wait_until(lambda: len(self.called("get_workflow")) > before))
        wait(150)

    def _repoll(self, view, signature: str) -> None:
        """A poll whose workflow row changed (``updated_at`` moved on)."""
        blocks = view._transcript.model().get("blocks")
        view._records.set_workflow(REPOSITORY, "wf-1", blocks, (signature,))

    def test_an_unchanged_poll_reads_and_renders_nothing(self):
        window, view = self._window(1440, 900)
        pane = view._records
        self.assertTrue(wait_until(lambda: bool(pane._diff.files())))
        with (
            patch("agentops.gui.right_pane.LIVE_DIFF_EVERY", 3600.0),
            patch.object(pane._diff, "set_payload", wraps=pane._diff.set_payload) as rendered,
            patch.object(pane._attempt, "clear", wraps=pane._attempt.clear) as rebuilt,
        ):
            self._poll(view)
            self._poll(view)
            self.assertEqual(self.called("workflow_diff"), [(REPOSITORY, "wf-1")])
            rendered.assert_not_called()
            rebuilt.assert_not_called()
            self._repoll(view, "updated later")  # new input: read again
            self.assertTrue(wait_until(lambda: len(self.called("workflow_diff")) == 2))
            wait(100)
            rendered.assert_not_called()  # the same diff is not re-laid out
        self.assertEqual(pane._attempt.currentText(), "Attempt 1 · live worktree")

    def test_a_running_attempt_rereads_its_diff_on_a_slower_clock(self):
        window, view = self._window(1440, 900)
        pane = view._records
        self.assertTrue(wait_until(lambda: bool(pane._diff.files())))
        with patch("agentops.gui.right_pane.LIVE_DIFF_EVERY", 0.0):
            self._poll(view)
        self.assertTrue(wait_until(lambda: len(self.called("workflow_diff")) == 2))

    def test_an_unchanged_log_is_not_rewritten_on_a_poll(self):
        window, view = self._window(1440, 900)
        pane = view._records
        pane.set_tab("logs")
        self.assertTrue(wait_until(lambda: pane._log.toPlainText() == "tail of the log"))
        with patch.object(pane._log, "set_log", wraps=pane._log.set_log) as rewritten:
            self._poll(view)
            self.assertTrue(wait_until(lambda: len(self.called("read_log")) == 2))
            wait(100)  # the running attempt's tail is re-read ...
            rewritten.assert_not_called()  # ... but the same text is not re-set
        self.assertEqual(len(self.called("list_logs")), 1)

    def test_a_hidden_pane_does_no_work_on_a_poll(self):
        window, view = self._window(1100, 700)
        pane = view._records
        self.assertFalse(pane.isVisible())
        with patch.object(pane._attempt, "clear", wraps=pane._attempt.clear) as rebuilt:
            self._poll(view)
            rebuilt.assert_not_called()
        self.assertEqual(self.called("workflow_diff"), [])
        view._pane_toggle.click()  # shown: it catches up with the newest poll
        self.assertTrue(wait_until(lambda: bool(pane._diff.files())))
        self.assertEqual(self.called("workflow_diff"), [(REPOSITORY, "wf-1")])

    def test_log_scroll_position_is_kept_unless_at_the_bottom(self):
        window, view = self._window(1440, 900, GrowingLogController())
        pane = view._records
        pane.set_tab("logs")
        log, bar = pane._log, pane._log.verticalScrollBar()
        self.assertTrue(wait_until(lambda: "line 199" in log.toPlainText()))
        wait(50)
        self.assertEqual(bar.value(), bar.maximum())  # a fresh log opens at its tail
        bar.setValue(20)
        self._poll(view)  # the running attempt's log grew by 50 lines
        self.assertTrue(wait_until(lambda: "line 249" in log.toPlainText()))
        wait(50)
        self.assertEqual(bar.value(), 20)  # the reader's place is held
        bar.setValue(bar.maximum())
        self._poll(view)
        self.assertTrue(wait_until(lambda: "line 299" in log.toPlainText()))
        wait(50)
        self.assertEqual(bar.value(), bar.maximum())  # at the bottom: it follows
        self.assertTrue(log.toPlainText().startswith("line 0\nline 1\n"))
        self.assertEqual(len(self.called("list_logs")), 1)  # same files: not re-listed

    def test_recorded_label_and_centred_empty_state(self):
        window, view = self._window(1440, 900)
        pane = view._records
        self.assertTrue(wait_until(lambda: bool(pane._diff.files())))
        self.assertIs(pane._diff_stack.currentWidget(), pane._diff)
        pane._on_diff({"source": "stat", "text": ""})
        self.assertEqual(pane._attempt.currentText(), "Attempt 1 · recorded")
        self.assertIs(pane._diff_stack.currentWidget(), pane._empty)
        self.assertEqual(pane._empty._heading.text(), "No changes recorded")
        self.assertEqual(pane._added.text(), "")


if __name__ == "__main__":
    unittest.main()
