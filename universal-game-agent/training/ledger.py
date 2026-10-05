"""Normalize experiment result artifacts into comparable records.

Stdlib only. Pure: no PPO, no environment, no CLI, no filesystem access
except through :func:`load_artifact`. Inputs are never mutated.

One artifact yields one record per experiment it contains: single-run
shapes (toy, external, ad-hoc comparison) yield exactly one record, while
a cadence-matrix artifact yields one record per cell. Every field is either
honestly extracted from the artifact or the explicit unavailable value
``None`` -- nothing is inferred from filenames, prose, or sibling files.

Field provenance:

* ``name`` -- stem of ``config_file`` (both ``/`` and ``\\`` separators);
  matrix cells append ``:<cell name>``. Falls back to the ``source``
  filename stem, else ``None``. The ad-hoc compare artifact records no
  config, so its name comes from the source filename alone.
* ``kind`` -- the artifact's own ``kind`` string when present; otherwise
  classified by structure (``cells`` list, external ``env_config``,
  ``untrained_baseline``/``trained`` pair, ``baseline_eval``/``final_eval``
  pair); ``"unknown"`` when nothing matches.
* ``timestamp_utc`` -- top-level string, verbatim, else ``None``.
* ``seed`` -- top-level ``seed``, else ``ppo_config.seed``. Integer only
  (booleans and seed lists are not seeds). Old toy artifacts record no
  seed, so they normalize to ``None`` rather than borrowing one.
* ``training_steps`` / ``training_updates`` -- top-level integers (matrix
  cells carry their own). Old artifacts predate ``training_updates``.
* ``baseline_mean_reward`` -- ``initial_mean_episode_reward``, else the
  ``baseline_eval`` mean; matrix cells and the ad-hoc comparison read
  their own eval summaries instead.
* ``final_mean_reward`` -- post-training *eval* mean only
  (``final_eval_mean_reward``, else ``final_eval`` mean; ad-hoc and matrix
  equivalents). Rolling-train means are deliberately excluded: they are a
  different quantity and must not share a comparison column.
* ``improvement`` -- ``final - baseline`` when both are numeric, else
  ``None``.
* ``decision_period_ms`` -- cadence-cell value; else the new-shape
  ``observation_timing.decision_period_ms_measured``; else derived as
  ``1000 / decision_fps`` from a positive ``decision_fps``. Toy and ad-hoc
  artifacts have no decision cadence, so ``None``.
* ``hold_ms`` -- cadence-cell value; else the single distinct nonzero
  hold across the action table (old shape) or ``hold_ms_by_action``
  (new shape). Ambiguous (per-action holds differ) means ``None``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import PurePosixPath

#: Normalized record columns, in order. Append-only contract: never reorder
#: or remove; add new trailing columns only.
FIELDS: tuple[str, ...] = (
    "name",
    "kind",
    "timestamp_utc",
    "seed",
    "training_steps",
    "training_updates",
    "baseline_mean_reward",
    "final_mean_reward",
    "improvement",
    "decision_period_ms",
    "hold_ms",
)


class LedgerError(ValueError):
    """A result artifact cannot be loaded or normalized."""


def _num(value):
    """A JSON number (bool is not a number here), else None. Passed through."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _count(value):
    """A JSON integer count (bool is not a count), else None."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _stem(value):
    """Filename stem tolerant of both path separators; None when absent."""
    if not isinstance(value, str) or not value:
        return None
    return PurePosixPath(value.replace("\\", "/")).stem or None


def _single_or_none(values):
    """The single distinct value, or None when zero or ambiguous."""
    distinct = sorted({float(v) for v in values})
    return distinct[0] if len(distinct) == 1 else None


def _hold_ms(data):
    """One honest hold duration, or None (see module docstring)."""
    cells_hold = _num(data.get("hold_ms"))
    if cells_hold is not None:
        return cells_hold
    env = _mapping(data.get("env_config"))
    table = env.get("actions", {}).get("table", []) if isinstance(env.get("actions"), Mapping) else []
    holds = [row.get("hold_ms") for row in table if isinstance(row, Mapping) and row.get("hold_ms") not in (None, 0, 0.0)]
    timing = _mapping(data.get("observation_timing"))
    by_action = timing.get("hold_ms_by_action")
    if isinstance(by_action, Mapping):
        holds += [v for v in by_action.values() if _num(v) not in (None, 0.0)]
    return _single_or_none([h for h in holds if _num(h) is not None]) if holds else None


def _decision_period_ms(data):
    period = _num(data.get("decision_period_ms"))
    if period is not None:
        return period
    measured = _mapping(data.get("observation_timing")).get("decision_period_ms_measured")
    if _num(measured) is not None:
        return measured
    fps = _num(data.get("decision_fps"))
    if fps is not None and fps > 0:
        return 1000.0 / fps
    return None


def _seed(data):
    seed = _count(data.get("seed"))
    if seed is not None:
        return seed
    return _count(_mapping(data.get("ppo_config")).get("seed"))


def _eval_mean(node):
    return _num(_mapping(node).get("mean_reward"))


def _flat_rewards(data):
    """(baseline, final) from the single-run eval conventions."""
    baseline = _num(data.get("initial_mean_episode_reward"))
    if baseline is None:
        baseline = _eval_mean(data.get("baseline_eval"))
    final = _num(data.get("final_eval_mean_reward"))
    if final is None:
        final = _eval_mean(data.get("final_eval"))
    return baseline, final


def _record(name, kind, data, baseline, final, timestamp):
    improvement = None
    if baseline is not None and final is not None:
        improvement = final - baseline
    return {
        "name": name,
        "kind": kind,
        "timestamp_utc": timestamp if isinstance(timestamp, str) else None,
        "seed": _seed(data),
        "training_steps": _count(data.get("training_steps")),
        "training_updates": _count(data.get("training_updates")),
        "baseline_mean_reward": baseline,
        "final_mean_reward": final,
        "improvement": improvement,
        "decision_period_ms": _decision_period_ms(data),
        "hold_ms": _hold_ms(data),
    }


def _classify(data):
    kind = data.get("kind")
    if isinstance(kind, str) and kind:
        return kind
    cells = data.get("cells")
    if isinstance(cells, list) and cells:
        return "synthetic-cadence-matrix"
    if _mapping(data.get("env_config")).get("type") == "external":
        return "external"
    if isinstance(data.get("untrained_baseline"), Mapping) and isinstance(data.get("trained"), Mapping):
        return "adhoc-comparison"
    if isinstance(data.get("baseline_eval"), Mapping) and isinstance(data.get("final_eval"), Mapping):
        return "toy"
    return "unknown"


def _cell_record(stem, kind, data, cell, timestamp):
    cell = _mapping(cell)
    name = cell.get("name")
    name = f"{stem}:{name}" if stem and isinstance(name, str) and name else (stem or None)
    baseline = _eval_mean(cell.get("baseline_eval"))
    final = _eval_mean(cell.get("final_eval"))
    merged = dict(cell)
    record = _record(name, kind, merged, baseline, final, timestamp)
    # Cells train under the matrix seed, not a per-cell one.
    if record["seed"] is None:
        record["seed"] = _seed(data)
    return record


def normalize_artifact(data, *, source=""):
    """Normalize one decoded ``*_results.json`` object into records.

    ``data`` must be the decoded top-level mapping; ``source`` is the
    artifact filename, used only as a name fallback. Returns one record
    per experiment (one per matrix cell). Never mutates ``data``.
    Raises :class:`LedgerError` on a wrong top-level type.
    """
    if not isinstance(data, Mapping):
        raise LedgerError(
            f"cannot normalize {source or 'artifact'}: expected a top-level "
            f"JSON object, got {type(data).__name__}")
    stem = _stem(data.get("config_file")) or _stem(source)
    kind = _classify(data)
    timestamp = data.get("timestamp_utc")
    cells = data.get("cells")
    if kind == "synthetic-cadence-matrix" and isinstance(cells, list):
        return [_cell_record(stem, kind, data, cell, timestamp) for cell in cells]
    if "untrained_baseline" in data or "trained" in data:
        baseline = _eval_mean(data.get("untrained_baseline"))
        final = _eval_mean(data.get("trained"))
    else:
        baseline, final = _flat_rewards(data)
    return [_record(stem, kind, data, baseline, final, timestamp)]


def load_artifact(path):
    """Read one ``*_results.json`` file and normalize it.

    Raises :class:`LedgerError` with the path attached on malformed JSON
    or a wrong top-level type. ``OSError`` (e.g. missing file) propagates.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"cannot load {path}: invalid JSON: {exc}") from exc
    return normalize_artifact(data, source=str(path))
