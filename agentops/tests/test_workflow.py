import asyncio
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

from agentops.agent_run import AgentRunMetadata
from agentops.config import AppConfig, AgentConfig
from agentops.agent_result import AgentResult, AgentResultStatus
from agentops.registry import DetectedAgent
from agentops.runner import OperationCancelled, RunResult
from agentops.state import StateStore
from agentops.tasks import TaskStatus
from agentops.verification_kernel import VerificationKernel
from agentops.verification_model import (
    VerificationCheckClass, VerificationCheckSpec, VerificationExecutionPolicy,
    VerificationProfile, VerificationProfileMode,
)
from agentops.workflow import WorkflowEngine


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            {"fallback": AgentConfig("fallback", "fake", ("{prompt}",), ("architecture", "implementation", "review", "debugging"))},
            {role: ("fallback",) for role in ("architecture", "implementation", "review", "debugging")},
            (("test",),), max_attempts=2, concurrency=2,
        )
        self.state = StateStore(":memory:")
        self.registry = MagicMock()
        self.registry.select.return_value = DetectedAgent(self.config.agents["fallback"], True, "fake")
        self.runner = MagicMock()
        self.runner.run_agent = self._run_agent
        self.verifier = MagicMock()
        # These tests assert on WORKFLOW behaviour, not on git. The real
        # collector reads the working tree, so on a clean checkout every one of
        # these tests failed: no dirty files -> no evidence -> implementation
        # blocked -> never READY. They passed only because the developer
        # happened to have uncommitted files lying around, which is exactly
        # what CI does not have.
        #
        # Evidence is therefore declared here, explicitly, instead of being
        # borrowed from whatever state the repository happens to be in.
        self.metadata_collector = MagicMock(
            return_value=AgentRunMetadata(files_changed=("src/app.py",)))

    async def _run_agent(self, agent, prompt, directory, task_id, cancel_event=None):
        return RunResult(agent.config.name, ("fake",), 0, "done", "", 0.01, False, Path(f"{task_id}.log"))

    def tearDown(self):
        self.state.close()

    def test_standard_workflow_runs_to_ready(self):
        passed_check = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[passed_check]))
        engine = WorkflowEngine(self.config, self.state, self.registry, self.runner, self.verifier,
                              metadata_collector=self.metadata_collector)
        result = asyncio.run(engine.run_high_level("Add dark mode", Path.cwd()))
        self.assertTrue(result.ready)
        self.assertEqual(result.summary, "READY")
        self.assertEqual(len(self.state.list_tasks(result.workflow_id)), 4)

    def test_failed_verification_creates_repair_flow(self):
        failed_check = MagicMock(succeeded=False, output="one test failed")
        passed_check = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(side_effect=[asyncio.sleep(0, result=[failed_check]), asyncio.sleep(0, result=[passed_check])])
        engine = WorkflowEngine(self.config, self.state, self.registry, self.runner, self.verifier,
                              metadata_collector=self.metadata_collector)
        result = asyncio.run(engine.run_high_level("Fix issue", Path.cwd()))
        self.assertTrue(result.ready)
        self.assertEqual(len(self.state.list_tasks(result.workflow_id)), 7)

    def test_custom_workflow_accepts_parallel_tasks(self):
        passed_check = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[passed_check]))
        engine = WorkflowEngine(self.config, self.state, self.registry, self.runner, self.verifier,
                              metadata_collector=self.metadata_collector)
        workflow_id, tasks = engine.create_workflow("custom", [
            {"id": "a", "description": "first", "role": "implementation"},
            {"id": "b", "description": "second", "role": "implementation"},
            {"id": "verify", "description": "check", "role": "verification", "dependencies": ["a", "b"]},
        ])
        asyncio.run(engine.execute(workflow_id, Path.cwd()))
        self.assertTrue(all(self.state.get_task(task.id).status.value == "passed" for task in tasks))


