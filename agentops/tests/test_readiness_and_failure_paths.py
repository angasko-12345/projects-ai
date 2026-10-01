"""Regression tests for the readiness gate, the .agentops self-deadlock,
task-result redaction, and verification setup failure handling."""

import asyncio
import inspect
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agentops import cli as cli_module
from agentops.config import AppConfig, AgentConfig
from agentops.execution_model import (
    StateTransitionError,
    assert_tasks_ready,
    assess_workflow_readiness,
)
from agentops.git import GitError, GitWorktreeManager
from agentops.registry import DetectedAgent
from agentops.runner import RunResult
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.verification_kernel import VerificationKernel
from agentops.verification_model import (
    VerificationCheckClass,
    VerificationCheckSpec,
    VerificationProfile,
    VerificationProfileMode,
)
from agentops.workflow import WorkflowEngine

# Fake credential for redaction tests. Never a real secret.
FAKE_SECRET = "sk-FAKE-TEST-SECRET-NOT-REAL"


def _engine(state, *, verifier=None, run_agent=None):
    config = AppConfig(
        {"fallback": AgentConfig("fallback", "fake", ("{prompt}",),
                                 ("architecture", "implementation", "review", "debugging"))},
        {role: ("fallback",) for role in
         ("architecture", "implementation", "review", "debugging")},
        (("test",),),
        max_attempts=1, concurrency=1, max_repair_cycles=0,
    )
    registry = MagicMock()
    registry.select.return_value = DetectedAgent(config.agents["fallback"], True, "fake")
    runner = MagicMock()

    async def default_run(agent, prompt, directory, task_id, cancel_event=None):
        return RunResult(agent.config.name, ("fake",), 0, "done", "", 0.01, False,
                         Path(f"{task_id}.log"))

    runner.run_agent = run_agent or default_run
    return WorkflowEngine(config, state, registry, runner, verifier or MagicMock())


def _passed(role, workflow_id):
    task = Task(f"{role} work", role, workflow_id)
    task.status = TaskStatus.PASSED
    return task


class ReadinessRuleTests(unittest.TestCase):
    """Every READY path must enforce verification + review + evidence."""

    def setUp(self):
        self.state = StateStore(":memory:")

    def tearDown(self):
        self.state.close()

    def _workflow(self, *, verification=True, review=True, evidence=True):
        workflow_id = self.state.create_workflow("readiness")
        self.state.add_task(_passed("implementation", workflow_id))
        verification_task = Task("verify", "verification", workflow_id)
        verification_task.status = TaskStatus.PASSED
        verification_task.verified = verification
        verification_task.verification_run_id = "run-1" if evidence else None
        verification_task.result = "transcript" if evidence else ""
        self.state.add_task(verification_task)
        if review:
            self.state.add_task(_passed("review", workflow_id))
        return workflow_id

    def test_verification_without_review_is_not_ready(self):
        workflow_id = self._workflow(review=False)
        readiness = assess_workflow_readiness(self.state.list_tasks(workflow_id), workflow_id)
        self.assertFalse(readiness.ready)
        self.assertFalse(readiness.review_ok)
        self.assertIn("no passed review task", readiness.reasons)

    def test_review_failure_is_not_ready(self):
        workflow_id = self._workflow()
        review = [t for t in self.state.list_tasks(workflow_id) if t.role == "review"][0]
        review.status = TaskStatus.FAILED
        self.state.update_task(review)
        readiness = assess_workflow_readiness(self.state.list_tasks(workflow_id), workflow_id)
        self.assertFalse(readiness.ready)
        self.assertIn("no passed review task", readiness.reasons)

    def test_unverified_verification_is_not_ready(self):
        workflow_id = self._workflow(verification=False)
        readiness = assess_workflow_readiness(self.state.list_tasks(workflow_id), workflow_id)
        self.assertFalse(readiness.ready)
        self.assertIn("no passed+verified verification task", readiness.reasons)

    def test_missing_evidence_is_not_ready(self):
        workflow_id = self._workflow(evidence=False)
        readiness = assess_workflow_readiness(self.state.list_tasks(workflow_id), workflow_id)
        self.assertFalse(readiness.ready)
        self.assertIn("no verification evidence", readiness.reasons)

    def test_all_conditions_pass_is_ready(self):
        workflow_id = self._workflow()
        readiness = assess_workflow_readiness(self.state.list_tasks(workflow_id), workflow_id)
        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.reasons, ())
        self.assertEqual(readiness.summary(), "READY")

    def test_assert_tasks_ready_names_the_missing_prerequisite(self):
        workflow_id = self._workflow(review=False)
        with self.assertRaises(StateTransitionError) as caught:
            assert_tasks_ready(self.state.list_tasks(workflow_id), workflow_id)
        self.assertIn("no passed review task", str(caught.exception))

    def test_engine_exposes_the_same_rule(self):
        workflow_id = self._workflow(review=False)
        self.assertFalse(_engine(self.state).workflow_readiness(workflow_id).ready)

    def test_custom_dag_without_review_is_not_ready(self):
        """Regression: the CLI declared READY from status+evidence alone."""
        workflow_id = self._workflow(review=False)
        self.assertEqual(self.state.refresh_workflow_status(workflow_id), TaskStatus.PASSED)
        self.assertFalse(_engine(self.state).workflow_readiness(workflow_id).ready)

    def test_cli_no_longer_computes_its_own_readiness(self):
        source = inspect.getsource(cli_module)
        self.assertIn("workflow_readiness", source)
        self.assertNotIn("refresh_workflow_status(workflow_id)", source)

    def test_standard_flow_uses_the_shared_rule(self):
        import agentops.workflow as workflow_module
        source = inspect.getsource(workflow_module)
        self.assertIn("assert_tasks_ready", source)
        self.assertIn("assess_workflow_readiness", source)


