import io
import unittest
from contextlib import redirect_stdout

from agentops.cli import main


class CliTests(unittest.TestCase):
    def test_agents_command_reports_known_profiles(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["agents"])
        self.assertEqual(exit_code, 0)
        self.assertIn("fcc-claude", output.getvalue())
        self.assertIn("codex", output.getvalue())

    def test_workflow_requires_description(self):
        with self.assertRaises(SystemExit):
            main([])
