import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentops.config import AgentConfig
from agentops.logging import LogManager
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner


class AgentRunnerTests(unittest.TestCase):
    def test_captures_output_and_writes_logs(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            config = AgentConfig("python", sys.executable, ("-c", "print('{prompt}')"))
            agent = DetectedAgent(config, True, sys.executable)
            result = asyncio.run(AgentRunner(LogManager(Path(directory) / "logs")).run_agent(agent, "hello", directory, "task-1"))
            self.assertTrue(result.succeeded)
            self.assertEqual(result.stdout.strip(), "hello")
            self.assertTrue(result.log_path.exists())

    def test_timeout_is_reported(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            config = AgentConfig("python", sys.executable, ("-c", "import time; time.sleep(2)"), timeout_seconds=1)
            agent = DetectedAgent(config, True, sys.executable)
            result = asyncio.run(AgentRunner(LogManager(Path(directory) / "logs")).run_agent(agent, "unused", directory))
            self.assertTrue(result.timed_out)
            self.assertFalse(result.succeeded)
