"""Dashboard v2 (Phase D): header, charts row, empty states."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from tests.test_gui_qt import REPOSITORY, FakeController
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from test_gui_qt import REPOSITORY, FakeController

EMPTY_SUMMARY = {
    "repository": REPOSITORY,
    "total_workflows": 0,
    "active_workflows": {},
    "task_counts": {},
    "verification_status": {},
    "needs_attention": [],
    "recent_workflows": [],
    "recent_events": [],
}


def hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


@requires_qt
class DashboardTest(unittest.TestCase):
    def setUp(self):
        from agentops.gui.settings import AppSettings
        from agentops.gui.shell import MainWindow

        qt_app()
        settings = AppSettings()
        settings.touch_repository(REPOSITORY)
        self.window = MainWindow(FakeController(), settings=settings)
        self.addCleanup(self._teardown)
        self.window.show()
        self.window.navigate("dashboard")
        self.view = self.window._views["dashboard"]
        self.assertTrue(wait_until(lambda: self.view._summary_loaded))
        wait(50)

    def _teardown(self):
        from PySide6.QtWidgets import QMessageBox

        with patch(
            "agentops.gui.shell.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            destroy(self.window)

    def test_header_names_the_repository_and_summarises(self):
        self.assertEqual(self.view._repo_title.text(), "repo")
        subtitle = self.view._page_header._subtitle.text()
        self.assertTrue(subtitle.startswith("1 running · "), subtitle)
        self.assertIn("need attention", subtitle)
        self.assertTrue(subtitle.endswith("1 workflows"), subtitle)
        self.assertEqual(self.view._page_header._heading.text(), "Dashboard")

    def test_empty_repository_shows_empty_states_not_zero_charts(self):
        self.view._on_summary(EMPTY_SUMMARY)
        self.view._on_read("runs", [])
        self.assertFalse(self.view._runs_chart.isVisible())
        self.assertTrue(self.view._runs_empty.isVisible())
        self.assertIn("No agent runs", self.view._runs_empty.text())
        self.assertFalse(self.view._donut.isVisible())
        self.assertFalse(self.view._donut_legend.isVisible())
        self.assertTrue(self.view._donut_empty.isVisible())
        self.assertEqual(self.view._page_header._subtitle.text(), "0 workflows")

    def test_runs_chart_highlights_the_latest_peak(self):
        runs = [
            {"started_at": hours_ago(0.1), "status": "completed"},
            {"started_at": hours_ago(0.2), "status": "timed_out"},
        ]
        self.view._on_read("runs", runs)
        chart = self.view._runs_chart
        self.assertTrue(chart.isVisible())
        self.assertFalse(self.view._runs_empty.isVisible())
        self.assertEqual(chart._highlight, 13)  # today is the last of 14 days
        self.assertTrue(chart._tooltip.endswith("2 runs"), chart._tooltip)
        self.assertEqual(chart._series[1][2][-1], 1.0)  # the timed-out run
        self.assertFalse(self.view._window_note.isVisible())
        self.view._on_read("runs", runs * 250)
        self.assertTrue(self.view._window_note.isVisible())
        self.assertIn("newest 500 runs", self.view._window_note.text())

    def test_verification_donut_reads_the_summary_outcomes(self):
        self.view._on_summary(
            dict(EMPTY_SUMMARY, verification_status={"passed": 3, "FAILED": 1, "cancelled": 4})
        )
        self.assertTrue(self.view._donut.isVisible())
        self.assertEqual(self.view._donut._big, "75%")
        self.assertEqual(self.view._verification_failed.text(), "1")

    def test_week_cards_compare_with_the_previous_week(self):
        view = self.view
        view._on_summary(dict(EMPTY_SUMMARY, needs_attention=[{"severity": "high"}]))
        view._on_read("workflows", {"workflows": [{"created_at": hours_ago(2)}]})
        view._on_read(
            "runs",
            [
                {"started_at": hours_ago(2), "status": "completed", "duration_seconds": 4680},
                {"started_at": hours_ago(200), "status": "failed", "duration_seconds": 3600},
            ],
        )
        view._on_read(
            "verifications",
            [
                {"started_at": hours_ago(1), "overall_status": "passed"},
                {"started_at": hours_ago(1), "overall_status": "failed"},
                {"started_at": hours_ago(190), "overall_status": "passed"},
            ],
        )
        self.assertEqual(view._workflows_card.value.text(), "1")
        self.assertEqual(view._workflows_card.note.text(), "no prior week")
        self.assertEqual(view._verification_card.value.text(), "50%")
        self.assertEqual(view._verification_card.delta.text(), "\u221250 pts")
        self.assertEqual(view._agents_card.value.text(), "1h 18m")
        self.assertEqual(view._agents_card.delta.text(), "+30%")
        self.assertEqual(view._tasks_card.value.text(), "1")

    def test_stat_cards_wrap_two_by_two_on_a_narrow_page(self):
        self.window.resize(1440, 900)
        wait(50)
        self.assertEqual(self.view._week_columns, 4)
        self.window.resize(960, 700)
        wait(50)
        self.assertEqual(self.view._week_columns, 2)

    def test_new_task_button_opens_the_task_dialog(self):
        from dataclasses import replace

        from PySide6.QtWidgets import QPushButton

        opened: list[tuple] = []
        self.view.ctx = replace(self.view.ctx, new_task=lambda *args: opened.append(args))
        button = next(b for b in self.view.findChildren(QPushButton) if b.text() == "New task")
        button.click()
        self.assertEqual(opened, [()])


if __name__ == "__main__":
    unittest.main()
