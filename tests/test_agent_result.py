"""Tests for versioned AgentResult schema, robust parser, and outcome rules."""

from __future__ import annotations

import json
import unittest

from agentops.agent_result import (
    RESULT_SCHEMA_VERSION,
    AgentResult,
    AgentResultStatus,
    ParseMode,
    coerce_agent_result,
    evaluate_execution,
    parse_agent_result,
)


def _envelope(**overrides):
    base = {
        "schema_version": 1,
        "status": "success",
        "summary": "did the thing",
        "files_changed": ["a.py"],
        "tests_run": 3,
        "tests_passed": 3,
        "tests_failed": 0,
        "verification_results": [{"check": "tests", "passed": True}],
        "review_findings": [],
        "requested_followup": None,
        "confidence": 0.9,
        "errors": [],
        "warnings": [],
        "metadata": {"agent": "test"},
    }
    base.update(overrides)
    return base


class ParseValidJsonTests(unittest.TestCase):
    def test_full_envelope_structured(self):
        stdout = "working...\n" + json.dumps(_envelope())
        parsed = parse_agent_result(stdout)
        self.assertEqual(parsed.parse_mode, ParseMode.STRUCTURED)
        self.assertEqual(parsed.result.status, AgentResultStatus.SUCCESS)
        self.assertEqual(parsed.result.files_changed, ("a.py",))
        self.assertEqual(parsed.result.tests_run, 3)
        self.assertEqual(parsed.result.confidence, 0.9)
        self.assertEqual(parsed.result.schema_version, RESULT_SCHEMA_VERSION)

    def test_whole_stdout_json_object(self):
        parsed = parse_agent_result(json.dumps(_envelope(status="failure", errors=["boom"])))
        self.assertEqual(parsed.parse_mode, ParseMode.STRUCTURED)
        self.assertEqual(parsed.result.status, AgentResultStatus.FAILURE)
        self.assertEqual(parsed.result.errors, ("boom",))

    def test_fenced_json_block(self):
        stdout = "summary text\n```json\n" + json.dumps(_envelope(status="partial")) + "\n```\n"
        parsed = parse_agent_result(stdout)
        self.assertIn(parsed.parse_mode, (ParseMode.STRUCTURED, ParseMode.PARTIAL))
        self.assertEqual(parsed.result.status, AgentResultStatus.PARTIAL)

    def test_all_twelve_fields_round_trip(self):
        result = parse_agent_result(json.dumps(_envelope())).result
        data = result.to_dict()
        for key in (
            "status", "summary", "files_changed", "tests_run", "tests_passed",
            "tests_failed", "verification_results", "review_findings",
            "requested_followup", "confidence", "errors", "warnings", "metadata",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["schema_version"], 1)


class ParseDegradedTests(unittest.TestCase):
    def test_malformed_truncated_json(self):
        stdout = '{"schema_version": 1, "status": "success", "summary": "half-written'
        parsed = parse_agent_result(stdout)
        self.assertEqual(parsed.parse_mode, ParseMode.MALFORMED)
        self.assertEqual(parsed.result.status, AgentResultStatus.UNKNOWN)

    def test_empty_output(self):
        for stdout in ("", "   \n  ", None):
            parsed = parse_agent_result(stdout)
            self.assertEqual(parsed.parse_mode, ParseMode.EMPTY)
            self.assertEqual(parsed.result.status, AgentResultStatus.UNKNOWN)

    def test_partial_output_missing_fields(self):
        parsed = parse_agent_result(json.dumps({"status": "success"}))
        self.assertIn(parsed.parse_mode, (ParseMode.STRUCTURED, ParseMode.PARTIAL))
        self.assertEqual(parsed.result.status, AgentResultStatus.SUCCESS)
        self.assertIsNone(parsed.result.summary)
        self.assertEqual(parsed.result.files_changed, ())

    def test_plain_text_fallback(self):
        parsed = parse_agent_result("All done, changed foo.py and tests pass.")
        self.assertEqual(parsed.parse_mode, ParseMode.TEXT)
        self.assertEqual(parsed.result.status, AgentResultStatus.UNKNOWN)
        self.assertIn("foo.py", parsed.result.summary or "")

    def test_wrong_types_coerced_not_crash(self):
        payload = _envelope(tests_run="three", confidence="high", files_changed="a.py",
                            verification_results={"check": "tests"}, status="COMPLETED")
        parsed = parse_agent_result(json.dumps(payload))
        self.assertEqual(parsed.result.status, AgentResultStatus.SUCCESS)  # alias
        self.assertIsNone(parsed.result.tests_run)
        self.assertIsNone(parsed.result.confidence)
        self.assertEqual(parsed.result.files_changed, ("a.py",))

    def test_bare_list_partial(self):
        parsed = parse_agent_result(json.dumps([{"finding": "x"}]))
        self.assertEqual(parsed.parse_mode, ParseMode.PARTIAL)
        self.assertEqual(parsed.result.status, AgentResultStatus.UNKNOWN)

    def test_parser_never_raises(self):
        class BadStr:
            def __str__(self):
                raise RuntimeError("nope")
        parsed = parse_agent_result(BadStr())
        self.assertEqual(parsed.result.status, AgentResultStatus.UNKNOWN)
        # Non-string garbage also safe
        self.assertEqual(parse_agent_result(12345).parse_mode, ParseMode.TEXT)