class ImplementationEvidenceTests(WorkflowTests):
    """exit 0 is a CLAIM of success, not evidence of it.

    Live defect (2026-10-05): a constrained opencode task returned exit_code=0 and
    `implementation` was recorded PASSED while the worktree held no MERGED.md --
    the delegate did nothing at all.

    `files_changed` is collected onto the persisted AgentRunOutcome, not onto
    RunResult, so these tests persist a run row the way the real runner does.
    """

    def _engine_with(self, files_changed, config=None, real_collector=False):
        # The workflow only enforces the evidence contract inside a git working
        # tree, because that is all GitRunMetadataCollector can observe. Case 1
        # therefore needs a REAL repo with nothing changed in it; cases 2 and 3
        # declare their evidence through an injected collector.
        self._git_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._git_dir, ignore_errors=True)
        env_dir = Path(self._git_dir)
        (env_dir / ".keep").write_text("", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=env_dir, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=env_dir, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=env_dir, check=True)
        subprocess.run(["git", "add", "-A"], cwd=env_dir, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=env_dir, check=True)
        collector = MagicMock(return_value=AgentRunMetadata(files_changed=files_changed))
        self._collector = collector

        engine_cfg = config or self.config
        # The workflow measures the working tree with its metadata collector.
        # Tests declare the evidence explicitly rather than depending on what
        # happens to be in the real cwd.
        collector = MagicMock(return_value=AgentRunMetadata(files_changed=files_changed))
        engine = WorkflowEngine(engine_cfg, self.state, self.registry,
                                self.runner, self.verifier,
                                metadata_collector=collector)
        original = self.runner.run_agent

        async def _run(agent, prompt, directory, task_id, cancel_event=None):
            return await original(agent, prompt, directory, task_id,
                                  cancel_event=cancel_event)

        self.runner.run_agent = _run
        return engine

    def _implementation(self, workflow_id):
        tasks = [t for t in self.state.list_tasks(workflow_id) if t.role == "implementation"]
        self.assertTrue(tasks, "no implementation task recorded")
        return tasks[0]

    def test_success_with_no_worktree_change_is_not_passed(self):
        # Case 1: the exact live failure. Agent claimed success, wrote nothing.
        engine = self._engine_with(files_changed=(), real_collector=True)
        result = asyncio.run(engine.run_high_level("Create PROOF.md", Path(self._git_dir)))
        task = self._implementation(result.workflow_id)
        self.assertNotEqual(
            task.status, TaskStatus.PASSED,
            "exit 0 with an empty worktree must not be a PASSED implementation")
        self.assertIn("no_evidence", task.result)

    def test_success_with_worktree_change_is_passed(self):
        # Case 2: the legitimate case must keep working.
        engine = self._engine_with(files_changed=("proof.md",))
        result = asyncio.run(engine.run_high_level("Create PROOF.md", Path(self._git_dir)))
        self.assertEqual(self._implementation(result.workflow_id).status, TaskStatus.PASSED)

    def test_research_task_with_no_files_is_allowed_to_pass(self):
        # Case 3: "investigate why X fails and report" legitimately changes no
        # files. A blanket "no changes = failure" rule would be wrong, so the
        # requirement must be scoped by role.
        config = AppConfig(
            {"fallback": AgentConfig("fallback", "fake", ("{prompt}",),
                                     ("architecture", "research", "verification"))},
            {role: ("fallback",) for role in ("architecture", "research", "verification")},
            (("test",),), max_attempts=2, concurrency=2,
        )
        engine = self._engine_with(files_changed=(), config=config)
        workflow_id, tasks = engine.create_workflow("research", [
            {"id": "investigate", "description": "Investigate the failure",
             "role": "research"},
        ])
        asyncio.run(engine.execute(workflow_id, Path.cwd()))
        research_task = next(t for t in tasks if t.role == "research")
        self.assertEqual(
            self.state.get_task(research_task.id).status, TaskStatus.PASSED,
            "a research task with no file changes must still be able to PASS")


if __name__ == "__main__":
    unittest.main()


