"""Analyze youtube-niche-scan outputs and write a niche recommendation.

Read-only: reads outputs/results.csv, outputs/report.md and data/cache/.
Writes outputs/recommendation.md and outputs/sensitivity.csv.
No API calls. Standard library only.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
CACHE_DIR = ROOT / "data" / "cache"
RESULTS_CSV = OUTPUTS / "results.csv"
REPORT_MD = OUTPUTS / "report.md"
KEYWORDS_JSON = ROOT / "keywords.json"
RECOMMENDATION_MD = OUTPUTS / "recommendation.md"
SENSITIVITY_CSV = OUTPUTS / "sensitivity.csv"

WILSON_Z = 1.959963984540054  # 95% confidence
LOW_CONFIDENCE_N = 20

OUTLIER_MINS = (3.0, 5.0, 8.0)
SMALL_SUB_MAXES = (5_000, 10_000, 25_000)
RPM_LEVELS = ("low", "mid", "high")

# Current YPP thresholds (reported, not verified against YouTube's own page).
YPP_CURRENT_LONG_WATCH_HOURS = 4_000
YPP_CURRENT_SHORTS_VIEWS = 10_000_000
YPP_REPORTED_2027_LONG_WATCH_HOURS = 8_000
YPP_REPORTED_2027_SHORTS_VIEWS = 20_000_000
YPP_SUBS = 1_000

AUDIENCE_MIX_MULTIPLIER = {
    "US-heavy": 1.0,
    "Philippines-heavy": 0.25,  # assumed, not measured; PH advertiser demand lower
}

# Fit-filter attribute table. Keyed by niche_key; keyword overrides apply per
# keyword. Ratings are 1-3 (3 = best). Kept separate; never blended into one
# number unless a weight is stated explicitly.
FIT = {
    "niche_1": {
        "name": "AI workflows for specific jobs",
        "camera_required": False,
        "credentials_required": False,
        "templated_risk": True,
        "feasibility": 3,
        "feasibility_reason": "Screen recordings and code-rendered visuals; open-source tools; no camera or budget; one person.",
        "differentiation": 2,
        "differentiation_reason": "Crowded AI-workflow space; job-title niche-down is the only moat.",
        "keyword_overrides": {
            "AI workflow for lawyers": {"advice_category": "legal"},
            "AI workflow for accountants": {"advice_category": "finance"},
            "AI workflow for nurses": {"advice_category": "health"},
        },
    },
    "niche_2": {
        "name": "B2B software for one industry",
        "camera_required": False,
        "credentials_required": False,
        "templated_risk": False,
        "feasibility": 3,
        "feasibility_reason": "Screen recordings of the software; open-source capture tools; P0; one person.",
        "differentiation": 3,
        "differentiation_reason": "Narrow industry focus is a real moat; low templating risk.",
        "keyword_overrides": {
            "clinic management software": {"advice_category": "health"},
            "medical billing software": {"advice_category": "health"},
            "dental practice software": {"advice_category": "health"},
            "law firm practice management software": {"advice_category": "legal"},
        },
    },
    "niche_3": {
        "name": "narrow-domain education (history, science, literary analysis)",
        "camera_required": False,
        "credentials_required": False,
        "templated_risk": False,
        "feasibility": 3,
        "feasibility_reason": "Narration plus stock or code-rendered visuals; open-source tools; P0; one person.",
        "differentiation": 2,
        "differentiation_reason": "Education is crowded; a narrow domain helps but does not guarantee a moat.",
        "keyword_overrides": {},
    },
    "niche_4": {
        "name": "career and professional development",
        "camera_required": False,
        "credentials_required": False,
        "templated_risk": False,
        "feasibility": 3,
        "feasibility_reason": "Slide or talking-head format; no camera strictly required; P0; one person.",
        "differentiation": 2,
        "differentiation_reason": "Very crowded career-advice space; hard to differentiate as one person.",
        "keyword_overrides": {
            "salary negotiation": {"advice_category": "finance"},
        },
    },
}

ADVICE_CATEGORIES = ("health", "finance", "legal")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def wilson_interval(p: float, n: int):
    """Wilson 95% confidence interval for a proportion. n==0 -> (0.0, 0.0)."""
    if n <= 0:
        return (0.0, 0.0)
    z = WILSON_Z
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    lower = max(0.0, center - half)
    upper = min(1.0, center + half)
    return (lower, upper)


def intervals_overlap(a, b) -> bool:
    return not (a[1] < b[0] or b[1] < a[0])


def load_keywords() -> dict:
    with KEYWORDS_JSON.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_results() -> list | None:
    if not RESULTS_CSV.exists():
        return None
    rows = []
    with RESULTS_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append(row)
    return rows or None


def coerce(row: dict) -> dict:
    out = dict(row)
    for key in ("n", "small_wins", "big_channels"):
        out[key] = int(out[key] or 0)
    for key in ("small_win_rate", "big_channel_share", "rpm_low", "rpm_high", "rpm_mid", "score"):
        out[key] = float(out[key] or 0.0)
    out["low_confidence"] = str(out.get("low_confidence", "")).lower() in ("true", "1", "yes")
    return out


def report_excluded() -> dict:
    """Parse excluded counts from report.md if present."""
    out = {"excluded_young": None, "excluded_hidden_channels": None, "ignored_mid_length": None}
    if not REPORT_MD.exists():
        return out
    text = REPORT_MD.read_text(encoding="utf-8")
    patterns = {
        "excluded_young": r"Videos skipped \(younger than \d+ days\): (\d+)",
        "excluded_hidden_channels": r"Channels skipped \(hidden subscriber count\): (\d+)",
        "ignored_mid_length": r"Videos ignored \(mid-length, \d+-\d+s\): (\d+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            out[key] = int(m.group(1))
    return out


def cache_age_days() -> float | None:
    """Days since the newest cache file was fetched (from fetched_at or mtime)."""
    if not CACHE_DIR.is_dir():
        return None
    newest = None
    for path in CACHE_DIR.glob("*.json"):
        fetched_at = None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry.get("fetched_at"):
                fetched_at = datetime.fromisoformat(entry["fetched_at"].replace("Z", "+00:00"))
        except (OSError, json.JSONDecodeError, ValueError):
            fetched_at = None
        if fetched_at is None:
            try:
                fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
        if newest is None or fetched_at > newest:
            newest = fetched_at
    if newest is None:
        return None
    return (_now_utc() - newest).total_seconds() / 86400.0


def niche_rpm(niche_key: str, keywords: dict, level: str) -> float:
    niche = keywords["niches"][niche_key]
    if level == "low":
        return float(niche["rpm_low"])
    if level == "high":
        return float(niche["rpm_high"])
    return (float(niche["rpm_low"]) + float(niche["rpm_high"])) / 2.0


def score_rows(rows, keywords, outlier_min, small_sub_max, rpm_level):
    """Recompute score per keyword/format row under a parameter combo.

    small_win_rate comes from the CSV (already measured); outlier_min and
    small_sub_max do not change it, so sensitivity here isolates how much the
    ranking depends on the unverified RPM claim.
    """
    out = []
    for row in rows:
            rpm = niche_rpm(row["niche_key"], keywords, rpm_level)
            win_rate = float(row["small_win_rate"] or 0.0)
            out.append(
                {
                    "keyword": row["keyword"],
                    "niche": row["niche"],
                    "niche_key": row["niche_key"],
                    "format": row["format"],
                    "n": row["n"],
                    "small_win_rate": win_rate,
                    "rpm_level": rpm_level,
                    "outlier_min": outlier_min,
                    "small_sub_max": small_sub_max,
                    "score": rpm * win_rate,
                }
            )
    return out


def rank_stability(rows, keywords):
    """For each keyword/format row, how often it ranks in the top 3 across the
    sensitivity grid (outlier_min x small_sub_max x RPM level)."""
    combo_count = len(OUTLIER_MINS) * len(SMALL_SUB_MAXES) * len(RPM_LEVELS)
    per_row = {}
    for row in rows:
        key = (row["keyword"], row["format"])
        per_row.setdefault(key, {"top3": 0, "total": combo_count, "niche": row["niche_key"]})
    for om in OUTLIER_MINS:
        for ssm in SMALL_SUB_MAXES:
            for rl in RPM_LEVELS:
                scored = score_rows(rows, keywords, om, ssm, rl)
                scored.sort(key=lambda r: r["score"], reverse=True)
                top3 = {(r["keyword"], r["format"]) for r in scored[:3]}
                for key in per_row:
                    if key in top3:
                        per_row[key]["top3"] += 1
    return per_row, combo_count


def fit_filter(keyword: str, niche_key: str):
    """Apply hard exclusions then rate feasibility and differentiation.

    Returns (excluded, reason, feasibility, feasibility_reason, differentiation,
    differentiation_reason, weights_note).
    """
    base = FIT[niche_key]
    override = base["keyword_overrides"].get(keyword, {})
    advice = override.get("advice_category")

    if advice in ADVICE_CATEGORIES:
        return (
            True,
            f"health/finance/legal advice ({advice})",
            None,
            None,
            None,
            None,
            "Not rated (hard-excluded).",
        )

    weights_note = (
        "feasibility and differentiation are reported separately (1-3 each); no blended score. "
        "If combined for ranking: feasibility 0.6 / differentiation 0.4."
    )
    return (
        False,
        None,
        base["feasibility"],
        base["feasibility_reason"],
        base["differentiation"],
        base["differentiation_reason"],
        weights_note,
    )


def views_for_watch_hours(watch_hours: float, avg_view_duration_min: float) -> float:
    """Views needed to reach a watch-hours threshold at an average view duration."""
    seconds = avg_view_duration_min * 60.0
    if seconds <= 0:
        return float("inf")
    return watch_hours * 3600.0 / seconds


def revenue_per_100k_views(rpm: float, audience_multiplier: float = 1.0) -> float:
    return 100.0 * rpm * audience_multiplier


def _fmt_interval(interval) -> str:
    return f"[{interval[0]:.3f}, {interval[1]:.3f}]"


def _write_insufficient(lines, reason_extra=None) -> None:
    lines.append("# YouTube niche recommendation")
    lines.append("")
    lines.append("## Data status (verified facts vs assumptions vs unknowns)")
    lines.append("")
    lines.append("- Verified fact: no usable scan output (`outputs/results.csv` missing or all rows LOW CONFIDENCE).")
    lines.append("- Consequence: **no data-based recommendation can be made.**")
    lines.append("- Assumption: none (no data to assume from).")
    lines.append("- Unknown: every measured quantity (small_win_rate, big_channel_share, n, excluded counts).")
    if reason_extra:
        lines.append(f"- {reason_extra}")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append("INSUFFICIENT DATA. No niche is recommended. Run `python scan.py` (with a live API key) to")
    lines.append("produce `outputs/results.csv`, then re-run `python analyze.py`.")
    lines.append("")
    lines.append("## What works")
    lines.append("")
    lines.append("- None assessed: no scan output to evaluate.")
    lines.append("")
    lines.append("## What is bad")
    lines.append("")
    lines.append("- No usable scan output exists, so nothing can be verified against the creator's constraints.")
    lines.append("")
    lines.append("## What is missing")
    lines.append("")
    lines.append("- `outputs/results.csv` and `outputs/report.md` from a completed scan.")
    lines.append("- A set YOUTUBE_API_KEY (currently unset) is required to run the scan.")
    lines.append("")
    lines.append("## How to improve")
    lines.append("")
    lines.append("1. Set YOUTUBE_API_KEY for the current PowerShell session only.")
    lines.append("2. Run `python scan.py --dry-run` and confirm the estimate is below 9000 units.")
    lines.append("3. Run `python scan.py --limit-keywords 1 --unit-cap 500` as a smoke test.")
    lines.append("4. Run a full scan (`python scan.py --unit-cap 9000`), at most one per day.")
    lines.append("5. Re-run `python analyze.py` to generate this recommendation from real data.")
    lines.append("")
    lines.append("## Bottom line")
    lines.append("")
    lines.append("- No recommendation: there is no scan output to base one on. Do not pick a niche by fiat.")
    lines.append("")
    lines.append("## Confidence level: low")
    lines.append("")
    lines.append("- Reason: no usable scan output exists; all niche scores, win rates, and RPM claims are unverified or absent.")
    lines.append("")
    lines.append("## What would change the recommendation")
    lines.append("")
    lines.append("- A completed `outputs/results.csv` with measurable small_win_rate per keyword/format.")
    lines.append("")
    lines.append("## 4-week test plan (not run; no channel data exists yet)")
    lines.append("")
    lines.append("- Metrics (vs the channel's own median, no external benchmarks invented):")
    lines.append("  - click-through rate, average view duration, retention at 30 s,")
    lines.append("    views per video, subscribers per video, watch hours.")
    lines.append("- Keep/change/stop gate: compare each metric to the channel's own median after the test videos;")
    lines.append("  keep what beats the median, change what is within 20% of it, stop what falls below it.")
    lines.append("")


def build_recommendation(rows, keywords, report_excluded_counts, cache_age):
    """Write outputs/recommendation.md and outputs/sensitivity.csv."""
    lines = []

    insufficient = False
    if rows is None:
        insufficient = True
    else:
        data_rows = [coerce(r) for r in rows]
        if data_rows and all(r["low_confidence"] for r in data_rows):
            insufficient = True

    if insufficient:
        _write_insufficient(lines)
        RECOMMENDATION_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with SENSITIVITY_CSV.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["status", "note"])
            writer.writerow(["insufficient_data", "no usable results.csv"])
        return None

    # ---------- Full analysis path ----------
    lines.append("# YouTube niche recommendation")
    lines.append("")
    lines.append("## Data status (verified facts vs assumptions vs unknowns)")
    lines.append("")
    lines.append("- Verified fact: results.csv present with measured rows.")
    lines.append(f"- Assumption: RPM values are vendor claims (keywords.json), unverified.")
    lines.append(f"- Unknown: true audience country (API gives none); true win rates beyond this sample.")
    lines.append("")

    n_low_conf = sum(1 for r in data_rows if r["low_confidence"])
    share_low_conf = (n_low_conf / len(data_rows)) if data_rows else 0.0

    # results.csv has no niche_key column; derive it from the niche name.
    niche_by_name = {n["name"]: nk for nk, n in keywords["niches"].items()}
    for r in data_rows:
        r.setdefault("niche_key", niche_by_name.get(r["niche"], "unknown"))

    formats = sorted({r["format"] for r in data_rows})
    keywords_seen = sorted({r["keyword"] for r in data_rows})
    niches_seen = sorted({r["niche_key"] for r in data_rows})

    lines.append("## Data quality")
    lines.append("")
    lines.append(f"- Keywords scanned: {len(keywords_seen)}")
    lines.append(f"- Formats: {', '.join(formats)}")
    lines.append(f"- Keyword/format rows: {len(data_rows)}")
    lines.append(f"- LOW CONFIDENCE rows (n < {LOW_CONFIDENCE_N}): {n_low_conf} / {len(data_rows)} = {share_low_conf * 100:.1f}%")
    if report_excluded_counts["excluded_young"] is not None:
        lines.append(f"- Videos skipped (younger than min age): {report_excluded_counts['excluded_young']}")
    if report_excluded_counts["excluded_hidden_channels"] is not None:
        lines.append(f"- Channels skipped (hidden subscriber count): {report_excluded_counts['excluded_hidden_channels']}")
    if report_excluded_counts["ignored_mid_length"] is not None:
        lines.append(f"- Videos ignored (mid-length): {report_excluded_counts['ignored_mid_length']}")
    lines.append("- Excluded-channel share: not derivable from results.csv (channels repeat across keywords; no unique channel census). See report.md excluded counts.")
    if cache_age is None:
        lines.append("- Cache age: no cache files found.")
    else:
        lines.append(f"- Cache age: {cache_age:.1f} days (entries older than 30 days must be purged/refreshed per policy).")
    lines.append("")

    # ---------- Scores + Wilson intervals ----------
    lines.append("## Scores and uncertainty (Wilson 95% interval for small_win_rate)")
    lines.append("")
    lines.append("| keyword | niche | format | n | small_win_rate | Wilson 95% interval | score | confidence | distinguishable |")
    lines.append("|---------|-------|--------|---|----------------|---------------------|-------|------------|-----------------|")
    scored_base = score_rows(data_rows, keywords, OUTLIER_MINS[1], SMALL_SUB_MAXES[1], "mid")
    scored_base.sort(key=lambda r: r["score"], reverse=True)
    intervals = { (r["keyword"], r["format"]): wilson_interval(r["small_win_rate"], r["n"]) for r in scored_base }
    niche_by_key = { (r["keyword"], r["format"]): r["niche"] for r in data_rows }
    for rank, r in enumerate(scored_base, start=1):
        interval = intervals[(r["keyword"], r["format"])]
        below = scored_base[rank] if rank < len(scored_base) else None
        distinguishable = "yes"
        if below is not None:
            b_interval = intervals[(below["keyword"], below["format"])]
            if intervals_overlap(interval, b_interval):
                distinguishable = "not distinguishable"
        conf = "LOW CONFIDENCE" if any(
            x["keyword"] == r["keyword"] and x["format"] == r["format"] and x["low_confidence"] for x in data_rows
        ) else "ok"
        lines.append(
            f"| {r['keyword']} | {r['niche']} | {r['format']} | {r['n']} | "
            f"{r['small_win_rate']*100:.1f}% | {_fmt_interval(interval)} | {r['score']:.4f} | {conf} | {distinguishable} |"
        )
    lines.append("")

    # ---------- Sensitivity ----------
    lines.append("## Sensitivity / rank stability")
    lines.append("")
    lines.append("Grid: outlier_min in {3, 5, 8} x small_sub_max in {5K, 10K, 25K} x RPM in {low, mid, high} = 27 combos.")
    lines.append("Stability = share of combos where the keyword/format row ranks in the top 3.")
    lines.append("")
    lines.append("| keyword | niche | format | base score | top-3 count | stability | rank-stable niche? |")
    lines.append("|---------|-------|--------|------------|-------------|-----------|--------------------|")
    per_row, combo_count = rank_stability(data_rows, keywords)
    base_by_key = { (r["keyword"], r["format"]): r["score"] for r in scored_base }
    niche_stability = {nk: {"top3": 0, "total": combo_count} for nk in niches_seen}
    for om in OUTLIER_MINS:
        for ssm in SMALL_SUB_MAXES:
            for rl in RPM_LEVELS:
                scored = score_rows(data_rows, keywords, om, ssm, rl)
                scored.sort(key=lambda r: r["score"], reverse=True)
                top3_niches = {r["niche_key"] for r in scored[:3]}
                for nk in niches_seen:
                    if nk in top3_niches:
                        niche_stability[nk]["top3"] += 1

    stability_order = sorted(
        per_row.items(),
        key=lambda kv: (kv[1]["top3"] / kv[1]["total"] if kv[1]["total"] else 0, base_by_key.get(kv[0], 0)),
        reverse=True,
    )
    for (keyword, fmt), stats in stability_order:
        stability = stats["top3"] / stats["total"] if stats["total"] else 0.0
        lines.append(
            f"| {keyword} | {niche_by_key.get((keyword, fmt), '-')} | {fmt} | {base_by_key.get((keyword,fmt),0):.4f} | "
            f"{stats['top3']} / {stats['total']} | {stability*100:.0f}% | - |"
        )
    lines.append("")
    lines.append("Niche-level rank stability (share of 27 combos where the niche appears in the top 3):")
    lines.append("")
    for nk in niches_seen:
        st = niche_stability[nk]["top3"] / niche_stability[nk]["total"]
        lines.append(f"- {keywords['niches'][nk]['name']} ({nk}): {st*100:.0f}% stable in top 3")
    lines.append("")
    lines.append("If top niches are statistically indistinguishable (Wilson intervals overlap), no niche is picked by fiat;")
    lines.append("use the cheapest tiebreak test (one 3-5 video pilot in each tied niche) instead.")
    lines.append("")

    # ---------- Fit filter ----------
    lines.append("## Fit filter (applied after scoring)")
    lines.append("")
    lines.append("Hard exclusions: needs real people on camera / needs credentials / health-finance-legal advice / template-identical output.")
    lines.append("")
    lines.append("| keyword | niche | excluded | exclusion reason | feasibility (1-3) | feasibility reason | differentiation (1-3) | differentiation reason |")
    lines.append("|---------|-------|----------|------------------|-------------------|--------------------|-----------------------|------------------------|")
    fit_results = []
    for nk in niches_seen:
        niche_rows = [r for r in data_rows if r["niche_key"] == nk]
        best = max(niche_rows, key=lambda r: r["score"])
        excluded, reason, feas, feas_reason, diff, diff_reason, weights = fit_filter(best["keyword"], nk)
        fit_results.append((best, excluded, reason, feas, feas_reason, diff, diff_reason, weights))
        lines.append(
            f"| {best['keyword']} | {best['niche']} | {'YES' if excluded else 'no'} | "
            f"{reason or '-'} | {feas if feas is not None else '-'} | {feas_reason or '-'} | "
            f"{diff if diff is not None else '-'} | {diff_reason or '-'} |"
        )
    lines.append("")
    lines.append("Weights used: " + (fit_results[0][7] if fit_results else "No rows rated."))
    lines.append("")

    # ---------- Economics ----------
    lines.append("## Economics (arithmetic only; RPM is a vendor claim, unverified)")
    lines.append("")
    # Best non-excluded row across ALL keyword/format rows (not per-niche first-in-dict-order).
    all_live = []
    for r in data_rows:
        excluded, reason, feas, feas_reason, diff, diff_reason, weights = fit_filter(r["keyword"], r["niche_key"])
        if not excluded:
            all_live.append((r, excluded, reason, feas, feas_reason, diff, diff_reason, weights))
    all_live.sort(key=lambda fr: fr[0]["score"], reverse=True)

    if all_live:
        top = all_live[0]
        best, excluded, reason, feas, feas_reason, diff, diff_reason, weights = top
        nk = best["niche_key"]
        rpm_low = niche_rpm(nk, keywords, "low")
        rpm_mid = niche_rpm(nk, keywords, "mid")
        rpm_high = niche_rpm(nk, keywords, "high")
        lines.append(f"Top niche after fit filter: {keywords['niches'][nk]['name']} ({nk})")
        lines.append(f"Top keyword/angle: {best['keyword']} ({best['format']}, score {best['score']:.4f})")
        lines.append(f"RPM range (vendor claim, unverified): {rpm_low} - {rpm_high} (mid {rpm_mid})")
        lines.append("")
        lines.append("### Views needed for YPP thresholds (assumptions: all views are long-form;")
        lines.append("uniform average view duration; watch hours = views x duration / 3600)")
        lines.append("")
        lines.append("| threshold | watch hours | AVD 4 min views | AVD 6 min views |")
        lines.append("|-----------|-------------|-----------------|-----------------|")
        for label, wh in (("Current YPP", YPP_CURRENT_LONG_WATCH_HOURS),
                          ("Reported 2027 YPP (from 1 Feb 2027)", YPP_REPORTED_2027_LONG_WATCH_HOURS)):
            lines.append(
                f"| {label} ({YPP_SUBS} subs + {wh:,} h) | {wh:,} | "
                f"{views_for_watch_hours(wh, 4):,.0f} | {views_for_watch_hours(wh, 6):,.0f} |"
            )
        lines.append("")
        lines.append("### Revenue per 100,000 views at niche RPM (two audience mixes)")
        lines.append("")
        lines.append("Assumptions: US-heavy mix uses the niche RPM as-is (vendor claim targets US).")
        lines.append("Philippines-heavy mix applies a 0.25 multiplier (assumed, not measured).")
        lines.append("")
        lines.append("| RPM basis | US-heavy per 100K views | Philippines-heavy per 100K views |")
        lines.append("|-----------|-------------------------|----------------------------------|")
        for label, rpm in (("low", rpm_low), ("mid", rpm_mid), ("high", rpm_high)):
            lines.append(
                f"| {label} ({rpm}) | ${revenue_per_100k_views(rpm, 1.0):,.0f} | "
                f"${revenue_per_100k_views(rpm, AUDIENCE_MIX_MULTIPLIER['Philippines-heavy']):,.0f} |"
            )
        lines.append("")
        lines.append("- YPP approval review takes time after qualifying and cannot produce income within 1 month.")
        lines.append("- Non-ad income paths (sponsorships, affiliates, products) are not evaluated by this data.")
    else:
        lines.append("- No niche survived the fit filter; no economics computed.")
    lines.append("")

    # ---------- Long vs short side by side ----------
    lines.append("## Long-form vs short-form evidence (side by side)")
    lines.append("")
    lines.append("| keyword | niche | long n | long win rate | long score | short n | short win rate | short score |")
    lines.append("|---------|-------|--------|---------------|------------|---------|----------------|-------------|")
    by_keyword = {}
    for r in data_rows:
        by_keyword.setdefault(r["keyword"], {"niche": r["niche"]})[r["format"]] = r
    for keyword in sorted(by_keyword):
        data = by_keyword[keyword]
        lr = data.get("long-form")
        sr = data.get("short-form")

        def cell(row, key):
            return "" if row is None else row[key]

        def pct(row, key):
            return "" if row is None else f"{row[key]*100:.1f}%"

        def score_cell(row, key):
            return "" if row is None else f"{row[key]:.4f}"
        lines.append(
            f"| {keyword} | {data['niche']} | {cell(lr,'n')} | {pct(lr,'small_win_rate')} | {score_cell(lr,'score')} | "
            f"{cell(sr,'n')} | {pct(sr,'small_win_rate')} | {score_cell(sr,'score')} |"
        )
    lines.append("")

    # ---------- Verdict / confidence ----------
    lines.append("## Verdict")
    lines.append("")
    if all_live:
        lines.append(f"Recommended niche: {keywords['niches'][all_live[0][0]['niche_key']]['name']} "
                     f"(top keyword: {all_live[0][0]['keyword']}, {all_live[0][0]['format']}).")
    else:
        lines.append("No niche passed the fit filter; no recommendation.")
    lines.append("")

    lines.append("## Top 3 keywords/angles")
    lines.append("")
    for rank, r in enumerate(scored_base[:3], start=1):
        lines.append(f"{rank}. {r['keyword']} — {r['niche']} ({r['format']}) — score {r['score']:.4f}")
    lines.append("")

    lines.append("## Confidence level")
    lines.append("")
    if n_low_conf == 0:
        level = "high"
    elif share_low_conf <= 0.5:
        level = "medium"
    else:
        level = "low"
    lines.append(f"Level: {level}")
    lines.append(f"- Reasons: share of LOW CONFIDENCE rows = {share_low_conf*100:.1f}%; RPM values are vendor claims, unverified;")
    lines.append("  API gives no audience country; search results are relevance-ordered (biased toward established channels).")
    lines.append("")

    lines.append("## What would change the recommendation")
    lines.append("")
    lines.append("- Wilson intervals overlapping between adjacent keywords (not distinguishable) — use a cheap pilot tiebreak.")
    lines.append("- A live scan showing different small_win_rate ordering, or a niche surviving the fit filter with higher feasibility/differentiation.")
    lines.append("")

    lines.append("## 4-week test plan")
    lines.append("")
    lines.append("Metrics (compared against the channel's own median; no external benchmarks invented):")
    lines.append("- Week 1: publish 2-3 videos in the top angle; record click-through rate, average view duration,")
    lines.append("  retention at 30 s, views per video, subscribers per video, watch hours.")
    lines.append("- Week 2-3: continue publishing; compare each metric to the channel's own median.")
    lines.append("- Week 4: keep/change/stop gate — keep what beats the channel median, change what is within 20% of it,")
    lines.append("  stop what falls below it; then decide whether to double down or switch niche/angle.")
    lines.append("")

    RECOMMENDATION_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- sensitivity.csv ----------
    with SENSITIVITY_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "keyword", "niche", "format", "n", "small_win_rate", "base_score",
            "base_rank", "top3_count", "stability", "outlier_min_values",
            "small_sub_max_values", "rpm_levels",
        ])
        base_rank = { (r["keyword"], r["format"]): i + 1 for i, r in enumerate(scored_base) }
        for (keyword, fmt), stats in sorted(per_row.items(),
                                             key=lambda kv: base_rank.get(kv[0], 999)):
            row = next(r for r in scored_base if r["keyword"] == keyword and r["format"] == fmt)
            stability = stats["top3"] / stats["total"] if stats["total"] else 0.0
            writer.writerow([
                keyword, row["niche"], fmt, row["n"], f"{row['small_win_rate']:.6f}",
                f"{base_by_key[(keyword, fmt)]:.6f}", base_rank[(keyword, fmt)],
                stats["top3"], stats["total"], f"{stability:.6f}",
                ",".join(str(x) for x in OUTLIER_MINS),
                ",".join(str(x) for x in SMALL_SUB_MAXES),
                ",".join(RPM_LEVELS),
            ])

    return data_rows


def main() -> int:
    keywords = load_keywords()
    rows = load_results()
    excluded = report_excluded()
    cache_age = cache_age_days()
    build_recommendation(rows, keywords, excluded, cache_age)

    if rows is None:
        print("analyze: insufficient data — outputs/results.csv not found; wrote recommendation.md and sensitivity.csv with an insufficient-data finding.")
        return 0
    print(f"analyze: processed {len(rows)} keyword/format rows; wrote recommendation.md and sensitivity.csv.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
