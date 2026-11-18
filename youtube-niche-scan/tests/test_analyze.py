"""Tests for analyze.py. No live API calls; standard library only."""
import csv
import os
from datetime import datetime, timezone

import pytest

import analyze


KEYWORDS = {
    "niches": {
        "niche_1": {"name": "AI workflows for specific jobs", "rpm_low": 8, "rpm_high": 15},
        "niche_2": {"name": "B2B software for one industry", "rpm_low": 10, "rpm_high": 20},
        "niche_3": {"name": "narrow-domain education (history, science, literary analysis)",
                    "rpm_low": 7, "rpm_high": 11},
        "niche_4": {"name": "career and professional development", "rpm_low": 5, "rpm_high": 12},
    }
}

EXCLUDED = {"excluded_young": 0, "excluded_hidden_channels": 0, "ignored_mid_length": 0}


def _tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "RESULTS_CSV", tmp_path / "results.csv")
    monkeypatch.setattr(analyze, "RECOMMENDATION_MD", tmp_path / "recommendation.md")
    monkeypatch.setattr(analyze, "SENSITIVITY_CSV", tmp_path / "sensitivity.csv")
    monkeypatch.setattr(analyze, "REPORT_MD", tmp_path / "report.md")
    monkeypatch.setattr(analyze, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(analyze, "KEYWORDS_JSON", tmp_path / "keywords.json")


def _row(keyword, niche_key, fmt, n, win_rate, low_conf=True):
    return {
        "keyword": keyword,
        "niche": KEYWORDS["niches"][niche_key]["name"],
        "niche_key": niche_key,
        "format": fmt,
        "n": str(n),
        "small_wins": str(int(win_rate * n)),
        "small_win_rate": str(win_rate),
        "big_channels": "0",
        "big_channel_share": "0.0",
        "rpm_low": "8",
        "rpm_high": "15",
        "rpm_mid": "11.5",
        "score": str(11.5 * win_rate),
        "low_confidence": "true" if low_conf else "false",
    }


# --- Wilson interval ----------------------------------------------------------

def test_wilson_interval_center_and_bounds():
    lo, hi = analyze.wilson_interval(0.5, 100)
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0
    assert lo > 0.4 and hi < 0.6


def test_wilson_extremes():
    lo, hi = analyze.wilson_interval(0.0, 50)
    assert lo == pytest.approx(0.0, abs=1e-9) and hi > 0.0 and hi <= 1.0
    lo, hi = analyze.wilson_interval(1.0, 50)
    assert hi == pytest.approx(1.0, abs=1e-9) and lo < 1.0 and lo >= 0.0


def test_wilson_zero_n():
    assert analyze.wilson_interval(0.5, 0) == (0.0, 0.0)


def test_intervals_overlap():
    assert analyze.intervals_overlap((0.1, 0.4), (0.3, 0.6)) is True
    assert analyze.intervals_overlap((0.1, 0.2), (0.3, 0.6)) is False


# --- rank stability -----------------------------------------------------------

def test_rank_stability_always_top3_and_never_top3():
    rows = []
    for i, wr in enumerate((0.9, 0.7, 0.5, 0.3)):
        rows.append(_row(f"kw-{i}", "niche_1", "long-form", 50, wr, low_conf=False))
    per_row, combo_count = analyze.rank_stability(rows, KEYWORDS)
    assert combo_count == 27
    # highest win rate always in top 3 (same niche => same rpm scaling)
    assert per_row[("kw-0", "long-form")]["top3"] == 27
    # lowest win rate always 4th (never top 3 with 4 rows)
    assert per_row[("kw-3", "long-form")]["top3"] == 0
    for key, stats in per_row.items():
        assert stats["total"] == 27


# --- fit filter ---------------------------------------------------------------

def test_fit_filter_excludes_advice_categories():
    excluded, reason, feas, *_ = analyze.fit_filter("AI workflow for lawyers", "niche_1")
    assert excluded is True
    assert "legal" in reason

    excluded, reason, feas, *_ = analyze.fit_filter("AI workflow for accountants", "niche_1")
    assert excluded is True
    assert "finance" in reason

    excluded, reason, feas, *_ = analyze.fit_filter("AI workflow for nurses", "niche_1")
    assert excluded is True
    assert "health" in reason

    excluded, reason, feas, *_ = analyze.fit_filter("salary negotiation", "niche_4")
    assert excluded is True
    assert "finance" in reason

    excluded, reason, feas, feas_reason, diff, diff_reason, weights = analyze.fit_filter(
        "construction software for contractors", "niche_2"
    )
    assert excluded is False
    assert reason is None
    assert feas == 3
    assert feas_reason is not None
    assert diff == 3
    assert diff_reason is not None
    assert "feasibility 0.6" in weights


def test_fit_filter_templated_risk_not_auto_excluded():
    excluded, reason, feas, *_ = analyze.fit_filter("AI workflow for software engineers", "niche_1")
    assert excluded is False
    assert feas == 3


# --- arithmetic ---------------------------------------------------------------

def test_views_for_watch_hours():
    assert analyze.views_for_watch_hours(4000, 4) == 60000.0
    assert analyze.views_for_watch_hours(4000, 6) == 40000.0
    assert analyze.views_for_watch_hours(8000, 4) == 120000.0
    assert analyze.views_for_watch_hours(8000, 6) == 80000.0


def test_revenue_per_100k_views():
    assert analyze.revenue_per_100k_views(10.0) == 1000.0
    assert analyze.revenue_per_100k_views(10.0, 0.25) == 250.0
    assert analyze.revenue_per_100k_views(9.0, 1.0) == 900.0
    assert analyze.revenue_per_100k_views(9.0, 0.25) == 225.0


# --- coerce -------------------------------------------------------------------

def test_coerce():
    row = analyze.coerce({"n": "10", "small_wins": "8", "big_channels": "0",
                          "small_win_rate": "0.8", "big_channel_share": "0.0",
                          "rpm_low": "8", "rpm_high": "15", "rpm_mid": "11.5", "score": "9.2",
                          "low_confidence": "false"})
    assert row["n"] == 10
    assert row["small_win_rate"] == 0.8
    assert row["score"] == 9.2
    assert row["low_confidence"] is False


# --- insufficient data handling ----------------------------------------------

def test_empty_input_insufficient_data(tmp_path, monkeypatch):
    _tmp_paths(tmp_path, monkeypatch)
    result = analyze.build_recommendation(None, KEYWORDS, EXCLUDED, None)
    assert result is None
    md = (tmp_path / "recommendation.md").read_text(encoding="utf-8")
    assert "INSUFFICIENT DATA" in md
    assert "no usable scan output" in md
    with (tmp_path / "sensitivity.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["status", "note"]
    assert rows[1][0] == "insufficient_data"


def test_all_low_confidence_insufficient_data(tmp_path, monkeypatch):
    _tmp_paths(tmp_path, monkeypatch)
    rows = [_row("kw-a", "niche_1", "long-form", 5, 0.8, low_conf=True),
            _row("kw-b", "niche_2", "short-form", 10, 0.6, low_conf=True)]
    result = analyze.build_recommendation(rows, KEYWORDS, EXCLUDED, None)
    assert result is None
    md = (tmp_path / "recommendation.md").read_text(encoding="utf-8")
    assert "INSUFFICIENT DATA" in md
    assert "no usable scan output" in md or "every keyword/format row" in md
    with (tmp_path / "sensitivity.csv").open(newline="", encoding="utf-8") as fh:
        assert "insufficient_data" in fh.read()


def test_full_path_writes_outputs(tmp_path, monkeypatch):
    _tmp_paths(tmp_path, monkeypatch)
    rows = [_row("kw-a", "niche_1", "long-form", 50, 0.8, low_conf=False),
            _row("kw-b", "niche_2", "long-form", 50, 0.5, low_conf=False),
            _row("kw-c", "niche_3", "short-form", 50, 0.4, low_conf=False)]
    analyze.build_recommendation(rows, KEYWORDS, EXCLUDED, 5.0)
    md = (tmp_path / "recommendation.md").read_text(encoding="utf-8")
    assert "## Data quality" in md
    assert "## Scores and uncertainty" in md
    assert "## Sensitivity / rank stability" in md
    assert "## Fit filter (applied after scoring)" in md
    assert "## Economics" in md
    assert "## Long-form vs short-form evidence" in md
    assert "## Verdict" in md
    assert "## Top 3 keywords/angles" in md
    assert "## Confidence level" in md
    assert "YPP approval review takes time" in md
    assert "not evaluated by this data" in md
    with (tmp_path / "sensitivity.csv").open(newline="", encoding="utf-8") as fh:
        header = fh.readline().strip()
    assert header.startswith("keyword,niche,format,n,")
