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

from agentops.cli import main as cli_main
from agentops.config import AppConfig, AgentConfig, load_config
from agentops.gui_controller import AgentOpsController
from agentops.logging import LogManager
from agentops.registry import DetectedAgent
from agentops.runner import RunResult
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.verification_kernel import VerificationKernel
from agentops.verification_model import (
    VerificationCheckClass,
    VerificationCheckSpec,
    VerificationCheckStatus,
    VerificationExecutionPolicy,
    VerificationProfile,
    VerificationProfileMode,
    VerificationReportStatus,
    VerificationRunStatus,
    parse_check_outcome,
)
from agentops.workflow import WorkflowEngine


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


class HangingProcess:
    def __init__(self):
        self.returncode = None
        self.pid = None
        self.killed = False

    async def communicate(self):
        while not self.killed:
            await asyncio.sleep(0.005)
        return b"partial", b""

    def kill(self):
        self.killed = True


def _profile(**overrides):
    values = {
        "name": "standard",
        "mode": VerificationProfileMode.FAIL_FAST,
        "concurrency": 1,
        "default_timeout_seconds": 30,
        "checks": (),
    }
    values.update(overrides)
    return VerificationProfile(**values)


def _harness(directory, profile, **kernel_overrides):
    state = StateStore(":memory:")
    logs = LogManager(Path(directory) / "logs")
    kernel = VerificationKernel(
        profiles={profile.name: profile},
        default_profile=profile.name,
        logs=logs,
        state=state,
        **kernel_overrides,
    )
    return state, kernel


