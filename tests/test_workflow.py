import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agentops.config import AppConfig, AgentConfig
from agentops.registry import DetectedAgent
from agentops.runner import RunResult
from agentops.state import StateStore
from agentops.workflow import WorkflowEngine


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            {"fallback": AgentConfig("fallback", "fake", ("{prompt}",), ("architecture", "implementation", "review", "debugging"))},
            {role: ("fallback",) for role in ("architecture", "implementation", "review", "debugging")},
            (("test",),), max_attempts=2, concurrency=2,
        )
        self.state = StateStore(":memory:")
        self.registry = MagicMock()
        self.registry.select.return_value = DetectedAgent(self.config.agents["fallback"], True, "fake")
        self.runner = MagicMock()
        self.runner.run_agent = self._run_agent
        self.verifier = MagicMock()

    async def _run_agent(self, agent, prompt, directory, task_id):
        return RunResult(agent.config.name, ("fake",), 0, "done", "", 0.01, False, Path(f"{task_id}.log"))

    def tearDown(self):
        self.state.close()

    def test_standard_workflow_runs_to_ready(self):
        passed_check = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[passed_check]))
        engine = WorkflowEngine(self.config, self.state, self.registry, self.runner, self.verifier)
        result = asyncio.run(engine.run_high_level("Add dark mode", Path.cwd()))
        self.assertTrue(result.ready)
        self.assertEqual(result.summary, "READY")
        self.assertEqual(len(self.state.list_tasks(result.workflow_id)), 4)

    def test_failed_verification_creates_repair_flow(self):
        failed_check = MagicMock(succeeded=False, output="one test failed")
        passed_check = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(side_effect=[asyncio.sleep(0, result=[failed_check]), asyncio.sleep(0, result=[passed_check])])
        engine = WorkflowEngine(self.config, self.state, self.registry, self.runner, self.verifier)
        result = asyncio.run(engine.run_high_level("Fix issue", Path.cwd()))
        self.assertTrue(result.ready)
        self.assertEqual(len(self.state.list_tasks(result.workflow_id)), 7)
