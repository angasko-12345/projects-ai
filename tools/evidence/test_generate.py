"""Tests for the evidence generator's failure paths."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate  # noqa: E402


class TimeoutTests(unittest.TestCase):
    def test_a_timed_out_run_fails_cleanly_and_writes_nothing(self):
        # Regression: the timeout record had no `dirty_paths`, so build_document
        # raised KeyError: 'dirty_paths' instead of reporting the timeout.
        real_run = subprocess.run

        def fake_run(argv, *args, **kwargs):
            if argv and argv[0] == sys.executable:
                raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
            return real_run(argv, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "verification.json"
            stderr = io.StringIO()
            with (
                mock.patch.object(generate.subprocess, "run", side_effect=fake_run),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(stderr),
            ):
                code = generate.main(
                    ["--in-place", "--timeout", "5", "--output", str(output), "ai-token-tracker"]
                )
            self.assertEqual(code, 1)
            self.assertFalse(output.exists(), "a timed-out run must write no evidence")
            self.assertIn("exceeded 5s", stderr.getvalue())
            self.assertIn("No evidence was written", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