class UnverifiedMustNotInventRepairTests(WorkflowTests):
    """verification=BLOCKED must stay BLOCKED all the way down.

    UNVERIFIED was added so "no applicable evidence" stops reading as "a check
    failed". The report and the task now preserve that distinction -- and then
    `run_high_level` collapsed it again: any non-PASSED verification with a
    passed implementation fell into the repair loop and invented
    "Repair the configured verification failure." for a failure that was never
    demonstrated. Observed live: implementation=passed, verification=blocked,
    debugging=passed with that repair description.
    """

    def _engine_unverifiable(self):
        """A kernel whose only required check is inapplicable here (no tests/),
        which is the UNVERIFIED -> BLOCKED path."""
        profile = VerificationProfile(
            name="standard", mode=VerificationProfileMode.FAIL_FAST,
            concurrency=1, default_timeout_seconds=30,
            checks=(VerificationCheckSpec(
                "unit", VerificationCheckClass.TESTS,
                ("python", "-m", "unittest", "discover", "-s", "tests"),
                required=True, policy=VerificationExecutionPolicy.SEQUENTIAL),),
        )
        kernel = VerificationKernel(profiles={"standard": profile},
                                    default_profile="standard", state=self.state)
        # The repository already has a committed file, so implementation passes
        # the evidence contract; otherwise it fails for an unrelated reason and
        # the repair branch is skipped, making the test vacuous.
        collector = MagicMock(return_value=AgentRunMetadata(files_changed=("base.txt",)))
        return WorkflowEngine(self.config, self.state, self.registry,
                              self.runner, self.verifier,
                              verification_kernel=kernel,
                              metadata_collector=collector)

    def _git_repo_with_work(self):
        import subprocess as sp
        import tempfile as tf
        directory = tf.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t.local"],
                    ["git", "config", "user.name", "T"]):
            sp.run(cmd, cwd=directory, check=True)
        Path(directory, "base.txt").write_text("base", encoding="utf-8")
        sp.run(["git", "add", "-A"], cwd=directory, check=True)
        sp.run(["git", "commit", "-qm", "base"], cwd=directory, check=True)
        return Path(directory)

    def test_unverified_verification_does_not_create_a_repair_task(self):
        engine = self._engine_unverifiable()
        directory = self._git_repo_with_work()
        result = asyncio.run(engine.run_high_level("Do the work", directory))
        tasks = self.state.list_tasks(result.workflow_id)
        descriptions = [t.description for t in tasks]
        self.assertFalse(
            any("Repair the configured verification failure" in d for d in descriptions),
            "UNVERIFIED/BLOCKED verification must not invent a repair task for a "
            "failure no check demonstrated")
        self.assertFalse(result.ready,
                         "an unverified workflow must never be READY")

    def test_blocked_verification_is_not_collapsed_into_failed(self):
        engine = self._engine_unverifiable()
        directory = self._git_repo_with_work()
        result = asyncio.run(engine.run_high_level("Do the work", directory))
        statuses = {t.role: t.status for t in self.state.list_tasks(result.workflow_id)}
        self.assertIs(statuses["implementation"], TaskStatus.PASSED)
        self.assertIs(statuses["verification"], TaskStatus.BLOCKED,
                      "UNVERIFIED must reach the task as BLOCKED, not FAILED")


if __name__ == "__main__":
    unittest.main()


class CancelledAndTerminatedAreNotSuccessTests(WorkflowTests):
    """A run that was killed or cancelled is not a successful run.

    `RunResult.succeeded` was `not timed_out and exit_code == 0`, which ignores
    the `cancelled` and `terminated` flags sitting right beside it on the same
    dataclass. `workflow.py` read only `succeeded` at the task decision, so a
    terminated agent with exit_code left at 0 carried implementation ->
    verification -> review -> READY.

    Same signature as the files_changed defect: the evidence exists in the
    translation object, and the decision-maker cannot see it. Proven live with
    a stub; the runner does not currently return exit_code=0 together with
    those flags, so this guards the latent path rather than a live one.
    """

    def _engine_returning(self, **result_kwargs):
        async def _run(agent, prompt, directory, task_id, cancel_event=None):
            return RunResult(agent.config.name, ("fake",), result_kwargs["exit_code"],
                             "partial output", "", 0.01, result_kwargs["timed_out"],
                             Path(f"{task_id}.log"),
                             cancelled=result_kwargs.get("cancelled", False),
                             terminated=result_kwargs.get("terminated", False))
        self.runner.run_agent = _run
        collector = MagicMock(return_value=AgentRunMetadata(files_changed=("a.txt",)))
        passed = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[passed]))
        return WorkflowEngine(self.config, self.state, self.registry,
                              self.runner, self.verifier, metadata_collector=collector)

    def _git_repo(self):
        import subprocess as sp
        import tempfile as tf
        directory = tf.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t.local"],
                    ["git", "config", "user.name", "T"]):
            sp.run(cmd, cwd=directory, check=True)
        Path(directory, "base.txt").write_text("base", encoding="utf-8")
        sp.run(["git", "add", "-A"], cwd=directory, check=True)
        sp.run(["git", "commit", "-qm", "base"], cwd=directory, check=True)
        return Path(directory)

    def _assert_not_ready(self, result):
        self.assertFalse(result.ready,
                         "a cancelled/terminated run must never reach READY")
        for task in self.state.list_tasks(result.workflow_id):
            self.assertNotEqual(task.status, TaskStatus.PASSED,
                                f"{task.role} PASSED on a cancelled/terminated run")

    def test_terminated_run_does_not_reach_ready(self):
        engine = self._engine_returning(exit_code=0, timed_out=False, terminated=True)
        self._assert_not_ready(
            asyncio.run(engine.run_high_level("Do it", self._git_repo())))

    def test_cancelled_run_does_not_reach_ready(self):
        engine = self._engine_returning(exit_code=0, timed_out=False, cancelled=True)
        self._assert_not_ready(
            asyncio.run(engine.run_high_level("Do it", self._git_repo())))

    def test_clean_run_still_reaches_ready(self):
        # The guard must not break the ordinary success path.
        engine = self._engine_returning(exit_code=0, timed_out=False)
        result = asyncio.run(engine.run_high_level("Do it", self._git_repo()))
        self.assertTrue(result.ready, result.summary)


