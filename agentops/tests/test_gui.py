import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentops.agent_run import AgentRunContext, AgentRunOutcome, AgentRunStatus
from agentops.config import AgentConfig, AppConfig
from agentops.failure import (
    Failure,
    FailureCategory,
    FailureSeverity,
    FailureSource,
    RecoveryState,
    RepairAction,
)
from agentops.gui_controller import AgentOpsController
from agentops.logging import LogManager
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner, OperationCancelled
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.verification_model import (
    VerificationProfile,
    VerificationProfileMode,
    VerificationRunStatus,
    VerificationReportStatus,
)
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
                    asyncio.run(
                        runner.run_agent(
                            agent,
                            "cancel me",
                            Path(directory),
                            timeout_seconds=30,
                            cancel_event=cancel_event,
                        )
                    )
            finally:
                timer.cancel()
                timer.join(timeout=5)


class StatusPollEfficiencyTests(unittest.TestCase):
    def test_operation_root_is_cached(self):
        from agentops.git import GitWorktreeManager

        with tempfile.TemporaryDirectory() as directory:
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)
            )
            with patch.object(
                GitWorktreeManager, "repository_root", return_value=Path(directory)
            ) as root:
                first = controller._operation_root(directory)
                second = controller._operation_root(directory)
            self.assertEqual(first, second)
            self.assertEqual(root.call_count, 1)

    def test_operation_root_failures_are_not_cached(self):
        from agentops.git import GitError, GitWorktreeManager

        with tempfile.TemporaryDirectory() as directory:
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)
            )
            with patch.object(
                GitWorktreeManager,
                "repository_root",
                side_effect=[GitError("nope"), Path(directory)],
            ) as root:
                controller._operation_root(directory)
                controller._operation_root(directory)
            self.assertEqual(root.call_count, 2)


class ControllerObservabilityTests(unittest.TestCase):
    def test_history_and_logs_use_canonical_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)
            )
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
            log_path = LogManager(root / ".agentops" / "logs").write_run(
                "task-1", "demo", "hi", "", "meta"
            )
            name = str(
                log_path.parent.name + "/" + log_path.name.replace(".meta.log", ".stdout.log")
            )
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
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)
            )
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("interrupted facade")
                task = state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
                state.claim_task(task.id)
                state.create_agent_run(AgentRunContext(workflow_id=workflow_id, task_id=task.id))
            finally:
                state.close()

            counts = controller.interrupted_work(root)
            self.assertEqual(
                counts, {"agent_runs": 1, "verification_runs": 0, "tasks": 1, "total": 2}
            )
            summary = controller.recover_interrupted(root)
            self.assertEqual(summary, {"agent_runs": 1, "verification_runs": 0, "tasks": 1})
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
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)
            )
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
                runs = [
                    state.create_agent_run(
                        AgentRunContext(agent="demo", workflow_id=workflow_id, task_id=task.id)
                    )
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
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)
            )
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
                runs = [
                    state.create_agent_run(
                        AgentRunContext(agent="demo", workflow_id=workflow_id, task_id=task.id)
                    )
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
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)
            )
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
                profile = VerificationProfile(
                    name="unit",
                    mode=VerificationProfileMode.FAIL_FAST,
                    concurrency=1,
                    default_timeout_seconds=30,
                    checks=(),
                )
                runs = [
                    state.create_verification_run(workflow_id, task.id, profile) for _ in range(5)
                ]
                for run in runs:
                    state.finish_verification_run(
                        run.id,
                        VerificationRunStatus.COMPLETED,
                        VerificationReportStatus.PASSED,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0.0,
                    )
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
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)
            )
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
                profile = VerificationProfile(
                    name="unit",
                    mode=VerificationProfileMode.FAIL_FAST,
                    concurrency=1,
                    default_timeout_seconds=30,
                    checks=(),
                )
                for _ in range(5):
                    run = state.create_verification_run(workflow_id, task.id, profile)
                    state.finish_verification_run(
                        run.id,
                        VerificationRunStatus.COMPLETED,
                        VerificationReportStatus.PASSED,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0.0,
                    )
            finally:
                state.close()

            page = controller.list_recent_verification_runs(root, limit=3, offset=5)
            self.assertEqual(page, [])

    def test_list_recent_failures_does_not_overflow_past_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AgentOpsController(
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)
            )
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
                for index in range(5):
                    state.create_failure(
                        Failure(
                            id=f"failure-{index}",
                            workflow_id=workflow_id,
                            task_id=task.id,
                            agent_run_id=None,
                            source=FailureSource.AGENT,
                            category=FailureCategory.AGENT_ERROR,
                            severity=FailureSeverity.HIGH,
                            retryable=True,
                            repairable=True,
                            evidence=f"boom {index}",
                            recommended_action=RepairAction.RETRY_SAME_AGENT,
                            recovery_state=RecoveryState.INTERRUPTED_AGENT_EXECUTION,
                        )
                    )
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
                config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)
            )
            state = StateStore(controller._state_path(root))
            try:
                workflow_id = state.create_workflow("pagination workflow")
                task = state.add_task(Task("work", "implementation", workflow_id, max_attempts=1))
                for index in range(5):
                    state.create_failure(
                        Failure(
                            id=f"failure-{index}",
                            workflow_id=workflow_id,
                            task_id=task.id,
                            agent_run_id=None,
                            source=FailureSource.AGENT,
                            category=FailureCategory.AGENT_ERROR,
                            severity=FailureSeverity.HIGH,
                            retryable=True,
                            repairable=True,
                            evidence=f"boom {index}",
                            recommended_action=RepairAction.RETRY_SAME_AGENT,
                            recovery_state=RecoveryState.INTERRUPTED_AGENT_EXECUTION,
                        )
                    )
            finally:
                state.close()

            page = controller.list_recent_failures(root, limit=3, offset=5)
            self.assertEqual(page, [])


