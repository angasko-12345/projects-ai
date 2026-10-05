"""Tests for training.telemetry (stdlib only, no torch)."""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import csv
import tempfile
import unittest
from pathlib import Path

from training.telemetry import FIELDNAMES, PPOUpdateCSVWriter


def _record(update=1):
    return {
        "timesteps": 128 * update,
        "fps": 100.0 + update,
        "policy_loss": -0.1 * update,
        "value_loss": 0.5 + update,
        "entropy": 1.0,
        "mean_reward": 0.25 * update,
        "mean_ext_reward": 0.2 * update,
        "mean_int_reward": 0.05 * update,
        "predictor_loss": 0.01 * update,
        "pixel_change": 0.001 * update,
        "episodes": update,
        "upd_episodes": 1,
        "upd_mean_length": 64.0,
        "upd_terminated": 1,
        "upd_truncated": 0,
        "upd_mean_ext": 0.5,
        "upd_mean_int": 0.1,
        "upd_mean_total": 0.6,
    }


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class TestPPOUpdateCSVWriter(unittest.TestCase):
    def test_header_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            PPOUpdateCSVWriter(Path(tmp) / "updates.csv")
            with open(Path(tmp) / "updates.csv", newline="", encoding="utf-8") as fh:
                self.assertEqual(next(csv.reader(fh)), list(FIELDNAMES))

    def test_one_row_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "updates.csv"
            PPOUpdateCSVWriter(path).append(_record())
            rows = _rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(int(rows[0]["timesteps"]), 128)
            self.assertAlmostEqual(float(rows[0]["policy_loss"]), -0.1)

    def test_multiple_rows_preserve_earlier_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "updates.csv"
            writer = PPOUpdateCSVWriter(path)
            for update in (1, 2, 3):
                writer.append(_record(update))
            rows = _rows(path)
            self.assertEqual(len(rows), 3)
            self.assertEqual([int(r["timesteps"]) for r in rows], [128, 256, 384])
            # Exactly one header line above the three data rows.
            with open(path, newline="", encoding="utf-8") as fh:
                self.assertEqual(len(list(csv.reader(fh))), 4)

    def test_reopen_does_not_duplicate_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "updates.csv"
            PPOUpdateCSVWriter(path).append(_record(1))
            PPOUpdateCSVWriter(path).append(_record(2))
            with open(path, newline="", encoding="utf-8") as fh:
                lines = list(csv.reader(fh))
            self.assertEqual(lines[0], list(FIELDNAMES))
            self.assertEqual(len(lines), 3)

    def test_zero_updates_writes_header_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "updates.csv"
            writer = PPOUpdateCSVWriter(path)
            history = {key: [] for key in FIELDNAMES}
            self.assertEqual(writer.write_history(history), 0)
            self.assertEqual(_rows(path), [])

    def test_write_history_appends_all_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "updates.csv"
            writer = PPOUpdateCSVWriter(path)
            history = {key: [] for key in FIELDNAMES}
            for update in (1, 2):
                rec = _record(update)
                for key in FIELDNAMES:
                    history[key].append(rec[key])
            self.assertEqual(writer.write_history(history), 2)
            rows = _rows(path)
            self.assertEqual([int(r["timesteps"]) for r in rows], [128, 256])

    def test_creates_temporary_nested_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "new" / "nested" / "updates.csv"
            PPOUpdateCSVWriter(path).append(_record())
            self.assertTrue(path.exists())
            self.assertEqual(len(_rows(path)), 1)


if __name__ == "__main__":
    unittest.main()
