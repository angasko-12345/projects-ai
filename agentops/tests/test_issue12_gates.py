"""Behavioral gates for Issue #12: strict review verdicts and merge safety.

Every test drives the real WorkflowEngine with a fake agent runner: the
verdict contract, structured-status handling, security gate, and bounded
repair loop are production code paths, not mocks.
"""

import asyncio
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentops.agent_run import AgentRunMetadata
from agentops.config import AppConfig, AgentConfig, load_config
from agentops.events import EventType
from agentops.execution_model import assess_workflow_readiness
from agentops.registry import DetectedAgent
from agentops.runner import RunResult, _safe_structured_result
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.workflow import (
    WorkflowEngine,
    _explicit_success_denied,
    _local_security_scan,
    _role_scope_text,
    _structured_agent_status,
    verdict_summary_for_task,
)
from agentops.agent_result import AgentResultStatus


APPROVE = (
    '{"schema_version":1,"verdict":"approve","blocking":[],"non_blocking":[],"confidence":0.9}'
)
REJECT = (
    '{"schema_version":1,"verdict":"reject","blocking":["unsafe input"],'
    '"non_blocking":[],"confidence":0.99}'
)
APPROVE_BLOCKING = (
    '{"schema_version":1,"verdict":"approve","blocking":["missing test"],'
    '"non_blocking":[],"confidence":0.9}'
)

_ALL_ROLES = ("architecture", "implementation", "review", "debugging", "security", "verification")


def _engine(security_review=False, max_review_rounds=2, max_attempts=1):
    config = AppConfig(
        {"fallback": AgentConfig("fallback", "fake", ("{prompt}",), _ALL_ROLES)},
        {role: ("fallback",) for role in _ALL_ROLES},
        (("test",),),
        max_attempts=max_attempts,
        concurrency=1,
        max_repair_cycles=1,
        security_review=security_review,
        max_review_rounds=max_review_rounds,
    )
    state = StateStore(":memory:")
    registry = MagicMock()
    registry.select.return_value = DetectedAgent(config.agents["fallback"], True, "fake")
    runner = MagicMock()
    verifier = MagicMock()
    return config, state, registry, runner, verifier


def _scratch_repo(testcase):
    """Throwaway git repo for one test, removed on cleanup.

    The engine inspects and may write to its working directory, so these
    tests must never point it at the developer's own checkout.
    """
    directory = Path(tempfile.mkdtemp(prefix="agentops-gate-"))
    testcase.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "gate@agentops.invalid"],
        ["git", "config", "user.name", "Gate Test"],
    ):
        subprocess.run(command, cwd=directory, check=True, env=env)
    (directory / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=directory, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=directory, check=True, env=env)
    return directory


def _ok_verifier():
    verifier = MagicMock()

    def _run(*args, **kwargs):
        return asyncio.sleep(0, result=[SimpleNamespace(succeeded=True, output="ok")])

    verifier.run = MagicMock(side_effect=_run)
    return verifier


def _evidence_collector():
    return MagicMock(return_value=AgentRunMetadata(files_changed=("src/app.py",)))


def _dispatch_runner(state, outputs_by_role):
    """Fake run_agent answering per task role; string or list per role."""
    cursors = {role: 0 for role in outputs_by_role}

    async def _run(agent, prompt, directory, task_id, cancel_event=None, **kwargs):
        role = state.get_task(task_id).role
        output = outputs_by_role.get(role, "done")
        if isinstance(output, list):
            output = output[min(cursors[role], len(output) - 1)]
            cursors[role] += 1
        if isinstance(output, tuple):
            stdout, structured = output
        else:
            stdout, structured = output, None
        return RunResult(
            agent.config.name,
            ("fake",),
            0,
            stdout,
            "",
            0.01,
            False,
            Path(f"{task_id}.log"),
            structured_result=structured,
        )

    return _run


