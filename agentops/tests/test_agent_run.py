import asyncio
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import tkinter as tk
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agentops.agent_run import (
    AgentRunContext,
    AgentRunMetadata,
    AgentRunOutcome,
    AgentRunRelationship,
    AgentRunStatus,
)
from agentops.cli import main as cli_main
from agentops.config import AgentConfig, AppConfig
from agentops.gui_controller import AgentOpsController, serialize_agent_run
from agentops.logging import LogManager
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner, OperationCancelled, RunResult
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.workflow import WorkflowEngine


SECRET = "api_key=sk-abcdef1234567890"


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int | None):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = None
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


class TimeoutProcess:
    def __init__(self):
        self.returncode = None
        self.pid = None
        self.killed = False

    async def communicate(self):
        while not self.killed:
            await asyncio.sleep(0.005)
        return b"partial-output", b""

    def kill(self):
        self.killed = True


def _agent(name="demo", timeout_seconds=30, model=None):
    config = AgentConfig(
        name, sys.executable, ("-c", "print('ok')"),
        ("architecture", "implementation", "debugging", "review"),
        timeout_seconds, True, model,
    )
    return DetectedAgent(config, True, sys.executable)


def _workflow_config(**overrides):
    values = {"max_attempts": 2, "concurrency": 1, "max_repair_cycles": 1}
    values.update(overrides)
    config = AgentConfig(
        "worker", "fake", ("{prompt}",),
        ("architecture", "implementation", "debugging", "review"),
    )
    return AppConfig(
        {"worker": config},
        {role: ("worker",) for role in (
            "architecture", "implementation", "debugging", "review")},
        (), max_attempts=values["max_attempts"], concurrency=values["concurrency"],
        max_repair_cycles=values["max_repair_cycles"],
    )


def _workflow_harness(**config_overrides):
    config = _workflow_config(**config_overrides)
    state = StateStore(":memory:")
    registry = MagicMock()
    registry.select.return_value = DetectedAgent(config.agents["worker"], True, "/resolved/fake")
    runner = MagicMock()
    verifier = MagicMock()
    return config, state, registry, runner, verifier


