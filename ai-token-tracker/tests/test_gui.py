import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# The default-location rule lives in token_tracker.db (Qt-free) so it can be
# unit-tested without the optional PySide6 dependency; the GUI re-exports it
# and only the windowing itself needs Qt.
from token_tracker.db import default_db_path


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


if __name__ == "__main__":
    unittest.main()