class StructuredStatusTests(unittest.TestCase):
    def _result(self, stdout, structured=None):
        return SimpleNamespace(stdout=stdout, structured_result=structured, succeeded=True)

    def test_verdict_only_output_is_not_an_agent_status(self):
        status = _structured_agent_status(self._result(APPROVE))
        self.assertIsNone(status)

    def test_real_running_envelope_verdict_only_output_is_not_an_agent_status(self):
        """Regression (2026-10-18, live acceptance round 2): the real runner
        attaches ``_safe_structured_result(stdout)`` as the envelope. For
        verdict-only review output the envelope parses the verdict as an
        UNKNOWN agent status, and ``_structured_agent_status`` returned that
        UNKNOWN, so a compliant reviewer (exactly what the review prompt
        demands: prose + final verdict line) FAILED the status gate. The
        verdict-aware filter that the stdout-candidate path applies must also
        hold when the only structured JSON came through the envelope path.
        Live: workflow b901b8ac reviews d8dd91b3/c567aa07/e0d2e455."""
        envelope = _safe_structured_result(APPROVE)
        gate = ReviewGateTests()
        task = gate._run_review_task(APPROVE, structured=envelope)
        self.assertEqual(task.status, TaskStatus.PASSED)

    def test_explicit_unknown_is_not_success(self):
        status = _structured_agent_status(
            self._result('{"schema_version":1,"status":"unknown","summary":"shrug"}')
        )
        self.assertEqual(status, AgentResultStatus.UNKNOWN)
        self.assertNotIn(status, (None, AgentResultStatus.SUCCESS))

    def test_explicit_failure_is_not_success(self):
        status = _structured_agent_status(
            self._result('{"schema_version":1,"status":"failure","summary":"broke"}')
        )
        self.assertEqual(status, AgentResultStatus.FAILURE)

    def test_explicit_success_false_is_denied(self):
        self.assertTrue(_explicit_success_denied(self._result('{"success": false}')))
        self.assertFalse(_explicit_success_denied(self._result("done")))
        self.assertFalse(_explicit_success_denied(self._result(APPROVE)))

    def test_success_false_inside_legacy_payload_is_denied(self):
        structured = {
            "schema_version": 1,
            "status": "success",
            "metadata": {"legacy_payload": {"success": False}},
        }
        self.assertTrue(_explicit_success_denied(self._result("done", structured)))

    def test_parse_verdict_reexported_in_agent_result(self):
        import agentops.agent_result as agent_result
        import agentops.verdict as verdict

        self.assertIs(agent_result.parse_verdict, verdict.parse_verdict)
        self.assertIs(agent_result.Verdict, verdict.Verdict)


class ReviewGateTests(unittest.TestCase):
    def _run_review_task(self, stdout, role="review", structured=None):
        config, state, registry, runner, verifier = _engine()
        runner.run_agent = _dispatch_runner(state, {role: [(stdout, structured)]})
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            workflow_id = state.create_workflow("gate test")
            task = state.add_task(Task("Review the change", role, workflow_id, max_attempts=1))
            asyncio.run(engine.execute(workflow_id, _scratch_repo(self)))
            return state.get_task(task.id)
        finally:
            state.close()

    def test_reject_with_exit_zero_fails_and_stays_not_ready(self):
        task = self._run_review_task(REJECT)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("unsafe input", task.result or "")
        readiness = assess_workflow_readiness([task], task.workflow_id, review_task_id=task.id)
        self.assertFalse(readiness.ready)

    def test_missing_verdict_is_not_approved(self):
        task = self._run_review_task("Looks good to me, approved!")
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("verdict", (task.result or "").lower())

    def test_malformed_verdict_is_not_approved(self):
        bad = (
            '{"schema_version":1,"verdict":"approve",'
            '"blocking":"everything","non_blocking":[],"confidence":0.9}'
        )
        task = self._run_review_task(bad)
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_truncated_verdict_is_not_approved(self):
        task = self._run_review_task('{"schema_version": 1, "verdict": "approve"')
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_success_false_is_not_approved(self):
        task = self._run_review_task('{"success": false}')
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_approve_with_empty_blocking_and_exit_zero_passes(self):
        task = self._run_review_task(APPROVE)
        self.assertEqual(task.status, TaskStatus.PASSED)

    def test_approve_with_blocking_is_not_approved(self):
        task = self._run_review_task(APPROVE_BLOCKING)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("missing test", task.result or "")

    def test_structured_failure_for_implementation_is_not_success(self):
        config, state, registry, runner, verifier = _engine()
        runner.run_agent = _dispatch_runner(
            state,
            {
                "implementation": [
                    ('{"schema_version":1,"status":"failure","summary":"broke"}', None)
                ],
            },
        )
        try:
            engine = WorkflowEngine(
                config, state, registry, runner, verifier, metadata_collector=_evidence_collector()
            )
            workflow_id = state.create_workflow("structured failure")
            task = state.add_task(
                Task("Do the thing", "implementation", workflow_id, max_attempts=1)
            )
            asyncio.run(engine.execute(workflow_id, _scratch_repo(self)))
            self.assertEqual(state.get_task(task.id).status, TaskStatus.FAILED)
        finally:
            state.close()

    def test_structured_unknown_for_implementation_is_not_success(self):
        config, state, registry, runner, verifier = _engine()
        runner.run_agent = _dispatch_runner(
            state,
            {
                "implementation": [('{"schema_version":1,"status":"unknown"}', None)],
            },
        )
        try:
            engine = WorkflowEngine(
                config, state, registry, runner, verifier, metadata_collector=_evidence_collector()
            )
            workflow_id = state.create_workflow("structured unknown")
            task = state.add_task(
                Task("Do the thing", "implementation", workflow_id, max_attempts=1)
            )
            asyncio.run(engine.execute(workflow_id, _scratch_repo(self)))
            self.assertEqual(state.get_task(task.id).status, TaskStatus.FAILED)
        finally:
            state.close()

    def test_review_verdict_event_is_persisted(self):
        config, state, registry, runner, verifier = _engine()
        runner.run_agent = _dispatch_runner(state, {"review": [REJECT]})
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            workflow_id = state.create_workflow("verdict event")
            state.add_task(Task("Review the change", "review", workflow_id, max_attempts=1))
            asyncio.run(engine.execute(workflow_id, _scratch_repo(self)))
            kinds = [event["kind"] for event in state.list_events(workflow_id)]
            self.assertIn(EventType.REVIEW_COMPLETED.value, kinds)
        finally:
            state.close()


