"""Phase B3 shell chrome: no top bar; its pieces re-parented, wiring intact."""

from __future__ import annotations

import unittest
from unittest.mock import patch

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from tests.test_gui_qt import REPOSITORY, FakeController
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from test_gui_qt import REPOSITORY, FakeController


@requires_qt
class ShellChromeTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

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
            destroy(self.window)

    def _go(self, view_id):
        self.window.navigate(view_id)
        wait(30)

    def test_top_bar_is_gone_and_minimum_size_holds(self):
        from PySide6.QtWidgets import QWidget

        self.assertIsNone(self.window.findChild(QWidget, "TopBar"))
        self.assertEqual(
            (self.window.minimumSize().width(), self.window.minimumSize().height()), (960, 620)
        )

    def test_sidebar_is_contextual_to_workflows_and_holds_the_repo_scope(self):
        self._go("dashboard")
        self.assertFalse(self.window._sidebar.isVisible())
        self.assertFalse(self.window._sidebar_toggle.isVisible())
        self._go("workflows")
        self.assertTrue(self.window._sidebar.isVisible())
        self.assertTrue(self.window._sidebar.isAncestorOf(self.window._repo_combo))
        self.assertTrue(self.window._repo_combo.isVisible())
        self.assertEqual(self.window._repo_combo.itemData(0), REPOSITORY)
        self.assertEqual(self.window._sidebar.width(), 248)  # 1100 to 1279 wide
        self.window.resize(1300, 760)
        wait(30)
        self.assertEqual(self.window._sidebar.width(), 296)

    def test_ctrl_b_parks_the_toggle_in_the_rail_and_back(self):
        from PySide6.QtWidgets import QWidget

        rail = self.window.findChild(QWidget, "Rail")
        self._go("workflows")
        self.assertTrue(self.window._sidebar.isAncestorOf(self.window._sidebar_toggle))
        self.window._shortcuts["Ctrl+B"].activated.emit()
        wait(20)
        self.assertFalse(self.window._sidebar.isVisible())
        self.assertTrue(rail.isAncestorOf(self.window._sidebar_toggle))
        self.assertTrue(self.window._sidebar_toggle.isVisible())
        self.assertEqual(self.window._sidebar_toggle.toolTip(), "Show sidebar  Ctrl+B")
        self.window._shortcuts["Ctrl+B"].activated.emit()
        wait(20)
        self.assertTrue(self.window._sidebar.isVisible())
        self.assertTrue(self.window._sidebar.isAncestorOf(self.window._sidebar_toggle))

    def test_cancel_sits_in_the_active_view_header_while_running(self):
        self._go("workflows")
        self.assertFalse(self.window._cancel_btn.isVisible())
        self.window._mark_operation(True, "Task running...")
        wait(20)
        header = self.window._views["workflows"]._page_header
        self.assertTrue(header.isAncestorOf(self.window._cancel_btn))
        self.assertTrue(self.window._cancel_btn.isVisible())
        self.assertEqual(self.window._status_label.text(), "Task running...")
        self.assertFalse(self.window._new_task_btn.isEnabled())
        self._go("runs")
        self.assertTrue(
            self.window._views["runs"]._page_header.isAncestorOf(self.window._cancel_btn)
        )
        self.assertTrue(self.window._cancel_btn.isVisible())
        self.window._set_status_idle()
        self.assertFalse(self.window._cancel_btn.isVisible())
        self.assertTrue(self.window._new_task_btn.isEnabled())

    def test_new_task_rail_button_opens_the_dialog_flow(self):
        with patch.object(type(self.window), "open_new_task") as opened:
            self.window._new_task_btn.click()
        opened.assert_called_once()
        self.assertEqual(self.window._new_task_btn.toolTip(), "New task  Ctrl+N")

    def test_recovery_bar_is_inline_above_the_views(self):
        self.window._show_recovery_banner()
        content = self.window._content
        self.assertTrue(content.isAncestorOf(self.window._recovery_banner))
        # The layout settles on a later event pass (slower under load).
        self.assertTrue(wait_until(lambda: self.window._stack.y() > 0))
        self.assertLess(self.window._recovery_banner.y(), self.window._stack.y())

    def test_toasts_anchor_bottom_right_of_the_content_inset_16_max_3(self):
        from agentops.gui.toast import ToastHost

        for index in range(4):
            self.window.toast(f"Toast {index}", "", "info")
        host = self.window.findChild(ToastHost)
        self.assertTrue(wait_until(lambda: len(host._toasts) == 3 and host.height() > 0))
        content = self.window._content
        origin = content.mapTo(self.window, content.rect().topLeft())
        self.assertEqual(host.geometry().right() + 1, origin.x() + content.width() - 16)
        self.assertEqual(host.geometry().bottom() + 1, origin.y() + content.height() - 16)
        self.assertGreaterEqual(host.geometry().left(), origin.x())


if __name__ == "__main__":
    unittest.main()
