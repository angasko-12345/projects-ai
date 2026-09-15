"""A1 invariant tests: the execution/result state machine never lies."""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agentops.agent_run import (
    AgentRunContext,
    AgentRunOutcome,
    AgentRunStatus,
)
from agentops.execution_model import (
    StateTransitionError,
    assert_agent_run_transition,
    assert_no_fabricated_success,
    assert_report_consistent,
    assert_task_completion,
    assert_workflow_ready,
    ladder_position,
    layer_requires,
)
from agentops.tasks import Task, TaskStatus
from agentops.verification_model import VerificationReport, VerificationReportStatus


def _report(**overrides) -> VerificationReport:
    values = {
        "id": "rep-1", "run_id": "run-1", "workflow_id": "w",
        "task_id": "t", "profile_name": "standard",
        "total_checks": 3, "passed_checks": 3, "failed_checks": 0,
        "skipped_checks": 0, "required_failures": 0,
        "overall_status": VerificationReportStatus.PASSED,
    }
    values.update(overrides)
    return VerificationReport(**values)


class AgentRunMatrixTests(unittest.TestCase):
    def test_terminal_to_running_raises(self):
        for terminal in ("completed", "failed", "cancelled", "timed_out", "terminated"):
            with self.assertRaises(StateTransitionError, msg=terminal):
                assert_agent_run_transition(terminal, "running")

    def test_terminal_to_pending_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_agent_run_transition(AgentRunStatus.FAILED, AgentRunStatus.PENDING)

    def test_pending_to_terminal_allowed(self):
        # Deliberate compatibility behavior (post-hoc recording/fallbacks).
        for target in ("completed", "failed", "cancelled", "timed_out", "terminated"):
            assert_agent_run_transition("pending", target)

    def test_pending_to_starting_and_running_path(self):
        assert_agent_run_transition("pending", "starting")
        assert_agent_run_transition("starting", "running")
        assert_agent_run_transition("running", "completed")

    def test_pending_directly_to_running_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_agent_run_transition("pending", "running")

    def test_same_state_is_noop(self):
        assert_agent_run_transition("running", "running")

    def test_unknown_status_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_agent_run_transition("running", "exploded")


class ReportConsistencyTests(unittest.TestCase):
    def test_passed_report_validates(self):
        assert_report_consistent(_report())

    def test_passed_with_required_failures_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_report_consistent(_report(failed_checks=1, passed_checks=2, required_failures=1))

    def test_passed_with_only_optional_failures_validates(self):
        # Optional-check failures do not contradict PASSED.
        assert_report_consistent(_report(failed_checks=1, passed_checks=2, required_failures=0))

    def test_passed_all_skipped_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_report_consistent(_report(passed_checks=0, skipped_checks=3))

    def test_passed_with_required_failures_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_report_consistent(_report(required_failures=1))

    def test_passed_empty_suite_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_report_consistent(_report(total_checks=0, passed_checks=0))

    def test_failed_report_always_validates(self):
        assert_report_consistent(_report(overall_status=VerificationReportStatus.FAILED))


class TaskCompletionTests(unittest.TestCase):
    def test_verified_verification_task_validates(self):
        task = Task("v", "verification", "w", status=TaskStatus.PASSED,
                    verified=True, verification_run_id="run-1")
        assert_task_completion(task)

    def test_unverified_verification_task_raises(self):
        task = Task("v", "verification", "w", status=TaskStatus.PASSED)
        with self.assertRaises(StateTransitionError):
            assert_task_completion(task)

    def test_verified_without_any_evidence_raises(self):
        task = Task("v", "verification", "w", status=TaskStatus.PASSED, verified=True)
        with self.assertRaises(StateTransitionError):
            assert_task_completion(task)

    def test_legacy_transcript_counts_as_evidence(self):
        # Legacy command path: no run object exists; output is the evidence.
        task = Task("v", "verification", "w", status=TaskStatus.PASSED,
                    verified=True, result="tests passed")
        assert_task_completion(task)

    def test_verified_implementation_task_raises(self):
        # Agent success is not verification.
        task = Task("i", "implementation", "w", status=TaskStatus.PASSED, verified=True)
        with self.assertRaises(StateTransitionError):
            assert_task_completion(task)

    def test_verified_review_task_raises(self):
        # Reviews approve; they do not verify.
        task = Task("r", "review", "w", status=TaskStatus.PASSED, verified=True)
        with self.assertRaises(StateTransitionError):
            assert_task_completion(task)

    def test_failed_tasks_never_checked(self):
        task = Task("v", "verification", "w", status=TaskStatus.FAILED)
        assert_task_completion(task)


