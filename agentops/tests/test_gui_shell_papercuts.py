"""Regression tests for three shell papercuts found in the GUI smoke test.

1. Esc in the command palette closes the palette, not the recovery bar.
2. Rapid toasts stay a non-overlapping stack of at most three.
3. The New Task dialog's content fits at its minimum size and at 1100x700.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, make_context, qt_app, requires_qt, wait, wait_until
    from tests.test_gui_qt import REPOSITORY, FakeController
except ModuleNotFoundError:
    from qt_display import destroy, make_context, qt_app, requires_qt, wait, wait_until
    from test_gui_qt import REPOSITORY, FakeController


@requires_qt
class ShellPapercutsTest(unittest.TestCase):
    def setUp(self):
        from agentops.gui.settings import AppSettings
        from agentops.gui.shell import MainWindow

        qt_app()
        settings = AppSettings()
        settings.touch_repository(REPOSITORY)
        self.window = MainWindow(
            FakeController(),
            settings=settings,
            settings_file=Path(tempfile.mkdtemp()) / "settings.json",
        )
        self.addCleanup(self._teardown)
        self.window.resize(1100, 700)
        self.window.show()
        wait(50)

    def _teardown(self):
        from PySide6.QtWidgets import QMessageBox

        with patch(
            "agentops.gui.shell.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            destroy(self.window._palette)
            destroy(self.window)

    def test_escape_closes_the_palette_before_the_recovery_bar(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        self.window._show_recovery_banner()
        self.window.open_palette()
        palette = self.window._palette
        self.assertTrue(wait_until(palette.isVisible))
        QTest.keyClick(palette.input, Qt.Key.Key_Escape)
        wait(30)
        self.assertFalse(palette.isVisible())
        self.assertTrue(self.window._recovery_banner.isVisible())
        self.assertTrue(self.window._banner_escape.isEnabled())

    def test_rapid_toasts_stack_without_overlap_and_drop_the_oldest(self):
        from PySide6.QtWidgets import QLabel

        from agentops.gui.toast import ToastHost

        for index in range(5):
            self.window.toast(f"Toast {index}", "a message line", "info")
            wait(10)  # inside the slide-in animation of the previous card
        host = self.window.findChild(ToastHost)
        wait(400)  # every enter animation (170 ms) has finished
        toasts = list(host._toasts)
        self.assertEqual(len(toasts), 3)
        titles = [toast.findChildren(QLabel)[0].text() for toast in toasts]
        self.assertEqual(titles, ["Toast 2", "Toast 3", "Toast 4"])
        geometries = [toast.geometry() for toast in toasts]
        for upper, lower in zip(geometries[:-1], geometries[1:], strict=True):
            self.assertFalse(upper.intersects(lower), (upper, lower))
            self.assertLess(upper.bottom(), lower.top())
        for geometry in geometries:
            self.assertTrue(host.rect().contains(geometry), (geometry, host.rect()))
        content = self.window._content
        origin = content.mapTo(self.window, content.rect().topLeft())
        self.assertEqual(host.geometry().right() + 1, origin.x() + content.width() - 16)
        self.assertEqual(host.geometry().bottom() + 1, origin.y() + content.height() - 16)


@requires_qt
class NewTaskDialogFitTest(unittest.TestCase):
    def _dialog(self, width: int | None = None, height: int | None = None):
        from agentops.gui.new_task import NewTaskDialog
        from agentops.gui.tokens import DARK, build_stylesheet, qfont

        dialog = NewTaskDialog(make_context(FakeController()))
        self.addCleanup(destroy, dialog)
        # The app's theme metrics, scoped to this dialog (no global app state).
        dialog.setFont(qfont(DARK))
        dialog.setStyleSheet(build_stylesheet(DARK))
        dialog.show()
        if width is None:
            dialog.resize(dialog.minimumSize())
        else:
            dialog.resize(width, height)
        self.assertTrue(wait_until(lambda: dialog._agent_combo.count() > 0))
        self.assertTrue(wait_until(lambda: bool(dialog._verification_hint.text())))
        wait(50)
        return dialog

    def _assert_fits(self, dialog):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QWidget

        area = dialog.contentsRect()
        for widget in dialog.findChildren(QWidget):
            if not widget.isVisible() or not isinstance(
                widget, (QLabel, QAbstractButton, QComboBox)
            ):
                continue
            name = widget.text() if hasattr(widget, "text") else widget.currentText()
            left = widget.mapTo(dialog, QPoint(0, 0)).x()
            self.assertLessEqual(left + widget.width() - 1, area.right(), name)
            if isinstance(widget, QLabel) and widget.wordWrap():
                self.assertGreaterEqual(
                    widget.height(), widget.heightForWidth(widget.width()), name
                )
            else:
                self.assertGreaterEqual(widget.width(), widget.sizeHint().width(), name)
        layout = dialog.layout()
        tops = [
            layout.itemAt(index).geometry()
            for index in range(layout.count())
            if not layout.itemAt(index).isEmpty()
        ]
        for upper, lower in zip(tops[:-1], tops[1:], strict=True):
            self.assertLess(upper.bottom(), lower.top(), (upper, lower))

    def test_fits_at_minimum_size(self):
        dialog = self._dialog()
        self.assertEqual((dialog.width(), dialog.height()), (580, 520))
        self._assert_fits(dialog)

    def test_fits_at_1100x700(self):
        self._assert_fits(self._dialog(1100, 700))


if __name__ == "__main__":
    unittest.main()
