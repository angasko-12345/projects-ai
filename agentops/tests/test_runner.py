import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agentops.config import AgentConfig
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner


class AgentRunnerTests(unittest.TestCase):
    @patch("agentops.runner.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_captures_output_and_writes_logs(self, create_process):
        logs = MagicMock()
        logs.write_run.return_value = Path("task-1.meta.log")
        process = MagicMock()
        process.communicate = AsyncMock(return_value=(b"hello\n", b""))
        process.returncode = 0
        create_process.return_value = process
        config = AgentConfig("python", sys.executable, ("-c", "print('{prompt}')"))
        agent = DetectedAgent(config, True, sys.executable)
        result = asyncio.run(AgentRunner(logs).run_agent(agent, "hello", Path.cwd(), "task-1"))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.stdout.strip(), "hello")
        self.assertEqual(result.log_path, Path("task-1.meta.log"))
        logs.write_run.assert_called_once()

    @patch("agentops.runner.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_timeout_is_reported(self, create_process):
        logs = MagicMock()
        logs.write_run.return_value = Path("timeout.meta.log")
        process = MagicMock()
        process.killed = False

        async def communicate():
            if not process.killed:
                await asyncio.sleep(0.1)
            return b"", b""

        process.communicate = communicate
        process.kill.side_effect = lambda: setattr(process, "killed", True)
        process.returncode = -9
        create_process.return_value = process
        config = AgentConfig("python", sys.executable, ("-c", "ignored"), timeout_seconds=0.01)
        agent = DetectedAgent(config, True, sys.executable)
        result = asyncio.run(AgentRunner(logs).run_agent(agent, "unused", Path.cwd()))
        self.assertTrue(result.timed_out)
        self.assertFalse(result.succeeded)
