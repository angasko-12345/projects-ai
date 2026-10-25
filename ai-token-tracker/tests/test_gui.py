import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

# The default-location rule lives in token_tracker.db (Qt-free) so it can be
# unit-tested without the optional PySide6 dependency; the GUI re-exports it
# and only the windowing itself needs Qt.
from token_tracker.db import default_db_path

# Widgets need a platform plugin; offscreen keeps the suite deterministic on a
# headless machine and on a desktop alike. Must be set before PySide6 loads.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from token_tracker import db
    from token_tracker.gui import PALETTE, MainWindow

    _HAS_QT = True
except ImportError:  # PySide6 is optional: the GUI is the only consumer
    _HAS_QT = False


class DatabasePathTest(unittest.TestCase):
    def test_directory_override_resolves_to_usage_database(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"AI_TOKEN_TRACKER_DB": directory}):
                self.assertEqual(default_db_path(), Path(directory) / "usage.db")

    def test_file_override_is_used_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tracker.db"
            with mock.patch.dict(os.environ, {"AI_TOKEN_TRACKER_DB": str(database)}):
                self.assertEqual(default_db_path(), database)


@unittest.skipUnless(_HAS_QT, "PySide6 is not installed")
class SyncSourcesTest(unittest.TestCase):
    """The collector sync runs off the GUI thread and reports back to it."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "usage.db")
        self.addCleanup(self.conn.close)
        self.window = MainWindow(self.conn)
        self.addCleanup(self._close_window)
        self._clear_override_cursors()

    def _close_window(self):
        thread = self.window._sync_thread
        if thread is not None and thread.isRunning():
            thread.wait(5000)
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()
        self._clear_override_cursors()

    @staticmethod
    def _clear_override_cursors():
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

    def _settle(self):
        """Wait for the worker and deliver its queued signals, without exec()."""
        thread = self.window._sync_thread
        if thread is not None:
            self.assertTrue(thread.wait(5000), "sync worker did not finish")
        for _ in range(5):
            QApplication.processEvents()

    def test_slow_collector_runs_off_thread_and_restores_the_ui(self):
        started = threading.Event()
        release = threading.Event()
        seen = {}

        def slow_sync(conn, cancel=None):
            seen["thread"] = threading.get_ident()
            seen["conn"] = conn
            started.set()
            release.wait(5)
            return {"fake-source": {"status": "ok", "inserted": 3}}

        with mock.patch("token_tracker.gui.collectors.sync_all", side_effect=slow_sync):
            self.window.sync_sources()
            # Busy state is applied before the worker even starts.
            self.assertFalse(self.window.import_button.isEnabled())
            self.assertEqual(self.window.sync_label.text(), "Syncing sources...")
            self.assertIsNotNone(QApplication.overrideCursor())
            self.assertEqual(QApplication.overrideCursor().shape(), Qt.WaitCursor)

            self.assertTrue(started.wait(5), "the worker never ran the collector")
            self.assertNotEqual(seen["thread"], threading.get_ident())
            # The worker must not share the GUI thread's sqlite3 connection.
            self.assertIsNot(seen["conn"], self.window.conn)
            # Still busy while the collector is blocked.
            self.assertFalse(self.window.import_button.isEnabled())
            self.assertIsNotNone(QApplication.overrideCursor())

            release.set()
            self._settle()

        self.assertTrue(self.window.import_button.isEnabled())
        self.assertIsNone(QApplication.overrideCursor())
        self.assertIn("3 new across 1 sources", self.window.sync_label.text())
        self.assertNotIn(PALETTE["amber"], self.window.sync_label.styleSheet())

    def test_failing_collector_reports_and_re_enables_the_button(self):
        def broken_sync(conn, cancel=None):
            raise RuntimeError("database is locked")

        with mock.patch("token_tracker.gui.collectors.sync_all", side_effect=broken_sync):
            self.window.sync_sources()
            self._settle()

        self.assertTrue(self.window.import_button.isEnabled())
        self.assertIsNone(QApplication.overrideCursor())
        self.assertEqual(self.window.sync_label.text(), "Sync failed: database is locked")
        self.assertIn(PALETTE["amber"], self.window.sync_label.styleSheet())
        self.assertEqual(
            self.window.status.currentMessage(), "Sync failed: database is locked"
        )

    def test_reentrant_click_does_not_start_a_second_worker(self):
        calls = []
        started = threading.Event()
        release = threading.Event()

        def slow_sync(conn, cancel=None):
            calls.append(1)
            started.set()
            release.wait(5)
            return {"fake-source": {"status": "ok", "inserted": 0}}

        with mock.patch("token_tracker.gui.collectors.sync_all", side_effect=slow_sync):
            self.window.sync_sources()
            first = self.window._sync_thread
            self.assertTrue(started.wait(5))
            self.window.sync_sources()  # a second click while the first runs
            self.assertIs(self.window._sync_thread, first)
            release.set()
            self._settle()

        self.assertEqual(len(calls), 1)
        self.assertTrue(self.window.import_button.isEnabled())

    def test_close_during_sync_cancels_and_waits_for_the_worker(self):
        started = threading.Event()
        seen = {}

        def slow_sync(conn, cancel=None):
            started.set()
            # The collector in flight is not interruptible: the close must
            # outlast it instead of giving up after a fixed timeout.
            cancel.wait(5)
            seen["cancelled"] = cancel.is_set()
            threading.Event().wait(0.3)
            return {}

        with mock.patch("token_tracker.gui.collectors.sync_all", side_effect=slow_sync):
            self.window.sync_sources()
            thread = self.window._sync_thread
            self.assertTrue(started.wait(5))
            self.window.close()
            self.assertFalse(thread.isRunning(), "close returned with the sync still running")

        self.assertTrue(seen["cancelled"])
        self.assertTrue(self.window.isHidden())
        self.assertIsNone(QApplication.overrideCursor())


@unittest.skipUnless(_HAS_QT, "PySide6 is not installed")
class WindowLifetimeTest(unittest.TestCase):
    """Dropping the last reference must destroy the window right away.

    A window kept alive by a slot cycle is destroyed during interpreter
    shutdown after QApplication, which segfaults at exit.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_is_not_kept_alive_by_its_own_slots(self):
        import gc
        import weakref

        with tempfile.TemporaryDirectory() as directory:
            conn = db.connect(Path(directory) / "usage.db")
            try:
                window = MainWindow(conn)
                ref = weakref.ref(window)
                gc.disable()  # only plain refcounting, as at interpreter exit
                try:
                    del window
                    self.assertIsNone(ref(), "MainWindow survived its last reference")
                finally:
                    gc.enable()
            finally:
                conn.close()

    def test_filters_and_breakdown_selectors_still_repopulate(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = db.connect(Path(directory) / "usage.db")
            try:
                window = MainWindow(conn)
                combo, table = window.breakdown_panes[0]
                combo.setCurrentText("Project")
                self.assertEqual(table.horizontalHeaderItem(0).text(), "Project")
                with mock.patch.object(window, "_populate_events_table") as populate:
                    window.filter_kind.setCurrentText("Exact only")
                    populate.assert_called_once()
                window.filter_search.setText("x")
                self.assertTrue(window._filter_timer.isActive())
                window.close()
                del window
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
