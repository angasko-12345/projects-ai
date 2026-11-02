"""The 52px icon rail that replaced the text navigation (Phase B2)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, qt_app, requires_qt, wait
    from tests.test_gui_qt import REPOSITORY, FakeController
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, requires_qt, wait
    from test_gui_qt import REPOSITORY, FakeController


@requires_qt
class IconRailTest(unittest.TestCase):
    def setUp(self):
        from agentops.gui.settings import AppSettings
        from agentops.gui.shell import MainWindow

        qt_app()
        settings = AppSettings()
        settings.touch_repository(REPOSITORY)
        self.window = MainWindow(FakeController(), settings=settings)
        self.addCleanup(self._teardown)
        self.window.resize(960, 620)
        self.window.show()
        wait(50)

    def _teardown(self):
        from PySide6.QtWidgets import QMessageBox

        with patch(
            "agentops.gui.shell.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            destroy(self.window)

    def test_one_icon_button_per_view_in_view_specs_order(self):
        from agentops.gui.shell import _RAIL_ICONS, RAIL_WIDTH
        from agentops.gui.views import VIEW_SPECS

        self.assertEqual(list(self.window._nav_buttons), [v for v, _t, _f in VIEW_SPECS])
        for index, (view_id, title, _factory) in enumerate(VIEW_SPECS):
            button = self.window._nav_buttons[view_id]
            self.assertEqual(button.icon_name(), _RAIL_ICONS[view_id])
            self.assertEqual(button.toolTip(), f"{title}  Ctrl+{(index + 1) % 10}")
            self.assertEqual(button.accessibleName(), title)
            self.assertTrue(button.isVisible(), view_id)
        rail = self.window.findChild(type(self.window._sidebar), "Rail")
        self.assertEqual(rail.width(), RAIL_WIDTH)

    def test_settings_is_pinned_below_status_and_palette(self):
        buttons = self.window._nav_buttons
        settings_y = buttons["settings"].mapTo(self.window, buttons["settings"].rect().topLeft())
        palette = self.window._palette_btn
        palette_y = palette.mapTo(self.window, palette.rect().topLeft())
        dot_y = self.window._status_dot.mapTo(self.window, self.window._status_dot.rect().topLeft())
        artifacts = buttons["artifacts"].mapTo(self.window, buttons["artifacts"].rect().topLeft())
        self.assertLess(artifacts.y(), dot_y.y())
        self.assertLess(dot_y.y(), palette_y.y())
        self.assertLess(palette_y.y(), settings_y.y())
        self.assertEqual(self.window._status_dot.toolTip(), "Idle")

    def test_rail_click_and_shortcuts_navigate_and_select(self):
        from agentops.gui.views import VIEW_SPECS

        self.window._nav_buttons["runs"].click()
        wait(20)
        self.assertIs(self.window.active_view(), self.window._views["runs"])
        self.assertTrue(self.window._nav_buttons["runs"].isChecked())
        for index, (view_id, _title, _factory) in enumerate(VIEW_SPECS):
            self.window._shortcuts[f"Ctrl+{(index + 1) % 10}"].activated.emit()
            wait(10)
            self.assertIs(self.window.active_view(), self.window._views[view_id])
            checked = [vid for vid, b in self.window._nav_buttons.items() if b.isChecked()]
            self.assertEqual(checked, [view_id])

    def test_palette_button_opens_the_command_palette(self):
        with patch.object(self.window, "open_palette") as opened:
            # Rebind: the button captured the bound method at construction.
            self.window._palette_btn.clicked.disconnect()
            self.window._palette_btn.clicked.connect(lambda _c=False: self.window.open_palette())
            self.window._palette_btn.click()
        opened.assert_called_once()

    def test_failures_badge_follows_the_dashboard_summary(self):
        view = self.window._views["dashboard"]
        badge = self.window._nav_buttons["failures"]
        summary = {
            "repository": REPOSITORY,
            "needs_attention": [{"severity": "HIGH"}, {"severity": "low"}],
        }
        view._on_summary(summary)
        self.assertEqual(badge.badge(), 2)
        view._on_summary(dict(summary, needs_attention=[]))
        self.assertEqual(badge.badge(), 0)


if __name__ == "__main__":
    unittest.main()