class PromptContractTests(unittest.TestCase):
    def _engine(self):
        config, state, registry, runner, verifier = _engine()
        engine = WorkflowEngine(config, state, registry, runner, verifier)
        return engine

    def test_nonreview_prompt_requires_structured_agent_result(self):
        """Regression (2026-10-18, live acceptance round 2): the non-review
        prompt told agents the AgentResult JSON was optional and that plain
        text remains valid, while the success gate fails any run whose
        structured status is UNKNOWN. Agents obeying the prompt were
        guaranteed to fail. The prompt must demand the structured object the
        gate enforces. Live: workflow 5b55fbd3 implementation 2026-10-17."""
        engine = self._engine()
        state = engine.state
        workflow_id = state.create_workflow("prompt contract")
        task = state.add_task(Task("Do the thing", "implementation", workflow_id, max_attempts=1))
        try:
            prompt = engine._prompt(task, _scratch_repo(self))
        finally:
            state.close()
        self.assertIn('"schema_version": 1', prompt)
        self.assertIn('"status"', prompt)
        self.assertIn("MUST", prompt)
        self.assertNotIn("plain-text summaries remain valid", prompt)


class RepairRoundTests(unittest.TestCase):
    def test_rejection_then_approval_within_rounds_is_ready(self):
        config, state, registry, runner, verifier = _engine(max_review_rounds=2)
        runner.run_agent = _dispatch_runner(
            state,
            {
                "review": [REJECT, APPROVE],
            },
        )
        try:
            engine = WorkflowEngine(
                config,
                state,
                registry,
                runner,
                _ok_verifier(),
                metadata_collector=_evidence_collector(),
            )
            result = asyncio.run(engine.run_high_level("Fix the thing", _scratch_repo(self)))
            self.assertTrue(result.ready, result.summary)
            self.assertIn("READY", result.summary)
        finally:
            state.close()

    def test_repeated_rejection_beyond_budget_is_blocked(self):
        config, state, registry, runner, verifier = _engine(max_review_rounds=1)
        runner.run_agent = _dispatch_runner(
            state,
            {
                "review": [REJECT, REJECT, REJECT, REJECT],
            },
        )
        try:
            engine = WorkflowEngine(
                config,
                state,
                registry,
                runner,
                _ok_verifier(),
                metadata_collector=_evidence_collector(),
            )
            result = asyncio.run(engine.run_high_level("Fix the thing", _scratch_repo(self)))
            self.assertFalse(result.ready)
            self.assertIn("BLOCKED", result.summary)
            self.assertIn("unsafe input", result.summary)
            tasks = state.list_tasks(result.workflow_id)
            self.assertEqual(len(tasks), 4 + 3)
            readiness = assess_workflow_readiness(tasks, result.workflow_id)
            self.assertFalse(readiness.ready)
        finally:
            state.close()


