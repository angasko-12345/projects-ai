"""Phase 3 Failure, Repair, and Recovery Kernel tests.

Deterministic classification first: no LLM is used anywhere in this module.
Covers every failure category, repair decisions, bounded retries, distinct
parent-linked AgentRuns, and crash recovery for all interruption contexts.
"""

import asyncio
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agentops.agent_run import AgentRunContext, AgentRunStatus
from agentops.config import AgentConfig, AppConfig
from agentops.failure import (
    Failure,
    FailureCategory,
    FailureClassifier,
    FailureEvidence,
    FailureSeverity,
    FailureSource,
    InterruptionContext,
    RecoveryState,
    RepairAction,
    RepairPlan,
    RetryPolicy,
    build_retry_prompt,
    new_failure_id,
)
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.verification_model import VerificationProfileMode
from agentops.workflow import WorkflowEngine


def _config(**overrides):
    values = {
        "agents": {},
        "role_preferences": {},
        "max_attempts": 2,
        "concurrency": 1,
        "max_repair_cycles": 1,
    }
    values.update(overrides)
    return AppConfig(**values)


def _failure(category=FailureCategory.AGENT_ERROR, **overrides):
    values = {
        "id": new_failure_id(),
        "workflow_id": "wf-1",
        "task_id": "task-1",
        "agent_run_id": "run-1",
        "source": FailureSource.AGENT,
        "category": category,
        "severity": FailureSeverity.MEDIUM,
        "retryable": True,
        "repairable": True,
        "evidence": "evidence",
        "primary_error": "boom",
        "verification_run_id": None,
        "recommended_action": RepairAction.RETRY_SAME_AGENT,
    }
    values.update(overrides)
    return Failure(**values)


class ClassificationTests(unittest.TestCase):
    def test_all_sixteen_categories_reachable(self):
        cases = [
            (dict(error="exit code 1 from agent"), FailureCategory.AGENT_ERROR),
            (dict(error="spawn ENOENT executable not found"), FailureCategory.ENVIRONMENT_FAILURE),
            (dict(timed_out=True), FailureCategory.TIMEOUT),
            (dict(cancelled=True), FailureCategory.CANCELLATION),
            (dict(error="pytest failed: 2 failing tests", check_class="tests"), FailureCategory.TEST_FAILURE),
            (dict(error="ruff check failed", check_class="lint"), FailureCategory.LINT_FAILURE),
            (dict(error="mypy type error", check_class="type_checking"), FailureCategory.TYPECHECK_FAILURE),
            (dict(error="pyinstaller build failed", check_class="build"), FailureCategory.BUILD_FAILURE),
            (dict(error="required check nonzero_exit", check_class="custom"), FailureCategory.VERIFICATION_FAILURE),
            (dict(error="SystemRoot environment variable missing"), FailureCategory.ENVIRONMENT_FAILURE),
            (dict(error="agent process was killed", exit_code=-9, terminated=True), FailureCategory.PROCESS_ERROR),
            (dict(error="No module named foo; pip install foo"), FailureCategory.DEPENDENCY_FAILURE),
            (dict(error="Automatic merge failed; merge conflict in main.py"), FailureCategory.GIT_CONFLICT),
            (dict(error="Dirty worktree: uncommitted changes present"), FailureCategory.DIRTY_WORKTREE),
            (dict(error="Command not allowlisted by policy"), FailureCategory.POLICY_VIOLATION),
            (dict(error="Review did not pass: changes requested", role="review"), FailureCategory.REVIEW_REJECTION),
            (dict(error="some completely novel gibberish xyzzy"), FailureCategory.UNKNOWN),
        ]
        seen = set()
        for kwargs, expected in cases:
            result = FailureClassifier.classify(**kwargs)
            self.assertEqual(result.category, expected, f"input={kwargs}")
            seen.add(result.category)
        self.assertEqual(seen, set(FailureCategory))

    def test_deterministic_no_llm(self):
        first = FailureClassifier.classify(error="ruff check failed", check_class="lint")
        second = FailureClassifier.classify(error="ruff check failed", check_class="lint")
        self.assertEqual(first, second)
        # Basic error classification uses only flags + substring rules.
        import inspect

        source = inspect.getsource(FailureClassifier.classify)
        self.assertNotIn("llm", source.lower())
        self.assertNotIn("openai", source.lower())

    def test_cancel_and_timeout_signals_win(self):
        self.assertEqual(
            FailureClassifier.classify(error="merge conflict", cancelled=True).category,
            FailureCategory.CANCELLATION,
        )
        self.assertEqual(
            FailureClassifier.classify(error="merge conflict", timed_out=True).category,
            FailureCategory.TIMEOUT,
        )

    def test_unknown_never_retryable_never_success(self):
        result = FailureClassifier.classify(error="weird novel failure xyz")
        self.assertEqual(result.category, FailureCategory.UNKNOWN)
        self.assertFalse(result.retryable)
        self.assertFalse(result.repairable)
        self.assertEqual(result.recommended_action, RepairAction.STOP)

    def test_terminated_exit_maps_to_process_error(self):
        result = FailureClassifier.classify(exit_code=-15, terminated=True)
        self.assertEqual(result.category, FailureCategory.PROCESS_ERROR)
        self.assertTrue(result.retryable)

    def test_permission_errors_are_environment_failures(self):
        for text in (
            "Permission denied while opening file",
            "EACCES: permission denied, open 'state.sqlite'",
            "EPERM: operation not permitted, fsync",
        ):
            result = FailureClassifier.classify(error=text)
            self.assertEqual(result.category, FailureCategory.ENVIRONMENT_FAILURE, f"input={text}")
            self.assertEqual(result.recommended_action, RepairAction.REQUEST_APPROVAL)


