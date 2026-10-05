"""Tests for training.ledger (stdlib only, no torch, no tracked files).

Schemas are covered with inline fixtures modeled on the artifacts tracked
in experiments/ (toy, old-external, new-external, cadence matrix, ad-hoc
comparison), so these tests make no filesystem or CWD assumptions beyond
absolute temporary paths.
"""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import copy
import tempfile
import unittest
from pathlib import Path

from training.ledger import FIELDS, LedgerError, load_artifact, normalize_artifact


def _toy():
    return {
        "config_file": "experiments\\exp_toy_ppo_01.yaml",
        "timestamp_utc": "2026-09-23T12:55:06.829252+00:00",
        "initial_mean_episode_reward": -0.4,
        "final_train_rolling_mean_reward": -0.22,
        "final_eval_mean_reward": -0.1,
        "baseline_eval": {"episodes": 20, "mean_reward": -0.4},
        "final_eval": {"episodes": 20, "mean_reward": -0.1},
        "training_seconds": 285.1,
        "training_steps": 30080,
        "curiosity_enabled": False,
        "history_tail": {"timesteps": [30080]},
    }


def _external_old():
    return {
        "config_file": "experiments\\exp_external_pong_01.yaml",
        "timestamp_utc": "2026-09-26T07:04:24.271623+00:00",
        "seed": 0,
        "ppo_config": {"seed": 0, "total_timesteps": 1024},
        "env_config": {
            "type": "external",
            "actions": {"table": [
                {"name": "NOOP", "kind": "noop"},
                {"name": "PRESS_LEFT", "kind": "key", "hold_ms": 60},
                {"name": "PRESS_RIGHT", "kind": "key", "hold_ms": 60},
            ]},
        },
        "initial_mean_episode_reward": -0.125,
        "final_eval_mean_reward": -0.125,
        "training_steps": 1024,
        "decision_fps": 6.681950288080425,
    }


def _external_new():
    data = _external_old()
    data["training_updates"] = 8
    data["observation_timing"] = {
        "decision_period_ms_measured": 149.5,
        "hold_ms_by_action": {"NOOP": 0.0, "PRESS_LEFT": 60.0, "PRESS_RIGHT": 60.0},
    }
    return data


def _matrix():
    def cell(name, period, hold, base, final):
        return {
            "name": name,
            "decision_period_ms": period,
            "hold_ms": hold,
            "baseline_eval": {"mean_reward": base},
            "final_eval": {"mean_reward": final},
            "training_steps": 16384,
            "training_updates": 128,
        }
    return {
        "config_file": "experiments/exp_cadence_synthetic.yaml",
        "timestamp_utc": "2026-10-05T03:56:38.300589+00:00",
        "kind": "synthetic-cadence-matrix",
        "ppo_config": {"seed": 0},
        "cells": [
            cell("current", 147.6, 60.0, -0.7, -0.6),
            cell("fast", 16.667, 16.667, -0.5, -0.5),
        ],
    }


def _compare():
    return {
        "timestamp_utc": "2026-09-25T12:27:00Z",
        "baseline_seeds": [0, 1, 2, 3, 4, 5],
        "untrained_baseline": {"mean_reward": 11.83},
        "trained": {"mean_reward": 10.83},
    }


def _write_tmp(tmp, name, text):
    path = Path(tmp) / name
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestLedgerSchemas(unittest.TestCase):
    def test_toy_schema(self):
        (rec,) = normalize_artifact(_toy())
        self.assertEqual(rec["name"], "exp_toy_ppo_01")
        self.assertEqual(rec["kind"], "toy")
        self.assertEqual(rec["timestamp_utc"], "2026-09-23T12:55:06.829252+00:00")
        self.assertIsNone(rec["seed"])  # old artifact records no seed
        self.assertEqual(rec["training_steps"], 30080)
        self.assertIsNone(rec["training_updates"])
        self.assertEqual(rec["baseline_mean_reward"], -0.4)
        self.assertEqual(rec["final_mean_reward"], -0.1)  # eval, not rolling
        self.assertAlmostEqual(rec["improvement"], 0.3)
        self.assertIsNone(rec["decision_period_ms"])
        self.assertIsNone(rec["hold_ms"])

    def test_old_external_schema_derives_cadence(self):
        (rec,) = normalize_artifact(_external_old())
        self.assertEqual(rec["name"], "exp_external_pong_01")
        self.assertEqual(rec["kind"], "external")
        self.assertEqual(rec["seed"], 0)
        self.assertEqual(rec["training_steps"], 1024)
        self.assertAlmostEqual(rec["decision_period_ms"], 1000.0 / 6.681950288080425)
        self.assertEqual(rec["hold_ms"], 60.0)
        self.assertEqual(rec["improvement"], 0.0)

    def test_new_external_shape_prefers_measured_cadence(self):
        (rec,) = normalize_artifact(_external_new())
        self.assertEqual(rec["decision_period_ms"], 149.5)
        self.assertEqual(rec["hold_ms"], 60.0)
        self.assertEqual(rec["training_updates"], 8)

    def test_matrix_yields_one_record_per_cell(self):
        recs = normalize_artifact(_matrix())
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["name"], "exp_cadence_synthetic:current")
        self.assertEqual(recs[1]["name"], "exp_cadence_synthetic:fast")
        for rec in recs:
            self.assertEqual(rec["kind"], "synthetic-cadence-matrix")
            self.assertEqual(rec["timestamp_utc"], "2026-10-05T03:56:38.300589+00:00")
            self.assertEqual(rec["seed"], 0)
            self.assertEqual(rec["training_steps"], 16384)
            self.assertEqual(rec["training_updates"], 128)
        self.assertEqual(
            [(r["decision_period_ms"], r["hold_ms"]) for r in recs],
            [(147.6, 60.0), (16.667, 16.667)])
        self.assertEqual([r["baseline_mean_reward"] for r in recs], [-0.7, -0.5])
        self.assertEqual([r["improvement"] for r in recs], [0.09999999999999998, 0.0])

    def test_adhoc_comparison_uses_source_name_and_no_seed(self):
        (rec,) = normalize_artifact(_compare(), source="exp_external_pong_compare01_results.json")
        self.assertEqual(rec["name"], "exp_external_pong_compare01_results")
        self.assertEqual(rec["kind"], "adhoc-comparison")
        self.assertIsNone(rec["seed"])  # seed lists are not a seed
        self.assertEqual(rec["baseline_mean_reward"], 11.83)
        self.assertEqual(rec["final_mean_reward"], 10.83)
        self.assertEqual(rec["improvement"], -1.0)
        self.assertIsNone(rec["training_steps"])

    def test_explicit_kind_wins_over_structure(self):
        data = _toy()
        data["kind"] = "custom-pilot"
        (rec,) = normalize_artifact(data)
        self.assertEqual(rec["kind"], "custom-pilot")

    def test_record_has_exactly_the_documented_fields(self):
        (rec,) = normalize_artifact(_toy())
        self.assertEqual(list(rec), list(FIELDS))