class CancelledWorkflowStatusTests(WorkflowTests):
    """Cancelling a workflow must leave a terminal workflow status row.

    Live defect (reproduced 2026-11-10 against a real agent run): `execute()`
    derived the workflow status only on a clean exit of its run loop.
    Cancellation raises OperationCancelled straight out of the loop, so the
    `workflows` row stayed `pending` forever after the user cancelled, while
    every task row was already terminal. `agentops recover` could not repair
    it -- it refreshes only workflows whose tasks it actually recovers, and it
    recovered none -- so the CLI headline and the GUI dashboard kept reporting
    an operation that was no longer running as active. Cancellation is not
    success: the row must end FAILED and the exception must still propagate.
    """

    def _engine(self):
        return WorkflowEngine(self.config, self.state, self.registry,
                              self.runner, self.verifier,
                              metadata_collector=self.metadata_collector)

    def test_cancellation_persists_terminal_workflow_status(self):
        started = threading.Event()

        async def run_agent(agent, prompt, directory, task_id,
                            cancel_event=None, **kwargs):
            # A real agent blocks until cooperative cancellation tears the
            # child down, then OperationCancelled propagates.
            started.set()
            while cancel_event is not None and not cancel_event.is_set():
                await asyncio.sleep(0.005)
            raise OperationCancelled

        self.runner.run_agent = run_agent
        engine = self._engine()
        workflow_id, _ = engine.create_standard_workflow("Cancel me")
        cancel = threading.Event()

        async def scenario():
            execution = asyncio.create_task(
                engine.execute(workflow_id, Path.cwd(), cancel))
            for _ in range(400):  # bounded wait: agent start within 2s
                if started.is_set():
                    break
                await asyncio.sleep(0.005)
            self.assertTrue(started.is_set(), "the plan agent never started")
            cancel.set()
            # Cancellation semantics are unchanged: OperationCancelled still
            # propagates out of execute() to its caller.
            with self.assertRaises(OperationCancelled):
                await execution

        asyncio.run(scenario())

        workflow = self.state.latest_workflow()
        self.assertIsNotNone(workflow)
        self.assertEqual(workflow.id, workflow_id)
        # The cancelled plan task is FAILED, so the derivation must produce a
        # terminal FAILED row -- never a stale `pending` row for an operation
        # that has stopped.
        self.assertEqual(workflow.status, TaskStatus.FAILED)
        plan = next(task for task in self.state.list_tasks(workflow_id)
                    if task.role == "architecture")
        self.assertEqual(plan.status, TaskStatus.FAILED)
        self.assertIn("cancelled", (plan.result or "").lower())

    def test_successful_workflow_status_is_unchanged(self):
        # The refresh on cancellation must not disturb the clean-exit path.
        passed = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[passed]))
        engine = self._engine()
        result = asyncio.run(engine.run_high_level("Do the work", Path.cwd()))
        self.assertTrue(result.ready, result.summary)
        workflow = self.state.latest_workflow()
        self.assertEqual(workflow.id, result.workflow_id)
        self.assertEqual(workflow.status, TaskStatus.PASSED)


