import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentops.config import AgentConfig, AppConfig
from agentops.gui_controller import AgentOpsController
from agentops.logging import LogManager
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner, OperationCancelled
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.workflow import WorkflowEngine


class RunnerCancellationTests(unittest.TestCase):
    def test_run_agent_honors_cancel_event(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = AgentRunner(LogManager(Path(directory) / "logs"))
            config = AgentConfig("python", sys.executable, ("-c", "import time; time.sleep(10)"))
            agent = DetectedAgent(config, True, sys.executable)
            cancel_event = threading.Event()
            timer = threading.Timer(0.2, cancel_event.set)
            timer.start()
            try:
                with self.assertRaises(OperationCancelled):
                    asyncio.run(runner.run_agent(agent, "cancel me", Path(directory),
                                                 timeout_seconds=30, cancel_event=cancel_event))
            finally:
                timer.cancel()
                timer.join(timeout=5)


class StatusPollEfficiencyTests(unittest.TestCase):
    def test_operation_root_is_cached(self):
        from agentops.git import GitWorktreeManager
        with tempfile.TemporaryDirectory() as directory:
            controller = AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            with patch.object(GitWorktreeManager, "repository_root",
                              return_value=Path(directory)) as root:
                first = controller._operation_root(directory)
                second = controller._operation_root(directory)
            self.assertEqual(first, second)
            self.assertEqual(root.call_count, 1)

    def test_operation_root_failures_are_not_cached(self):
        from agentops.git import GitError, GitWorktreeManager
        with tempfile.TemporaryDirectory() as directory:
            controller = AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            with patch.object(GitWorktreeManager, "repository_root",
                              side_effect=[GitError("nope"), Path(directory)]) as root:
                controller._operation_root(directory)
                controller._operation_root(directory)
            self.assertEqual(root.call_count, 2)



class ControllerObservabilityTests(unittest.TestCase):
    def test_history_and_logs_use_canonical_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("observable workflow")
                state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
            finally:
                state.close()
            page = controller.list_workflows(root)
            self.assertEqual(page["total"], 1)
            detail = controller.get_workflow(root, workflow_id)
            assert detail is not None
            self.assertEqual(len(detail["tasks"]), 1)
            log_path = LogManager(root / ".agentops" / "logs").write_run("task-1", "demo", "hi", "", "meta")
            name = str(log_path.parent.name + "/" + log_path.name.replace(".meta.log", ".stdout.log"))
            entries = controller.list_logs(root)
            self.assertTrue(any(entry["name"] == name for entry in entries))
            content = controller.read_log(root, name)
            self.assertIn("hi", str(content["text"]))


class WorkflowCancellationTests(unittest.TestCase):
    def test_execute_checks_cancel_event_before_starting(self):
        config = AppConfig({}, {}, (), max_attempts=1, concurrency=1)
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("cancelled workflow")
            task = state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
            engine = WorkflowEngine(config, state, MagicMock(), MagicMock(), MagicMock())
            cancel_event = threading.Event()
            cancel_event.set()
            with self.assertRaises(OperationCancelled):
                asyncio.run(engine.execute(workflow_id, Path.cwd(), cancel_event))
            self.assertEqual(state.get_task(task.id).status, TaskStatus.PENDING)
        finally:
            state.close()