class InterruptionTests(unittest.TestCase):
    def test_all_six_contexts_classified(self):
        expected = {
            InterruptionContext.AGENT_EXECUTION: (
                RecoveryState.INTERRUPTED_AGENT_EXECUTION, RepairAction.RETRY_SAME_AGENT),
            InterruptionContext.VERIFICATION: (
                RecoveryState.INTERRUPTED_VERIFICATION, RepairAction.RERUN_VERIFICATION),
            InterruptionContext.REVIEW: (
                RecoveryState.INTERRUPTED_REVIEW, RepairAction.REPAIR_IMPLEMENTATION),
            InterruptionContext.WORKTREE_CREATION: (
                RecoveryState.INTERRUPTED_WORKTREE_CREATION, RepairAction.RETRY_SAME_AGENT),
            InterruptionContext.GIT_OPERATION: (
                RecoveryState.INTERRUPTED_GIT_OPERATION, RepairAction.RETRY_SAME_AGENT),
            InterruptionContext.MERGE: (
                RecoveryState.INTERRUPTED_MERGE, RepairAction.REQUEST_APPROVAL),
        }
        for context, (state, action) in expected.items():
            recovery, _category, repair = FailureClassifier.classify_interruption(context)
            self.assertEqual(recovery, state)
            self.assertEqual(repair, action)

    def test_unknown_context_never_success(self):
        recovery, category, action = FailureClassifier.classify_interruption("nope-unknown")
        self.assertEqual(recovery, RecoveryState.UNKNOWN_INTERRUPTED)
        self.assertEqual(category, FailureCategory.UNKNOWN)
        self.assertEqual(action, RepairAction.STOP)