class ProfileConfigTests(unittest.TestCase):
    def test_valid_profiles_load(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({
                "agents": {},
                "verification": {
                    "default_profile": "standard",
                    "profiles": {
                        "standard": {
                            "mode": "continue_on_failure",
                            "concurrency": 2,
                            "default_timeout_seconds": 60,
                            "checks": [
                                {"name": "unit", "class": "tests",
                                 "command": ["python", "-m", "unittest"]},
                                {"name": "style", "class": "formatting",
                                 "command": ["check-format"], "required": False,
                                 "policy": "parallel", "timeout_seconds": 10},
                            ],
                        }
                    },
                },
            }, handle)
            path = handle.name
        try:
            config = load_config(path)
            profile = config.verification_profiles["standard"]
            self.assertEqual(profile.mode, VerificationProfileMode.CONTINUE_ON_FAILURE)
            self.assertEqual(profile.concurrency, 2)
            self.assertEqual(profile.checks[1].check_class, VerificationCheckClass.FORMATTING)
            self.assertFalse(profile.checks[1].required)
            self.assertEqual(config.default_verification_profile, "standard")
        finally:
            os.unlink(path)

    def test_invalid_profiles_rejected(self):
        cases = [
            {"profiles": {"dup": {"checks": [
                {"name": "a", "class": "tests", "command": ["x"]},
                {"name": "a", "class": "tests", "command": ["x"]}]}}},
            {"profiles": {"bad": {"checks": [
                {"name": "a", "class": "nope", "command": ["x"]}]}}},
            {"profiles": {
                "one": {"checks": [{"name": "a", "class": "tests", "command": ["x"]}]},
                "two": {"checks": [{"name": "b", "class": "tests", "command": ["x"]}]}}},
            {"profiles": {"bad": {"checks": []}}},
        ]
        for data in cases:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                json.dump({"agents": {}, "verification": data}, handle)
                path = handle.name
            try:
                with self.assertRaises(ValueError):
                    load_config(path)
            finally:
                os.unlink(path)


class ParseOutcomeTests(unittest.TestCase):
    def test_deterministic_mapping(self):
        self.assertEqual(
            parse_check_outcome(0, False, False, b"ok", b""),
            (VerificationCheckStatus.PASSED, None))
        self.assertEqual(
            parse_check_outcome(2, False, False, b"boom", b""),
            (VerificationCheckStatus.FAILED, "nonzero_exit"))
        self.assertEqual(
            parse_check_outcome(None, True, False, b"partial", b""),
            (VerificationCheckStatus.TIMED_OUT, "timeout"))
        self.assertEqual(
            parse_check_outcome(None, False, True, b"", b""),
            (VerificationCheckStatus.CANCELLED, "cancelled"))
        self.assertEqual(
            parse_check_outcome(0, False, False, b"\xff\xfe invalid", b""),
            (VerificationCheckStatus.FAILED, "malformed_output"))
        first = parse_check_outcome(3, False, False, b"same", b"same")
        second = parse_check_outcome(3, False, False, b"same", b"same")
        self.assertEqual(first, second)


class KernelExecutionTests(unittest.TestCase):
    def test_fail_fast_skips_remaining_checks(self):
        calls = []

        async def factory(*command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                return FakeProcess(b"ok", b"", 0)
            return FakeProcess(b"boom", b"", 1)

        profile = _profile(checks=(
            VerificationCheckSpec("first", VerificationCheckClass.TESTS, ("t1",)),
            VerificationCheckSpec("second", VerificationCheckClass.LINT, ("t2",)),
            VerificationCheckSpec("third", VerificationCheckClass.BUILD, ("t3",)),
        ))
        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                report = asyncio.run(kernel.run_verification(None, None, directory))
                self.assertEqual(report.overall_status, VerificationReportStatus.FAILED)
                self.assertEqual(report.required_failures, 1)
                self.assertEqual(len(calls), 2)
                statuses = {check.name: check.status for check in report.checks}
                self.assertEqual(statuses["third"], VerificationCheckStatus.SKIPPED)
            finally:
                state.close()

    def test_continue_on_failure_allows_optional_failure(self):
        async def factory(*command, **kwargs):
            if command == ("optional",):
                return FakeProcess(b"bad", b"", 1)
            return FakeProcess(b"good", b"", 0)

        profile = _profile(
            mode=VerificationProfileMode.CONTINUE_ON_FAILURE,
            checks=(
                VerificationCheckSpec("optional", VerificationCheckClass.FORMATTING,
                                      ("optional",), required=False),
                VerificationCheckSpec("required", VerificationCheckClass.TESTS, ("required",)),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                report = asyncio.run(kernel.run_verification(None, None, directory))
                self.assertEqual(report.overall_status, VerificationReportStatus.PASSED)
                self.assertEqual(report.failed_checks, 1)
                self.assertEqual(report.required_failures, 0)
            finally:
                state.close()

    def test_parallel_independent_checks_overlap(self):
        started = []

        async def factory(*command, **kwargs):
            started.append(command)
            for _ in range(200):
                if len(started) >= 2:
                    break
                await asyncio.sleep(0.005)
            return FakeProcess(b"ok", b"", 0)

        profile = _profile(
            mode=VerificationProfileMode.CONTINUE_ON_FAILURE,
            concurrency=2,
            checks=(
                VerificationCheckSpec("one", VerificationCheckClass.TESTS, ("one",),
                                      policy=VerificationExecutionPolicy.PARALLEL),
                VerificationCheckSpec("two", VerificationCheckClass.LINT, ("two",),
                                      policy=VerificationExecutionPolicy.PARALLEL),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                report = asyncio.run(kernel.run_verification(None, None, directory))
                self.assertEqual(report.overall_status, VerificationReportStatus.PASSED)
                self.assertEqual(len(started), 2)
            finally:
                state.close()

    def test_per_check_timeout(self):
        profile = _profile(checks=(
            VerificationCheckSpec("slow", VerificationCheckClass.TESTS, ("slow",),
                                  timeout_seconds=1),
        ))
        async def factory(*args, **kwargs):
            return HangingProcess()

        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                report = asyncio.run(kernel.run_verification(None, None, directory))
                self.assertEqual(report.checks[0].status, VerificationCheckStatus.TIMED_OUT)
                self.assertEqual(report.overall_status, VerificationReportStatus.TIMED_OUT)
            finally:
                state.close()

    def test_cancel_before_start_skips_checks(self):
        calls = []

        async def factory(*command, **kwargs):
            calls.append(command)
            return FakeProcess(b"ok", b"", 0)

        profile = _profile(checks=(
            VerificationCheckSpec("one", VerificationCheckClass.TESTS, ("one",)),
        ))
        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                cancel_event = threading.Event()
                cancel_event.set()
                report = asyncio.run(
                    kernel.run_verification(None, None, directory, cancel_event=cancel_event))
                self.assertEqual(report.overall_status, VerificationReportStatus.CANCELLED)
                self.assertEqual(calls, [])
            finally:
                state.close()

    def test_cancel_mid_run(self):
        profile = _profile(checks=(
            VerificationCheckSpec("slow", VerificationCheckClass.TESTS, ("slow",),
                                  timeout_seconds=30),
        ))
        async def factory(*args, **kwargs):
            return HangingProcess()

        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                cancel_event = threading.Event()
                timer = threading.Timer(0.05, cancel_event.set)
                timer.start()
                try:
                    report = asyncio.run(kernel.run_verification(
                        None, None, directory, cancel_event=cancel_event))
                finally:
                    timer.cancel()
                    timer.join(timeout=5)
                self.assertEqual(report.overall_status, VerificationReportStatus.CANCELLED)
            finally:
                state.close()

    def test_malformed_output_fails(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"\xff\xfe", b"", 0)

        profile = _profile(checks=(
            VerificationCheckSpec("odd", VerificationCheckClass.TESTS, ("odd",)),
        ))
        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                report = asyncio.run(kernel.run_verification(None, None, directory))
                self.assertEqual(report.checks[0].failure_reason, "malformed_output")
                self.assertEqual(report.overall_status, VerificationReportStatus.FAILED)
            finally:
                state.close()

    def test_process_failure_classified(self):
        async def factory(*command, **kwargs):
            raise OSError("cannot start")

        profile = _profile(checks=(
            VerificationCheckSpec("broken", VerificationCheckClass.BUILD, ("broken",)),
        ))
        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                report = asyncio.run(kernel.run_verification(None, None, directory))
                self.assertIn("process_error", report.checks[0].failure_reason or "")
                self.assertEqual(report.overall_status, VerificationReportStatus.FAILED)
            finally:
                state.close()

    def test_working_directory_cannot_escape(self):
        calls = []

        async def factory(*command, **kwargs):
            calls.append(command)
            return FakeProcess(b"ok", b"", 0)

        profile = _profile(checks=(
            VerificationCheckSpec("escaped", VerificationCheckClass.CUSTOM, ("x",),
                                  working_directory=".."),
        ))
        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                report = asyncio.run(kernel.run_verification(None, None, directory))
                self.assertEqual(calls, [])
                self.assertIn("invalid_working_directory", report.checks[0].failure_reason or "")
            finally:
                state.close()

    def test_fail_fast_parallel_pass_does_not_cancel_siblings(self):
        async def factory(*command, **kwargs):
            if command == ("slow",):
                await asyncio.sleep(0.2)
            return FakeProcess(b"ok", b"", 0)

        profile = _profile(
            concurrency=2,
            checks=(
                VerificationCheckSpec("fast", VerificationCheckClass.TESTS, ("fast",),
                                      policy=VerificationExecutionPolicy.PARALLEL),
                VerificationCheckSpec("slow", VerificationCheckClass.LINT, ("slow",),
                                      policy=VerificationExecutionPolicy.PARALLEL),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                report = asyncio.run(kernel.run_verification(None, None, directory))
                self.assertEqual(report.overall_status, VerificationReportStatus.PASSED)
                self.assertTrue(all(
                    check.status is VerificationCheckStatus.PASSED for check in report.checks))
            finally:
                state.close()

    def test_external_cancellation_finalizes_run(self):
        profile = _profile(checks=(
            VerificationCheckSpec("slow", VerificationCheckClass.TESTS, ("slow",)),
        ))
        async def factory(*args, **kwargs):
            return HangingProcess()

        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                async def go():
                    task = asyncio.create_task(
                        kernel.run_verification(None, None, directory))
                    await asyncio.sleep(0.05)
                    task.cancel()
                    await task
                with self.assertRaises(asyncio.CancelledError):
                    asyncio.run(go())
                runs = state.list_verification_runs()
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0].status, VerificationRunStatus.CANCELLED)
                self.assertEqual(runs[0].overall_status, VerificationReportStatus.CANCELLED)
            finally:
                state.close()

    def test_duplicate_check_names_rejected(self):
        profile = VerificationProfile(
            name="dup",
            checks=(
                VerificationCheckSpec("same", VerificationCheckClass.TESTS, ("a",)),
                VerificationCheckSpec("same", VerificationCheckClass.LINT, ("b",)),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile)
            try:
                with self.assertRaises(ValueError):
                    asyncio.run(kernel.run_verification(None, None, directory))
            finally:
                state.close()

    def test_legacy_commands_become_default_profile(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"legacy ok", b"", 0)

        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(":memory:")
            logs = LogManager(Path(directory) / "logs")
            try:
                kernel = VerificationKernel(
                    legacy_commands=(("legacy-one",), ("legacy-two",)),
                    logs=logs, state=state, process_factory=factory)
                report = asyncio.run(kernel.run_verification(None, None, directory))
                self.assertEqual(report.profile_name, "legacy")
                self.assertEqual(report.overall_status, VerificationReportStatus.PASSED)
            finally:
                state.close()

    def test_empty_profile_raises_instead_of_vacuous_pass(self):
        """A1R-[1]: zero-check profiles fail fast, never emit PASSED-for-nothing."""
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(":memory:")
            logs = LogManager(Path(directory) / "logs")
            try:
                kernel = VerificationKernel(
                    profiles={}, legacy_commands=(), logs=logs, state=state)
                with self.assertRaises(ValueError):
                    asyncio.run(kernel.run_verification(None, None, directory))
            finally:
                state.close()


class KernelPersistenceTests(unittest.TestCase):
    def test_run_check_report_persist_and_recover(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"evidence", b"", 0)

        profile = _profile(checks=(
            VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
        ))
        with tempfile.TemporaryDirectory() as directory:
            state, kernel = _harness(directory, profile, process_factory=factory)
            try:
                workflow_id = state.create_workflow("verification persistence")
                report = asyncio.run(
                    kernel.run_verification(workflow_id, "task-1", directory))
                stored = state.get_verification_run(report.run_id)
                self.assertEqual(stored.overall_status, VerificationReportStatus.PASSED)
                checks = state.list_verification_checks(report.run_id)
                self.assertEqual(len(checks), 1)
                self.assertTrue(Path(checks[0].stdout_path or "").is_file())
                persisted = state.get_verification_report_by_run(report.run_id)
                self.assertEqual(persisted.passed_checks, 1)

                stranded = state.create_verification_run(workflow_id, "task-2", profile)
                state.start_verification_run(stranded.id)
                state.close()
            except Exception:
                state.close()
                raise
            reopened = StateStore(":memory:")
            try:
                self.assertEqual(reopened.recover_verification_runs(), [])
            finally:
                reopened.close()

    def test_recover_file_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "state.sqlite")
            state = StateStore(database)
            try:
                workflow_id = state.create_workflow("recover verification")
                profile = _profile()
                run = state.create_verification_run(workflow_id, "task-1", profile)
                state.start_verification_run(run.id)
            finally:
                state.close()
            reopened = StateStore(database)
            try:
                recovered = reopened.recover_verification_runs()
                self.assertEqual(len(recovered), 1)
                self.assertEqual(recovered[0].status, VerificationRunStatus.FAILED)
                self.assertEqual(recovered[0].overall_status, VerificationReportStatus.FAILED)
            finally:
                reopened.close()

    def test_recovery_counts_only_required_interruptions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "state.sqlite")
            state = StateStore(database)
            try:
                workflow_id = state.create_workflow("recovery counts")
                profile = _profile()
                run = state.create_verification_run(workflow_id, "task-1", profile)
                state.start_verification_run(run.id)
                required = state.create_verification_check(
                    run.id, workflow_id, "task-1", profile.name, "req",
                    VerificationCheckClass.TESTS.value, ("req",), directory, 30, True,
                    VerificationExecutionPolicy.SEQUENTIAL.value)
                optional = state.create_verification_check(
                    run.id, workflow_id, "task-1", profile.name, "opt",
                    VerificationCheckClass.LINT.value, ("opt",), directory, 30, False,
                    VerificationExecutionPolicy.SEQUENTIAL.value)
                state.start_verification_check(required.id)
                state.start_verification_check(optional.id)
            finally:
                state.close()
            reopened = StateStore(database)
            try:
                recovered = reopened.recover_verification_runs()
                self.assertEqual(len(recovered), 1)
                finished = reopened.get_verification_run(run.id)
                self.assertEqual(finished.failed_checks, 2)
                self.assertEqual(finished.required_failures, 1)
                reasons = {check.name: check.failure_reason
                           for check in reopened.list_verification_checks(run.id)}
                self.assertEqual(reasons, {"req": "interrupted", "opt": "interrupted"})
            finally:
                reopened.close()

    def test_mode_rehydrated_as_enum(self):
        from agentops.verification_model import VerificationProfileMode

        state = StateStore(":memory:")
        try:
            run = state.create_verification_run(None, None, _profile())
            stored = state.get_verification_run(run.id)
            self.assertIsInstance(stored.mode, VerificationProfileMode)
        finally:
            state.close()

    def test_unknown_source_agent_run_rejected(self):
        state = StateStore(":memory:")
        try:
            with self.assertRaises(ValueError):
                state.create_verification_run(None, None, _profile(),
                                              source_agent_run_id="missing")
        finally:
            state.close()

    def test_legacy_task_table_migrates(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "legacy.sqlite")
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    CREATE TABLE tasks (
                        id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL,
                        description TEXT NOT NULL, role TEXT NOT NULL, assigned_agent TEXT,
                        dependencies TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
                        max_attempts INTEGER NOT NULL, result TEXT, created_at TEXT NOT NULL,
                        started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE workflows (
                        id TEXT PRIMARY KEY, description TEXT NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE events (
                        id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, task_id TEXT,
                        kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()
            state = StateStore(database)
            try:
                workflow_id = state.create_workflow("legacy verification")
                task = state.add_task(Task("check", "verification", workflow_id, max_attempts=1))
                self.assertFalse(task.verified)
                self.assertIsNone(task.verification_run_id)
                self.assertEqual(state.list_verification_runs(workflow_id), [])
            finally:
                state.close()

    def test_concurrent_workflows_persist(self):
        profile = _profile(checks=(
            VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
        ))
        state = StateStore(":memory:")
        try:
            run_ids: list[str] = []
            errors: list[BaseException] = []

            def make_run(index: int):
                try:
                    workflow_id = state.create_workflow(f"workflow-{index}")
                    run = state.create_verification_run(workflow_id, f"task-{index}", profile)
                    state.start_verification_run(run.id)
                    state.finish_verification_run(
                        run.id, VerificationRunStatus.COMPLETED,
                        VerificationReportStatus.PASSED, 1, 1, 0, 0, 0, 0.01)
                    run_ids.append(run.id)
                except BaseException as error:
                    errors.append(error)

            threads = [threading.Thread(target=make_run, args=(index,)) for index in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(run_ids), 4)
        finally:
            state.close()


class WorkflowIntegrationTests(unittest.TestCase):
    def test_agent_success_does_not_verify_task(self):
        config = AppConfig(
            {"worker": AgentConfig("worker", "fake", ("{prompt}",),
                                   ("architecture", "implementation", "review", "debugging"))},
            {role: ("worker",) for role in ("architecture", "implementation", "review", "debugging")},
            (), max_attempts=1, concurrency=1, max_repair_cycles=1,
        )
        state = StateStore(":memory:")
        registry = MagicMock()
        registry.select.return_value = DetectedAgent(config.agents["worker"], True, "/resolved/fake")
        runner = MagicMock()

        async def _run_agent(agent, prompt, directory, task_id, cancel_event=None,
                            run_context=None, run_observer=None, metadata_collector=None):
            return RunResult(agent.config.name, ("fake",), 0, "agent claims: verified: true",
                             "", 0.01, False, Path(f"{task_id}.log"))

        runner.run_agent = _run_agent
        calls = []

        async def factory(*command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                return FakeProcess(b"", b"failing test", 1)
            return FakeProcess(b"all green", b"", 0)

        with tempfile.TemporaryDirectory() as directory:
            logs = LogManager(Path(directory) / "logs")
            profile = _profile(checks=(
                VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
            ))
            kernel = VerificationKernel(
                profiles={profile.name: profile}, default_profile=profile.name,
                logs=logs, state=state, process_factory=factory)
            engine = WorkflowEngine(config, state, registry, runner, MagicMock(),
                                    verification_kernel=kernel)
            try:
                result = asyncio.run(engine.run_high_level("verify me", Path(directory)))
                self.assertTrue(result.ready)
                tasks = {task.role: task for task in state.list_tasks(result.workflow_id)}
                self.assertEqual(tasks["implementation"].status, TaskStatus.PASSED)
                self.assertFalse(tasks["implementation"].verified)
                verifications = [task for task in state.list_tasks(result.workflow_id)
                                 if task.role == "verification" and task.verified]
                self.assertTrue(verifications)
                self.assertTrue(all(task.verification_run_id for task in verifications))
            finally:
                state.close()


class VerificationInspectionTests(unittest.TestCase):
    def test_cli_verify_lists_and_details(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"ok", b"", 0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = StateStore(root / ".agentops" / "state.sqlite")
            try:
                workflow_id = state.create_workflow("verify inspection")
                profile = _profile(checks=(
                    VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
                ))
                logs = LogManager(root / ".agentops" / "logs")
                kernel = VerificationKernel(
                    profiles={profile.name: profile}, default_profile=profile.name,
                    logs=logs, state=state, process_factory=factory)
                report = asyncio.run(kernel.run_verification(workflow_id, "task-1", root))
            finally:
                state.close()
            with patch("agentops.cli.Path.cwd", return_value=root):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = cli_main(["verify", "--workflow", workflow_id])
                self.assertEqual(code, 0)
                self.assertIn(report.run_id, output.getvalue())
                detail = io.StringIO()
                with redirect_stdout(detail):
                    code = cli_main(["verify", "--run", report.run_id])
                self.assertEqual(code, 0)
                self.assertIn("unit", detail.getvalue())
                status_output = io.StringIO()
                with redirect_stdout(status_output):
                    code = cli_main(["status"])
                self.assertEqual(code, 0)
                self.assertIn("verification passed", status_output.getvalue())

    def test_controller_exposes_verification(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"ok", b"", 0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = StateStore(root / ".agentops" / "state.sqlite")
            try:
                workflow_id = state.create_workflow("controller verification")
                profile = _profile(checks=(
                    VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
                ))
                logs = LogManager(root / ".agentops" / "logs")
                kernel = VerificationKernel(
                    profiles={profile.name: profile}, default_profile=profile.name,
                    logs=logs, state=state, process_factory=factory)
                report = asyncio.run(kernel.run_verification(workflow_id, "task-1", root))
            finally:
                state.close()
            controller = AgentOpsController(config=AppConfig({}, {}, (), max_attempts=1, concurrency=1))
            runs = controller.list_verification_runs(root, workflow_id=workflow_id)
            self.assertEqual(len(runs), 1)
            detail = controller.get_verification_run(root, report.run_id)
            assert detail is not None
            self.assertEqual(detail["report"]["overall_status"], "passed")
            self.assertEqual(len(detail["checks"]), 1)
            workflow = controller.get_workflow(root, workflow_id)
            assert workflow is not None
            self.assertEqual(len(workflow["verifications"]), 1)


class VerificationGuiTests(unittest.TestCase):
    def test_selected_task_shows_verification(self):
        from agentops.gui import AgentOpsApp

        class Controller:
            def detect_agents(self):
                return {"demo": object()}

            def latest_workflow(self, directory):
                return None

            def list_agent_runs(self, directory, workflow_id=None, task_id=None,
                                status=None, limit=50, offset=0):
                return []

            def list_verification_runs(self, directory, workflow_id=None, task_id=None,
                                       limit=50, offset=0):
                return [{
                    "overall_status": "passed",
                    "profile_name": "standard",
                    "passed_checks": 2,
                    "total_checks": 2,
                }]

        root = tk.Tk()
        root.withdraw()
        try:
            app = AgentOpsApp(root, controller=Controller())
            app._update_tasks([{
                "id": "verify-1", "role": "verification", "status": "passed",
                "assigned_agent": "", "attempts": 1, "dependencies": (),
                "description": "check", "result": "ok",
            }])
            app.task_tree.selection_set("task-0")
            app.show_selected_task()
            self.assertIn("Verification:", app.output.get("1.0", "end"))
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass


if __name__ == "__main__":
    unittest.main()
