"""Focused regression tests for desktop visual-state behavior.

Guards the shell/view invariants a refactor can silently break: sidebar
group coverage, per-view page headers, dashboard empty-state visibility,
the list-detail error cycle, the sidebar toggle's discoverability path,
the navigation fade releasing its graphics effect (a persistent opacity
effect left stale regions from the previous view), and the combo-arrow
asset wiring. Widget internals are reached the same way ``test_gui_qt``
does; no persistence logic is re-tested here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentops.gui.tokens import ASSET_DIR, DARK, build_stylesheet

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from tests.test_gui_qt import REPOSITORY, FakeController
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from test_gui_qt import REPOSITORY, FakeController


# Every test in this class builds a real MainWindow, and `setUp` imports
# `agentops.gui.shell` (PySide6) before `qt_app()` can raise SkipTest. Without
# this marker the class errors on a machine without the optional desktop
# dependency instead of skipping, which is what turned the agentops CI job red.
@requires_qt
class VisualStateTest(unittest.TestCase):
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
        from PySide6.QtWidgets import QMessageBox

        with patch("agentops.gui.shell.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            destroy(self.window)

    def _activate(self, view_id: str) -> None:
        self.window.navigate(view_id)
        wait(50)

    # ------------------------------------------------------------------
    def test_nav_groups_cover_every_view_exactly_once(self):
        from agentops.gui.shell import _NAV_GROUPS
        from agentops.gui.views import VIEW_SPECS

        grouped = [view_id for _name, ids in _NAV_GROUPS for view_id in ids]
        declared = [view_id for view_id, _title, _factory in VIEW_SPECS]
        self.assertEqual(sorted(grouped), sorted(declared))
        self.assertEqual(len(grouped), len(set(grouped)),
                         "a view id appears in more than one nav group")

    def test_every_view_has_a_matching_page_header(self):
        for view_id, view in self.window._views.items():
            header = getattr(view, "_page_header", None)
            self.assertIsNotNone(header, f"{view_id} has no page header")
            self.assertEqual(header._heading.text(), view.title, view_id)

    def test_dashboard_hides_empty_lists_and_shows_populated(self):
        self._activate("dashboard")
        view = self.window._views["dashboard"]

        empty_payload = {
            "repository": REPOSITORY,
            "total_workflows": 0,
            "active_workflows": {},
            "task_counts": {},
            "verification_status": {},
            "needs_attention": [],
            "recent_workflows": [],
            "recent_events": [],
        }
        view._on_summary(empty_payload)
        self.assertFalse(view._attention_list.isVisible())
        self.assertTrue(view._attention_empty.isVisible())
        self.assertFalse(view._activity_list.isVisible())
        self.assertTrue(view._activity_empty.isVisible())
        self.assertIn("0 workflows", view._page_header._subtitle.text())

        populated = dict(
            empty_payload,
            total_workflows=3,
            needs_attention=[{
                "category": "test_failure",
                "severity": "high",
                "recommended_action": "retry_same_agent",
                "primary_error": "assertion failed",
                "created_at": "2026-10-04 10:00:00",
            }],
            recent_events=[{
                "timestamp": "2026-10-04 10:00:00",
                "message": "workflow started",
            }],
        )
        view._on_summary(populated)
        self.assertTrue(view._attention_list.isVisible())
        self.assertFalse(view._attention_empty.isVisible())
        self.assertEqual(view._attention_list.count(), 1)
        first = view._attention_list.item(0)
        self.assertEqual(first.foreground().color().name(), DARK.danger)
        self.assertIn("assertion failed", first.toolTip())
        self.assertTrue(view._activity_list.isVisible())
        self.assertFalse(view._activity_empty.isVisible())
        self.assertEqual(view._activity_list.count(), 1)

    def test_listdetail_error_state_restores_after_first_load(self):
        # Fresh view: not loaded yet, so a load failure must surface as an
        # error state instead of the permanent empty heading.
        view = self.window._views["tasks"]
        self.assertFalse(view._loaded)
        view._on_load_error("state store unavailable")
        self.assertEqual(view._table._empty._heading.text(),
                         "State unavailable")

        # Once rows arrive the normal empty heading is restored.
        self._activate("tasks")
        self.assertTrue(wait_until(lambda: view._loaded))
        self.assertTrue(wait_until(
            lambda: view._table._empty._heading.text() == "No tasks yet"))

    def test_sidebar_toggle_is_always_visible_in_the_top_bar(self):
        with tempfile.TemporaryDirectory() as directory:
            self.window._settings_file = Path(directory) / "settings.json"
            self.window.update_settings({"sidebar_collapsed": True})
            self.assertFalse(self.window._sidebar.isVisible())
            self.assertEqual(self.window._sidebar_toggle.text(), "Show sidebar")
            self.assertTrue(self.window._sidebar_toggle.isVisible())

            # The top-bar toggle must restore the sidebar it hid.
            self.window._sidebar_toggle.click()
            wait(20)
            self.assertTrue(self.window._sidebar.isVisible())
            self.assertEqual(self.window._sidebar_toggle.text(), "Hide sidebar")

    def test_navigation_fade_releases_its_graphics_effect(self):
        # The fade effect must not stay installed after the transition:
        # a persistent opacity effect buffers the whole stack offscreen and
        # left stale regions from the previous view behind.
        self._activate("settings")
        self._activate("workflows")
        wait(300)  # fade is 140ms; allow the animation to finish
        self.assertIsNone(self.window._stack.graphicsEffect())

    def test_combo_arrow_asset_exists_and_is_referenced(self):
        asset = ASSET_DIR / "chevron-down.svg"
        self.assertTrue(asset.is_file(), f"missing UI asset: {asset}")
        self.assertIn(asset.as_posix(), build_stylesheet())


if __name__ == "__main__":
    unittest.main()
