"""Regression tests for the windowed desktop bootstrap.

``AgentOps.exe`` has no console. These tests pin the behaviour that keeps a
double-clicked application debuggable anyway: usable stdio, a log file that
records startup, and a fatal-error report the user can actually see.
"""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from agentops.gui import launcher


class EnsureStdioTests(unittest.TestCase):
    def test_replaces_absent_streams(self):
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            launcher.ensure_stdio()
            sys.stdout.write("no console, but writing must not raise")
            sys.stdout.flush()
            self.assertFalse(sys.stderr.isatty())

    def test_keeps_existing_streams(self):
        original = sys.stderr
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", original):
            launcher.ensure_stdio()
            self.assertIsInstance(sys.stdout, launcher._NullStream)
            self.assertIs(sys.stderr, original)


class LogTests(unittest.TestCase):
    def test_writes_timestamped_line(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "nested" / launcher.LOG_FILE_NAME
            self.assertTrue(launcher.write_log("starting", target))
            content = target.read_text(encoding="utf-8")
            self.assertIn("starting", content)
            self.assertRegex(content, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z starting\n$")

    def test_appends_rather_than_truncates(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "agentops.log"
            launcher.write_log("first", target)
            launcher.write_log("second", target)
            self.assertEqual(len(target.read_text(encoding="utf-8").strip().splitlines()), 2)

    def test_unwritable_destination_reports_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            blocker = Path(temp) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            self.assertFalse(launcher.write_log("nope", blocker / "agentops.log"))

    def test_log_path_sits_beside_settings(self):
        with mock.patch.object(launcher, "settings_path", return_value=Path("/x/settings.json")):
            self.assertEqual(launcher.log_path(), Path("/x/logs/agentops.log"))


class ExceptionHookTests(unittest.TestCase):
    def test_records_unhandled_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "agentops.log"
            with mock.patch.object(launcher, "log_path", return_value=target):
                try:
                    raise ValueError("boom")
                except ValueError as error:
                    launcher._log_unhandled_exception(type(error), error, error.__traceback__)
            content = target.read_text(encoding="utf-8")
            self.assertIn("ValueError: boom", content)
            self.assertIn("Traceback", content)

    def test_thread_exception_hook_is_wired(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "agentops.log"
            with mock.patch.object(launcher, "log_path", return_value=target):
                try:
                    raise RuntimeError("thread boom")
                except RuntimeError as error:
                    launcher._log_unhandled_thread_exception(
                        threading.ExceptHookArgs(
                            (type(error), error, error.__traceback__, threading.current_thread())
                        )
                    )
            self.assertIn("RuntimeError: thread boom", target.read_text(encoding="utf-8"))

    def test_keyboard_interrupt_keeps_default_behaviour(self):
        with mock.patch.object(sys, "__excepthook__") as default_hook:
            launcher._log_unhandled_exception(KeyboardInterrupt, KeyboardInterrupt(), None)
        default_hook.assert_called_once()


class ReportFatalTests(unittest.TestCase):
    def test_reports_to_injected_box_with_log_location(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "logs" / "agentops.log"
            shown: list[tuple[str, str]] = []
            with mock.patch.object(launcher, "log_path", return_value=target):
                launcher.report_fatal(
                    ImportError("no PySide6"),
                    f"Details were written to:\n{target}",
                    box=lambda title, message: shown.append((title, message)),
                )
            title, message = shown[0]
            self.assertEqual(title, "AgentOps")
            self.assertIn("ImportError: no PySide6", message)
            self.assertIn(str(target), message)
            self.assertIn("ImportError", target.read_text(encoding="utf-8"))

    def test_detail_is_optional(self):
        shown: list[tuple[str, str]] = []
        launcher.report_fatal(
            ValueError("plain"), box=lambda title, message: shown.append((title, message))
        )
        self.assertEqual(shown[0][1], "ValueError: plain")


class RunTests(unittest.TestCase):
    def test_missing_pyside6_is_reported_not_raised(self):
        reported: list[BaseException] = []
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "agentops.log"
            with mock.patch.object(launcher, "log_path", return_value=target), \
                    mock.patch.object(launcher, "install_handlers", return_value=target), \
                    mock.patch.object(launcher, "report_fatal") as fatal:
                fatal.side_effect = lambda error, detail="": reported.append(error)
                with mock.patch.dict(sys.modules, {"agentops.gui": None}):
                    self.assertEqual(launcher.run(), 1)
                self.assertEqual(len(reported), 1)
                self.assertIsInstance(reported[0], ImportError)
                self.assertIn("starting", target.read_text(encoding="utf-8"))

    def test_startup_failure_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "agentops.log"
            with mock.patch.object(launcher, "log_path", return_value=target), \
                    mock.patch.object(launcher, "install_handlers", return_value=target), \
                    mock.patch.object(launcher, "report_fatal") as fatal, \
                    mock.patch("agentops.gui.main", side_effect=OSError("no display")):
                self.assertEqual(launcher.run(), 1)
            fatal.assert_called_once()
            self.assertIn("startup failed", target.read_text(encoding="utf-8"))

    def test_clean_exit_returns_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "agentops.log"
            with mock.patch.object(launcher, "log_path", return_value=target), \
                    mock.patch.object(launcher, "install_handlers", return_value=target), \
                    mock.patch("agentops.gui.main") as gui_main:
                self.assertEqual(launcher.run(), 0)
            gui_main.assert_called_once_with()
            self.assertIn("exited cleanly", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()