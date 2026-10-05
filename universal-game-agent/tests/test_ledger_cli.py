"""Tests for the training.ledger CLI (stdlib only, no torch).

All fixtures are inline dicts written to absolute temporary paths: nothing
here depends on the tracked experiment artifacts or on the current
working directory.
"""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from training import ledger
from training.ledger import TABLE_COLUMNS, main


def _toy(name="exp_toy", base=-0.4, final=-0.1, seed=None):
    data = {
        "config_file": f"experiments\\{name}.yaml",
        "timestamp_utc": "2026-09-23T12:55:06+00:00",
        "initial_mean_episode_reward": base,
        "final_eval_mean_reward": final,
        "final_eval": {"mean_reward": final},
        "training_steps": 30080,
    }
    if seed is not None:
        data["seed"] = seed
    return data


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _write(tmp, name, data):
    path = Path(tmp) / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


class TestLedgerCLI(unittest.TestCase):
    def test_explicit_file_input(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "a_results.json", _toy("exp_a"))
            code, out, err = _run([path])
            self.assertEqual(code, 0)
            self.assertIn("exp_a", out)
            self.assertEqual(err, "")

    def test_explicit_directory_input(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "a_results.json", _toy("exp_a"))
            _write(tmp, "b_results.json", _toy("exp_b"))
            _write(tmp, "other.json", _toy("exp_other"))  # wrong suffix: ignored
            code, out, _ = _run([tmp])
            self.assertEqual(code, 0)
            self.assertIn("exp_a", out)
            self.assertIn("exp_b", out)
            self.assertNotIn("exp_other", out)

    def test_default_discovery(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "d_results.json", _toy("exp_d"))
            with patch.object(ledger, "_default_dir", return_value=Path(tmp)):
                code, out, _ = _run([])
            self.assertEqual(code, 0)
            self.assertIn("exp_d", out)

    def test_table_output_shape(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "a_results.json", _toy("exp_a"))
            code, out, _ = _run([path])
            self.assertEqual(code, 0)
            lines = out.splitlines()
            self.assertEqual(len(lines), 3)  # header, separator, one row
            for column in TABLE_COLUMNS:
                self.assertIn(column, lines[0])
            self.assertIn("n/a", lines[2])  # toy seed is unavailable

    def test_json_output(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "a_results.json", _toy("exp_a", seed=3))
            code, out, _ = _run(["--json", path])
            self.assertEqual(code, 0)
            (rec,) = json.loads(out)
            self.assertEqual(rec["name"], "exp_a")
            self.assertEqual(rec["seed"], 3)
            self.assertIsNone(rec["training_updates"])
            self.assertAlmostEqual(rec["improvement"], 0.3)

    def test_deterministic_ordering(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            zb = _write(tmp, "z_results.json", _toy("exp_z"))
            ab = _write(tmp, "a_results.json", _toy("exp_a"))
            code, out, _ = _run([zb, ab])  # reversed on purpose
            self.assertEqual(code, 0)
            self.assertLess(out.index("exp_a"), out.index("exp_z"))

    def test_missing_fields_displayed_not_faked(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "m_results.json", {"config_file": "x.yaml"})
            code, out, _ = _run([path])
            self.assertEqual(code, 0)
            self.assertIn("n/a", out)
            self.assertNotIn("None", out)

    def test_malformed_artifact_continues_with_diagnostic(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            good = _write(tmp, "g_results.json", _toy("exp_g"))
            bad = Path(tmp) / "b_results.json"
            bad.write_text("{nope", encoding="utf-8")
            code, out, err = _run([str(bad), good])
            self.assertEqual(code, 0)  # valid artifact still compared
            self.assertIn("exp_g", out)
            self.assertIn("b_results.json", err)

    def test_all_malformed_fails(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "b_results.json"
            bad.write_text("{nope", encoding="utf-8")
            code, out, err = _run([str(bad)])
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("b_results.json", err)

    def test_missing_explicit_file_is_diagnostic(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "gone_results.json")
            code, out, err = _run([missing])
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("gone_results.json", err)

    def test_empty_directory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = _run([tmp])
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("no ", err)

    def test_empty_discovery_dir(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ledger, "_default_dir", return_value=Path(tmp) / "absent"):
                code, out, err = _run([])
            self.assertEqual(code, 1)
            self.assertEqual(out, "")

    def test_artifacts_are_not_modified(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "a_results.json", _toy("exp_a"))
            before = Path(path).read_bytes()
            _run([tmp])
            _run(["--json", path])
            self.assertEqual(Path(path).read_bytes(), before)

    def test_cwd_independence(self):
        import tempfile

        previous = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "a_results.json", _toy("exp_a"))
            os.chdir(tmp)
            try:
                code, out, _ = _run([path])
            finally:
                os.chdir(previous)
            self.assertEqual(code, 0)
            self.assertIn("exp_a", out)

    def test_bad_flag_exits_two(self):
        with self.assertRaises(SystemExit) as ctx:
            _run(["--nope"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
