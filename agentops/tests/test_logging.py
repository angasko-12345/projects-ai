import tempfile
import unittest
from pathlib import Path

from agentops.logging import LogManager


class LogManagerTests(unittest.TestCase):
    def test_read_tail_returns_text_and_truncation_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "logs"
            manager = LogManager(root)
            path = manager.write_run("task-1", "demo", "first\nsecond\n", "", "meta")
            name = path.name.replace(".meta.log", ".stdout.log")
            text, truncated = manager.read_tail(Path("task-1") / name, max_bytes=1024)
            self.assertFalse(truncated)
            self.assertIn("second", text)
            text, truncated = manager.read_tail(Path("task-1") / name, max_bytes=3)
            self.assertTrue(truncated)
            self.assertEqual(len(text.encode("utf-8", errors="replace")), 3)

    def test_read_tail_rejects_paths_outside_log_root(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = LogManager(Path(directory) / "logs")
            with self.assertRaises(ValueError):
                manager.read_tail("../outside.log")
            with self.assertRaises(FileNotFoundError):
                manager.read_tail("missing.log")
            with self.assertRaises(ValueError):
                manager.read_tail("x.log", max_bytes=0)
