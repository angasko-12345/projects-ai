import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agentops.agent_run import AgentRunMetadata
from agentops.config import AppConfig
from agentops.persistence import PersistencePolicy
from agentops.runner import OperationCancelled, RunResult
from agentops.state import StateStore
from agentops.tasks import Task
from agentops.workflow import WorkflowEngine


def _success_result() -> RunResult:
    return RunResult(
        agent="worker", command=("fake",), exit_code=0,
        stdout="{}", stderr="", duration_seconds=0.1,
        timed_out=False, log_path=Path("log.txt"),
    )


class _LegacyRunner:
    """Runner double without observer kwargs: exercises _run_task_agent's
    manual observer path (the one whose arms used to swallow silently)."""

    def __init__(self, effect: BaseException | None = None):
        self.effect = effect

    def build_command(self, agent, prompt):
        return ("fake",)

    async def run_agent(self, agent, prompt, working_directory, task_id,
                        cancel_event=None):
        if self.effect is not None:
            raise self.effect
        return _success_result()


def _engine(runner=None, run_observer=None) -> WorkflowEngine:
    config = AppConfig({}, {}, (), max_attempts=1, concurrency=1)
    state = StateStore(":memory:")
    return WorkflowEngine(
        config, state, MagicMock(), runner or _LegacyRunner(), MagicMock(),
        run_observer=run_observer,
        metadata_collector=lambda wd: AgentRunMetadata(),
    )


def _task() -> Task:
    return Task("do the thing", "implementation", "wf-1")


def _agent():
    return SimpleNamespace(
        config=SimpleNamespace(name="worker", command=("fake",), model=None),
        executable="fake",
    )


def _entries(engine: WorkflowEngine, operation: str):
    return [entry for entry in engine.degradation.degradations
            if entry.operation == operation]


class RecordFailureSwallowTests(unittest.TestCase):
    def test_agent_failure_recording_error_is_visible(self):
        engine = _engine()
        task = _task()
        result = SimpleNamespace(
            stderr="boom", command=("fake",), exit_code=1,
            timed_out=False, cancelled=False, terminated=False)
        with patch.object(engine, "record_failure",
                          side_effect=RuntimeError("store down")):
            # The arm must keep swallowing: task handling continues.
            engine._record_agent_failure(task, result)
        entries = _entries(engine, "failure.create")
        self.assertEqual(len(entries), 1)
        self.assertIs(entries[0].policy, PersistencePolicy.SAFE_TO_DEGRADE)
        engine.state.close()

    def test_legacy_verification_failure_recording_error_is_visible(self):
        engine = _engine()
        task = _task()
        task.result = "verification failed"
        failing = SimpleNamespace(timed_out=False, exit_code=1, command=("t",))
        with patch.object(engine, "record_failure",
                          side_effect=RuntimeError("store down")):
            engine._record_legacy_verification_failure(task, [failing])
        entries = _entries(engine, "failure.create")
        self.assertEqual(len(entries), 1)
        engine.state.close()


class ObserverSwallowTests(unittest.TestCase):
    def test_completion_finish_run_error_is_visible(self):
        observer = MagicMock()
        observer.create_run.return_value = "run-1"
        observer.finish_run.side_effect = RuntimeError("store down")
        engine = _engine(run_observer=observer)
        task = _task()
        result = asyncio.run(
            engine._run_task_agent(_agent(), task, tempfile.gettempdir()))
        # Behavior unchanged: the run result still comes back.
        self.assertEqual(result.exit_code, 0)
        entries = _entries(engine, "agent_run.transition")
        self.assertEqual(len(entries), 1)
        self.assertIs(entries[0].policy, PersistencePolicy.SAFE_TO_DEGRADE)
        engine.state.close()

    def test_create_run_error_is_visible(self):
        observer = MagicMock()
        observer.create_run.side_effect = RuntimeError("store down")
        engine = _engine(run_observer=observer)
        task = _task()
        result = asyncio.run(
            engine._run_task_agent(_agent(), task, tempfile.gettempdir()))
        self.assertEqual(result.exit_code, 0)
        entries = _entries(engine, "agent_run.create")
        self.assertEqual(len(entries), 1)
        engine.state.close()

    def test_cancel_finish_run_error_is_visible_and_cancellation_propagates(self):
        observer = MagicMock()
        observer.create_run.return_value = "run-1"
        observer.finish_run.side_effect = RuntimeError("store down")
        engine = _engine(
            runner=_LegacyRunner(effect=OperationCancelled()),
            run_observer=observer)
        task = _task()
        with self.assertRaises(OperationCancelled):
            asyncio.run(
                engine._run_task_agent(_agent(), task, tempfile.gettempdir()))
        entries = _entries(engine, "agent_run.transition")
        self.assertEqual(len(entries), 1)
        engine.state.close()


if __name__ == "__main__":
    unittest.main()