class WorkflowReadyTests(unittest.TestCase):
    def test_ready_with_all_signals(self):
        assert_workflow_ready(verification_ok=True, review_ok=True,
                              evidence_present=True, workflow_id="w")

    def test_ready_without_evidence_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_workflow_ready(verification_ok=True, review_ok=True,
                                  evidence_present=False, workflow_id="w")

    def test_ready_without_review_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_workflow_ready(verification_ok=True, review_ok=False,
                                  evidence_present=True, workflow_id="w")

    def test_ready_without_verification_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_workflow_ready(verification_ok=False, review_ok=True,
                                  evidence_present=True, workflow_id="w")


class HonestyAndLadderTests(unittest.TestCase):
    def test_success_without_evidence_raises(self):
        for status in ("completed", "passed", "ready", "merged", "success"):
            with self.assertRaises(StateTransitionError, msg=status):
                assert_no_fabricated_success(status, False, "recovery")

    def test_success_with_evidence_validates(self):
        assert_no_fabricated_success("completed", True, "recovery")

    def test_failed_without_evidence_is_legal(self):
        assert_no_fabricated_success("failed", False, "recovery")

    def test_ladder_ordering(self):
        self.assertLess(ladder_position("process"), ladder_position("agent"))
        self.assertLess(ladder_position("verification"), ladder_position("review"))
        self.assertLess(ladder_position("merge"), ladder_position("workflow"))
        self.assertEqual(layer_requires("process"), ())
        self.assertIn("verification", layer_requires("review"))
        with self.assertRaises(StateTransitionError):
            ladder_position("vibes")


class KernelEmptySuiteWiringTests(unittest.TestCase):
    """A1R-[1]: kernel-empty verification fails the task, never aborts."""

    def test_kernel_empty_task_fails_without_abort(self):
        from agentops.config import AgentConfig, AppConfig
        from agentops.state import StateStore
        from agentops.verification_kernel import VerificationKernel
        from agentops.workflow import WorkflowEngine
        config = AppConfig(
            {"fallback": AgentConfig("fallback", "fake", ("{prompt}",),
                                       ("implementation",))},
            {"implementation": ("fallback",), "verification": (),
             "review": (), "debugging": ()},
            (), max_attempts=1, concurrency=1,
        )
        state = StateStore(":memory:")
        try:
            engine = WorkflowEngine(
                config, state, MagicMock(), MagicMock(), MagicMock(),
                verification_kernel=VerificationKernel(profiles={}, legacy_commands=()))
            wid, created = engine.create_workflow("custom", [
                {"id": "v", "description": "check", "role": "verification"},
            ])
            # Must not raise: empty suite -> task FAILED via the generic
            # handler (consistent with the legacy empty-suite rule).
            asyncio.run(engine.execute(wid, Path.cwd()))
            final = state.get_task(created[0].id)
            self.assertEqual(final.status, TaskStatus.FAILED)
            self.assertFalse(final.verified)
        finally:
            state.close()


class RecoveryHonestyWiringTests(unittest.TestCase):
    """A1R-[2]: recover_* post-conditions never mint success."""

    def test_recovery_outputs_contain_no_success(self):
        from agentops.state import StateStore
        from agentops.verification_model import VerificationProfile
        state = StateStore(":memory:")
        try:
            wid = state.create_workflow("w")
            run = state.create_agent_run(AgentRunContext(workflow_id=wid, task_id="t"))
            state.start_agent_run(run.id)
            task = state.add_task(Task("work", "implementation", wid))
            claimed = state.claim_task(task.id)
            assert claimed is not None
            profile = VerificationProfile(name="p", checks=())
            vrun = state.create_verification_run(wid, task.id, profile)
            recovered_runs = state.recover_agent_runs()
            recovered_vruns = state.recover_verification_runs()
            recovered_tasks = state.recover_tasks()
            self.assertTrue(recovered_runs and recovered_vruns and recovered_tasks)
            for item in (*recovered_runs, *recovered_vruns, *recovered_tasks):
                self.assertNotIn(item.status.value,
                                 {"completed", "passed", "ready", "merged", "success"})
            self.assertEqual(vrun.id, recovered_vruns[0].id)
        finally:
            state.close()


class SameStateNoopTests(unittest.TestCase):
    """A1R-[4]: same-state transitions write nothing and emit nothing."""

    def test_repeat_finish_emits_no_event(self):
        from agentops.state import StateStore
        state = StateStore(":memory:")
        try:
            wid = state.create_workflow("w")
            run = state.create_agent_run(AgentRunContext(workflow_id=wid, task_id="t"))
            state.finish_agent_run(run.id, AgentRunOutcome(status=AgentRunStatus.FAILED))
            before = state.list_events(wid)
            state.finish_agent_run(run.id, AgentRunOutcome(status=AgentRunStatus.FAILED))
            after = state.list_events(wid)
            self.assertEqual(len(before), len(after))
            self.assertEqual(state.get_agent_run(run.id).status, AgentRunStatus.FAILED)
        finally:
            state.close()


if __name__ == "__main__":
    unittest.main()
