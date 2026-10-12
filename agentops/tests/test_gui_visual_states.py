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
    from tests.qt_display import destroy, make_context, qt_app, requires_qt, wait, wait_until
    from tests.test_gui_qt import (
        FAILURES, REPOSITORY, RUNS, TASKS, VERIFICATIONS, FakeController)
except ModuleNotFoundError:
    from qt_display import destroy, make_context, qt_app, requires_qt, wait, wait_until
    from test_gui_qt import (
        FAILURES, REPOSITORY, RUNS, TASKS, VERIFICATIONS, FakeController)


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

    def test_toast_notification_is_laid_out_visible(self):
        # A toast added to the host layout is still hidden on the same event
        # pass, and box layouts exclude hidden widgets from size hints and
        # geometry. Sizing the host before the toast shows leaves a 24px
        # sliver, so no success/error feedback ever reaches the user.
        from agentops.gui.toast import ToastHost

        self.window.toast("Repository switched", "D:/repo", "info")
        host = self.window.findChild(ToastHost)
        self.assertIsNotNone(host)
        self.assertTrue(
            wait_until(lambda: host._toasts and host._toasts[0].isVisible()),
            "toast never became visible",
        )
        toast = host._toasts[0]
        self.assertGreater(toast.height(), 0)
        self.assertLessEqual(host.geometry().right(), self.window.width())
        self.assertLessEqual(host.geometry().bottom(), self.window.height())

    def test_workflow_tally_splits_task_and_check_counts(self):
        # Nine tally blocks in one row overflow the detail pane at the
        # minimum window width, clipping the check counts. Task outcomes
        # and check totals get their own rows so every count stays visible.
        view = self.window._views["workflows"]
        view._apply_tally(TASKS, RUNS, VERIFICATIONS, FAILURES)
        self.assertEqual(view._tally.text_for("Tasks"), "4")
        self.assertEqual(view._tally.text_for("Passed"), "1")
        self.assertEqual(view._tally.text_for("Failed"), "0")
        self.assertEqual(view._tally.text_for("Runs"), "3")
        self.assertEqual(view._tally.text_for("Failures"), "1")
        self.assertEqual(len(view._tally._items), 6)
        self.assertEqual(view._checks_tally.text_for("Checks passed"), "3")
        self.assertEqual(view._checks_tally.text_for("Checks failed"), "1")
        self.assertEqual(view._checks_tally.text_for("Checks skipped"), "0")
        self.assertEqual(len(view._checks_tally._items), 3)

    def test_activity_feed_wraps_long_entries(self):
        # Single-line activity items wider than the column force a
        # horizontal scrollbar and truncate entries mid-word; wrapped rows
        # keep the full timestamped message readable without scrolling.
        view = self.window._views["dashboard"]
        self.assertTrue(view._activity_list.wordWrap())

    def test_status_columns_paint_pills_while_text_columns_do_not(self):
        # Lifecycle states render as tinted pills in every table; the
        # delegate is wired by column key so all surfaces share the idiom.
        from agentops.gui.widgets import StatusPillDelegate

        panel = self.window._views["workflows"]._list
        keys = [column.key for column in panel.model.columns]
        pill_index = keys.index("status")
        self.assertIsInstance(
            panel.view.itemDelegateForColumn(pill_index), StatusPillDelegate)
        text_index = keys.index("description")
        self.assertNotIsInstance(
            panel.view.itemDelegateForColumn(text_index), StatusPillDelegate)

    def test_truncated_cells_expose_their_full_text_as_tooltips(self):
        from PySide6.QtCore import Qt
        from agentops.gui.widgets import TableColumn, TableModel

        model = TableModel(
            [TableColumn("id", "ID", 90)], [{"id": "failure-1234"}])
        index = model.index(0, 0)
        self.assertEqual(
            model.data(index, Qt.ItemDataRole.ToolTipRole), "failure-1234")
        self.assertEqual(
            model.data(index, Qt.ItemDataRole.ToolTipRole),
            model.data(index, Qt.ItemDataRole.DisplayRole))

    def test_combo_arrow_asset_exists_and_is_referenced(self):
        asset = ASSET_DIR / "chevron-down.svg"
        self.assertTrue(asset.is_file(), f"missing UI asset: {asset}")
        self.assertIn(asset.as_posix(), build_stylesheet())

    def test_stat_cards_navigate_to_their_views(self):
        # Dashboard headline numbers are shortcuts: activating a card must
        # land on the view behind the number, not just restyle the card.
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        self._activate("dashboard")
        view = self.window._views["dashboard"]
        self.assertTrue(wait_until(lambda: view._summary_loaded))
        for card, target in (
            (view._workflows_card, "workflows"),
            (view._tasks_card, "tasks"),
            (view._verification_card, "verification"),
        ):
            card.clicked.emit()
            self.assertIs(
                self.window._stack.currentWidget(),
                self.window._views[target])
        # The real path matters too: a mouse press must navigate, and every
        # card must stay keyboard-focusable for Enter/Space activation.
        self._activate("dashboard")
        QTest.mouseClick(view._agents_card,
                         Qt.MouseButton.LeftButton)
        self.assertIs(
            self.window._stack.currentWidget(),
            self.window._views["agents"])
        for card in (view._workflows_card, view._tasks_card,
                     view._verification_card, view._agents_card):
            self.assertTrue(card.focusPolicy() != Qt.FocusPolicy.NoFocus)

    def test_verification_health_stat_turns_red_on_failure(self):
        # The canned summary carries one failed verification, so the health
        # number must render in the danger color; a clean summary flips it
        # back to success instead of leaving the stale red behind.
        self._activate("dashboard")
        view = self.window._views["dashboard"]
        self.assertTrue(wait_until(lambda: view._summary_loaded))
        self.assertEqual(view._verification_stat.text(), "1")
        self.assertIn(DARK.danger, view._verification_stat.styleSheet())
        view._on_summary({
            "repository": REPOSITORY,
            "total_workflows": 1,
            "active_workflows": {"running": 0, "pending": 0},
            "task_counts": {},
            "verification_status": {"passed": 2},
            "needs_attention": [],
            "recent_workflows": [],
            "recent_events": [],
        })
        self.assertEqual(view._verification_stat.text(), "2")
        self.assertIn(DARK.success, view._verification_stat.styleSheet())

    def test_control_center_tabs_show_live_record_counts(self):
        # Tab labels carry the current record counts so the operator sees
        # which pane holds data without counting table rows.
        view = self.window._views["workflows"]
        self._activate("workflows")
        self.assertTrue(wait_until(lambda: view._list.proxy.rowCount() == 1))
        view._list.select_row_index(0)
        self.assertTrue(wait_until(lambda: view._tasks.proxy.rowCount() == 4))
        self.assertEqual(view._tabs.tabText(0), "Tasks (4)")
        self.assertEqual(view._tabs.tabText(1), "Runs (3)")
        self.assertEqual(view._tabs.tabText(2), "Verification (2)")
        self.assertEqual(view._tabs.tabText(3), "Failures (1)")

    def test_task_action_buttons_follow_the_selection(self):
        # Rename/Category/Delete act on the selected row, so they stay
        # disabled until a task is picked — no action without a target.
        view = self.window._views["tasks"]
        self.assertFalse(view._rename_btn.isEnabled())
        self.assertFalse(view._category_btn.isEnabled())
        self.assertFalse(view._delete_btn.isEnabled())
        self._activate("tasks")
        self.assertTrue(wait_until(lambda: view._table.proxy.rowCount() == 4))
        view._table.select_row_index(0)
        self.assertTrue(wait_until(lambda: view._rename_btn.isEnabled()))
        self.assertTrue(view._category_btn.isEnabled())
        self.assertTrue(view._delete_btn.isEnabled())

    def test_rename_task_calls_through_with_the_edited_text(self):
        calls: list[tuple] = []
        self.controller.rename_task = (  # type: ignore[attr-defined]
            lambda directory, task_id, description: calls.append(
                (directory, task_id, description)) or {
                    "id": task_id, "description": description,
                    "workflow_id": "wf-1"})
        view = self.window._views["tasks"]
        self._activate("tasks")
        self.assertTrue(wait_until(lambda: view._table.proxy.rowCount() == 4))
        view._table.select_row_index(1)
        row = view._table.selected_row()
        assert row is not None
        expected_id = str(row.get("id") or "")
        with patch("agentops.gui.views.tasks.QInputDialog.getText",
                   return_value=("renamed from GUI", True)):
            view._rename_selected()
        self.assertTrue(wait_until(lambda: len(calls) == 1))
        self.assertEqual(calls[0][1], expected_id)
        self.assertEqual(calls[0][2], "renamed from GUI")

    def test_delete_task_confirms_and_clears_the_selection(self):
        calls: list[tuple] = []
        self.controller.delete_task = (  # type: ignore[attr-defined]
            lambda directory, task_id: calls.append((directory, task_id)) or {
                "id": task_id, "description": "gone", "workflow_id": "wf-1"})
        from PySide6.QtWidgets import QMessageBox

        view = self.window._views["tasks"]
        self._activate("tasks")
        self.assertTrue(wait_until(lambda: view._table.proxy.rowCount() == 4))
        view._table.select_row_index(0)
        self.assertTrue(wait_until(lambda: view._delete_btn.isEnabled()))
        with patch("agentops.gui.views.tasks.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            view._delete_selected()
        self.assertTrue(wait_until(lambda: len(calls) == 1))

    def test_agent_detail_explains_unavailable_agents(self):
        # Screenshot bug: the detail pane showed a bare placeholder with an
        # empty grid above it. It now swaps placeholder/content and says why
        # an agent cannot run.
        from agentops.gui.detail import AgentDetailPanel

        view = self.window._views["agents"]
        self.assertIsInstance(view._detail, AgentDetailPanel)
        view.fill_detail(None)
        self.assertNotEqual(
            view._detail._stack.currentWidget(), view._detail._content)
        view.fill_detail({
            "display_name": "hermes-agent", "identifier": "hermes-agent",
            "executable_path": None, "detected_version": None,
            "availability": False, "configured_priority": 0,
            "metadata": {}, "roles": [], "capabilities": [],
        })
        self.assertEqual(
            view._detail._stack.currentWidget(), view._detail._content)
        grid_texts = [
            view._detail._grid._slots[column].itemAt(row).layout().itemAt(1).widget().text()
            for column in range(2)
            for row in range(view._detail._grid._slots[column].count())
        ]
        self.assertTrue(any("Executable not found" in text for text in grid_texts))
        view.fill_detail({
            "display_name": "kilo", "identifier": "kilo",
            "executable_path": "D:/kilo.CMD",
            "detected_version": "EPERM: operation not permitted",
            "availability": False, "configured_priority": 0,
            "metadata": {}, "roles": [], "capabilities": [],
        })
        grid_texts = [
            view._detail._grid._slots[column].itemAt(row).layout().itemAt(1).widget().text()
            for column in range(2)
            for row in range(view._detail._grid._slots[column].count())
        ]

    def test_failure_table_shows_suffixes_and_plain_words(self):
        # Screenshot bug: every failure id read "failure-" and recovery /
        # action cells showed raw snake_case. Ids render their unique tail
        # and snake case becomes plain words.
        from agentops.gui.views.failures import _COLUMNS

        by_key = {column.key: column for column in _COLUMNS}
        format_id = by_key["id"].format
        assert format_id is not None
        identity = "failure-71f34b4e9d2a"
        rendered = format_id(identity)
        self.assertNotEqual(rendered, "failure-")
        self.assertTrue(identity.endswith(rendered))
        self.assertEqual(
            by_key["recovery_state"].format("interrupted_agent_execution"),
            "interrupted agent execution")
        self.assertEqual(
            by_key["recommended_action"].format("retry_same_agent"),
            "retry same agent")

    def test_dashboard_hero_hands_its_text_to_task_creation(self):
        from dataclasses import replace

        received: list[str] = []
        self._activate("dashboard")
        view = self.window._views["dashboard"]
        view.ctx = replace(view.ctx, new_task=received.append)
        view._hero_input.setPlainText("  hero task  ")
        view._start_hero_task()
        self.assertEqual(received, ["hero task"])
        view._hero_input.setPlainText("   ")
        view._start_hero_task()
        self.assertEqual(received, ["hero task"])

    def test_new_task_dialog_accepts_a_prefilled_description(self):
        from agentops.gui.new_task import NewTaskDialog

        context = make_context(object())
        dialog = NewTaskDialog(context, initial_description="prefilled work")
        try:
            self.assertEqual(
                dialog._description.toPlainText(), "prefilled work")
        finally:
            dialog.deleteLater()



if __name__ == "__main__":
    unittest.main()
