import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentops.agent_run import AgentRunContext, AgentRunOutcome, AgentRunStatus
from agentops.config import AgentConfig, AppConfig
from agentops.failure import Failure, FailureCategory, FailureSeverity, FailureSource, RecoveryState, RepairAction
from agentops.gui_controller import AgentOpsController
from agentops.logging import LogManager
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner, OperationCancelled
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.verification_model import VerificationProfile, VerificationProfileMode, VerificationRunStatus, VerificationReportStatus
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


class InterruptedWorkFacadeTests(unittest.TestCase):
    """Read-only interrupted-work probe backs the shell recovery banner."""

    def test_probe_counts_then_recovery_consumes_them(self):
        from agentops.agent_run import AgentRunContext

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("interrupted facade")
                task = state.add_task(Task("work", "implementation",
                                            workflow_id, max_attempts=1))
                state.claim_task(task.id)
                state.create_agent_run(AgentRunContext(
                    workflow_id=workflow_id, task_id=task.id))
            finally:
                state.close()

            counts = controller.interrupted_work(root)
            self.assertEqual(counts, {"agent_runs": 1, "verification_runs": 0,
                                      "tasks": 1, "total": 2})
            summary = controller.recover_interrupted(root)
            self.assertEqual(summary, {"agent_runs": 1, "verification_runs": 0,
                                       "tasks": 1})
            self.assertEqual(controller.interrupted_work(root)["total"], 0)


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


class RecentRunsPaginationTests(unittest.TestCase):
    """list_recent_* must not return more items than requested."""

    def test_list_recent_agent_runs_does_not_overflow_past_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation",
                                            workflow_id, max_attempts=1))
                runs = [
                    state.create_agent_run(AgentRunContext(
                        agent="demo", workflow_id=workflow_id, task_id=task.id))
                    for _ in range(5)
                ]
                # Finish the runs so they have deterministic terminal status
                for run in runs:
                    state.finish_agent_run(
                        run.id,
                        AgentRunOutcome(status=AgentRunStatus.COMPLETED, exit_code=0),
                    )
            finally:
                state.close()

            # Request page 2 (skip the 3 newest). There are only 2 older runs,
            # so the response must contain exactly 2 items, not 3.
            page = controller.list_recent_agent_runs(root, limit=3, offset=3)
            self.assertEqual(len(page), 2)
            # The two oldest runs, newest-first.
            self.assertEqual(page[0]["id"], runs[1].id)
            self.assertEqual(page[1]["id"], runs[0].id)

    def test_list_recent_agent_runs_returns_empty_when_offset_exceeds_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation",
                                            workflow_id, max_attempts=1))
                runs = [
                    state.create_agent_run(AgentRunContext(
                        agent="demo", workflow_id=workflow_id, task_id=task.id))
                    for _ in range(5)
                ]
                for run in runs:
                    state.finish_agent_run(
                        run.id,
                        AgentRunOutcome(status=AgentRunStatus.COMPLETED, exit_code=0),
                    )
            finally:
                state.close()

            page = controller.list_recent_agent_runs(root, limit=3, offset=5)
            self.assertEqual(page, [])

    def test_list_recent_verification_runs_does_not_overflow_past_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation",
                                            workflow_id, max_attempts=1))
                profile = VerificationProfile(
                    name="unit", mode=VerificationProfileMode.FAIL_FAST,
                    concurrency=1, default_timeout_seconds=30, checks=())
                runs = [
                    state.create_verification_run(workflow_id, task.id, profile)
                    for _ in range(5)
                ]
                for run in runs:
                    state.finish_verification_run(
                        run.id, VerificationRunStatus.COMPLETED,
                        VerificationReportStatus.PASSED,
                        0, 0, 0, 0, 0, 0.0)
            finally:
                state.close()

            page = controller.list_recent_verification_runs(root, limit=3, offset=3)
            self.assertEqual(len(page), 2)
            self.assertEqual(page[0]["id"], runs[1].id)
            self.assertEqual(page[1]["id"], runs[0].id)

    def test_list_recent_verification_runs_returns_empty_when_offset_exceeds_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation",
                                            workflow_id, max_attempts=1))
                profile = VerificationProfile(
                    name="unit", mode=VerificationProfileMode.FAIL_FAST,
                    concurrency=1, default_timeout_seconds=30, checks=())
                for _ in range(5):
                    run = state.create_verification_run(workflow_id, task.id, profile)
                    state.finish_verification_run(
                        run.id, VerificationRunStatus.COMPLETED,
                        VerificationReportStatus.PASSED,
                        0, 0, 0, 0, 0, 0.0)
            finally:
                state.close()

            page = controller.list_recent_verification_runs(root, limit=3, offset=5)
            self.assertEqual(page, [])

    def test_list_recent_failures_does_not_overflow_past_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation",
                                            workflow_id, max_attempts=1))
                failures = [
                    state.create_failure(Failure(
                        id=f"failure-{index}", workflow_id=workflow_id, task_id=task.id,
                        agent_run_id=None, source=FailureSource.AGENT,
                        category=FailureCategory.AGENT_ERROR,
                        severity=FailureSeverity.HIGH,
                        retryable=True, repairable=True, evidence=f"boom {index}",
                        recommended_action=RepairAction.RETRY_SAME_AGENT,
                        recovery_state=RecoveryState.INTERRUPTED_AGENT_EXECUTION,
                    ))
                    for index in range(5)
                ]
            finally:
                state.close()

            page = controller.list_recent_failures(root, limit=3, offset=3)
            self.assertEqual(len(page), 2)
            self.assertEqual(page[0]["id"], "failure-1")
            self.assertEqual(page[1]["id"], "failure-0")

    def test_list_recent_failures_returns_empty_when_offset_exceeds_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation",
                                            workflow_id, max_attempts=1))
                for index in range(5):
                    state.create_failure(Failure(
                        id=f"failure-{index}", workflow_id=workflow_id, task_id=task.id,
                        agent_run_id=None, source=FailureSource.AGENT,
                        category=FailureCategory.AGENT_ERROR,
                        severity=FailureSeverity.HIGH,
                        retryable=True, repairable=True, evidence=f"boom {index}",
                        recommended_action=RepairAction.RETRY_SAME_AGENT,
                        recovery_state=RecoveryState.INTERRUPTED_AGENT_EXECUTION,
                    ))
            finally:
                state.close()

            page = controller.list_recent_failures(root, limit=3, offset=5)
            self.assertEqual(page, [])
