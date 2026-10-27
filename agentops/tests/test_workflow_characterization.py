"""Characterization tests for the workflow verdict, status and security helpers.

These pin the current behaviour of the safety-critical helpers in
``agentops.workflow`` (the Issue #12 / PR #24 fail-closed gates) and of the
engine paths that consume them, so the module can be restructured without a
behaviour change. They describe what the code does today; they are not a
specification of new behaviour.
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentops.agent_result import AgentResultStatus
from agentops.agent_run import AgentRunMetadata
from agentops.config import AgentConfig, AppConfig
from agentops.registry import DetectedAgent
from agentops.runner import RunResult
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.workflow import (
    STANDARD_TASK_ROLES,
    WorkflowEngine,
    _explicit_success_denied,
    _is_security_role,
    _is_verdict_only,
    _local_security_scan,
    _payload_denies_success,
    _requires_review_verdict,
    _review_output_denies_success,
    _review_payload_denies_success,
    _role_scope_text,
    _scrub_structured,
    _stdout_candidate_dicts,
    _structured_agent_status,
    verdict_summary_for_task,
)

APPROVE = (
    '{"schema_version":1,"verdict":"approve","blocking":[],"non_blocking":[],"confidence":0.9}'
)
REJECT = (
    '{"schema_version":1,"verdict":"reject","blocking":["unsafe input"],'
    '"non_blocking":[],"confidence":0.99}'
)
STATUS_FAILURE = '{"schema_version":1,"status":"failure"}'
STATUS_SUCCESS = '{"schema_version":1,"status":"success"}'
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"

_ALL_ROLES = ("architecture", "implementation", "review", "debugging", "security", "verification")


def _result(stdout="", structured=None):
    return SimpleNamespace(stdout=stdout, structured_result=structured)


def _git_env():
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _scratch_repo(testcase):
    if shutil.which("git") is None:
        raise unittest.SkipTest("git is not available")
    directory = Path(tempfile.mkdtemp(prefix="agentops-char-"))
    testcase.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    env = _git_env()
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "char@agentops.invalid"],
        ["git", "config", "user.name", "Char Test"],
    ):
        subprocess.run(command, cwd=directory, check=True, env=env)
    (directory / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=directory, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=directory, check=True, env=env)
    return directory


def _config(security_review=False, max_review_rounds=2, max_attempts=1):
    return AppConfig(
        {"fallback": AgentConfig("fallback", "fake", ("{prompt}",), _ALL_ROLES)},
        {role: ("fallback",) for role in _ALL_ROLES},
        (("test",),),
        max_attempts=max_attempts,
        concurrency=1,
        max_repair_cycles=1,
        security_review=security_review,
        max_review_rounds=max_review_rounds,
    )


def _dispatch_runner(state, outputs_by_role, calls=None):
    """Fake run_agent answering per task role; each value is a list of outputs."""
    cursors = {role: 0 for role in outputs_by_role}

    async def _run(agent, prompt, directory, task_id, cancel_event=None, **kwargs):
        role = state.get_task(task_id).role
        if calls is not None:
            calls.append(role)
        outputs = outputs_by_role.get(role, ["done"])
        output = outputs[min(cursors.get(role, 0), len(outputs) - 1)]
        cursors[role] = cursors.get(role, 0) + 1
        stdout, structured = output if isinstance(output, tuple) else (output, None)
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


def _verifier(outcomes):
    """Verifier double returning one succeeded flag per call (last one repeats)."""
    verifier = MagicMock()
    cursor = {"index": 0}

    def _run(*args, **kwargs):
        index = min(cursor["index"], len(outcomes) - 1)
        cursor["index"] += 1
        ok = outcomes[index]
        return asyncio.sleep(
            0, result=[SimpleNamespace(succeeded=ok, output="ok" if ok else "tests failed")]
        )

    verifier.run = MagicMock(side_effect=_run)
    return verifier


def _engine(state, config, outputs_by_role, verifier=None, calls=None):
    registry = MagicMock()
    registry.select.return_value = DetectedAgent(config.agents["fallback"], True, "fake")
    runner = MagicMock()
    runner.run_agent = _dispatch_runner(state, outputs_by_role, calls)
    return WorkflowEngine(
        config,
        state,
        registry,
        runner,
        verifier if verifier is not None else _verifier([True]),
        metadata_collector=MagicMock(return_value=AgentRunMetadata(files_changed=("src/app.py",))),
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class RoleClassificationTests(unittest.TestCase):
    def test_review_verdict_roles(self):
        for role in ("review", "Review", "security", " SECURITY ", "security review"):
            self.assertTrue(_requires_review_verdict(role), role)
        for role in ("Security_Review", "security-review"):
            self.assertTrue(_requires_review_verdict(role), role)
        for role in ("implementation", "verification", "code review", "", None, 3):
            self.assertFalse(_requires_review_verdict(role), role)

    def test_security_role_is_a_subset_of_review_roles(self):
        for role in ("security", "Security_Review", "security-review", " SECURITY "):
            self.assertTrue(_is_security_role(role), role)
        for role in ("review", "implementation", "", None):
            self.assertFalse(_is_security_role(role), role)


class VerdictOnlyTests(unittest.TestCase):
    def test_bare_verdict_object_is_verdict_only(self):
        self.assertTrue(_is_verdict_only({"verdict": "reject", "blocking": ["x"]}))

    def test_status_or_success_key_makes_it_a_status_signal(self):
        self.assertFalse(_is_verdict_only({"verdict": "approve", "status": "success"}))
        for key in ("success", "successful", "succeeded", "passed", "pass", "ok"):
            self.assertFalse(_is_verdict_only({"verdict": "approve", key: True}), key)

    def test_non_verdict_and_non_dict_payloads(self):
        self.assertFalse(_is_verdict_only({"status": "success"}))
        for payload in (None, "verdict", ["verdict"], 1):
            self.assertFalse(_is_verdict_only(payload))


class PayloadDeniesSuccessTests(unittest.TestCase):
    def test_false_like_values_deny_success(self):
        for payload in (
            {"success": False},
            {"ok": 0},
            {"pass": 0.0},
            {"passed": "No"},
            {"succeeded": ""},
            {"successful": " FAILED "},
            {"success": "error"},
            {"ok": "off"},
        ):
            self.assertTrue(_payload_denies_success(payload), payload)

    def test_true_like_none_and_odd_types_do_not_deny(self):
        for payload in (
            {"success": True},
            {"success": None},
            {"ok": 1},
            {"success": "yes"},
            {"success": []},
            {"status": "failure"},
            {},
        ):
            self.assertFalse(_payload_denies_success(payload), payload)

    def test_non_dict_never_denies(self):
        for payload in (None, "success: false", [{"success": False}]):
            self.assertFalse(_payload_denies_success(payload))


class StdoutCandidateTests(unittest.TestCase):
    def test_only_the_last_json_line_is_taken(self):
        self.assertEqual(_stdout_candidate_dicts('x\n{"a":1}\n{"b":2}\n'), [{"b": 2}])

    def test_whole_payload_and_line_scan_both_match(self):
        self.assertEqual(_stdout_candidate_dicts('{"a":1}'), [{"a": 1}, {"a": 1}])

    def test_fenced_block_and_trailing_comma_line(self):
        self.assertEqual(
            _stdout_candidate_dicts('```json\n{"a":1}\n```\n{"b":2},'), [{"a": 1}, {"b": 2}]
        )

    def test_malformed_and_non_string_input_yield_nothing(self):
        self.assertEqual(_stdout_candidate_dicts('{"status": "success"'), [])
        self.assertEqual(_stdout_candidate_dicts(""), [])
        self.assertEqual(_stdout_candidate_dicts(None), [])
        self.assertEqual(_stdout_candidate_dicts(b'{"a":1}'), [])

    def test_pathologically_nested_line_does_not_hide_an_earlier_signal(self):
        deep = '{"a":' * 100000 + "1" + "}" * 100000
        self.assertEqual(
            _stdout_candidate_dicts(STATUS_FAILURE + "\n" + deep),
            [{"schema_version": 1, "status": "failure"}],
        )


class StructuredAgentStatusTests(unittest.TestCase):
    def test_malformed_json_without_envelope_is_the_legacy_path(self):
        self.assertIsNone(_structured_agent_status(_result('{"status": "success"')))

    def test_malformed_stdout_falls_back_to_the_envelope(self):
        result = _result('{"status": "success"', {"schema_version": 1, "status": "failure"})
        self.assertIs(_structured_agent_status(result), AgentResultStatus.FAILURE)

    def test_fenced_failure_is_failure(self):
        result = _result("text\n```json\n" + STATUS_FAILURE + "\n```\n")
        self.assertIs(_structured_agent_status(result), AgentResultStatus.FAILURE)

    def test_unrecognized_json_object_is_unknown_not_success(self):
        self.assertIs(_structured_agent_status(_result('{"foo": 1}')), AgentResultStatus.UNKNOWN)

    def test_success_false_without_status_is_unknown(self):
        self.assertIs(
            _structured_agent_status(_result('{"success": false}')), AgentResultStatus.UNKNOWN
        )

    def test_envelope_status_is_used_for_plaintext_stdout(self):
        result = _result("plain", {"schema_version": 1, "status": "success"})
        self.assertIs(_structured_agent_status(result), AgentResultStatus.SUCCESS)

    def test_verdict_only_envelope_or_line_is_not_a_status(self):
        self.assertIsNone(_structured_agent_status(_result("plain", {"verdict": "reject"})))
        self.assertIsNone(
            _structured_agent_status(_result('prose\n{"verdict":"reject","blocking":["x"]}'))
        )

    def test_missing_attributes_never_raise(self):
        self.assertIsNone(_structured_agent_status(SimpleNamespace()))
        self.assertIsNone(_structured_agent_status(None))


class ExplicitSuccessDeniedTests(unittest.TestCase):
    def test_string_envelope_is_parsed(self):
        self.assertTrue(_explicit_success_denied(_result("", '{"success": false}')))

    def test_malformed_string_envelope_still_scans_stdout(self):
        self.assertFalse(_explicit_success_denied(_result("", "{bad")))
        self.assertTrue(_explicit_success_denied(_result('{"ok": 0}', "{bad")))

    def test_fenced_block_denial(self):
        self.assertTrue(_explicit_success_denied(_result('```\n{"passed": "FAILED "}\n```')))

    def test_verdict_only_reject_is_left_to_the_verdict_gate(self):
        self.assertFalse(_explicit_success_denied(_result('{"verdict":"reject"}')))

    def test_success_true_and_missing_attributes(self):
        self.assertFalse(_explicit_success_denied(_result('{"success": true}')))
        self.assertFalse(_explicit_success_denied(SimpleNamespace()))


class ReviewOutputDeniesSuccessTests(unittest.TestCase):
    def test_top_level_denials(self):
        for payload in (
            {"success": False},
            {"ok": "no"},
            {"status": "failure"},
            {"status": "error"},
            {"status": "partial"},
            {"verdict": "reject"},
            {"verdict": "REJECT"},
            {"verdict": "Changes Requested"},
            {"verdict": "request-changes"},
            {"verdict": "approve", "blocking": ["x"]},
        ):
            with self.subTest(payload=payload):
                self.assertTrue(_review_payload_denies_success(payload))

    def test_non_denials(self):
        for payload in (
            {"success": True},
            {"status": "success"},
            {"status": "running"},
            {"verdict": "approve", "blocking": []},
            {"tool": "x", "result": {"success": False}},
            {"blocking": ["not a verdict object"]},
            "success: false",
            None,
        ):
            with self.subTest(payload=payload):
                self.assertFalse(_review_payload_denies_success(payload))

    def test_output_scan(self):
        self.assertTrue(_review_output_denies_success('{"success": false}\n' + APPROVE))
        self.assertTrue(_review_output_denies_success(REJECT + "\nlog\n" + APPROVE))
        self.assertFalse(_review_output_denies_success("log line\n" + APPROVE))
        self.assertFalse(_review_output_denies_success('```python\n{"ok": False}\n```\n'))
        self.assertFalse(_review_output_denies_success(""))
        self.assertFalse(_review_output_denies_success(None))


class VerdictSummaryCharacterizationTests(unittest.TestCase):
    def test_missing_verdict_reads_as_not_approved(self):
        summary = verdict_summary_for_task(SimpleNamespace(role="review", result="looks good"))
        self.assertEqual(
            summary,
            {
                "role": "review",
                "verdict": "missing",
                "approved": False,
                "blocking": [],
                "non_blocking": [],
            },
        )

    def test_reject_is_not_approved_and_lists_blocking(self):
        summary = verdict_summary_for_task(SimpleNamespace(role="security", result=REJECT))
        self.assertEqual(summary["verdict"], "reject")
        self.assertFalse(summary["approved"])
        self.assertEqual(summary["blocking"], ["unsafe input"])

    def test_blocking_items_are_truncated(self):
        long_reject = (
            '{"schema_version":1,"verdict":"reject","blocking":["' + "x" * 900 + '"],'
            '"non_blocking":[],"confidence":0.9}'
        )
        summary = verdict_summary_for_task(SimpleNamespace(role="review", result=long_reject))
        self.assertEqual(len(summary["blocking"][0]), 500)

    def test_non_review_and_broken_tasks_give_none(self):
        self.assertIsNone(verdict_summary_for_task(SimpleNamespace(role="implementation")))

        class Exploding:
            role = "review"

            @property
            def result(self):
                raise RuntimeError("boom")

        self.assertIsNone(verdict_summary_for_task(Exploding()))


class ScrubStructuredTests(unittest.TestCase):
    def test_nested_secrets_are_redacted_and_tuples_become_lists(self):
        token = "ghp_" + "a" * 36
        self.assertEqual(
            _scrub_structured({"k": [token, ("x",)], "n": 1}),
            {"k": ["[REDACTED]", ["x"]], "n": 1},
        )


class LocalSecurityScanTests(unittest.TestCase):
    def test_missing_directory_is_not_a_completed_scan(self):
        missing = Path(tempfile.gettempdir(), "agentops-char-does-not-exist-xyz")
        self.assertEqual(_local_security_scan(missing), ([], False))

    def test_clean_repo_is_a_completed_empty_scan(self):
        self.assertEqual(_local_security_scan(_scratch_repo(self)), ([], True))

    def test_untracked_file_with_secret_is_found_and_redacted(self):
        root = _scratch_repo(self)
        (root / "new.py").write_text(f'KEY = "{FAKE_AWS_KEY}"\n', encoding="utf-8")
        findings, completed = _local_security_scan(root)
        self.assertTrue(completed)
        self.assertTrue(findings)
        self.assertNotIn(FAKE_AWS_KEY, " ".join(f.describe() for f in findings))

    def test_large_untracked_file_is_skipped(self):
        root = _scratch_repo(self)
        padding = "#" * (101 * 1024)
        (root / "big.py").write_text(f'KEY = "{FAKE_AWS_KEY}"\n{padding}\n', encoding="utf-8")
        self.assertEqual(_local_security_scan(root), ([], True))


class RoleScopeCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="agentops-char-roles-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "roles").mkdir()

    def test_hyphen_and_underscore_spellings(self):
        (self.root / "roles" / "security-review.md").write_text("hyphen\n", encoding="utf-8")
        self.assertEqual(_role_scope_text("Security Review", self.root), "hyphen")
        (self.root / "roles" / "code_owner.md").write_text("under\n", encoding="utf-8")
        self.assertEqual(_role_scope_text("code owner", self.root), "under")

    def test_separators_and_empty_roles_are_rejected(self):
        (self.root / "secret.md").write_text("outside\n", encoding="utf-8")
        self.assertEqual(_role_scope_text("../secret", self.root), "")
        self.assertEqual(_role_scope_text("..\\secret", self.root), "")
        self.assertEqual(_role_scope_text("", self.root), "")
        self.assertEqual(_role_scope_text(None, self.root), "")

    def test_role_text_is_capped(self):
        (self.root / "roles" / "review.md").write_text("y" * 5000, encoding="utf-8")
        self.assertEqual(len(_role_scope_text("review", self.root)), 4000)


# ---------------------------------------------------------------------------
# Engine paths that consume the helpers
# ---------------------------------------------------------------------------


class EngineSingleTaskGateTests(unittest.TestCase):
    def _run_task(self, role, outputs):
        state = StateStore(":memory:")
        self.addCleanup(state.close)
        engine = _engine(state, _config(), {role: outputs})
        workflow_id = state.create_workflow("characterization")
        task = state.add_task(Task("Do the work", role, workflow_id, max_attempts=1))
        asyncio.run(engine.execute(workflow_id, _scratch_repo(self)))
        return state.get_task(task.id)

    def test_implementation_success_false_with_exit_zero_fails(self):
        task = self._run_task("implementation", ['{"success": false}'])
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_implementation_explicit_success_passes(self):
        task = self._run_task("implementation", [STATUS_SUCCESS])
        self.assertEqual(task.status, TaskStatus.PASSED)

    def test_implementation_malformed_json_with_exit_zero_uses_the_exit_code(self):
        # Malformed JSON is not a structured signal, so the legacy exit-code
        # path applies to non-review roles.
        task = self._run_task("implementation", ['{"status": "failure"'])
        self.assertEqual(task.status, TaskStatus.PASSED)

    def test_review_malformed_json_with_exit_zero_fails(self):
        task = self._run_task("review", ['{"verdict": "approve"'])
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_review_fenced_reject_with_exit_zero_fails(self):
        task = self._run_task("review", ["Findings below.\n```json\n" + REJECT + "\n```\n"])
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("unsafe input", task.result or "")

    def test_review_reject_with_success_envelope_still_fails(self):
        task = self._run_task("review", [(REJECT, {"schema_version": 1, "status": "success"})])
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_review_missing_verdict_with_exit_zero_fails(self):
        task = self._run_task("review", ["Looks fine to me, no structured verdict."])
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("review verdict missing or malformed", task.result or "")

    def test_review_bare_reject_with_exit_zero_fails(self):
        task = self._run_task("review", [REJECT])
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("unsafe input", task.result or "")

    def test_security_malformed_and_missing_verdicts_fail(self):
        for output in ('{"verdict": "approve"', "No issues found."):
            with self.subTest(output=output):
                task = self._run_task("security", [output])
                self.assertEqual(task.status, TaskStatus.FAILED)

    def test_review_single_approve_passes(self):
        task = self._run_task("review", [APPROVE])
        self.assertEqual(task.status, TaskStatus.PASSED)

    def test_review_approve_after_plain_log_lines_passes(self):
        output = "Ran the suite: 12 passed.\nNo blocking findings.\n" + APPROVE
        task = self._run_task("review", [output])
        self.assertEqual(task.status, TaskStatus.PASSED)

    def test_review_success_status_payload_with_approve_passes(self):
        task = self._run_task("review", [STATUS_SUCCESS + "\n" + APPROVE])
        self.assertEqual(task.status, TaskStatus.PASSED)

    def test_review_success_false_line_before_a_final_approve_fails(self):
        # Formerly pinned as "not endorsed" (it PASSED): only the last JSON
        # line was checked. The gate now fails closed on contradictory output.
        task = self._run_task("review", ['{"success": false}\n' + APPROVE])
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("review output is contradictory", task.result or "")

    def test_review_denial_then_later_approval_fails(self):
        outputs = (
            REJECT + "\n" + APPROVE,
            STATUS_FAILURE + "\n" + APPROVE,
            '{"status": "error"}\n' + APPROVE,
            '{"success": false}\n{"success": true}\n' + APPROVE,
            '{"verdict": "changes-requested"}\nfixed it myself\n' + APPROVE,
            "```json\n" + REJECT + "\n```\n" + APPROVE,
            '{"ok": false}\n```json\n' + APPROVE + "\n```\n",
        )
        for output in outputs:
            with self.subTest(output=output):
                task = self._run_task("review", [output])
                self.assertEqual(task.status, TaskStatus.FAILED)

    def test_review_nested_or_quoted_denials_do_not_block_an_approval(self):
        outputs = (
            # Nested tool output inside a top-level object is not inspected.
            '{"tool": "pytest", "result": {"success": false}, "note": "pre-existing"}\n' + APPROVE,
            # JSON quoted mid-line in prose is not a structured payload.
            'The old CI run printed {"success": false}; fixed since.\n' + APPROVE,
            # Illustrative content in a non-JSON fence is not a payload.
            '```text\n{"success": false}\n```\n' + APPROVE,
            # One whole-output object: its nested values are not candidates.
            APPROVE[:-1] + ',"evidence":{"previous_run":{"success":false}}}',
        )
        for output in outputs:
            with self.subTest(output=output):
                task = self._run_task("review", [output])
                self.assertEqual(task.status, TaskStatus.PASSED, task.result)

    def test_security_denial_then_approval_fails_on_a_clean_tree(self):
        for output in ('{"success": false}\n' + APPROVE, REJECT + "\n" + APPROVE):
            with self.subTest(output=output):
                task = self._run_task("security", [output])
                self.assertEqual(task.status, TaskStatus.FAILED)

    def test_implementation_role_is_unchanged_by_the_review_scan(self):
        # The contradiction scan is a review/security gate only; non-review
        # roles keep the existing last-line status behaviour.
        task = self._run_task("implementation", ['{"success": false}\n' + STATUS_SUCCESS])
        self.assertEqual(task.status, TaskStatus.PASSED)

    def test_review_approve_with_success_false_in_one_object_fails(self):
        approve_denied = APPROVE[:-1] + ',"success":false}'
        task = self._run_task("review", [approve_denied])
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_security_reject_with_exit_zero_fails_on_a_clean_tree(self):
        task = self._run_task("security", [REJECT])
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_security_approve_on_a_clean_tree_passes(self):
        task = self._run_task("security", [APPROVE])
        self.assertEqual(task.status, TaskStatus.PASSED)


class EnginePlanTests(unittest.TestCase):
    def test_standard_workflow_roles_and_dependencies(self):
        for security_review, roles in (
            (False, STANDARD_TASK_ROLES),
            (True, (*STANDARD_TASK_ROLES, "security")),
        ):
            state = StateStore(":memory:")
            self.addCleanup(state.close)
            engine = _engine(state, _config(security_review=security_review), {})
            _workflow_id, tasks = engine.create_standard_workflow("Add a feature")
            self.assertEqual(tuple(task.role for task in tasks), roles)
            plan, implementation, verification, review = tasks[:4]
            self.assertEqual(plan.dependencies, ())
            self.assertEqual(implementation.dependencies, (plan.id,))
            self.assertEqual(verification.dependencies, (implementation.id,))
            self.assertEqual(verification.max_attempts, 1)
            self.assertEqual(review.dependencies, (verification.id, implementation.id))
            if security_review:
                self.assertEqual(tasks[4].dependencies, (review.id, implementation.id))


class EngineVerificationRepairSecurityTests(unittest.TestCase):
    """Verification-failure repair cycle with the security gate enabled."""

    def _run(self, security_outputs):
        state = StateStore(":memory:")
        self.addCleanup(state.close)
        calls: list[str] = []
        engine = _engine(
            state,
            _config(security_review=True),
            {"review": [APPROVE], "security": security_outputs},
            verifier=_verifier([False, True]),
            calls=calls,
        )
        result = asyncio.run(engine.run_high_level("Fix the thing", _scratch_repo(self)))
        return result, calls, state.list_tasks(result.workflow_id)

    def test_repaired_change_with_security_approval_is_ready(self):
        result, calls, _tasks = self._run([APPROVE])
        self.assertTrue(result.ready, result.summary)
        self.assertIn("debugging", calls)
        self.assertIn("security", calls)

    def test_repaired_change_with_security_rejection_is_not_ready(self):
        result, calls, tasks = self._run([REJECT])
        self.assertFalse(result.ready, result.summary)
        self.assertIn("security review could not be completed", result.summary)
        repaired_security = [
            task for task in tasks if task.role == "security" and "repaired" in task.description
        ]
        self.assertEqual(len(repaired_security), 1)
        self.assertEqual(repaired_security[0].status, TaskStatus.FAILED)


class EngineReviewRepairContradictionTests(unittest.TestCase):
    """The review repair loop re-runs reviews through the same gate."""

    def _run(self, review_outputs, security_outputs=None):
        state = StateStore(":memory:")
        self.addCleanup(state.close)
        calls: list[str] = []
        outputs = {"review": review_outputs}
        if security_outputs is not None:
            outputs["security"] = security_outputs
        engine = _engine(
            state,
            _config(security_review=security_outputs is not None, max_review_rounds=1),
            outputs,
            calls=calls,
        )
        result = asyncio.run(engine.run_high_level("Fix the thing", _scratch_repo(self)))
        return result, calls

    def test_contradictory_re_review_is_not_ready(self):
        contradictory = '{"success": false}\n' + APPROVE
        result, calls = self._run([REJECT, contradictory])
        self.assertFalse(result.ready, result.summary)
        self.assertIn("debugging", calls)
        self.assertEqual(calls.count("review"), 2)

    def test_clean_re_review_is_ready(self):
        result, _calls = self._run([REJECT, APPROVE])
        self.assertTrue(result.ready, result.summary)

    def test_contradictory_repair_security_is_not_ready(self):
        result, calls = self._run([REJECT, APPROVE], [REJECT + "\n" + APPROVE])
        self.assertFalse(result.ready, result.summary)
        self.assertIn("security", calls)


class ImportSurfaceTests(unittest.TestCase):
    """Every name callers import from ``agentops.workflow`` stays there."""

    MODULE_NAMES = (
        "HANDOFF_RESULT_CHARS",
        "HANDOFF_TOTAL_CHARS",
        "REJECT",
        "STANDARD_TASK_ROLES",
        "WorkflowEngine",
        "WorkflowResult",
        "_FALSE_LIKE",
        "_ROLE_SCOPE_MAX_CHARS",
        "_RecordingObserver",
        "_SUCCESS_KEYS",
        "_explicit_success_denied",
        "_is_git_working_tree",
        "_is_security_role",
        "_is_verdict_only",
        "_local_security_scan",
        "_payload_denies_success",
        "_requires_review_verdict",
        "_review_output_denies_success",
        "_review_payload_denies_success",
        "_role_scope_text",
        "_safe_workflow_structured",
        "_scrub_structured",
        "_stdout_candidate_dicts",
        "_structured_agent_status",
        "assert_tasks_ready",
        "assess_workflow_readiness",
        "verdict_summary_for_task",
    )

    ENGINE_MEMBERS = (
        "_agent_parent",
        "_agent_run_context",
        "_check_attribute",
        "_dependency_handoff",
        "_evidence_satisfied",
        "_execute_task",
        "_gate_repair_security",
        "_historical_performance",
        "_note_run_degradation",
        "_prompt",
        "_prompt_with_history",
        "_record_agent_failure",
        "_record_cancelled_run",
        "_record_completed_run",
        "_record_failed_run",
        "_record_legacy_verification_failure",
        "_record_no_agent_run",
        "_record_review_verdict",
        "_record_routing_decision",
        "_record_usage_limit_cooldown",
        "_record_verification_failure",
        "_repair_failed_review",
        "_repository_characteristics",
        "_review_failure_context",
        "_routing_gate",
        "_routing_gate_active",
        "_run_observer_for_task",
        "_run_task_agent",
        "_security_prescan_reason",
        "_select_agent",
        "_supported_runner_kwargs",
        "_usage_limit_kwargs",
        "_validate_acyclic",
        "_verification_source_run",
        "create_standard_workflow",
        "create_workflow",
        "execute",
        "plan_repair_for_failure",
        "record_failure",
        "recover_incomplete",
        "retry_policy",
        "run_high_level",
        "validate_workflow_specifications",
        "verification_evidence",
        "workflow_readiness",
    )

    def test_module_names_are_importable(self):
        import agentops.workflow as workflow_module

        missing = [name for name in self.MODULE_NAMES if not hasattr(workflow_module, name)]
        self.assertEqual(missing, [])

    def test_engine_members_are_present(self):
        missing = [name for name in self.ENGINE_MEMBERS if not hasattr(WorkflowEngine, name)]
        self.assertEqual(missing, [])

    def test_engine_member_kinds_are_unchanged(self):
        import inspect

        self.assertIsInstance(inspect.getattr_static(WorkflowEngine, "retry_policy"), property)
        self.assertIsInstance(
            inspect.getattr_static(WorkflowEngine, "validate_workflow_specifications"),
            classmethod,
        )
        for name in ("_validate_acyclic", "_check_attribute"):
            self.assertIsInstance(inspect.getattr_static(WorkflowEngine, name), staticmethod)
        for name in (
            "execute",
            "_execute_task",
            "_run_task_agent",
            "_gate_repair_security",
            "_repair_failed_review",
            "run_high_level",
        ):
            self.assertTrue(inspect.iscoroutinefunction(getattr(WorkflowEngine, name)), name)


if __name__ == "__main__":
    unittest.main()
