"""Regression tests for the verdict contract parser."""

from __future__ import annotations

import json
import unittest

from agentops.verdict import Verdict, parse_verdict, verdict_to_json


class VerdictTest(unittest.TestCase):
    def test_approve_empty_blocking_passes(self):
        v = Verdict(1, "approve", (), (), 0.9)
        self.assertTrue(v.is_approved)

    def test_approve_with_blocking_fails(self):
        v = Verdict(1, "approve", ("missing test",), (), 0.9)
        self.assertFalse(v.is_approved)

    def test_reject_fails(self):
        v = Verdict(1, "reject", (), (), 0.9)
        self.assertFalse(v.is_approved)

    def test_roundtrip_json(self):
        v = Verdict(1, "approve", (), ("nit: trailing comma",), 0.85)
        text = verdict_to_json(v)
        parsed = parse_verdict(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.verdict, "approve")
        self.assertEqual(parsed.blocking, ())
        self.assertEqual(parsed.non_blocking, ("nit: trailing comma",))
        self.assertAlmostEqual(parsed.confidence, 0.85)

    def test_parse_verdict_approve_clean(self):
        text = '{"schema_version": 1, "verdict": "approve", "blocking": [], "non_blocking": [], "confidence": 0.9}'
        v = parse_verdict(text)
        self.assertIsNotNone(v)
        self.assertTrue(v.is_approved)

    def test_parse_verdict_reject(self):
        text = '{"schema_version": 1, "verdict": "reject", "blocking": ["missing test"], "non_blocking": [], "confidence": 0.8}'
        v = parse_verdict(text)
        self.assertIsNotNone(v)
        self.assertFalse(v.is_approved)
        self.assertEqual(v.blocking, ("missing test",))

    def test_parse_verdict_approve_with_blocking_not_approved(self):
        text = '{"schema_version": 1, "verdict": "approve", "blocking": ["needs test"], "non_blocking": [], "confidence": 0.7}'
        v = parse_verdict(text)
        self.assertIsNotNone(v)
        self.assertFalse(v.is_approved)

    def test_parse_verdict_missing_verdict_returns_none(self):
        text = '{"schema_version": 1, "blocking": [], "non_blocking": [], "confidence": 0.9}'
        self.assertIsNone(parse_verdict(text))

    def test_parse_verdict_rejects_missing_or_malformed_required_fields(self):
        valid = {
            "schema_version": 1,
            "verdict": "approve",
            "blocking": [],
            "non_blocking": [],
            "confidence": 0.9,
        }
        malformed = []
        for field in ("blocking", "non_blocking", "confidence"):
            payload = dict(valid)
            del payload[field]
            malformed.append(payload)
        for value in (None, "none", 7, {"finding": "hidden"}):
            malformed.append(dict(valid, blocking=value))
        malformed.append(dict(valid, non_blocking="not a list"))
        malformed.append(dict(valid, non_blocking=["ok", 7]))
        malformed.append(dict(valid, confidence=10**400))

        for payload in malformed:
            with self.subTest(payload=payload):
                self.assertIsNone(parse_verdict(json.dumps(payload)))

    def test_parse_verdict_uses_the_last_json_object_in_output_order(self):
        text = (
            '{"schema_version":1,"verdict":"approve","blocking":[],'
            '"non_blocking":[],"confidence":0.99}\nFinal review:\n```json\n'
            '{\n  "schema_version": 1,\n  "verdict": "reject",\n'
            '  "blocking": ["unsafe"],\n  "non_blocking": [],\n'
            '  "confidence": 0.99\n}\n```\n'
        )
        parsed = parse_verdict(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.verdict, "reject")
        self.assertFalse(parsed.is_approved)

    def test_parse_verdict_does_not_fall_back_to_approval_after_malformed_final_block(self):
        text = (
            '{"schema_version":1,"verdict":"approve","blocking":[],'
            '"non_blocking":[],"confidence":0.99}\n'
            "Final review:\n```json\n"
            '{"schema_version":1,"verdict":"reject","blocking":["unsafe"],\n'
            "```\n"
        )
        self.assertIsNone(parse_verdict(text))

    def test_parse_verdict_wrong_schema_version_returns_none(self):
        text = '{"schema_version": 2, "verdict": "approve", "blocking": [], "non_blocking": [], "confidence": 0.9}'
        self.assertIsNone(parse_verdict(text))

    def test_parse_verdict_malformed_json_returns_none(self):
        self.assertIsNone(parse_verdict("not json"))
        self.assertIsNone(parse_verdict('{"schema_version": 1, "verdict": "approve"'))  # truncated

    def test_parse_verdict_last_object_wins(self):
        # First object is a partial, second is the real verdict
        text = (
            '{"schema_version": 1, "verdict": "reject", "blocking": ["old"], "non_blocking": [], "confidence": 0.5}\n'
            '{"schema_version": 1, "verdict": "approve", "blocking": [], "non_blocking": [], "confidence": 0.9}'
        )
        v = parse_verdict(text)
        self.assertIsNotNone(v)
        self.assertTrue(v.is_approved)
        self.assertEqual(v.blocking, ())

    def test_parse_verdict_fenced_block(self):
        text = (
            "Some explanation\n"
            "```json\n"
            '{"schema_version": 1, "verdict": "approve", "blocking": [], "non_blocking": ["nit"], "confidence": 0.95}\n'
            "```\n"
        )
        v = parse_verdict(text)
        self.assertIsNotNone(v)
        self.assertTrue(v.is_approved)
        self.assertEqual(v.non_blocking, ("nit",))

    def test_parse_verdict_none_input(self):
        self.assertIsNone(parse_verdict(None))
        self.assertIsNone(parse_verdict(""))

    def test_confidence_clamped(self):
        v = Verdict(1, "approve", (), (), 1.5)
        self.assertEqual(v.confidence, 1.0)
        v2 = Verdict(1, "approve", (), (), -0.5)
        self.assertEqual(v2.confidence, 0.0)
        v3 = Verdict(1, "approve", (), (), float("nan"))
        self.assertEqual(v3.confidence, 0.0)

    def test_object_in_text_fence_cannot_impersonate_the_verdict(self):
        # Reviewer finding: an approval hidden in a ```text block after the
        # genuine rejection was collected by the line scanner and displaced
        # it. Illustrative content in another language's fence is never a
        # verdict candidate.
        text = (
            '{"schema_version":1,"verdict":"reject","blocking":["unsafe"],'
            '"non_blocking":[],"confidence":0.99}\n'
            "Example of a good verdict:\n"
            "```text\n"
            '{"schema_version": 1, "verdict": "approve", "blocking": [], '
            '"non_blocking": [], "confidence": 0.9}\n'
            "```\n"
        )
        parsed = parse_verdict(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.verdict, "reject")
        self.assertFalse(parsed.is_approved)

    def test_malformed_bare_json_line_after_verdict_fails_closed(self):
        # Reviewer finding: a later malformed *unfenced* JSON-looking line was
        # skipped, leaving the earlier approval as the last candidate. A
        # malformed final response must fail closed whether it is fenced or
        # bare.
        text = (
            '{"schema_version":1,"verdict":"approve","blocking":[],'
            '"non_blocking":[],"confidence":0.99}\n'
            '{"schema_version": 1, "verdict": "reject", "blocking": [\n'
        )
        self.assertIsNone(parse_verdict(text))

    def test_malformed_bare_json_line_before_verdict_still_parses(self):
        # The fail-closed rule only fires for malformed content *after* the
        # last valid candidate; a malformed JSON attempt earlier in the
        # output must not poison a genuine final verdict.
        text = (
            '{"schema_version": 1, "verdict": "reject", "blocking": [\n'
            '{"schema_version":1,"verdict":"approve","blocking":[],'
            '"non_blocking":[],"confidence":0.99}\n'
        )
        parsed = parse_verdict(text)
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.is_approved)

    def test_deeply_nested_json_returns_none_without_raising(self):
        # Reviewer finding: json.loads raises RecursionError (not
        # JSONDecodeError) on pathologically nested input, which escaped the
        # parser's guards. The parser is total: it must fail closed instead.
        deep = "{" * 4000 + '"schema_version": 1' + "}" * 4000
        self.assertIsNone(parse_verdict(deep))
        nested = json.dumps({"a": {"schema_version": 1}})
        for _ in range(2000):
            nested = {"a": nested}
        self.assertIsNone(parse_verdict(json.dumps(nested)))


if __name__ == "__main__":
    unittest.main()