class RejectionIsNotReRolledTests(unittest.TestCase):
    """A well-formed REJECT must be repaired, never re-rolled.

    Live defect (found 2026-10-25): every task FAILED with attempts left is
    retried, and a review REJECT marks the review FAILED. With the shipped
    default ``runtime.max_attempts: 2`` the same reviewer was simply asked
    again about the same unchanged code, so a stochastic reviewer that
    rejected once and approved on the second roll reached READY with no
    repair -- the rejection never reached ``_repair_failed_review``. The gate
    tests above all used ``max_attempts=1`` and could not see it.
    """

    def _single_task(self, role, outputs, max_attempts=2):
        config, state, registry, runner, verifier = _engine(max_attempts=max_attempts)
        runner.run_agent = _dispatch_runner(state, {role: outputs})
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            workflow_id = state.create_workflow("re-roll test")
            task = state.add_task(
                Task("Review the change", role, workflow_id, max_attempts=max_attempts)
            )
            asyncio.run(engine.execute(workflow_id, _scratch_repo(self)))
            return state.get_task(task.id)
        finally:
            state.close()

    def test_review_reject_is_not_retried_by_the_same_reviewer(self):
        task = self._single_task("review", [REJECT, APPROVE])
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.attempts, 1)
        self.assertIn("unsafe input", task.result or "")

    def test_security_reject_is_not_retried(self):
        task = self._single_task("security", [REJECT, APPROVE])
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.attempts, 1)

    def test_missing_verdict_is_still_retried(self):
        # No verdict is a format failure, not a judgment: retrying is fine.
        task = self._single_task("review", ["no verdict here", APPROVE])
        self.assertEqual(task.status, TaskStatus.PASSED)
        self.assertEqual(task.attempts, 2)

    def test_default_attempts_rejection_goes_through_repair(self):
        config, state, registry, runner, verifier = _engine(max_attempts=2)
        calls = []
        dispatch = _dispatch_runner(state, {"review": [REJECT, APPROVE]})

        async def _recording(agent, prompt, directory, task_id, cancel_event=None, **kwargs):
            calls.append(state.get_task(task_id).role)
            return await dispatch(agent, prompt, directory, task_id, cancel_event, **kwargs)

        runner.run_agent = _recording
        try:
            engine = WorkflowEngine(
                config,
                state,
                registry,
                runner,
                _ok_verifier(),
                metadata_collector=_evidence_collector(),
            )
            result = asyncio.run(engine.run_high_level("Fix the thing", _scratch_repo(self)))
            self.assertTrue(result.ready, result.summary)
            first = calls.index("review")
            self.assertIn(
                "debugging", calls[first + 1 :], f"REJECT reached READY without a repair: {calls}"
            )
            self.assertEqual(calls.count("review"), 2)
        finally:
            state.close()