class VersioningTests(unittest.TestCase):
    def test_unknown_version_coerced_partial_with_warning(self):
        payload = _envelope(schema_version=99)
        parsed = parse_agent_result(json.dumps(payload))
        self.assertEqual(parsed.parse_mode, ParseMode.PARTIAL)
        self.assertEqual(parsed.result.status, AgentResultStatus.PARTIAL)
        self.assertTrue(any("schema_version" in warning for warning in parsed.warnings))
        self.assertEqual(parsed.result.metadata.get("original_schema_version"), 99)
        self.assertEqual(parsed.result.schema_version, RESULT_SCHEMA_VERSION)

    def test_invalid_version_defaults(self):
        parsed = parse_agent_result(json.dumps(_envelope(schema_version="nonsense")))
        self.assertEqual(parsed.result.schema_version, RESULT_SCHEMA_VERSION)


class BackwardCompatTests(unittest.TestCase):
    def test_old_raw_dict_preserved_in_metadata(self):
        result = coerce_agent_result({"changed": True})
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.metadata.get("legacy_payload"), {"changed": True})

    def test_old_raw_list_preserved(self):
        result = coerce_agent_result([1, 2])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.metadata.get("legacy_payload"), [1, 2])

    def test_none_stays_none(self):
        self.assertIsNone(coerce_agent_result(None))

    def test_new_envelope_round_trip(self):
        original = parse_agent_result(json.dumps(_envelope())).result
        restored = coerce_agent_result(original.to_dict())
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.status, AgentResultStatus.SUCCESS)
        self.assertEqual(restored.files_changed, ("a.py",))

    def test_garbage_never_raises(self):
        self.assertIsNone(coerce_agent_result(object()))


class OutcomeDistinctionTests(unittest.TestCase):
    def test_all_signals_required_for_merge(self):
        full = evaluate_execution(process_success=True, agent_status="success",
                                  verification_success=True, review_approved=True)
        self.assertTrue(full.merge_eligible)
        self.assertEqual(full.reasons, ())

    def test_process_success_alone_not_mergeable(self):
        outcome = evaluate_execution(process_success=True, agent_status="unknown",
                                     verification_success=None, review_approved=None)
        self.assertTrue(outcome.process_success)
        self.assertFalse(outcome.merge_eligible)

    def test_agent_success_without_verification_not_mergeable(self):
        outcome = evaluate_execution(process_success=True, agent_status="success",
                                     verification_success=False, review_approved=True)
        self.assertFalse(outcome.merge_eligible)
        self.assertIn("verification did not pass", outcome.reasons)

    def test_process_failure(self):
        outcome = evaluate_execution(process_success=False, agent_status="success",
                                     verification_success=True, review_approved=True)
        self.assertFalse(outcome.merge_eligible)
        self.assertIn("process did not succeed", outcome.reasons)

    def test_verification_failure(self):
        outcome = evaluate_execution(process_success=True, agent_status="success",
                                     verification_success=False, review_approved=True)
        self.assertFalse(outcome.merge_eligible)

    def test_reviewer_rejection(self):
        outcome = evaluate_execution(process_success=True, agent_status="success",
                                     verification_success=True, review_approved=False)
        self.assertFalse(outcome.merge_eligible)
        self.assertIn("review did not approve", outcome.reasons)

    def test_signals_not_equivalent(self):
        # Each signal can be true while the verdict is still False.
        cases = [
            evaluate_execution(process_success=True, agent_status="failure",
                               verification_success=False, review_approved=False),
            evaluate_execution(process_success=False, agent_status="success",
                               verification_success=True, review_approved=True),
        ]
        for outcome in cases:
            self.assertFalse(outcome.merge_eligible)

    def test_agent_result_status_enum_accepted(self):
        outcome = evaluate_execution(process_success=True,
                                     agent_status=AgentResultStatus.SUCCESS,
                                     verification_success=True, review_approved=True)
        self.assertTrue(outcome.merge_eligible)