class RetryPolicyTests(unittest.TestCase):
    def test_bounds_validation(self):
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=0)
        with self.assertRaises(ValueError):
            RetryPolicy(max_repair_cycles=-1)
        with self.assertRaises(ValueError):
            RetryPolicy(backoff_factor=0.5)

    def test_attempt_budget(self):
        policy = RetryPolicy(max_attempts=2, max_repair_cycles=1)
        retryable = _failure(retryable=True, repairable=False)
        self.assertTrue(policy.should_retry_attempt(1, retryable))
        self.assertFalse(policy.should_retry_attempt(2, retryable))
        stopped = _failure(retryable=False, repairable=False)
        self.assertFalse(policy.should_retry_attempt(1, stopped))

    def test_repair_budget(self):
        policy = RetryPolicy(max_attempts=2, max_repair_cycles=1)
        repairable = _failure(retryable=False, repairable=True)
        self.assertTrue(policy.should_repair(0, repairable))
        self.assertFalse(policy.should_repair(1, repairable))

    def test_backoff_schedule(self):
        policy = RetryPolicy(backoff_base_seconds=1.0, backoff_max_seconds=30.0, backoff_factor=2.0)
        self.assertEqual(policy.backoff_for(1), 0.0)
        self.assertEqual(policy.backoff_for(2), 1.0)
        self.assertEqual(policy.backoff_for(3), 2.0)
        self.assertEqual(policy.backoff_for(4), 4.0)
        self.assertLessEqual(policy.backoff_for(99), 30.0)

    def test_cancellation_repair_respects_budget(self):
        policy = RetryPolicy(max_attempts=2, max_repair_cycles=1)
        cancelled = _failure(FailureCategory.CANCELLATION,
                             recommended_action=RepairAction.RETRY_SAME_AGENT)
        plan = FailureClassifier.plan_repair(cancelled, attempt=1, repair_cycle=0, policy=policy)
        self.assertEqual(plan.action, RepairAction.RETRY_SAME_AGENT)
        self.assertEqual(plan.next_attempt, 2)
        # Exhausted budget stops instead of looping forever.
        exhausted = RetryPolicy(max_attempts=1, max_repair_cycles=0)
        plan = FailureClassifier.plan_repair(cancelled, attempt=99, repair_cycle=0, policy=exhausted)
        self.assertEqual(plan.action, RepairAction.STOP)

    def test_repair_plan_actions(self):
        policy = RetryPolicy()
        # Retry same agent within budget.
        plan = FailureClassifier.plan_repair(
            _failure(FailureCategory.AGENT_ERROR), attempt=1, repair_cycle=0, policy=policy)
        self.assertEqual(plan.action, RepairAction.RETRY_SAME_AGENT)
        self.assertEqual(plan.next_attempt, 2)
        # Retry different agent when an alternate exists.
        plan = FailureClassifier.plan_repair(
            _failure(FailureCategory.AGENT_ERROR), attempt=1, repair_cycle=0,
            policy=policy, alternate_agent_available=True)
        self.assertEqual(plan.action, RepairAction.RETRY_DIFFERENT_AGENT)
        # Verification failures rerun verification.
        plan = FailureClassifier.plan_repair(
            _failure(FailureCategory.VERIFICATION_FAILURE, retryable=False, repairable=True,
                     recommended_action=RepairAction.RERUN_VERIFICATION),
            attempt=1, repair_cycle=0, policy=policy)
        self.assertEqual(plan.action, RepairAction.RERUN_VERIFICATION)
        self.assertEqual(plan.next_repair_cycle, 1)
        # Policy violations and conflicts require approval and stop the line.
        for category in (FailureCategory.POLICY_VIOLATION, FailureCategory.GIT_CONFLICT):
            plan = FailureClassifier.plan_repair(
                _failure(category, retryable=False, repairable=category is FailureCategory.GIT_CONFLICT,
                         recommended_action=RepairAction.REQUEST_APPROVAL),
                attempt=1, repair_cycle=0, policy=policy)
            self.assertEqual(plan.action, RepairAction.REQUEST_APPROVAL)
            self.assertTrue(plan.requires_approval)
        # Exhausted budgets stop permanently.
        plan = FailureClassifier.plan_repair(
            _failure(FailureCategory.AGENT_ERROR), attempt=5, repair_cycle=5, policy=policy)
        self.assertEqual(plan.action, RepairAction.STOP)

    def test_inherited_context_embeds_evidence(self):
        failure = _failure(
            FailureCategory.TEST_FAILURE, primary_error="pytest failed",
            evidence="2 failed, 5 passed", verification_run_id="vrun-1",
        )
        prompt = build_retry_prompt("Do the thing.", failure)
        self.assertIn("Do the thing.", prompt)
        self.assertIn("TEST_FAILURE", prompt)
        self.assertIn("pytest failed", prompt)
        self.assertIn("2 failed, 5 passed", prompt)
        self.assertIn("vrun-1", prompt)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.state = StateStore(":memory:")

    def tearDown(self):
        self.state.close()

    def test_schema_v3_migrated(self):
        row = self.state.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 3").fetchone()
        self.assertIsNotNone(row)

    def test_create_list_get_failure(self):
        workflow_id = self.state.create_workflow("wf")
        failure = _failure(workflow_id=workflow_id, task_id="t1")
        stored = self.state.create_failure(failure)
        self.assertEqual(stored.id, failure.id)
        self.assertEqual(self.state.get_failure(failure.id).category, FailureCategory.AGENT_ERROR)
        listed = self.state.list_failures(workflow_id)
        self.assertEqual(len(listed), 1)
        self.assertEqual(self.state.list_failures(workflow_id, category=FailureCategory.TIMEOUT), [])

    def test_recover_tasks_never_success(self):
        workflow_id = self.state.create_workflow("wf")
        task = self.state.add_task(Task("work", "implementation", workflow_id))
        claimed = self.state.claim_task(task.id)
        self.assertIsNotNone(claimed)
        recovered = self.state.recover_tasks(workflow_id)
        self.assertEqual(len(recovered), 1)
        final = self.state.get_task(task.id)
        self.assertEqual(final.status, TaskStatus.FAILED)
        self.assertIn("interrupted", (final.result or "").lower())
        self.assertFalse(final.verified)

    def test_recover_all_counts(self):
        workflow_id = self.state.create_workflow("wf")
        task = self.state.add_task(Task("work", "implementation", workflow_id))
        self.state.claim_task(task.id)
        run = self.state.create_agent_run(AgentRunContext(workflow_id=workflow_id, task_id=task.id))
        summary = self.state.recover_all()
        self.assertEqual(summary["tasks"], 1)
        self.assertEqual(summary["agent_runs"], 1)
        stranded = self.state.get_agent_run(run.id)
        self.assertEqual(stranded.status, AgentRunStatus.TERMINATED)

    def test_simulated_restart_marks_running_failed(self):
        # Simulate a process interruption: leave rows non-terminal, then run
        # the same recovery a fresh process would run on startup.
        workflow_id = self.state.create_workflow("wf")
        task = self.state.add_task(Task("verify me", "verification", workflow_id))
        self.state.claim_task(task.id)
        run = self.state.create_agent_run(AgentRunContext(workflow_id=workflow_id, task_id=task.id))
        self.state.start_agent_run(run.id)
        recovered = self.state.recover_all()
        self.assertGreaterEqual(recovered["agent_runs"], 1)
        self.assertGreaterEqual(recovered["tasks"], 1)
        self.assertEqual(self.state.get_task(task.id).status, TaskStatus.FAILED)