class SecurityGateTests(unittest.TestCase):
    @staticmethod
    def _repo_with_secret():
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is not available")
        root = Path(tempfile.mkdtemp(prefix="agentops-security-"))
        _git_args = ("-c", "user.name=t", "-c", "user.email=t@example.com")
        subprocess.run(("git", "init", "-q"), cwd=str(root), capture_output=True, check=True)
        (root / "app.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(
            ("git", *_git_args, "add", "-A"), cwd=str(root), capture_output=True, check=True
        )
        subprocess.run(
            ("git", *_git_args, "commit", "-q", "-m", "base"),
            cwd=str(root),
            capture_output=True,
            check=True,
        )
        with (root / "app.py").open("a", encoding="utf-8") as handle:
            handle.write('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
        return root

    def test_fake_secret_in_diff_blocks_the_workflow(self):
        root = self._repo_with_secret()
        try:
            config, state, registry, runner, verifier = _engine(security_review=True)
            runner.run_agent = _dispatch_runner(
                state,
                {
                    "review": [APPROVE],
                    "security": [APPROVE],
                },
            )
            engine = WorkflowEngine(
                config,
                state,
                registry,
                runner,
                _ok_verifier(),
                metadata_collector=_evidence_collector(),
            )
            result = asyncio.run(engine.run_high_level("Add the feature", root))
            self.assertFalse(result.ready, result.summary)
            security = next(
                task for task in state.list_tasks(result.workflow_id) if task.role == "security"
            )
            self.assertEqual(security.status, TaskStatus.FAILED)
            self.assertIn("secret scan", (security.result or "").lower())
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", security.result or "")
        finally:
            state.close()
            shutil.rmtree(root, ignore_errors=True)

    def test_clean_tree_passes_security(self):
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is not available")
        root = Path(tempfile.mkdtemp(prefix="agentops-security-clean-"))
        try:
            subprocess.run(("git", "init", "-q"), cwd=str(root), capture_output=True, check=True)
            config, state, registry, runner, verifier = _engine(security_review=True)
            runner.run_agent = _dispatch_runner(
                state,
                {
                    "review": [APPROVE],
                    "security": [APPROVE],
                },
            )
            engine = WorkflowEngine(
                config,
                state,
                registry,
                runner,
                _ok_verifier(),
                metadata_collector=_evidence_collector(),
            )
            result = asyncio.run(engine.run_high_level("Add the feature", root))
            self.assertTrue(result.ready, result.summary)
        finally:
            state.close()
            shutil.rmtree(root, ignore_errors=True)

    def test_scan_reports_completion_not_just_findings(self):
        # Reviewer finding: git errors and scan exceptions collapsed to an
        # empty finding list, so "the scan could not run" read as "the scan
        # found nothing". The helper now reports completion explicitly.
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is not available")
        root = self._repo_with_secret()
        plain = Path(tempfile.mkdtemp(prefix="agentops-security-plain-"))
        try:
            findings, completed = _local_security_scan(root)
            self.assertTrue(completed)
            self.assertTrue(findings)
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", " ".join(f.describe() for f in findings))
            self.assertEqual(_local_security_scan(plain), ([], False))
        finally:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(plain, ignore_errors=True)

    def test_prescan_unavailable_is_recorded_not_silent(self):
        # Reviewer finding: a prescan that could not run left no trace. The
        # degradation must be visible in the timeline instead of reading as
        # a clean change.
        config, state, registry, runner, verifier = _engine(security_review=True)
        try:
            engine = WorkflowEngine(
                config,
                state,
                registry,
                runner,
                _ok_verifier(),
                metadata_collector=_evidence_collector(),
            )
            workflow_id = state.create_workflow("prescan unavailable")
            task = state.add_task(Task("Security-review", "security", workflow_id, max_attempts=1))
            with tempfile.TemporaryDirectory() as plain:
                self.assertIsNone(engine._security_prescan_reason(task, plain))
            events = state.query_events(event_type=EventType.NOTE)
            self.assertTrue(events)
            self.assertEqual(events[0].severity.value, "warning")
            self.assertIn("prescan", events[0].message)
        finally:
            state.close()

    def test_security_gate_error_fails_closed(self):
        # Reviewer finding: when security_review is enabled but the security
        # task cannot be created or read, _gate_repair_security returns None
        # and the callers read it as "gate disabled" -- waiving the gate on
        # a state error. None must mean failure whenever the gate is on.
        root = self._repo_with_secret()
        try:
            config, state, registry, runner, verifier = _engine(
                security_review=True, max_review_rounds=1
            )

            class SecurityTaskFailingState:
                """State proxy that fails only the post-repair security task."""

                def __init__(self, inner):
                    self._inner = inner

                def add_task(self, task):
                    if getattr(task, "role", None) == "security" and "repaired" in (
                        task.description or ""
                    ):
                        raise RuntimeError("state unavailable")
                    return self._inner.add_task(task)

                def __getattr__(self, name):
                    return getattr(self._inner, name)

            runner.run_agent = _dispatch_runner(
                state,
                {
                    "review": [REJECT, APPROVE],
                },
            )
            engine = WorkflowEngine(
                config,
                SecurityTaskFailingState(state),
                registry,
                runner,
                _ok_verifier(),
                metadata_collector=_evidence_collector(),
            )
            result = asyncio.run(engine.run_high_level("Add the feature", root))
            self.assertFalse(result.ready, result.summary)
            self.assertIn("security", result.summary.lower())
        finally:
            state.close()
            shutil.rmtree(root, ignore_errors=True)


class RoleScopeTests(unittest.TestCase):
    def test_role_file_is_used_when_present(self):
        config, state, registry, runner, verifier = _engine()
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            with tempfile.TemporaryDirectory() as directory:
                Path(directory, "roles").mkdir()
                Path(directory, "roles", "review.md").write_text(
                    "Always check the changelog.", encoding="utf-8"
                )
                self.assertIn(
                    "Always check the changelog.",
                    engine._prompt(Task("r", "review", "w"), directory),
                )
                self.assertEqual(
                    _role_scope_text("review", directory), "Always check the changelog."
                )
        finally:
            state.close()

    def test_missing_role_file_falls_back_to_builtin(self):
        config, state, registry, runner, verifier = _engine()
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            with tempfile.TemporaryDirectory() as directory:
                self.assertEqual(_role_scope_text("review", directory), "")
                prompt = engine._prompt(Task("r", "review", "w"), directory)
                self.assertIn('"schema_version": 1', prompt)
        finally:
            state.close()


class WorkflowsConfigTests(unittest.TestCase):
    def test_workflows_section_parses(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(
                {"agents": {}, "workflows": {"security_review": True, "max_review_rounds": 3}},
                handle,
            )
            path = handle.name
        try:
            config = load_config(path)
            self.assertTrue(config.security_review)
            self.assertEqual(config.max_review_rounds, 3)
        finally:
            Path(path).unlink()

    def test_workflows_defaults_are_off_and_two(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"agents": {}}, handle)
            path = handle.name
        try:
            config = load_config(path)
            self.assertFalse(config.security_review)
            self.assertEqual(config.max_review_rounds, 2)
        finally:
            Path(path).unlink()

    def test_invalid_workflows_section_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"agents": {}, "workflows": {"security_review": "yes"}}, handle)
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                load_config(path)
        finally:
            Path(path).unlink()