class TestLedgerGaps(unittest.TestCase):
    def test_empty_object_yields_unknown_all_none(self):
        (rec,) = normalize_artifact({})
        self.assertEqual(rec["kind"], "unknown")
        self.assertIsNone(rec["name"])
        for field in FIELDS:
            if field != "kind":
                self.assertIsNone(rec[field], field)

    def test_missing_optionals_stay_none(self):
        (rec,) = normalize_artifact({"config_file": "experiments/exp_x.yaml"})
        self.assertEqual(rec["name"], "exp_x")
        self.assertEqual(rec["kind"], "unknown")
        self.assertIsNone(rec["baseline_mean_reward"])
        self.assertIsNone(rec["improvement"])

    def test_improvement_needs_both_values(self):
        base = {"config_file": "x.yaml", "initial_mean_episode_reward": 1.0}
        (rec,) = normalize_artifact(base)
        self.assertIsNone(rec["improvement"])
        final = {"config_file": "x.yaml", "final_eval_mean_reward": 2.0,
                 "final_eval": {"mean_reward": 2.0}}
        (rec,) = normalize_artifact(final)
        self.assertIsNone(rec["improvement"])

    def test_ambiguous_hold_is_none(self):
        data = _external_old()
        table = data["env_config"]["actions"]["table"]
        table[2]["hold_ms"] = 30
        (rec,) = normalize_artifact(data)
        self.assertIsNone(rec["hold_ms"])

    def test_nonpositive_fps_gives_no_period(self):
        for fps in (0, 0.0, -5.0):
            data = _external_old()
            data["decision_fps"] = fps
            (rec,) = normalize_artifact(data)
            self.assertIsNone(rec["decision_period_ms"])

    def test_empty_matrix_yields_no_records(self):
        data = _matrix()
        data["cells"] = []
        self.assertEqual(normalize_artifact(data), [])

    def test_input_is_never_mutated(self):
        for data in (_toy(), _external_old(), _external_new(), _matrix(), _compare(), {}):
            snapshot = copy.deepcopy(data)
            normalize_artifact(data, source="s.json")
            self.assertEqual(data, snapshot)

    def test_normalization_is_deterministic(self):
        data = _matrix()
        self.assertEqual(normalize_artifact(data), normalize_artifact(data))


class TestLedgerNumerics(unittest.TestCase):
    def test_numeric_values_keep_their_types(self):
        (rec,) = normalize_artifact(_toy())
        self.assertIs(type(rec["training_steps"]), int)
        self.assertIs(type(rec["baseline_mean_reward"]), float)

    def test_booleans_are_not_numbers(self):
        data = _toy()
        data["seed"] = True
        data["training_steps"] = True
        data["initial_mean_episode_reward"] = True
        data["baseline_eval"] = {"mean_reward": True}
        (rec,) = normalize_artifact(data)
        self.assertIsNone(rec["seed"])
        self.assertIsNone(rec["training_steps"])
        self.assertIsNone(rec["baseline_mean_reward"])
        self.assertIsNone(rec["improvement"])

    def test_seed_lists_and_strings_are_not_seeds(self):
        for seed in ([0, 1], "0", 1.5):
            (rec,) = normalize_artifact({"seed": seed})
            self.assertIsNone(rec["seed"])


class TestLedgerErrors(unittest.TestCase):
    def test_wrong_top_level_type_raises(self):
        for bad in ([], [1], "x", 42, 3.5, None, True):
            with self.assertRaises(LedgerError, msg=repr(bad)):
                normalize_artifact(bad)

    def test_malformed_json_raises_with_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_tmp(tmp, "bad_results.json", "{not json")
            with self.assertRaises(LedgerError) as ctx:
                load_artifact(path)
            self.assertIn("bad_results.json", str(ctx.exception))

    def test_wrong_typed_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_tmp(tmp, "list_results.json", "[1, 2]")
            with self.assertRaises(LedgerError):
                load_artifact(path)

    def test_load_round_trip_matches_normalize(self):
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            data = _external_old()
            path = _write_tmp(tmp, "r.json", _json.dumps(data))
            self.assertEqual(load_artifact(path), normalize_artifact(data, source=path))


if __name__ == "__main__":
    unittest.main()