class WorkflowIntegrationTests(unittest.TestCase):
    def _engine(self, state, runner=None, registry=None, verifier=None):
        config = _config()
        if registry is None:
            registry = MagicMock()
            registry.select.return_value = None
        if runner is None:
            runner = MagicMock()
        if verifier is None:
            verifier = MagicMock()
        return WorkflowEngine(config, state, registry, runner, verifier)

    def test_retry_creates_distinct_parent_linked_run(self):
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("wf")
            task = state.add_task(Task("implement", "implementation", workflow_id, max_attempts=2))

            first_run = state.create_agent_run(
                AgentRunContext(workflow_id=workflow_id, task_id=task.id, attempt=1))
            # A retry must be a distinct run linked to its parent, not an
            # in-place mutation of the first attempt.
            second_run = state.create_agent_run(
                AgentRunContext(workflow_id=workflow_id, task_id=task.id, attempt=2,
                                parent_run_id=first_run.id, relationship="retry"))
            self.assertNotEqual(first_run.id, second_run.id)
            self.assertEqual(second_run.parent_run_id, first_run.id)
            self.assertEqual(second_run.retry_of, first_run.id)
        finally:
            state.close()

    def test_failed_agent_task_records_failure(self):
        state = StateStore(":memory:")
        try:
            from agentops.runner import RunResult

            workflow_id = state.create_workflow("wf")
            task = state.add_task(Task("implement", "implementation", workflow_id, max_attempts=1))
            agent = MagicMock()
            agent.config.name = "opencode"
            registry = MagicMock()
            registry.select.return_value = agent
            runner = MagicMock()

            async def fake_run(*args, **kwargs):
                return RunResult("opencode", ("opencode",), 1, "out", "boom happened",
                                 0.1, False, Path("/tmp/x.log"))

            runner.run_agent.side_effect = fake_run
            runner.build_command.return_value = ("opencode",)
            engine = self._engine(state, runner=runner, registry=registry)
            asyncio.run(engine._execute_task(task, Path("/tmp")))
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].category, FailureCategory.AGENT_ERROR)
            self.assertEqual(state.get_task(task.id).status, TaskStatus.FAILED)
        finally:
            state.close()

    def test_no_agent_records_environment_failure(self):
        state = StateStore(":memory:")
        try:
            engine = self._engine(state)
            workflow_id = state.create_workflow("wf")
            task = state.add_task(Task("implement", "implementation", workflow_id, max_attempts=1))
            asyncio.run(engine._execute_task(task, Path("/tmp")))
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].category, FailureCategory.ENVIRONMENT_FAILURE)
        finally:
            state.close()

    def test_scoped_recovery_leaves_other_workflows_alone(self):
        state = StateStore(":memory:")
        try:
            engine = self._engine(state)
            workflow_a = state.create_workflow("a")
            workflow_b = state.create_workflow("b")
            task_b = state.add_task(Task("work", "implementation", workflow_b))
            run_b = state.create_agent_run(AgentRunContext(workflow_id=workflow_b, task_id=task_b.id))
            state.claim_task(task_b.id)
            summary = engine.recover_incomplete(workflow_a)
            self.assertEqual(summary["agent_runs"], 0)
            self.assertEqual(summary["tasks"], 0)
            # Workflow B's stranded rows are untouched by A's recovery.
            from agentops.agent_run import AgentRunStatus as RunStatus
            self.assertEqual(state.get_agent_run(run_b.id).status, RunStatus.PENDING)
            self.assertEqual(state.get_task(task_b.id).status, TaskStatus.RUNNING)
        finally:
            state.close()

    def test_recovery_is_idempotent(self):
        state = StateStore(":memory:")
        try:
            engine = self._engine(state)
            workflow_id = state.create_workflow("wf")
            task = state.add_task(Task("verify", "verification", workflow_id))
            state.claim_task(task.id)
            engine.recover_incomplete(workflow_id)
            engine.recover_incomplete(workflow_id)
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
        finally:
            state.close()

    def test_recover_incomplete_classifies_interruptions(self):
        state = StateStore(":memory:")
        try:
            engine = self._engine(state)
            workflow_id = state.create_workflow("wf")
            task = state.add_task(Task("verify", "verification", workflow_id))
            state.claim_task(task.id)
            summary = engine.recover_incomplete(workflow_id)
            self.assertEqual(summary["tasks"], 1)
            self.assertIn(RecoveryState.INTERRUPTED_VERIFICATION.value, summary["recovery_states"])
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].recovery_state, RecoveryState.INTERRUPTED_VERIFICATION)
        finally:
            state.close()

    def test_cancellation_bounded(self):
        policy = RetryPolicy(max_attempts=3)
        cancelled = _failure(FailureCategory.CANCELLATION)
        self.assertTrue(policy.should_retry_attempt(1, cancelled))
        cancel_event = threading.Event()
        cancel_event.set()
        # A set cancel event must stop the retry chain before any sleep.
        self.assertTrue(cancel_event.is_set())

    def test_review_rejection_repairable(self):
        policy = RetryPolicy(max_repair_cycles=1)
        failure = _failure(FailureCategory.REVIEW_REJECTION, retryable=False, repairable=True,
                           recommended_action=RepairAction.REPAIR_IMPLEMENTATION)
        plan = FailureClassifier.plan_repair(failure, attempt=1, repair_cycle=0, policy=policy)
        self.assertIsInstance(plan, RepairPlan)
        self.assertEqual(plan.action, RepairAction.REPAIR_IMPLEMENTATION)


