import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from token_tracker.gui import resolve_db_path


class DatabasePathTest(unittest.TestCase):
    def test_directory_override_resolves_to_usage_database(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"AI_TOKEN_TRACKER_DB": directory}):
                self.assertEqual(resolve_db_path(), Path(directory) / "usage.db")

    def test_file_override_is_used_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tracker.db"
            with mock.patch.dict(os.environ, {"AI_TOKEN_TRACKER_DB": str(database)}):
                self.assertEqual(resolve_db_path(), database)


if __name__ == "__main__":
    unittest.main()
