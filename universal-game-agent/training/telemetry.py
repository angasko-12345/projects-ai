"""Per-update PPO telemetry as CSV. Stdlib only.

One row per PPO update, columns fixed by ``FIELDNAMES``. The columns are
exactly the scalar per-update series ``PPOTrainer.train`` appends to its
``history`` dict (``training/ppo.py``); the two non-scalar series
(``components``, ``upd_action_share``) are dicts and have no CSV encoding,
so they are excluded rather than stringified.

Wired into training via ``PPOConfig.telemetry_path``: when set,
``PPOTrainer.train`` writes one row per completed update straight from its
history row. Files are only ever opened in
append mode, so existing rows are never truncated; the header is written
exactly once, when the file is created (or empty).
"""
from __future__ import annotations

import csv
from collections.abc import Mapping
from pathlib import Path

#: Stable column order. Append-only contract: never reorder, never remove;
#: add new trailing columns only.
FIELDNAMES: tuple[str, ...] = (
    "timesteps",
    "fps",
    "policy_loss",
    "value_loss",
    "entropy",
    "mean_reward",
    "mean_ext_reward",
    "mean_int_reward",
    "predictor_loss",
    "pixel_change",
    "episodes",
    "upd_episodes",
    "upd_mean_length",
    "upd_terminated",
    "upd_truncated",
    "upd_mean_ext",
    "upd_mean_int",
    "upd_mean_total",
)

#: Fast-engine CSV (``training/fast_ppo.py``): the legacy columns, unchanged and
#: in order, then Stage 2 columns appended at the end (CleanRL diagnostics and
#: the raw full-game score, which differs from the training return under sign
#: clipping / episodic life). Legacy CSVs keep ``FIELDNAMES`` exactly.
FAST_FIELDNAMES: tuple[str, ...] = FIELDNAMES + (
    "approx_kl",
    "old_approx_kl",
    "clipfrac",
    "explained_variance",
    "learning_rate",
    "mean_game_score",
    "game_episodes",
    "upd_mean_game_score",
)


class PPOUpdateCSVWriter:
    """Append-only CSV writer for PPO per-update records."""

    def __init__(self, path: str | Path, fieldnames: tuple[str, ...] = FIELDNAMES) -> None:
        self.path = Path(path)
        self.fieldnames = tuple(fieldnames)
        self._ensure_header()

    def _ensure_header(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            with open(self.path, "a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=self.fieldnames).writeheader()

    def append(self, record: Mapping) -> None:
        """Append one update row. Missing columns raise ``KeyError``."""
        self._ensure_header()
        with open(self.path, "a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=self.fieldnames, extrasaction="ignore").writerow(
                {key: record[key] for key in self.fieldnames}
            )

    def write_history(self, history: Mapping) -> int:
        """Append every update in a ``PPOTrainer.history`` dict.

        Zero updates writes the header only and returns 0.
        """
        self._ensure_header()
        updates = len(history.get("timesteps") or [])
        for index in range(updates):
            self.append({key: history[key][index] for key in self.fieldnames})
        return updates