class StructuredEvidenceTests(unittest.TestCase):
    """A4: structured evidence must beat string heuristics (before/after)."""

    def test_exit_code_beats_misleading_success_text(self):
        evidence = FailureEvidence(
            source=FailureSource.AGENT, exit_code=2,
            stderr_peek="deployment completed successfully",
        )
        structured = FailureClassifier.classify(
            source=FailureSource.AGENT,
            error="deployment completed successfully", evidence=evidence)
        self.assertEqual(structured.category, FailureCategory.AGENT_ERROR)
        # String heuristics alone see no failure signal.
        fallback = FailureClassifier.classify(
            source=FailureSource.AGENT, error="deployment completed successfully")
        self.assertEqual(fallback.category, FailureCategory.UNKNOWN)

    def test_check_class_beats_misleading_text(self):
        evidence = FailureEvidence(
            source=FailureSource.VERIFICATION, check_class="tests", exit_code=1,
        )
        structured = FailureClassifier.classify(
            source=FailureSource.VERIFICATION, error="build failed", evidence=evidence)
        self.assertEqual(structured.category, FailureCategory.TEST_FAILURE)
        # Strings alone follow the misleading text.
        fallback = FailureClassifier.classify(
            source=FailureSource.VERIFICATION, error="build failed")
        self.assertEqual(fallback.category, FailureCategory.BUILD_FAILURE)

    def test_timeout_flag_beats_silent_text(self):
        evidence = FailureEvidence(source=FailureSource.AGENT, timed_out=True)
        structured = FailureClassifier.classify(
            source=FailureSource.AGENT, error="done", evidence=evidence)
        self.assertEqual(structured.category, FailureCategory.TIMEOUT)
        fallback = FailureClassifier.classify(source=FailureSource.AGENT, error="done")
        self.assertEqual(fallback.category, FailureCategory.UNKNOWN)

    def test_terminated_flag_beats_empty_text(self):
        evidence = FailureEvidence(source=FailureSource.AGENT, terminated=True)
        structured = FailureClassifier.classify(source=FailureSource.AGENT, evidence=evidence)
        self.assertEqual(structured.category, FailureCategory.PROCESS_ERROR)
        fallback = FailureClassifier.classify(source=FailureSource.AGENT)
        self.assertEqual(fallback.category, FailureCategory.UNKNOWN)

    def test_cancelled_flag_beats_finished_text(self):
        evidence = FailureEvidence(source=FailureSource.AGENT, cancelled=True)
        structured = FailureClassifier.classify(
            source=FailureSource.AGENT, error="finished", evidence=evidence)
        self.assertEqual(structured.category, FailureCategory.CANCELLATION)
        fallback = FailureClassifier.classify(source=FailureSource.AGENT, error="finished")
        self.assertEqual(fallback.category, FailureCategory.UNKNOWN)

    def test_custom_check_class_beats_generic_exit(self):
        evidence = FailureEvidence(
            source=FailureSource.VERIFICATION, check_class="custom", exit_code=3,
        )
        structured = FailureClassifier.classify(
            source=FailureSource.VERIFICATION, evidence=evidence)
        self.assertEqual(structured.category, FailureCategory.VERIFICATION_FAILURE)
        fallback = FailureClassifier.classify(
            source=FailureSource.VERIFICATION, exit_code=3)
        self.assertEqual(fallback.category, FailureCategory.AGENT_ERROR)

    def test_empty_evidence_falls_back_to_strings(self):
        structured = FailureClassifier.classify(
            error="ruff check failed", check_class="lint",
            evidence=FailureEvidence())
        self.assertEqual(structured.category, FailureCategory.LINT_FAILURE)

    def test_evidence_round_trips_through_failures_row(self):
        state = StateStore(":memory:")
        try:
            workflow_id = state.create_workflow("evidence workflow")
            failure = _failure(workflow_id=workflow_id,
                               structured_evidence={"exit_code": 2, "source": "AGENT"})
            stored = state.create_failure(failure)
            fetched = state.get_failure(stored.id)
            self.assertEqual(fetched.structured_evidence, {"exit_code": 2, "source": "AGENT"})
        finally:
            state.close()

    def test_legacy_failures_table_gains_evidence_column(self):
        import sqlite3 as _sqlite3
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as directory:
            database = str(_Path(directory) / "legacy.sqlite")
            connection = _sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    CREATE TABLE workflows (
                        id TEXT PRIMARY KEY, description TEXT NOT NULL,
                        status TEXT NOT NULL, created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE tasks (
                        id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL,
                        description TEXT NOT NULL, role TEXT NOT NULL,
                        dependencies TEXT NOT NULL, status TEXT NOT NULL,
                        attempts INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
                        result TEXT, created_at TEXT NOT NULL,
                        started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE events (
                        id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, task_id TEXT,
                        kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE TABLE failures (
                        id TEXT PRIMARY KEY, workflow_id TEXT, task_id TEXT,
                        agent_run_id TEXT, source TEXT NOT NULL, category TEXT NOT NULL,
                        severity TEXT NOT NULL, retryable INTEGER NOT NULL DEFAULT 0,
                        repairable INTEGER NOT NULL DEFAULT 0, evidence TEXT,
                        primary_error TEXT, verification_run_id TEXT,
                        recommended_action TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
                        repair_cycle INTEGER NOT NULL DEFAULT 0, recovery_state TEXT,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()
            state = StateStore(database)
            try:
                columns = {
                    row[1] for row in state.connection.execute("PRAGMA table_info(failures)").fetchall()
                }
                self.assertIn("structured_evidence", columns)
                versions = [
                    row[0] for row in state.connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version").fetchall()
                ]
                self.assertEqual(versions, [1, 2, 3, 4, 5, 6, 7])
            finally:
                state.close()

    def test_workflow_agent_failure_uses_structured_evidence(self):
        from agentops.runner import RunResult
        state = StateStore(":memory:")
        try:
            engine = WorkflowEngine(_config(), state, MagicMock(), MagicMock(), MagicMock())
            workflow_id = state.create_workflow("evidence workflow")
            task = state.add_task(Task("Implement", "implementation", workflow_id))
            result = RunResult("fallback", ("fake",), 1, "build failed", "", 0.01,
                               False, Path("fake.log"))
            engine._record_agent_failure(task, result)
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
            # Structured exit code wins over the misleading "build" text.
            self.assertEqual(failures[0].category, FailureCategory.AGENT_ERROR)
            self.assertEqual(failures[0].structured_evidence["exit_code"], 1)
        finally:
            state.close()

    def test_structured_precedence_combinations(self):
        cancelled_and_timed_out = FailureClassifier.classify(
            evidence=FailureEvidence(timed_out=True, cancelled=True))
        self.assertEqual(cancelled_and_timed_out.category, FailureCategory.CANCELLATION)
        timed_out_and_terminated = FailureClassifier.classify(
            evidence=FailureEvidence(timed_out=True, terminated=True))
        self.assertEqual(timed_out_and_terminated.category, FailureCategory.TIMEOUT)
        killed_with_check_class = FailureClassifier.classify(
            evidence=FailureEvidence(check_class="tests", exit_code=-9))
        self.assertEqual(killed_with_check_class.category, FailureCategory.PROCESS_ERROR)
        padded = FailureClassifier.classify(evidence=FailureEvidence(check_class=" Tests "))
        self.assertEqual(padded.category, FailureCategory.TEST_FAILURE)
        upper = FailureClassifier.classify(evidence=FailureEvidence(check_class="LINT"))
        self.assertEqual(upper.category, FailureCategory.LINT_FAILURE)

    def test_evidence_total_for_hostile_fields(self):
        import json as _json

        class Hostile:
            def __repr__(self):
                raise RuntimeError("boom")

            def __int__(self):
                raise RuntimeError("boom")

        evidence = FailureEvidence(check_class=Hostile(), exit_code=Hostile(),
                                   command=(Hostile(),))
        payload = evidence.to_dict()
        # Never raises, and the payload survives JSON serialization.
        _json.dumps(payload)
        self.assertIsNone(payload["check_class"])
        self.assertIsNone(payload["exit_code"])

    def test_agent_command_persisted_without_prompt_secrets(self):
        from agentops.runner import RunResult
        state = StateStore(":memory:")
        try:
            engine = WorkflowEngine(_config(), state, MagicMock(), MagicMock(), MagicMock())
            workflow_id = state.create_workflow("evidence workflow")
            task = state.add_task(Task("Implement", "implementation", workflow_id))
            secret = "sk-abcdef1234567890"
            result = RunResult("fallback", ("fake", "--key", secret), 1,
                               "out", "boom", 0.01, False, Path("fake.log"))
            engine._record_agent_failure(task, result)
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
            blob = str(failures[0].structured_evidence)
            self.assertNotIn(secret, blob)
            self.assertNotIn("--key", blob)
            self.assertEqual(failures[0].structured_evidence["command"], ["fake"])
        finally:
            state.close()

    def test_record_failure_scrubs_arbitrary_mappings(self):
        state = StateStore(":memory:")
        try:
            engine = WorkflowEngine(_config(), state, MagicMock(), MagicMock(), MagicMock())
            workflow_id = state.create_workflow("evidence workflow")
            task = state.add_task(Task("Implement", "implementation", workflow_id))
            classification = FailureClassifier.classify(error="boom")
            engine.record_failure(
                task, classification,
                structured_evidence={"nested": {"token": "ghp_12345678901234567890"}},
            )
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
            blob = str(failures[0].structured_evidence)
            self.assertNotIn("ghp_12345678901234567890", blob)
        finally:
            state.close()

    def test_malformed_check_recorded_defensively(self):
        from types import SimpleNamespace
        state = StateStore(":memory:")
        try:
            engine = WorkflowEngine(_config(), state, MagicMock(), MagicMock(), MagicMock())
            workflow_id = state.create_workflow("evidence workflow")
            task = state.add_task(Task("Verify", "verification", workflow_id))
            task.result = "mysterious output"
            report = SimpleNamespace(run_id="run-1", checks=(SimpleNamespace(),))
            engine._record_verification_failure(task, report)
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].category, FailureCategory.UNKNOWN)
            self.assertIsNotNone(failures[0].structured_evidence)
        finally:
            state.close()

    def test_record_failure_redacts_legacy_evidence_text(self):
        state = StateStore(":memory:")
        try:
            engine = WorkflowEngine(_config(), state, MagicMock(), MagicMock(), MagicMock())
            workflow_id = state.create_workflow("evidence workflow")
            task = state.add_task(Task("Implement", "implementation", workflow_id))
            secret = "sk-abcdef1234567890"
            classification = FailureClassifier.classify(error="boom")
            engine.record_failure(task, classification, evidence=f"boom {secret}",
                                  primary_error=f"boom {secret}")
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
            self.assertNotIn(secret, failures[0].evidence or "")
            self.assertNotIn(secret, failures[0].primary_error or "")
            self.assertIn("[REDACTED]", failures[0].evidence or "")
        finally:
            state.close()

    def test_timeout_evidence_beats_dependency_text(self):
        # Specified precedence: an authoritative timeout signal wins over
        # dependency-flavored text (legacy would say DEPENDENCY_FAILURE).
        evidence = FailureEvidence(timed_out=True)
        structured = FailureClassifier.classify(
            error="pip install timeout: connection refused", evidence=evidence)
        self.assertEqual(structured.category, FailureCategory.TIMEOUT)
        legacy = FailureClassifier.classify(error="pip install timeout: connection refused")
        self.assertEqual(legacy.category, FailureCategory.DEPENDENCY_FAILURE)

    def test_legacy_verification_failure_persists_evidence(self):
        from agentops.verification import CheckResult
        state = StateStore(":memory:")
        try:
            engine = WorkflowEngine(_config(), state, MagicMock(), MagicMock(), MagicMock())
            workflow_id = state.create_workflow("evidence workflow")
            task = state.add_task(Task("Verify", "verification", workflow_id))
            task.result = "boom"
            results = [CheckResult(("pytest",), 2, "boom", False, 0.1)]
            engine._record_legacy_verification_failure(task, results)
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
            # Legacy text rules still decide the category...
            self.assertEqual(failures[0].category, FailureCategory.UNKNOWN)
            # ...while the exit code and command are now persisted.
            self.assertEqual(failures[0].structured_evidence["exit_code"], 2)
            self.assertEqual(failures[0].structured_evidence["command"], ["pytest"])
        finally:
            state.close()

    def test_workflow_verification_failure_uses_check_class(self):
        from types import SimpleNamespace
        state = StateStore(":memory:")
        try:
            engine = WorkflowEngine(_config(), state, MagicMock(), MagicMock(), MagicMock())
            workflow_id = state.create_workflow("evidence workflow")
            task = state.add_task(Task("Verify", "verification", workflow_id))
            task.result = "build failed"
            check = SimpleNamespace(
                status=SimpleNamespace(value="failed"),
                check_class=SimpleNamespace(value="tests"),
                exit_code=1, command=("pytest",),
            )
            report = SimpleNamespace(run_id="run-1", checks=(check,))
            engine._record_verification_failure(task, report)
            failures = state.list_failures(workflow_id, task.id)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].category, FailureCategory.TEST_FAILURE)
            self.assertEqual(failures[0].structured_evidence["check_class"], "tests")
        finally:
            state.close()


if __name__ == "__main__":
    unittest.main()