class FreshRepositoryTests(unittest.TestCase):
    """A repo with no AgentOps ignore rules must still reach its own merge."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="agentops-fresh-"))
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "test")
        (self.root / "README.md").write_text("seed", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "init")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)

    def test_merge_succeeds_on_a_fresh_repository(self):
        self.assertFalse((self.root / ".gitignore").exists())
        manager = GitWorktreeManager()
        worktree = manager.create(self.root, "fresh repo task")
        self.assertEqual(self._git("status", "--porcelain").stdout.strip(), "")
        (worktree.path / "feature.txt").write_text("work", encoding="utf-8")
        manager.commit_changes(worktree, "agentops: fresh")
        manager.merge(worktree)
        self.assertTrue((self.root / "feature.txt").exists())

    def test_genuine_user_changes_still_block_merge(self):
        manager = GitWorktreeManager()
        worktree = manager.create(self.root, "dirty base task")
        (self.root / "user_edit.py").write_text("mine", encoding="utf-8")
        (worktree.path / "feature.txt").write_text("work", encoding="utf-8")
        manager.commit_changes(worktree, "agentops: work")
        with self.assertRaises(GitError):
            manager.merge(worktree)

    def test_existing_exclude_content_is_preserved(self):
        exclude = self.root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("*.log\n", encoding="utf-8")
        GitWorktreeManager().create(self.root, "preserve exclude")
        content = exclude.read_text(encoding="utf-8")
        self.assertIn("*.log", content)
        self.assertIn("/.agentops/", content)



class TaskResultRedactionTests(unittest.TestCase):
    """Raw agent output must not reach persistent state."""

    def setUp(self):
        self.state = StateStore(":memory:")

    def tearDown(self):
        self.state.close()

    def _run(self, stdout, stderr):
        async def run_agent(agent, prompt, directory, task_id, cancel_event=None):
            return RunResult(agent.config.name, ("fake",), 0, stdout, stderr, 0.01,
                             False, Path(f"{task_id}.log"))

        engine = _engine(self.state, run_agent=run_agent)
        workflow_id = self.state.create_workflow("redaction")
        task_id = self.state.add_task(
            Task("implement", "implementation", workflow_id)).id
        asyncio.run(engine.execute(workflow_id, tempfile.gettempdir()))
        return self.state.get_task(task_id).result

    def test_secret_in_stdout_is_redacted(self):
        self.assertNotIn(FAKE_SECRET, self._run(f"deploying {FAKE_SECRET}", ""))

    def test_secret_in_stderr_is_redacted(self):
        self.assertNotIn(FAKE_SECRET, self._run("", f"auth failed {FAKE_SECRET}"))

    def test_secret_in_mixed_output_is_redacted(self):
        result = self._run(f"token={FAKE_SECRET}", f"retry {FAKE_SECRET}")
        self.assertNotIn(FAKE_SECRET, result)

    def test_log_path_and_diagnostics_are_kept(self):
        result = self._run(f"built ok {FAKE_SECRET}", "")
        self.assertIn("log=", result)
        self.assertIn("[REDACTED]", result)

    def test_no_secret_reaches_any_persisted_task_row(self):
        self._run(f"value {FAKE_SECRET}", f"other {FAKE_SECRET}")
        rows = self.state.connection.execute(
            "SELECT result FROM tasks WHERE result IS NOT NULL").fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertNotIn(FAKE_SECRET, row["result"])

    def test_secret_in_legacy_verification_output_is_redacted(self):
        passed_check = MagicMock(succeeded=True, output=f"tests ok {FAKE_SECRET}")
        verifier = MagicMock()
        verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[passed_check]))
        engine = _engine(self.state, verifier=verifier)

class VerificationSetupFailureTests(unittest.TestCase):
    """A partial verification setup must not leave the run RUNNING."""

    def setUp(self):
        self.state = StateStore(":memory:")
        self.workflow_id = self.state.create_workflow("setup")
        self.task_id = self.state.add_task(
            Task("verify", "verification", self.workflow_id)).id
        self.profile = VerificationProfile(
            name="p", mode=VerificationProfileMode.CONTINUE_ON_FAILURE,
            checks=tuple(
                VerificationCheckSpec(name=f"c{i}", check_class=VerificationCheckClass.CUSTOM,
                                      command=("echo", "x"), required=True)
                for i in range(3)
            ),
        )

    def tearDown(self):
        self.state.close()

    def _run_and_expect_failure(self):
        real = self.state.create_verification_check
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("check row could not be written")
            return real(*args, **kwargs)

        self.state.create_verification_check = flaky
        kernel = VerificationKernel(profiles={"p": self.profile}, default_profile="p",
                                   state=self.state)
        with self.assertRaises(RuntimeError) as caught:
            asyncio.run(kernel.run_verification(
                self.workflow_id, self.task_id, tempfile.gettempdir()))
        return str(caught.exception)

    def test_check_creation_failure_leaves_a_terminal_run(self):
        self._run_and_expect_failure()
        runs = self.state.list_verification_runs(self.workflow_id)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status.value, "failed")

    def test_created_checks_are_closed_out_not_left_pending(self):
        self._run_and_expect_failure()
        run = self.state.list_verification_runs(self.workflow_id)[0]
        statuses = {c.status.value for c in self.state.list_verification_checks(run.id)}
        self.assertNotIn("pending", statuses)

    def test_original_error_is_not_masked_by_the_closeout(self):
        self.assertIn("check row could not be written", self._run_and_expect_failure())


class ScopedRecoveryTests(unittest.TestCase):
    """Workflow-scoped recovery must not reach into other workflows."""

    def test_controller_scopes_every_recovery_pass(self):
        import agentops.gui_controller as controller_module

        calls = {}

        class FakeState:
            def recover_all(self):
                calls["all"] = True
                return {}

            def recover_agent_runs(self, workflow_id=None):
                calls["runs"] = workflow_id
                return []

            def recover_verification_runs(self, workflow_id=None):
                calls["verification"] = workflow_id
                return []

            def recover_tasks(self, workflow_id=None):
                calls["tasks"] = workflow_id
                return []

            def close(self):
                pass

        controller = controller_module.AgentOpsController.__new__(
            controller_module.AgentOpsController)
        controller._operation_root = lambda directory: directory
        controller._state_path = lambda root: root / "state.sqlite"
        stored = controller_module.StateStore
        controller_module.StateStore = lambda path: FakeState()
        try:
            controller.recover_interrupted(Path(tempfile.gettempdir()), "wf-scoped")
        finally:
            controller_module.StateStore = stored
        self.assertEqual(calls.get("runs"), "wf-scoped")
        self.assertEqual(calls.get("verification"), "wf-scoped")
        self.assertEqual(calls.get("tasks"), "wf-scoped")

    def test_engine_recovery_scopes_its_delegated_passes(self):
        source = inspect.getsource(WorkflowEngine.recover_incomplete)
        self.assertNotIn("recover_agent_runs()", source)
        self.assertNotIn("recover_verification_runs()", source)