class VerdictSummaryTests(unittest.TestCase):
    def test_summary_reports_blocking_role_and_findings(self):
        task = Task("Review", "review", "w", result=f"notes\n{APPROVE_BLOCKING}")
        summary = verdict_summary_for_task(task)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary["role"], "review")
        self.assertEqual(summary["verdict"], "approve")
        self.assertFalse(summary["approved"])
        self.assertEqual(list(summary["blocking"]), ["missing test"])

    def test_summary_ignores_non_review_roles(self):
        self.assertIsNone(
            verdict_summary_for_task(Task("Build", "implementation", "w", result="done"))
        )

    def test_serialize_task_carries_review_verdict(self):
        from agentops.gui_controller import serialize_task

        payload = serialize_task(Task("Review", "review", "w", result=APPROVE))
        self.assertEqual(payload["review_verdict"]["verdict"], "approve")
        self.assertTrue(payload["review_verdict"]["approved"])
        plain = serialize_task(Task("Build", "implementation", "w"))
        self.assertIsNone(plain["review_verdict"])

    def test_status_command_shows_the_verdict(self):
        from agentops.cli import CliServices, main

        store = StateStore(":memory:")
        workflow_id = store.create_workflow("ship it")
        review = store.add_task(Task("Review it", "review", workflow_id))
        review.status = TaskStatus.PASSED
        review.result = f"done\n{APPROVE_BLOCKING}"
        store.update_task(review)

        class _Manager:
            def repository_root(self, directory):
                return Path("/fake-root")

        services = CliServices(
            config=AppConfig({}, {}, (), max_attempts=1, concurrency=1),
            state_store=lambda path: store,
            worktree_manager=lambda: _Manager(),
            log_manager=lambda root: MagicMock(name="logs"),
        )
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                code = main(["status"], services=services)
        finally:
            store.close()
        self.assertEqual(code, 0)
        self.assertIn("approve", buffer.getvalue())
        self.assertIn("missing test", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
