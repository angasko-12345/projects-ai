"""Persistence-failure policy (A8).

Covers the degradation recorder, the safe-to-degrade / must-fail-closed
classification, and the regression that a verification report must never
claim PASSED when a check's terminal state was not durably recorded.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentops.config import AppConfig, AgentConfig
from agentops.events import Event, EventSeverity, EventType
from agentops.failure import FailureClassifier, FailureSource
from agentops.finalize import finalize_worktree, record_worktree_provenance
from agentops.git import GitError, Worktree
from agentops.logging import LogManager
from agentops.persistence import (
    PERSISTENCE_POLICIES,
    Degradation,
    DegradationRecorder,
    PersistencePolicy,
    event_emitter,
    policy_for,
)
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner
from agentops.runtime import ProcessRuntime
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.verification_kernel import VerificationKernel
from agentops.verification_model import (
    VerificationCheckClass,
    VerificationCheckSpec,
    VerificationProfile,
    VerificationProfileMode,
    VerificationReportStatus,
)
from agentops.workflow import WorkflowEngine


def _profile(*checks: VerificationCheckSpec) -> VerificationProfile:
    return VerificationProfile(
        name="standard",
        mode=VerificationProfileMode.FAIL_FAST,
        concurrency=1,
        default_timeout_seconds=30,
        checks=checks,
    )


def _harness(directory, profile, state, **overrides):
    kernel = VerificationKernel(
        profiles={profile.name: profile},
        default_profile=profile.name,
        logs=LogManager(Path(directory) / "logs"),
        state=state,
        **overrides,
    )
    return kernel


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


class DegradationRecorderTests(unittest.TestCase):
    def test_recorder_collects_and_reports_degradations(self):
        recorder = DegradationRecorder()
        entry = recorder.record("failure.create", RuntimeError("disk full"), task_id="t-1")
        self.assertIsInstance(entry, Degradation)
        self.assertEqual(entry.operation, "failure.create")
        self.assertEqual(entry.error_type, "RuntimeError")
        self.assertEqual(entry.task_id, "t-1")
        self.assertEqual(len(recorder.degradations), 1)
        self.assertFalse(recorder.has_fail_closed)

    def test_recorder_emits_warning_event_best_effort(self):
        emitted: list[Event] = []
        recorder = DegradationRecorder(
            emit=event_emitter(emitted.append), )
        recorder.record("agent_run.create", RuntimeError("locked"), workflow_id="w-1")
        self.assertEqual(len(emitted), 1)
        event = emitted[0]
        self.assertIs(event.type, EventType.PERSISTENCE_DEGRADED)
        self.assertIs(event.severity, EventSeverity.WARNING)
        self.assertEqual(event.workflow_id, "w-1")
        self.assertIn("agent_run.create", event.message)
        self.assertEqual(event.payload["policy"], "safe_to_degrade")

    def test_recorder_survives_a_broken_emitter(self):
        def boom(_event: Event) -> None:
            raise RuntimeError("event store is down too")

        recorder = DegradationRecorder(emit=boom)
        recorder.record("agent_run.create", RuntimeError("locked"))
        self.assertEqual(len(recorder.degradations), 1)

    def test_recorder_tracks_fail_closed_degradations(self):
        recorder = DegradationRecorder()
        recorder.record("agent_run.create", RuntimeError("locked"))
        self.assertFalse(recorder.has_fail_closed)
        recorder.record("verification_check.finish", RuntimeError("locked"))
        self.assertTrue(recorder.has_fail_closed)
        recorder.clear()
        self.assertEqual(recorder.degradations, ())

    def test_recorder_trims_to_its_limit(self):
        recorder = DegradationRecorder(limit=3)
        for index in range(6):
            recorder.record("failure.create", RuntimeError(f"boom {index}"))
        self.assertEqual(len(recorder.degradations), 3)
        # Oldest entries are evicted first.
        self.assertEqual(
            [item.message for item in recorder.degradations],
            ["boom 3", "boom 4", "boom 5"],
        )

    def test_recorder_concurrent_records_do_not_lose_entries(self):
        """The GUI controller records from background threads."""
        import threading

        recorder = DegradationRecorder(limit=1000)
        errors: list[BaseException] = []

        def worker(offset: int) -> None:
            try:
                for index in range(50):
                    recorder.record("failure.create", RuntimeError(f"{offset}-{index}"))
            except BaseException as error:  # pragma: no cover - failure path
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(recorder.degradations), 200)
        self.assertEqual(
            len({item.message for item in recorder.degradations}), 200)

    def test_degradation_is_json_shaped(self):
        payload = Degradation("task.update", PersistencePolicy.MUST_FAIL_CLOSED,
                              "sqlite3.OperationalError", "locked").to_dict()
        self.assertEqual(payload["operation"], "task.update")
        self.assertEqual(payload["policy"], "must_fail_closed")
        self.assertEqual(payload["error_type"], "sqlite3.OperationalError")


class PersistencePolicyTests(unittest.TestCase):
    def test_every_recorded_operation_is_classified(self):
        for operation, policy in PERSISTENCE_POLICIES.items():
            self.assertIsInstance(policy, PersistencePolicy, operation)
            self.assertIs(policy_for(operation), policy)

    def test_critical_transitions_are_fail_closed(self):
        for operation in (
            "verification_check.finish",
            "verification_run.finish",
            "verification_report.create",
            "task.update",
            "merge.conflict_task",
        ):
            self.assertIs(
                policy_for(operation), PersistencePolicy.MUST_FAIL_CLOSED, operation
            )

    def test_diagnostic_writes_are_safe_to_degrade(self):
        for operation in (
            "agent_run.create",
            "agent_run.transition",
            "failure.create",
            "event.routing_decision",
        ):
            self.assertIs(
                policy_for(operation), PersistencePolicy.SAFE_TO_DEGRADE, operation
            )

    def test_unknown_operation_is_treated_as_fail_closed(self):
        self.assertIs(policy_for("something.new"), PersistencePolicy.MUST_FAIL_CLOSED)

    def test_worktree_provenance_is_classified_safe_to_degrade(self):
        self.assertIs(
            policy_for("worktree_ref.create"), PersistencePolicy.SAFE_TO_DEGRADE)


class KernelFailClosedTests(unittest.TestCase):
    """A8 regression: persistence failure must not fabricate verification."""

    def test_report_is_not_passed_when_check_state_cannot_persist(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"ok", b"", 0)

        profile = _profile(
            VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
        )
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(":memory:")
            kernel = _harness(directory, profile, state, process_factory=factory)
            try:
                workflow_id = state.create_workflow("persistence degradation")
                with patch.object(
                    state, "finish_verification_check",
                    side_effect=KeyError("check row vanished"),
                ):
                    report = asyncio.run(
                        kernel.run_verification(workflow_id, "task-1", directory))
                self.assertIsNot(
                    report.overall_status, VerificationReportStatus.PASSED,
                    "a report must never claim PASSED when check state was not persisted",
                )
                self.assertIs(report.overall_status, VerificationReportStatus.FAILED)
                self.assertIn("persist", report.transcript.lower())
            finally:
                state.close()

    def test_healthy_run_still_passes(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"ok", b"", 0)

        profile = _profile(
            VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
        )
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(":memory:")
            kernel = _harness(directory, profile, state, process_factory=factory)
            try:
                workflow_id = state.create_workflow("healthy")
                report = asyncio.run(
                    kernel.run_verification(workflow_id, "task-1", directory))
                self.assertIs(report.overall_status, VerificationReportStatus.PASSED)
            finally:
                state.close()

    def test_optional_check_persistence_failure_still_fails_closed(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"ok", b"", 0)

        profile = _profile(
            VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
            VerificationCheckSpec(
                "lint", VerificationCheckClass.LINT, ("lint",), required=False,
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(":memory:")
            kernel = _harness(directory, profile, state, process_factory=factory)
            try:
                workflow_id = state.create_workflow("optional check")
                with patch.object(
                    state, "finish_verification_check",
                    side_effect=KeyError("check row vanished"),
                ):
                    report = asyncio.run(
                        kernel.run_verification(workflow_id, "task-1", directory))
                self.assertIs(report.overall_status, VerificationReportStatus.FAILED)
            finally:
                state.close()

    def test_degradation_is_recorded_on_the_shared_recorder(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"ok", b"", 0)

        profile = _profile(
            VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
        )
        recorder = DegradationRecorder()
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(":memory:")
            kernel = _harness(
                directory, profile, state, process_factory=factory, degradation=recorder)
            try:
                workflow_id = state.create_workflow("recorded degradation")
                with patch.object(
                    state, "finish_verification_check",
                    side_effect=KeyError("check row vanished"),
                ):
                    asyncio.run(kernel.run_verification(workflow_id, "task-1", directory))
                operations = [item.operation for item in recorder.degradations]
                self.assertIn("verification_check.finish", operations)
            finally:
                state.close()

    def test_unclassified_persistence_errors_still_propagate(self):
        """A8 keeps the existing boundary: only the swallow is changed.

        An error the kernel never swallowed (a real SQLite failure) keeps
        aborting the run, which is already fail-closed.
        """
        async def factory(*command, **kwargs):
            return FakeProcess(b"ok", b"", 0)

        profile = _profile(
            VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
        )
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(":memory:")
            kernel = _harness(directory, profile, state, process_factory=factory)
            try:
                workflow_id = state.create_workflow("hard failure")
                with patch.object(
                    state, "finish_verification_check",
                    side_effect=sqlite3.OperationalError("disk I/O error"),
                ):
                    with self.assertRaises(sqlite3.OperationalError):
                        asyncio.run(
                            kernel.run_verification(workflow_id, "task-1", directory))
            finally:
                state.close()

    def test_check_that_cannot_be_started_does_not_report_passed(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"ok", b"", 0)

        profile = _profile(
            VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),
        )
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(":memory:")
            kernel = _harness(directory, profile, state, process_factory=factory)
            try:
                workflow_id = state.create_workflow("cannot start")
                with patch.object(
                    state, "start_verification_check",
                    side_effect=KeyError("check row vanished"),
                ):
                    report = asyncio.run(
                        kernel.run_verification(workflow_id, "task-1", directory))
                self.assertIs(report.overall_status, VerificationReportStatus.FAILED)
            finally:
                state.close()


class SafeToDegradeTests(unittest.TestCase):
    """A8: diagnostic writes may degrade, but never silently."""

    def test_runner_records_a_lost_run_row_and_still_executes(self):
        recorder = DegradationRecorder()
        with tempfile.TemporaryDirectory() as directory:
            async def factory(*command, **kwargs):
                return FakeProcess(b"done", b"", 0)

            runner = AgentRunner(
                LogManager(Path(directory) / "logs"),
                run_observer=MagicMock(**{
                    "create_run.side_effect": sqlite3.OperationalError("locked"),
                }),
                runtime=ProcessRuntime(spawn=factory),
                degradation=recorder,
            )
            agent = DetectedAgent(
                AgentConfig("pi", "pi", ("--print", "{prompt}"), ("implementation",)),
                True, "pi", "1.0.0")
            result = asyncio.run(
                runner.run_agent(agent, "hello", directory, task_id="t-1"))
            self.assertEqual(result.exit_code, 0)
        operations = [item.operation for item in recorder.degradations]
        self.assertIn("agent_run.create", operations)
        self.assertFalse(recorder.has_fail_closed)

    def test_runner_records_a_lost_run_transition(self):
        """A stranded RUNNING run is recoverable, so it degrades — loudly."""
        recorder = DegradationRecorder()
        with tempfile.TemporaryDirectory() as directory:
            async def factory(*command, **kwargs):
                return FakeProcess(b"done", b"", 0)

            observer = MagicMock()
            observer.mark_running.side_effect = sqlite3.OperationalError("locked")
            runner = AgentRunner(
                LogManager(Path(directory) / "logs"),
                run_observer=observer,
                runtime=ProcessRuntime(spawn=factory),
                degradation=recorder,
            )
            agent = DetectedAgent(
                AgentConfig("pi", "pi", ("--print", "{prompt}"), ("implementation",)),
                True, "pi", "1.0.0")
            result = asyncio.run(
                runner.run_agent(agent, "hello", directory, task_id="t-1"))
            self.assertEqual(result.exit_code, 0)
        operations = [item.operation for item in recorder.degradations]
        self.assertIn("agent_run.transition", operations)

    def test_workflow_records_a_lost_recovery_failure_row(self):
        """recover_incomplete bypasses record_failure and needs its own record."""
        recorder = DegradationRecorder()
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("recovery degradation")
            task = state.add_task(
                Task("do", "implementation", workflow_id, max_attempts=1))
            # A stranded task: FAILED with an interrupted result is what
            # recover_incomplete actually looks for.
            state.update_task(replace(
                task, status=TaskStatus.FAILED,
                result="Task execution was interrupted before completion."))
            engine = WorkflowEngine(
                AppConfig({}, {}), state, MagicMock(), MagicMock(), MagicMock(),
                degradation=recorder,
            )
            with patch.object(
                state, "create_failure",
                side_effect=sqlite3.OperationalError("locked"),
            ):
                engine.recover_incomplete(workflow_id)
            operations = [item.operation for item in recorder.degradations]
            self.assertIn("failure.create", operations)
        finally:
            state.close()

    def test_worktree_provenance_loss_is_reported(self):
        """A lost base-commit row must not be silent: retry_merge depends on it."""
        recorder = DegradationRecorder()
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("provenance")
            worktree = Worktree(
                Path("C:/repo"), Path("C:/wt"), "agentops/demo-12345678", "main", "abc123")
            with patch.object(state, "record_worktree_ref",
                              side_effect=sqlite3.OperationalError("locked")):
                stored = record_worktree_provenance(
                    state, worktree, workflow_id, recorder)
            self.assertFalse(stored)
            self.assertEqual(
                [item.operation for item in recorder.degradations],
                ["worktree_ref.create"])
        finally:
            state.close()

    def test_worktree_provenance_is_stored_on_success(self):
        recorder = DegradationRecorder()
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("provenance ok")
            worktree = Worktree(
                Path("C:/repo"), Path("C:/wt"), "agentops/demo-87654321", "main", "abc123")
            self.assertTrue(
                record_worktree_provenance(state, worktree, workflow_id, recorder))
            self.assertEqual(recorder.degradations, ())
            refs = state.list_worktree_refs(workflow_id)
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0].base_commit, "abc123")
        finally:
            state.close()

    def test_workflow_records_a_lost_failure_row(self):
        recorder = DegradationRecorder()
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("degraded failure row")
            task = state.add_task(Task("do", "implementation", workflow_id, max_attempts=1))
            engine = WorkflowEngine(
                AppConfig({}, {}), state, MagicMock(), MagicMock(), MagicMock(),
                degradation=recorder,
            )
            with patch.object(
                state, "create_failure",
                side_effect=sqlite3.OperationalError("locked"),
            ):
                failure = engine.record_failure(
                    task,
                    FailureClassifier.classify(source=FailureSource.SYSTEM, error="boom"),
                )
            self.assertIsNotNone(failure)
            operations = [item.operation for item in recorder.degradations]
            self.assertIn("failure.create", operations)
        finally:
            state.close()

    def test_merge_conflict_task_failure_is_fail_closed(self):
        """A8: a merge must never be reported handled without its record."""
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("merge degradation")
            manager = MagicMock()
            manager.commit_changes.return_value = True
            manager.merge.side_effect = GitError("conflict")
            worktree = Worktree(
                Path("C:/repo"), Path("C:/wt"), "agentops/demo-12345678", "main", "abc123")
            with patch.object(
                state, "add_task",
                side_effect=sqlite3.OperationalError("locked"),
            ):
                with self.assertRaises(sqlite3.OperationalError):
                    finalize_worktree(
                        manager, state, worktree, "thing", workflow_id, 2)
        finally:
            state.close()


if __name__ == "__main__":
    unittest.main()