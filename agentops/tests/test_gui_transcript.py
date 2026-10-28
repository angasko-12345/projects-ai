"""The run transcript shows payload fields and nothing else.

A fixture workflow goes through the real :class:`Transcript` widget and the
visible text is asserted line by line, in layout order: every string is either
a payload value (or its plain-words/duration rendering) or a fixed caption.
"""

from __future__ import annotations

import unittest

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import qt_app, requires_qt, wait
    from tests.test_control_center import transcript_fixture
except ModuleNotFoundError:
    from qt_display import qt_app, requires_qt, wait
    from test_control_center import transcript_fixture

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


if __name__ == "__main__":
    unittest.main()