class StoredResultTests(unittest.TestCase):
    def test_runner_helper_shape(self):
        # The runner stores AgentResult.to_dict(); it must coerce back cleanly.
        parsed = parse_agent_result(json.dumps(_envelope()))
        stored = parsed.result.to_dict()
        restored = coerce_agent_result(stored)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.status, AgentResultStatus.SUCCESS)

    def test_agent_result_defaults(self):
        result = AgentResult()
        self.assertEqual(result.status, AgentResultStatus.UNKNOWN)
        self.assertEqual(result.schema_version, RESULT_SCHEMA_VERSION)
        data = result.to_dict()
        self.assertEqual(data["status"], "unknown")


class ReviewFindingRegressionTests(unittest.TestCase):
    """Copilot snapshot review 2026-09-15: 4 findings, all fixed."""

    def test_coerce_accepts_json_text_envelope(self):
        stored = json.dumps(_envelope())
        result = coerce_agent_result(stored)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, AgentResultStatus.SUCCESS)

    def test_coerce_accepts_json_text_legacy(self):
        self.assertEqual(
            coerce_agent_result(json.dumps({"changed": True})).metadata.get("legacy_payload"),
            {"changed": True},
        )
        self.assertEqual(
            coerce_agent_result(json.dumps([1, 2])).metadata.get("legacy_payload"),
            [1, 2],
        )
        self.assertIsNone(coerce_agent_result("   "))
        self.assertIsNone(coerce_agent_result("not json at all"))

    def test_process_success_string_false_not_mergeable(self):
        outcome = evaluate_execution(process_success="false",  # type: ignore[arg-type]
                                     agent_status="success",
                                     verification_success=True, review_approved=True)
        self.assertFalse(outcome.process_success)
        self.assertFalse(outcome.merge_eligible)

    def test_unsupported_version_downgrades_all_statuses(self):
        for status in ("success", "failure", "partial", "unknown"):
            parsed = parse_agent_result(json.dumps(_envelope(schema_version=99, status=status)))
            self.assertEqual(parsed.result.status, AgentResultStatus.PARTIAL)
            self.assertEqual(parsed.result.metadata.get("original_schema_version"), 99)

    def test_runner_helper_preserves_warnings_and_mode(self):
        from agentops.runner import _safe_structured_result
        stored = _safe_structured_result(json.dumps(_envelope(schema_version=99, status="success")))
        self.assertIsInstance(stored, dict)
        assert isinstance(stored, dict)
        self.assertTrue(any("schema_version" in warning for warning in stored["warnings"]))
        self.assertEqual(stored["metadata"].get("parse_mode"), "partial")
        self.assertEqual(stored["metadata"].get("original_schema_version"), 99)

    def test_from_dict_total_on_hostile_payload(self):
        from agentops.agent_result import agent_result_from_dict

        class Hostile:
            def __str__(self):
                raise RuntimeError("boom")
            def __repr__(self):
                raise RuntimeError("boom")

        # Must never raise, regardless of payload hostility.
        result, warnings = agent_result_from_dict({
            "status": Hostile(),
            "summary": Hostile(),
            "files_changed": [Hostile()],
            "schema_version": Hostile(),
        })
        self.assertEqual(result.status, AgentResultStatus.UNKNOWN)
        self.assertTrue(warnings)

        class ExplodingGet(dict):
            def get(self, key, default=None):
                raise RuntimeError("boom")

        # dict(ExplodingGet(...)) copies into a plain dict, so direct .get
        # hostility is neutralized at copy time; a non-mapping payload takes
        # the unparseable-payload path instead. Both must stay total.
        result2, warnings2 = agent_result_from_dict(12345)  # type: ignore[arg-type]
        self.assertEqual(result2.status, AgentResultStatus.UNKNOWN)
        self.assertTrue(warnings2)


if __name__ == "__main__":
    unittest.main()
