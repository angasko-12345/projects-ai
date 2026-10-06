"""Tests for the evidence checker.

The checker's value is entirely in what it rejects, so most of these tests
corrupt a known-good document and assert the checker notices. If a check cannot
fail, it is not a check.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import (  # noqa: E402
    COUNT_FREE_FILES,
    EVIDENCE_REFERENCE,
    PRODUCTS,
    REPO_ROOT,
    check,
    check_documents,
    check_schema,
    check_staleness,
    parse_unittest_summary,
)

TEMPLATE = {
    "schema_version": 1,
    "generated_at": "2026-01-01T00:00:00+00:00",
    "commit": "0" * 40,
    "python": "3.11.0",
    "platform": "Linux 5.0 (x86_64)",
    "clean_checkout": True,
    "products": [
        {
            "product": entry["product"],
            "directory": entry["directory"],
            "command": ["python", *entry["command"]],
            "total": 100,
            "skipped": 1,
            "failures": 0,
            "errors": 0,
            "result": "pass",
            "summary": ["Ran 100 tests in 1.0s", "OK (skipped=1)"],
        }
        for entry in PRODUCTS
    ],
}


def good_document() -> dict:
    return copy.deepcopy(TEMPLATE)


class SummaryParsingTests(unittest.TestCase):
    """The generator must read counts, not infer them."""

    def test_parses_a_clean_run(self):
        record = parse_unittest_summary(
            "Ran 624 tests in 73.263s\n\nOK (skipped=4)\n")
        self.assertEqual(record["total"], 624)
        self.assertEqual(record["skipped"], 4)
        self.assertEqual(record["result"], "pass")

    def test_parses_a_run_with_no_skips(self):
        record = parse_unittest_summary("Ran 10 tests in 0.1s\n\nOK\n")
        self.assertEqual(record["skipped"], 0)
        self.assertEqual(record["result"], "pass")

    def test_parses_failures_and_errors(self):
        record = parse_unittest_summary(
            "Ran 12 tests in 1.0s\n\nFAILED (failures=2, errors=1, skipped=3)\n")
        self.assertEqual((record["failures"], record["errors"], record["skipped"]),
                         (2, 1, 3))
        self.assertEqual(record["result"], "fail")

    def test_unparseable_output_yields_nothing(self):
        # A run that never printed a summary is not a pass with zero tests; it is
        # an unknown run, and the generator must record it as a failure.
        self.assertEqual(parse_unittest_summary("Traceback ..."), {})


class SchemaTests(unittest.TestCase):
    def test_the_template_is_accepted(self):
        self.assertEqual(check_schema(good_document()), [])

    def test_a_hand_edited_count_is_caught_by_the_recorded_summary(self):
        document = good_document()
        document["products"][0]["total"] = 999
        self.assertTrue(
            any("the recorded runner summary says" in p
                for p in check_schema(document)), check_schema(document))

    def test_a_self_consistent_forgery_is_the_documented_limit(self):
        # Editing the count AND the summary to match defeats the summary check:
        # nothing in the file distinguishes that from a measurement. This test
        # exists so the limit stays deliberate. What still holds is staleness --
        # a forged number cannot outlive the code it describes, because the next
        # commit under a measured product invalidates the whole record -- and
        # `generate.py` is the only way to produce a real one.
        document = good_document()
        document["products"][0]["total"] = 999
        document["products"][0]["summary"] = [
            f"Ran 999 tests in 1.0s", "OK (skipped=1)"]
        self.assertEqual(check_schema(document), [])

    def test_a_skipped_count_beyond_the_total_is_rejected(self):
        document = good_document()
        document["products"][0]["skipped"] = document["products"][0]["total"] + 1
        self.assertTrue(any("exceeds total" in p for p in check_schema(document)))

    def test_pass_with_failures_is_rejected(self):
        document = good_document()
        document["products"][0]["failures"] = 1
        self.assertTrue(any("recorded pass with" in p
                            for p in check_schema(document)))

    def test_every_test_skipped_is_not_a_pass(self):
        document = good_document()
        document["products"][0]["skipped"] = document["products"][0]["total"]
        self.assertTrue(any("never a pass" in p for p in check_schema(document)))

    def test_empty_run_is_not_a_pass(self):
        document = good_document()
        document["products"][0]["total"] = 0
        self.assertTrue(any("empty run is never a pass" in p
                            for p in check_schema(document)))

    def test_missing_product_is_rejected(self):
        document = good_document()
        document["products"].pop()
        self.assertTrue(any("has no evidence record" in p
                            for p in check_schema(document)))

    def test_unknown_product_is_rejected(self):
        document = good_document()
        document["products"][0]["product"] = "ghost-product"
        self.assertTrue(any("not a product of this repository" in p
                            for p in check_schema(document)))

    def test_duplicate_product_is_rejected(self):
        document = good_document()
        document["products"].append(copy.deepcopy(document["products"][0]))
        self.assertTrue(any("recorded more than once" in p
                            for p in check_schema(document)))

    def test_wrong_schema_version_is_rejected(self):
        document = good_document()
        document["schema_version"] = 99
        self.assertTrue(any("schema_version" in p for p in check_schema(document)))

    def test_truncated_sha_is_rejected(self):
        document = good_document()
        document["commit"] = "abc1234"
        self.assertTrue(any("40-character SHA" in p for p in check_schema(document)))

    def test_missing_required_field_is_rejected(self):
        document = good_document()
        del document["commit"]
        self.assertTrue(any("missing required field 'commit'" in p
                            for p in check_schema(document)))

    def test_non_boolean_clean_checkout_is_rejected(self):
        document = good_document()
        document["clean_checkout"] = "yes"
        self.assertTrue(any("clean_checkout must be a boolean" in p
                            for p in check_schema(document)))


class StalenessTests(unittest.TestCase):
    def test_evidence_at_head_is_current(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, check=True).stdout.strip()
        document = good_document()
        document["commit"] = head
        self.assertEqual(check_staleness(document), [])

    def test_unresolvable_commit_is_rejected(self):
        document = good_document()
        document["commit"] = "f" * 40
        self.assertTrue(any("cannot resolve" in p
                            for p in check_staleness(document)))

    def test_dirty_generation_is_rejected(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, check=True).stdout.strip()
        document = good_document()
        document["commit"] = head
        document["clean_checkout"] = False
        self.assertTrue(any("dirty working tree" in p
                            for p in check_staleness(document)))

    def test_product_change_after_the_measurement_is_stale(self):
        # A commit that only touches documentation must NOT invalidate evidence,
        # so build the two cases from real history: HEAD itself, and a synthetic
        # document claiming a measurement at an older commit that did touch a
        # product.
        commits = subprocess.run(
            ["git", "log", "--format=%H", "--", "agentops"], cwd=str(REPO_ROOT),
            capture_output=True, text=True, check=True).stdout.split()
        self.assertGreater(len(commits), 1, "expected product history to compare")
        document = good_document()
        document["commit"] = commits[-1]
        problems = check_staleness(document)
        self.assertTrue(any("stale" in p for p in problems), problems)


class DocumentClaimTests(unittest.TestCase):
    """Instruction files must point at the evidence, not compete with it."""

    def test_repository_documents_are_current(self):
        document = good_document()
        self.assertEqual(check_documents(document), [])

    def test_a_count_in_an_instruction_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in COUNT_FREE_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{EVIDENCE_REFERENCE}\n", encoding="utf-8")
            target = root / COUNT_FREE_FILES[0]
            target.write_text(
                f"{EVIDENCE_REFERENCE}\n- 512 tests, 4 skipped by environment, OK\n",
                encoding="utf-8")
            problems = check_documents(good_document(), root)
            self.assertTrue(any("states a measured count" in p for p in problems),
                            problems)

    def test_a_missing_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in COUNT_FREE_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# no evidence reference here\n", encoding="utf-8")
            problems = check_documents(good_document(), root)
            self.assertTrue(any("does not reference" in p for p in problems),
                            problems)


class EndToEndTests(unittest.TestCase):
    """The checker as a command, on the real evidence file and on a corrupted copy."""

    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(Path(__file__).with_name("check.py")), *extra],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)

    def _recorded(self) -> dict:
        return json.loads(
            (REPO_ROOT / ".agents" / "evidence" / "verification.json")
            .read_text(encoding="utf-8"))

    def _run_corrupted(self, document: dict) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            corrupted = Path(directory) / "verification.json"
            corrupted.write_text(json.dumps(document), encoding="utf-8")
            return self._run("--evidence", str(corrupted))

    def test_the_committed_evidence_file_passes(self):
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_deliberately_corrupted_count_fails(self):
        # The negative test performed by hand, kept here so it cannot rot:
        # edit one recorded number, leave the runner summary it came from, and
        # require the checker to name the disagreement.
        document = self._recorded()
        before = document["products"][0]["total"]
        document["products"][0]["total"] = 999
        completed = self._run_corrupted(document)
        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            f"total is recorded as 999 but the recorded runner summary says {before}",
            completed.stderr)

    def test_deliberately_corrupted_count_fails(self):
        document = self._recorded()
        document["products"][0]["total"] = 0
        completed = self._run_corrupted(document)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("empty run is never a pass", completed.stderr)

    def test_deliberately_corrupted_dirty_flag_fails(self):
        document = self._recorded()
        document["clean_checkout"] = False
        completed = self._run_corrupted(document)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("dirty working tree", completed.stderr)

    def test_deliberately_corrupted_commit_fails(self):
        document = self._recorded()
        document["commit"] = "0" * 40
        completed = self._run_corrupted(document)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("cannot resolve", completed.stderr)

    def test_a_contradictory_pass_is_rejected(self):
        document = self._recorded()
        document["products"][0]["failures"] = 3
        document["products"][0]["result"] = "pass"
        completed = self._run_corrupted(document)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("recorded pass with failures=3", completed.stderr)

    def test_missing_evidence_file_fails(self):
        completed = self._run("--evidence", str(REPO_ROOT / "no-such-file.json"))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("does not exist", completed.stderr)

    def test_invalid_json_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "verification.json"
            broken.write_text("{not json", encoding="utf-8")
            completed = self._run("--evidence", str(broken))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("not valid JSON", completed.stderr)


if __name__ == "__main__":
    unittest.main()