class TaskManagementTests(unittest.TestCase):
    """Rename, recategorize, and delete flow through the real store."""

    def _seed(self, directory):
        root = Path(directory)
        controller = AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
        state = StateStore(controller._state_path(root))
        try:
            workflow_id = state.create_workflow("managed workflow")
            first = state.add_task(
                Task("first work", "implementation", workflow_id, max_attempts=1)
            )
            second = state.add_task(Task("second work", "review", workflow_id, max_attempts=1))
        finally:
            state.close()
        return controller, root, workflow_id, first, second

    def test_rename_task_updates_the_description(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root, _workflow_id, first, _second = self._seed(directory)
            updated = controller.rename_task(root, first.id, "renamed work")
            self.assertEqual(updated["description"], "renamed work")
            state = StateStore(controller._state_path(root))
            try:
                self.assertEqual(state.get_task(first.id).description, "renamed work")
            finally:
                state.close()

    def test_rename_task_rejects_empty_unknown_and_running(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root, _workflow_id, first, _second = self._seed(directory)
            with self.assertRaises(ValueError):
                controller.rename_task(root, first.id, "   ")
            with self.assertRaises(KeyError):
                controller.rename_task(root, "no-such-task", "name")
            state = StateStore(controller._state_path(root))
            try:
                state.claim_task(first.id)
            finally:
                state.close()
            with self.assertRaises(ValueError):
                controller.rename_task(root, first.id, "new name")

    def test_recategorize_task_moves_pending_work(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root, _workflow_id, first, _second = self._seed(directory)
            updated = controller.recategorize_task(root, first.id, "debugging")
            self.assertEqual(updated["role"], "debugging")
            state = StateStore(controller._state_path(root))
            try:
                self.assertEqual(state.get_task(first.id).role, "debugging")
            finally:
                state.close()

    def test_recategorize_task_rejects_bad_role_and_live_history(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root, _workflow_id, first, second = self._seed(directory)
            with self.assertRaises(ValueError):
                controller.recategorize_task(root, first.id, "janitor")
            with self.assertRaises(KeyError):
                controller.recategorize_task(root, "no-such-task", "review")
            state = StateStore(controller._state_path(root))
            try:
                state.claim_task(first.id)
                second.status = TaskStatus.PASSED
                state.update_task(second)
            finally:
                state.close()
            with self.assertRaises(ValueError):
                controller.recategorize_task(root, first.id, "review")
            with self.assertRaises(ValueError):
                controller.recategorize_task(root, second.id, "review")

    def test_delete_task_removes_the_row_and_refreshes_the_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root, workflow_id, first, second = self._seed(directory)
            removed = controller.delete_task(root, first.id)
            self.assertEqual(removed["id"], first.id)
            self.assertEqual(removed["workflow_id"], workflow_id)
            state = StateStore(controller._state_path(root))
            try:
                remaining = state.list_tasks(workflow_id)
                self.assertEqual([task.id for task in remaining], [second.id])
                with self.assertRaises(KeyError):
                    state.get_task(first.id)
            finally:
                state.close()

    def test_delete_task_refuses_when_another_task_depends_on_it(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root, workflow_id, first, _second = self._seed(directory)
            state = StateStore(controller._state_path(root))
            try:
                downstream = state.add_task(
                    Task("downstream", "review", workflow_id, dependencies=(first.id,))
                )
            finally:
                state.close()

            with self.assertRaisesRegex(ValueError, "dependency"):
                controller.delete_task(root, first.id)

            state = StateStore(controller._state_path(root))
            try:
                self.assertEqual(state.get_task(first.id).status, TaskStatus.PENDING)
                self.assertEqual(state.get_task(downstream.id).status, TaskStatus.PENDING)
            finally:
                state.close()

    def test_delete_task_refuses_running_and_last_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root, _workflow_id, first, _second = self._seed(directory)
            state = StateStore(controller._state_path(root))
            try:
                state.claim_task(first.id)
            finally:
                state.close()
            with self.assertRaises(ValueError):
                controller.delete_task(root, first.id)
            with self.assertRaises(KeyError):
                controller.delete_task(root, "no-such-task")
            lonely_state = StateStore(controller._state_path(root))
            try:
                lonely_workflow = lonely_state.create_workflow("lonely workflow")
                lonely = lonely_state.add_task(
                    Task("only work", "implementation", lonely_workflow, max_attempts=1)
                )
            finally:
                lonely_state.close()
            with self.assertRaises(ValueError):
                controller.delete_task(root, lonely.id)


class RecordManagementTests(unittest.TestCase):
    """Delete/rename for workflows, runs, verifications, failures, artifacts."""

    def _controller(self, directory):
        from pathlib import Path as _Path

        root = _Path(directory)
        return (
            AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1)),
            root,
        )

    def _seed_workflow(self, controller, root):
        from agentops.artifacts import Artifact, ArtifactKind

        state = StateStore(controller._state_path(root))
        try:
            workflow_id = state.create_workflow("managed workflow")
            first = state.add_task(
                Task("first work", "implementation", workflow_id, max_attempts=1)
            )
            second = state.add_task(Task("second work", "review", workflow_id, max_attempts=1))
            run = state.create_agent_run(
                AgentRunContext(agent="codex", workflow_id=workflow_id, task_id=first.id)
            )
            profile = VerificationProfile(
                name="unit",
                mode=VerificationProfileMode.FAIL_FAST,
                concurrency=1,
                default_timeout_seconds=30,
                checks=(),
            )
            verification = state.create_verification_run(
                workflow_id, first.id, profile, source_agent_run_id=run.id
            )
            state.finish_verification_run(
                verification.id,
                VerificationRunStatus.COMPLETED,
                VerificationReportStatus.PASSED,
                0,
                0,
                0,
                0,
                0,
                0.0,
            )
            failure = state.create_failure(
                Failure(
                    id="failure-1",
                    workflow_id=workflow_id,
                    task_id=first.id,
                    agent_run_id=run.id,
                    source=FailureSource.AGENT,
                    category=FailureCategory.AGENT_ERROR,
                    severity=FailureSeverity.HIGH,
                    retryable=True,
                    repairable=True,
                    evidence="boom",
                    recommended_action=RepairAction.RETRY_SAME_AGENT,
                    recovery_state=RecoveryState.INTERRUPTED_AGENT_EXECUTION,
                )
            )
            artifact = state.create_artifact_record(
                Artifact(
                    workflow_id=workflow_id,
                    task_id=first.id,
                    kind=ArtifactKind.PLANS,
                    name="plan.md",
                )
            )
            state.finish_agent_run(
                run.id, AgentRunOutcome(status=AgentRunStatus.COMPLETED, exit_code=0)
            )
        finally:
            state.close()
        return workflow_id, first, second, run, verification, failure, artifact

    def test_rename_workflow_updates_the_description(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root = self._controller(directory)
            workflow_id, *_rest = self._seed_workflow(controller, root)
            updated = controller.rename_workflow(root, workflow_id, "renamed flow")
            self.assertEqual(updated["description"], "renamed flow")
            with self.assertRaises(ValueError):
                controller.rename_workflow(root, workflow_id, "   ")
            with self.assertRaises(KeyError):
                controller.rename_workflow(root, "no-such-flow", "name")

    def test_delete_workflow_cascades_everything(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root = self._controller(directory)
            workflow_id, _first, _second, run, verification, _failure, _artifact = (
                self._seed_workflow(controller, root)
            )
            removed = controller.delete_workflow(root, workflow_id)
            counts = removed["removed"]
            self.assertEqual(counts["tasks"], 2)
            self.assertEqual(counts["agent_runs"], 1)
            self.assertEqual(counts["verification_runs"], 1)
            self.assertEqual(counts["failures"], 1)
            self.assertEqual(counts["artifacts"], 1)
            self.assertEqual(counts["workflows"], 1)
            state = StateStore(controller._state_path(root))
            try:
                self.assertIsNone(state.get_workflow(workflow_id))
                self.assertEqual(state.list_tasks(workflow_id), [])
                self.assertEqual(state.list_events(workflow_id), [])
                with self.assertRaises(KeyError):
                    state.get_agent_run(run.id)
                with self.assertRaises(KeyError):
                    state.get_verification_run(verification.id)
            finally:
                state.close()
            with self.assertRaises(KeyError):
                controller.delete_workflow(root, workflow_id)

    def test_delete_workflow_removes_files_for_stored_artifacts(self):
        from agentops.artifacts import ArtifactKind, ArtifactStore

        with tempfile.TemporaryDirectory() as directory:
            controller, root = self._controller(directory)
            workflow_id, *_ = self._seed_workflow(controller, root)
            store = ArtifactStore(controller._artifact_root(root))
            artifact = store.write(
                b"private workflow artifact",
                kind=ArtifactKind.PLANS,
                name="private-plan",
                workflow_id=workflow_id,
                redact_text=False,
            )
            artifact_path = store.root / artifact.rel_path
            state = StateStore(controller._state_path(root))
            try:
                state.create_artifact_record(artifact)
            finally:
                state.close()
            self.assertTrue(artifact_path.is_file())

            controller.delete_workflow(root, workflow_id)

            self.assertFalse(artifact_path.exists())
            self.assertEqual(store.find_orphans([]), [])

    def test_delete_workflow_refuses_live_work(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root = self._controller(directory)
            workflow_id, first, _second, _run, _ver, _fail, _art = self._seed_workflow(
                controller, root
            )
            state = StateStore(controller._state_path(root))
            try:
                state.claim_task(first.id)
            finally:
                state.close()
            with self.assertRaises(ValueError):
                controller.delete_workflow(root, workflow_id)

    def test_delete_agent_run_cascades_verifications_but_keeps_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root = self._controller(directory)
            _wf, _first, _second, run, verification, failure, _art = self._seed_workflow(
                controller, root
            )
            removed = controller.delete_agent_run(root, run.id)
            self.assertEqual(removed["id"], run.id)
            state = StateStore(controller._state_path(root))
            try:
                with self.assertRaises(KeyError):
                    state.get_agent_run(run.id)
                with self.assertRaises(KeyError):
                    state.get_verification_run(verification.id)
                kept = [item for item in state.list_failures() if item.id == failure.id]
                self.assertEqual(len(kept), 1)
            finally:
                state.close()

    def test_delete_agent_run_refuses_live_and_parent_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root = self._controller(directory)
            workflow_id, first, _second, _run, _ver, _fail, _art = self._seed_workflow(
                controller, root
            )
            state = StateStore(controller._state_path(root))
            try:
                live = state.create_agent_run(
                    AgentRunContext(agent="codex", workflow_id=workflow_id, task_id=first.id)
                )
                parent = state.create_agent_run(
                    AgentRunContext(agent="codex", workflow_id=workflow_id, task_id=first.id)
                )
                child = state.create_agent_run(
                    AgentRunContext(
                        agent="codex",
                        workflow_id=workflow_id,
                        task_id=first.id,
                        parent_run_id=parent.id,
                        relationship="retry",
                    )
                )
                state.finish_agent_run(
                    child.id, AgentRunOutcome(status=AgentRunStatus.COMPLETED, exit_code=0)
                )
                state.finish_agent_run(
                    parent.id, AgentRunOutcome(status=AgentRunStatus.COMPLETED, exit_code=0)
                )
            finally:
                state.close()
            with self.assertRaises(ValueError):
                controller.delete_agent_run(root, live.id)
            with self.assertRaises(ValueError):
                controller.delete_agent_run(root, parent.id)
            with self.assertRaises(KeyError):
                controller.delete_agent_run(root, "no-such-run")
            controller.delete_agent_run(root, child.id)

    def test_delete_verification_run_drops_checks_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root = self._controller(directory)
            _wf, _first, _second, _run, verification, _fail, _art = self._seed_workflow(
                controller, root
            )
            removed = controller.delete_verification_run(root, verification.id)
            self.assertEqual(removed["id"], verification.id)
            state = StateStore(controller._state_path(root))
            try:
                with self.assertRaises(KeyError):
                    state.get_verification_run(verification.id)
                self.assertEqual(state.list_verification_checks(verification.id), [])
            finally:
                state.close()
            with self.assertRaises(KeyError):
                controller.delete_verification_run(root, verification.id)

    def test_delete_verification_run_refuses_live_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root = self._controller(directory)
            workflow_id, first, _second, _run, _ver, _fail, _art = self._seed_workflow(
                controller, root
            )
            profile = VerificationProfile(
                name="unit",
                mode=VerificationProfileMode.FAIL_FAST,
                concurrency=1,
                default_timeout_seconds=30,
                checks=(),
            )
            state = StateStore(controller._state_path(root))
            try:
                live = state.create_verification_run(workflow_id, first.id, profile)
                state.start_verification_run(live.id)
            finally:
                state.close()
            with self.assertRaises(ValueError):
                controller.delete_verification_run(root, live.id)

    def test_delete_failure_and_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, root = self._controller(directory)
            _wf, _first, _second, _run, _ver, failure, artifact = self._seed_workflow(
                controller, root
            )
            self.assertEqual(controller.delete_failure(root, failure.id)["id"], failure.id)
            with self.assertRaises(KeyError):
                controller.delete_failure(root, failure.id)
            removed = controller.delete_artifact(root, artifact.id)
            self.assertEqual(removed["name"], "plan.md")
            with self.assertRaises(KeyError):
                controller.delete_artifact(root, artifact.id)
