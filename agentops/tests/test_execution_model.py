"""A1 invariant tests: the execution/result state machine never lies."""

import unittest

from agentops.agent_run import AgentRunStatus
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

    def test_passed_with_failed_checks_raises(self):
        with self.assertRaises(StateTransitionError):
            assert_report_consistent(_report(failed_checks=1, passed_checks=2))

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


if __name__ == "__main__":
    unittest.main()
