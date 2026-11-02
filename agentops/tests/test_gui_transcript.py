"""The run transcript shows payload fields and nothing else.

A fixture workflow goes through the real :class:`Transcript` widget and the
visible text is asserted line by line, in layout order: every string is either
a payload value (or its plain-words/duration rendering) or a fixed caption.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from tests.test_control_center import transcript_fixture
    from tests.test_gui_qt import REPOSITORY, FakeController
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from test_control_center import transcript_fixture
    from test_gui_qt import REPOSITORY, FakeController

EXPECTED = [
    "ship the control center",
    "Started 22m ago · agent: codex · verify: default",
    "Worked for 11m 50s", "· 3 agent runs · 1 retry",
    "Plan", "architecture work", "codex", "Use JWT.",
    "Attempt 1", "codex · gpt-5-codex · exit 0", "1m 06s",
    "Implement", "implementation work", "codex",
    "Attempt 1", "codex · gpt-5-codex · exit 1", "3m 08s",
    "Verification failed", "1/2 checks · profile default · 22.0s",
    "pytest", "tests", "3 failed, 41 passed", "4.0s", "ruff", "lint",
    "Test failure", "High", "Recommended:", "Retry same agent", "assert 1 == 2",
    "3 changed files", "+41", "\u22129",
    "Attempt 2", "codex · gpt-5-codex", "Running · 7m 36s",
    "Verify", "verification work",
    "Finalize", "Not ready to merge", "verification missing\nreview missing",
]  # fmt: skip


def visible_text(root) -> list[str]:
    """Visible label text under ``root``, in layout (reading) order."""
    from PySide6.QtWidgets import QLabel

    out: list[str] = []

    def walk_layout(layout) -> None:
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item.widget() is not None:
                walk_widget(item.widget())
            elif item.layout() is not None:
                walk_layout(item.layout())

    def walk_widget(widget) -> None:
        if not widget.isVisibleTo(root):
            return
        if isinstance(widget, QLabel) and widget.text():
            out.append(widget.text())
        if widget.layout() is not None:
            walk_layout(widget.layout())

    walk_layout(root.layout())
    return out


@requires_qt
class TranscriptTextTest(unittest.TestCase):
    def test_transcript_text_is_the_payload(self):
        from agentops.gui.transcript import Transcript

        qt_app()
        widget = Transcript()
        self.addCleanup(widget.deleteLater)
        widget.resize(900, 1400)
        widget.show()
        widget.set_workflow(**transcript_fixture())
        wait(20)
        self.assertEqual(visible_text(widget), EXPECTED)
        self.assertEqual(widget.model()["readiness"]["chip"], "Not ready · 2 gates open")

        opened: list[str] = []
        widget.open_diff_requested.connect(opened.append)
        from PySide6.QtWidgets import QPushButton

        widget.findChild(QPushButton).click()
        self.assertEqual(opened, ["r-2"])

        widget.clear()
        self.assertEqual(visible_text(widget), [])


@requires_qt
class ReadinessChipTest(unittest.TestCase):
    def test_compact_chip_stays_icon_sized_when_the_header_has_spare_width(self):
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

        from agentops.gui.transcript import ReadinessChip

        qt_app()
        host = QWidget()
        self.addCleanup(host.deleteLater)
        layout = QHBoxLayout(host)
        chip = ReadinessChip()
        layout.addWidget(chip)
        layout.addWidget(QPushButton("..."))
        host.resize(600, 40)
        host.show()
        chip.set_readiness({"ready": False, "chip": "Not ready · 3 gates open"})
        wait(20)
        full = chip.sizeHint().width()
        self.assertEqual(chip.width(), full)
        chip.set_compact(True)
        wait(20)
        self.assertLess(chip.sizeHint().width(), 40)
        self.assertEqual(chip.width(), chip.sizeHint().width())
        self.assertEqual(chip.accessibleName(), "Not ready · 3 gates open")

    def test_compact_width_applies_on_the_very_next_layout_pass(self):
        from PySide6.QtWidgets import QHBoxLayout, QWidget

        from agentops.gui.transcript import ReadinessChip, TextLabel

        qt_app()
        host = QWidget()
        self.addCleanup(host.deleteLater)
        layout = QHBoxLayout(host)
        title = TextLabel("Upgrade Django 4.2 to 5.1 and fix deprecations", 14, "#fff", elide=True)
        layout.addWidget(title, 1)
        chip = ReadinessChip()
        layout.addWidget(chip)
        host.resize(520, 40)
        host.show()
        chip.set_readiness({"ready": False, "chip": "Not ready · 3 gates open"})
        wait(20)
        wide_title = title.width()
        chip.set_compact(True)
        layout.activate()  # no event loop turn: a stale cached hint would win here
        self.assertEqual(chip.width(), chip.sizeHint().width())
        self.assertLess(chip.width(), 40)
        self.assertGreater(title.width(), wide_title)


@requires_qt
class WorkspaceHeaderTest(unittest.TestCase):
    def setUp(self):
        from agentops.gui.settings import AppSettings
        from agentops.gui.shell import MainWindow

        qt_app()
        settings = AppSettings()
        settings.touch_repository(REPOSITORY)
        self.window = MainWindow(FakeController(), settings=settings)
        self.addCleanup(self._teardown)
        self.window.resize(1440, 900)
        self.window.show()
        wait(50)

    def _teardown(self):
        from PySide6.QtWidgets import QMessageBox

        with patch(
            "agentops.gui.shell.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            destroy(self.window)

    def test_header_and_transcript_follow_the_selected_workflow(self):
        view = self.window._views["workflows"]
        self.window.navigate("workflows")
        self.assertTrue(wait_until(lambda: bool(view._transcript.model().get("blocks"))))
        self.assertEqual(view._repo_label.text(), "repo")
        self.assertEqual(view._title_label.text(), "workflow under test")
        self.assertEqual(view._readiness_chip.text(), "Not ready · 1 gate open")
        self.assertEqual(view._readiness_chip.toolTip(), "no passed review task")
        texts = visible_text(view._transcript)
        self.assertEqual(texts[0], "workflow under test")
        self.assertIn("· 3 agent runs · 0 retries", texts)
        self.assertEqual(
            [b["label"] for b in view._transcript.model()["blocks"]],
            ["Plan", "Implement", "Verify", "Review"],
        )
        actions = [a.text() for a in view._more_btn.menu().actions() if a.text()]
        self.assertEqual(actions, ["Rename...", "Recover interrupted", "Delete"])
        self.assertTrue(view._records.isVisible())
        view._pane_toggle.click()
        self.assertFalse(view._records.isVisible())
        self.assertEqual(view._pane_toggle.toolTip(), "Show records")

    def test_status_pill_shows_the_workflow_status_not_the_idle_state(self):
        from agentops.gui.control_center import cancellation_state

        view = self.window._views["workflows"]
        self.window.navigate("workflows")
        self.assertTrue(wait_until(lambda: bool(view._transcript.model().get("blocks"))))
        idle = cancellation_state({"running": False, "status": "failed"})
        view._apply_state(idle, {"status": "failed"})
        self.assertEqual(view._status_badge.text(), "FAILED")
        view._apply_state(idle, {"status": "passed"})
        self.assertEqual(view._status_badge.text(), "PASSED")
        live = cancellation_state({"running": True, "status": "running"})
        view._apply_state(live, {"status": "pending"})
        self.assertEqual(view._status_badge.text(), "RUNNING")
        view._apply_state(cancellation_state(live, cancel_requested=True), {"status": "running"})
        self.assertEqual(view._status_badge.text(), "CANCELLING")
        gone = cancellation_state({"running": False, "status": "cancelled"})
        view._apply_state(gone, {"status": "cancelled"})
        self.assertEqual(view._status_badge.text(), "CANCELLED")


if __name__ == "__main__":
    unittest.main()