class StructuredAgentResultTaskStatusTests(WorkflowTests):
    """exit_code 0 is process success, not agent success.

    The task decision read only ``RunResult.succeeded`` (timeout/cancel/
    terminate/exit-code), so an agent that exited 0 while its structured
    result explicitly reported failure/partial still carried the task to
    PASSED -- and the workflow to READY.
    """

    def _engine_returning(self, exit_code=0, status=None, stdout=None,
                          structured_envelope=True):
        if status is not None:
            payload = AgentResult(
                status=status, summary=f"agent reports {status.value}").to_dict()
            structured = payload if structured_envelope else None
            text = stdout if stdout is not None else json.dumps(payload)
        else:
            structured = None
            text = stdout if stdout is not None else "done"

        async def _run(agent, prompt, directory, task_id, cancel_event=None):
            return RunResult(agent.config.name, ("fake",), exit_code,
                             text, "", 0.01, False, Path(f"{task_id}.log"),
                             structured_result=structured)
        self.runner.run_agent = _run
        collector = MagicMock(return_value=AgentRunMetadata(files_changed=("a.txt",)))
        passed = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[passed]))
        return WorkflowEngine(self.config, self.state, self.registry,
                              self.runner, self.verifier, metadata_collector=collector)

    def _git_repo(self):
        import subprocess as sp
        import tempfile as tf
        directory = tf.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t.local"],
                    ["git", "config", "user.name", "T"]):
            sp.run(cmd, cwd=directory, check=True)
        Path(directory, "base.txt").write_text("base", encoding="utf-8")
        sp.run(["git", "add", "-A"], cwd=directory, check=True)
        sp.run(["git", "commit", "-qm", "base"], cwd=directory, check=True)
        return Path(directory)

    def _statuses(self, workflow_id):
        return {t.role: t.status for t in self.state.list_tasks(workflow_id)}

    def test_exit_zero_structured_success_passes(self):
        engine = self._engine_returning(
            exit_code=0, status=AgentResultStatus.SUCCESS)
        result = asyncio.run(engine.run_high_level("Do it", self._git_repo()))
        self.assertTrue(result.ready, result.summary)
        self.assertIs(
            self._statuses(result.workflow_id)["implementation"], TaskStatus.PASSED)

    def test_exit_zero_structured_failure_never_passes(self):
        engine = self._engine_returning(
            exit_code=0, status=AgentResultStatus.FAILURE)
        result = asyncio.run(engine.run_high_level("Do it", self._git_repo()))
        self.assertFalse(result.ready,
                         "structured FAILURE at exit 0 must never reach READY")
        statuses = self._statuses(result.workflow_id)
        # Architecture runs first with the same failing stub, so it FAILED
        # and implementation is BLOCKED behind it: neither may ever PASS.
        self.assertIs(statuses["architecture"], TaskStatus.FAILED)
        for role, status in statuses.items():
            self.assertIsNot(status, TaskStatus.PASSED,
                             f"{role} PASSED on structured FAILURE")

    def test_exit_zero_structured_partial_never_passes(self):
        engine = self._engine_returning(
            exit_code=0, status=AgentResultStatus.PARTIAL)
        result = asyncio.run(engine.run_high_level("Do it", self._git_repo()))
        self.assertFalse(result.ready,
                         "structured PARTIAL at exit 0 must never reach READY")
        statuses = self._statuses(result.workflow_id)
        self.assertIs(statuses["architecture"], TaskStatus.FAILED)
        for role, status in statuses.items():
            self.assertIsNot(status, TaskStatus.PASSED,
                             f"{role} PASSED on structured PARTIAL")

    def test_nonzero_exit_structured_success_still_fails(self):
        # Structured SUCCESS must not override a failed process.
        engine = self._engine_returning(
            exit_code=1, status=AgentResultStatus.SUCCESS)
        result = asyncio.run(engine.run_high_level("Do it", self._git_repo()))
        self.assertFalse(result.ready)
        self.assertIs(
            self._statuses(result.workflow_id)["architecture"], TaskStatus.FAILED)

    def test_legacy_plaintext_exit_zero_still_passes(self):
        # No structured payload: historical exit-code behaviour is preserved.
        engine = self._engine_returning(exit_code=0, stdout="done")
        result = asyncio.run(engine.run_high_level("Do it", self._git_repo()))
        self.assertTrue(result.ready, result.summary)
        self.assertIs(
            self._statuses(result.workflow_id)["implementation"], TaskStatus.PASSED)

    def test_stdout_json_failure_without_envelope_still_fails(self):
        # Runner doubles that skip the structured envelope are still honoured
        # via the stdout fallback.
        payload = AgentResult(
            status=AgentResultStatus.FAILURE, summary="broke it").to_dict()
        engine = self._engine_returning(
            exit_code=0, status=AgentResultStatus.FAILURE,
            stdout=json.dumps(payload), structured_envelope=False)
        result = asyncio.run(engine.run_high_level("Do it", self._git_repo()))
        self.assertFalse(result.ready)
        self.assertIs(
            self._statuses(result.workflow_id)["architecture"], TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main()