class AgentRunPersistenceTests(unittest.TestCase):
    def test_create_records_safe_metadata_and_lifecycle(self):
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("agent run workflow")
            context = AgentRunContext(
                agent="demo",
                executable="/resolved/demo",
                role="implementation",
                model="test-model",
                workflow_id=workflow_id,
                task_id="task-1",
                attempt=1,
                working_directory="/repo",
                worktree="/repo/.agentops/worktrees/demo",
                prompt=f"do work {SECRET}",
                command=(sys.executable, f"do work {SECRET}"),
            )
            created = state.create_agent_run(context)
            self.assertEqual(created.status, AgentRunStatus.PENDING)
            self.assertEqual(created.executable, "/resolved/demo")
            self.assertEqual(created.model, "test-model")
            raw = state.connection.execute(
                "SELECT command, command_metadata, prompt_metadata FROM agent_runs WHERE id = ?",
                (created.id,),
            ).fetchone()
            self.assertNotIn(SECRET, raw["command"])
            self.assertNotIn(SECRET, raw["command_metadata"])
            self.assertNotIn(SECRET, raw["prompt_metadata"])
            self.assertIn("[REDACTED]", raw["command"])

            started = state.start_agent_run(created.id)
            self.assertEqual(started.status, AgentRunStatus.STARTING)
            self.assertIsNotNone(started.started_at)
            running = state.mark_agent_run_running(created.id)
            self.assertEqual(running.status, AgentRunStatus.RUNNING)
            finished = state.finish_agent_run(
                created.id,
                AgentRunOutcome(
                    status=AgentRunStatus.COMPLETED,
                    exit_code=0,
                    duration_seconds=1.25,
                    log_path="run.meta.log",
                    structured_result={"changed": True},
                ),
            )
            self.assertTrue(finished.terminal)
            self.assertEqual(finished.structured_result, {"changed": True})
            with self.assertRaises(ValueError):
                state.start_agent_run(created.id)
        finally:
            state.close()

    def test_migration_preserves_legacy_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "legacy.sqlite")
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    CREATE TABLE workflows (
                        id TEXT PRIMARY KEY, description TEXT NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE tasks (
                        id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
                        description TEXT NOT NULL, role TEXT NOT NULL, assigned_agent TEXT,
                        dependencies TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
                        max_attempts INTEGER NOT NULL, result TEXT, created_at TEXT NOT NULL,
                        started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE events (
                        id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, task_id TEXT,
                        kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    INSERT INTO workflows VALUES ('legacy', 'legacy workflow', 'pending', 'now', 'now');
                    """
                )
                connection.commit()
            finally:
                connection.close()

            state = StateStore(database)
            try:
                self.assertIsNotNone(state.get_workflow("legacy"))
                self.assertEqual(state.list_agent_runs(), [])
                run = state.create_agent_run(AgentRunContext(agent="demo"))
                self.assertEqual(state.get_agent_run(run.id).agent, "demo")
            finally:
                state.close()

    def test_retry_relationship_and_recovery(self):
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("retry workflow")
            parent = state.create_agent_run(AgentRunContext(
                agent="demo", workflow_id=workflow_id, task_id="task-1", attempt=1))
            state.start_agent_run(parent.id)
            state.mark_agent_run_running(parent.id)
            state.finish_agent_run(parent.id, AgentRunOutcome(status=AgentRunStatus.FAILED, exit_code=1))
            child = state.create_agent_run(AgentRunContext(
                agent="demo", workflow_id=workflow_id, task_id="task-1", attempt=2,
                parent_run_id=parent.id, relationship=AgentRunRelationship.RETRY))
            self.assertEqual(child.parent_run_id, parent.id)
            self.assertEqual(child.retry_of, parent.id)
            self.assertIsNone(child.repair_of)

        finally:
            state.close()

    def test_recover_marks_interrupted_runs_terminated(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "state.sqlite")
            state = StateStore(database)
            run_id = state.create_agent_run(AgentRunContext(agent="demo")).id
            state.start_agent_run(run_id)
            state.close()

            reopened = StateStore(database)
            try:
                recovered = reopened.recover_agent_runs()
                self.assertEqual([run.id for run in recovered], [run_id])
                finished = reopened.get_agent_run(run_id)
                self.assertEqual(finished.status, AgentRunStatus.TERMINATED)
                self.assertTrue(finished.terminated)
                self.assertEqual(finished.failure_classification, "interrupted")
            finally:
                reopened.close()

    def test_concurrent_runs_all_persist(self):
        state = StateStore(":memory:")
        try:
            results: list[str] = []
            errors: list[BaseException] = []

            def make_run(index: int):
                try:
                    run = state.create_agent_run(AgentRunContext(agent=f"agent-{index}"))
                    state.start_agent_run(run.id)
                    state.mark_agent_run_running(run.id)
                    state.finish_agent_run(
                        run.id, AgentRunOutcome(status=AgentRunStatus.COMPLETED, exit_code=0))
                    results.append(run.id)
                except BaseException as error:
                    errors.append(error)

            threads = [threading.Thread(target=make_run, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(state.list_agent_runs(limit=20)), 8)
        finally:
            state.close()


class RunnerAgentRunTests(unittest.TestCase):
    def _harness(self, directory: str):
        state = StateStore(":memory:")
        logs = LogManager(Path(directory) / "logs")
        runner = AgentRunner(logs, run_observer=state.agent_run_observer())
        return state, runner

    @patch("agentops.runner.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_successful_execution_persists_run(self, create_process):
        with tempfile.TemporaryDirectory() as directory:
            state, runner = self._harness(directory)
            try:
                workflow_id = state.create_workflow("runner workflow")
                agent = _agent(model="test-model")
                prompt = f"implement safely {SECRET}"
                command = (sys.executable, prompt)
                create_process.return_value = FakeProcess(b"done\n", b"", 0)
                result = asyncio.run(runner.run_agent(
                    agent, prompt, directory, "task-1", run_context=AgentRunContext(
                        agent=agent.config.name,
                        executable=sys.executable,
                        role="implementation",
                        model="test-model",
                        workflow_id=workflow_id,
                        task_id="task-1",
                        attempt=1,
                        working_directory=directory,
                        prompt=prompt,
                        command=command,
                    ),
                    metadata_collector=lambda path: AgentRunMetadata(("changed.txt",), " changed.txt | 1 +"),
                ))
                self.assertTrue(result.succeeded)
                self.assertIsNotNone(result.run_id)
                assert result.run_id is not None
                run = state.get_agent_run(result.run_id)
                self.assertEqual(run.status, AgentRunStatus.COMPLETED)
                self.assertEqual(run.role, "implementation")
                self.assertEqual(run.model, "test-model")
                self.assertEqual(run.files_changed, ("changed.txt",))
                self.assertIn("changed.txt", run.diff_stat or "")
                stdout_text = Path(run.stdout_path or "").read_text(encoding="utf-8")
                self.assertIn("done", stdout_text)
                self.assertNotIn(SECRET, run.command or ())
                self.assertNotIn(SECRET, json.dumps(run.command_metadata))
                self.assertNotIn(SECRET, json.dumps(run.prompt_metadata))
            finally:
                state.close()

    @patch("agentops.runner.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_failed_execution_classification(self, create_process):
        with tempfile.TemporaryDirectory() as directory:
            state, runner = self._harness(directory)
            try:
                create_process.return_value = FakeProcess(b"", b"boom", 3)
                result = asyncio.run(runner.run_agent(_agent(), "fail", directory))
                assert result.run_id is not None
                run = state.get_agent_run(result.run_id)
                self.assertEqual(run.status, AgentRunStatus.FAILED)
                self.assertEqual(run.exit_code, 3)
                self.assertEqual(run.failure_classification, "nonzero_exit")
            finally:
                state.close()

    @patch("agentops.runner.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_timeout_execution(self, create_process):
        with tempfile.TemporaryDirectory() as directory:
            state, runner = self._harness(directory)
            try:
                create_process.return_value = TimeoutProcess()
                result = asyncio.run(runner.run_agent(
                    _agent(timeout_seconds=1), "slow", directory, timeout_seconds=0.05))
                self.assertTrue(result.timed_out)
                assert result.run_id is not None
                run = state.get_agent_run(result.run_id)
                self.assertEqual(run.status, AgentRunStatus.TIMED_OUT)
                self.assertTrue(run.timed_out)
                self.assertIsNone(run.exit_code)
            finally:
                state.close()

    @patch("agentops.runner.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_cancelled_execution(self, create_process):
        with tempfile.TemporaryDirectory() as directory:
            state, runner = self._harness(directory)
            try:
                create_process.return_value = FakeProcess(b"", b"", 0)
                cancel_event = threading.Event()
                cancel_event.set()
                with self.assertRaises(OperationCancelled):
                    asyncio.run(runner.run_agent(
                        _agent(), "cancel", directory, cancel_event=cancel_event))
                runs = state.list_agent_runs(status=AgentRunStatus.CANCELLED)
                self.assertEqual(len(runs), 1)
                self.assertTrue(runs[0].cancelled)
            finally:
                state.close()

    @patch("agentops.runner.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_terminated_execution(self, create_process):
        with tempfile.TemporaryDirectory() as directory:
            state, runner = self._harness(directory)
            try:
                create_process.return_value = FakeProcess(b"", b"killed", -9)
                result = asyncio.run(runner.run_agent(_agent(), "terminate", directory))
                assert result.run_id is not None
                run = state.get_agent_run(result.run_id)
                self.assertTrue(result.terminated)
                self.assertEqual(run.status, AgentRunStatus.TERMINATED)
                self.assertTrue(run.terminated)
            finally:
                state.close()


class WorkflowAgentRunTests(unittest.TestCase):
    def test_retry_creates_linked_runs(self):
        config, state, registry, runner, verifier = _workflow_harness()
        calls: list[str] = []

        async def _run_agent(agent, prompt, directory, task_id, cancel_event=None,
                            run_context=None, run_observer=None, metadata_collector=None):
            calls.append(task_id)
            code = 1 if len(calls) == 1 else 0
            return RunResult(agent.config.name, ("fake",), code, "output", "", 0.01, False, Path("fake.log"))

        runner.run_agent = _run_agent
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            workflow_id, _ = engine.create_workflow("retry", [{
                "id": "work", "description": "do work", "role": "implementation",
            }])
            asyncio.run(engine.execute(workflow_id, Path.cwd()))
            task = state.list_tasks(workflow_id)[0]
            self.assertEqual(task.status, TaskStatus.PASSED)
            runs = state.list_agent_runs(workflow_id, task.id)
            self.assertEqual(len(runs), 2)
            self.assertEqual(runs[0].status, AgentRunStatus.FAILED)
            self.assertEqual(runs[1].status, AgentRunStatus.COMPLETED)
            self.assertEqual(runs[1].attempt, 2)
            self.assertEqual(runs[1].parent_run_id, runs[0].id)
            self.assertEqual(runs[1].retry_of, runs[0].id)
            self.assertEqual(runs[1].relationship, AgentRunRelationship.RETRY)
        finally:
            state.close()

    def test_repair_run_links_to_source_run(self):
        config, state, registry, runner, verifier = _workflow_harness()

        async def _run_agent(agent, prompt, directory, task_id, cancel_event=None,
                            run_context=None, run_observer=None, metadata_collector=None):
            return RunResult(agent.config.name, ("fake",), 0, "done", "", 0.01, False, Path("fake.log"))

        runner.run_agent = _run_agent
        failed = MagicMock(succeeded=False, output="test failed")
        passed = MagicMock(succeeded=True, output="tests passed")
        verifier.run = MagicMock(side_effect=[
            asyncio.sleep(0, result=[failed]),
            asyncio.sleep(0, result=[passed]),
        ])
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            result = asyncio.run(engine.run_high_level("repair flow", Path.cwd()))
            self.assertTrue(result.ready)
            repair = next(
                run for run in state.list_agent_runs(result.workflow_id)
                if run.role == "debugging"
            )
            self.assertEqual(repair.status, AgentRunStatus.COMPLETED)
            self.assertEqual(repair.relationship, AgentRunRelationship.REPAIR)
            self.assertIsNotNone(repair.parent_run_id)
            assert repair.parent_run_id is not None
            self.assertEqual(repair.repair_of, repair.parent_run_id)
        finally:
            state.close()


class InspectionTests(unittest.TestCase):
    def test_cli_runs_lists_persisted_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = StateStore(root / ".agentops" / "state.sqlite")
            try:
                workflow_id = state.create_workflow("inspection workflow")
                run = state.create_agent_run(AgentRunContext(
                    agent="demo", workflow_id=workflow_id, task_id="task-1"))
                state.start_agent_run(run.id)
                state.mark_agent_run_running(run.id)
                state.finish_agent_run(run.id, AgentRunOutcome(status=AgentRunStatus.COMPLETED, exit_code=0))
            finally:
                state.close()
            output = io.StringIO()
            with patch("agentops.cli.Path.cwd", return_value=root):
                with redirect_stdout(output):
                    code = cli_main(["runs", "--workflow", workflow_id])
            self.assertEqual(code, 0)
            self.assertIn(run.id, output.getvalue())
            self.assertIn("completed", output.getvalue())

    def test_cli_status_includes_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = StateStore(root / ".agentops" / "state.sqlite")
            try:
                workflow_id = state.create_workflow("status workflow")
                run = state.create_agent_run(AgentRunContext(agent="demo", workflow_id=workflow_id))
                state.finish_agent_run(run.id, AgentRunOutcome(status=AgentRunStatus.FAILED, exit_code=1))
            finally:
                state.close()
            output = io.StringIO()
            with patch("agentops.cli.Path.cwd", return_value=root):
                with redirect_stdout(output):
                    code = cli_main(["status"])
            self.assertEqual(code, 0)
            self.assertIn(workflow_id, output.getvalue())
            self.assertIn("run failed", output.getvalue())

    def test_controller_and_serialization_expose_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = StateStore(root / ".agentops" / "state.sqlite")
            try:
                workflow_id = state.create_workflow("controller workflow")
                run = state.create_agent_run(AgentRunContext(
                    agent="demo", executable="/resolved/demo", role="implementation",
                    model="test-model", workflow_id=workflow_id, task_id="task-1"))
                state.finish_agent_run(run.id, AgentRunOutcome(status=AgentRunStatus.COMPLETED, exit_code=0))
            finally:
                state.close()
            controller = AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            runs = controller.list_agent_runs(root, task_id="task-1")
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["executable"], "/resolved/demo")
            self.assertEqual(runs[0]["model"], "test-model")
            workflow = controller.get_workflow(root, workflow_id)
            assert workflow is not None
            self.assertEqual(len(workflow["runs"]), 1)
            self.assertEqual(runs[0]["status"], "completed")


class RunsGuiController:
    def detect_agents(self):
        return {"demo": object()}

    def latest_workflow(self, directory):
        return None

    def list_agent_runs(self, directory, workflow_id=None, task_id=None,
                        status=None, limit=50, offset=0):
        return [{
            "status": "completed",
            "agent": "demo",
            "attempt": 2,
            "exit_code": 0,
            "duration_seconds": 1.5,
            "log_path": "demo.meta.log",
        }]


class RunsGuiTests(unittest.TestCase):
    def test_selected_task_shows_agent_runs(self):
        from agentops.gui import AgentOpsApp

        root = tk.Tk()
        root.withdraw()
        try:
            app = AgentOpsApp(root, controller=RunsGuiController())
            app._update_tasks([{
                "id": "task-1",
                "role": "implementation",
                "status": "passed",
                "assigned_agent": "demo",
                "attempts": 2,
                "dependencies": (),
                "description": "do work",
                "result": "done",
            }])
            app.task_tree.selection_set("task-0")
            app.show_selected_task()
            output = app.output.get("1.0", "end")
            self.assertIn("Agent runs:", output)
            self.assertIn("completed demo attempt 2", output)
            self.assertIn("Log: demo.meta.log", output)
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass


class NoWindowCollectorTests(unittest.TestCase):
    def test_collector_hides_consoles(self):
        import subprocess as stdlib_subprocess
        from unittest.mock import patch
        from agentops.agent_run import GitRunMetadataCollector
        completed = stdlib_subprocess.CompletedProcess(args=["git"], returncode=1, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            with patch("agentops.agent_run.subprocess.run", return_value=completed) as run:
                metadata = GitRunMetadataCollector()(directory)
        self.assertEqual(metadata.files_changed, ())
        self.assertIsNone(metadata.diff_stat)
        for call in run.call_args_list:
            kwargs = call.kwargs
            if sys.platform == "win32":
                self.assertEqual(kwargs.get("creationflags"), stdlib_subprocess.CREATE_NO_WINDOW)
            else:
                self.assertNotIn("creationflags", kwargs)


if __name__ == "__main__":
    unittest.main()
