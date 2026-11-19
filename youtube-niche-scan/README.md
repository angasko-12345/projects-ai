# YouTube niche-scan tool

Read-only, API-key-only tool for choosing a niche for an English-language,
AI-assisted YouTube channel targeting ad revenue (YPP). It ranks candidate
keywords by **RPM tier x small-channel win rate**, computed separately for
long-form and short-form video, using only read-only YouTube Data API v3
endpoints (`search.list`, `videos.list`, `channels.list`, `playlistItems.list`).
No OAuth, no uploads, no scraping, no write calls.

## Files

| file | purpose |
|------|---------|
| `scan.py` | CLI: scans keywords, scores videos, writes CSV + Markdown report |
| `keywords.json` | 30 English seed keywords (7-8 per niche) with RPM ranges |
| `tests/test_scan.py` | pytest suite, fully mocked HTTP (no live API calls) |
| `data/cache/` | raw API response cache (git-ignored) |
| `outputs/` | `results.csv` and `report.md` from the last run (git-ignored) |

## Requirements

- Python 3 (3.10+). Standard library only (`urllib`); no packages to install.
- A YouTube Data API v3 key in the `YOUTUBE_API_KEY` environment variable.

## PowerShell usage (current session only)

Set the key for the current PowerShell session only (do not persist it):

```powershell
$env:YOUTUBE_API_KEY="REDACTED_REAL_KEY"
```

Then run from the `youtube-niche-scan` folder:

```powershell
cd D:\admin\code\projects\youtube-niche-scan
python scan.py --limit-keywords 30
```

Common flags:

```powershell
# Estimate quota cost, make no API calls (no key needed)
python scan.py --dry-run

# Scan fewer keywords (useful for smoke tests)
python scan.py --limit-keywords 1

# Tighter quota guard
python scan.py --unit-cap 3000

# Different small-channel ceiling / outlier threshold / minimum video age
python scan.py --small-sub-max 5000 --outlier-min 5 --min-age-days 14

# Change the bounded search sample (each search query consumes quota)
python scan.py --sort viewCount --pages 2 --region US --region GB

# Compare multiple search orders in one run (deduplicated by video ID)
python scan.py --sort relevance --sort viewCount

# Purge cache entries older than N days before running (default 30 days,
# per the YouTube API Services developer policies)
python scan.py --purge-cache-older-than 30
```

Search order, region, and page depth change which videos can enter the sample;
none of these choices is random or representative. Multiple query combinations
are deduplicated before scoring and do not become independent samples. The
Markdown report records the chosen settings and these limits. Outputs:
`outputs/results.csv` (per keyword/format aggregates) and
`outputs/report.md` (ranking by keyword, aggregate by niche, long-form vs
short-form side by side, excluded counts, units used).

## Analysis (`analyze.py`)

Read-only analysis of `outputs/results.csv` (no API calls, standard library
only). Writes `outputs/recommendation.md` (niche recommendation) and
`outputs/sensitivity.csv` (rank-stability across the sensitivity grid).

```powershell
# Analyze the last scan output and write the recommendation
python analyze.py

# Re-run after a new scan produces outputs/results.csv
python analyze.py
```

`analyze.py` recomputes Wilson 95% intervals for `small_win_rate`, flags
keywords whose interval overlaps the one below as "not distinguishable",
runs a sensitivity grid (`outlier_min` in {3, 5, 8} × `small_sub_max` in
{5K, 10K, 25K} × RPM in {low, mid, high}), applies a hard-exclusion fit
filter (camera / credentials / health-finance-legal / templated output),
rates feasibility and differentiation separately (1-3 each, never blended),
and computes YPP-threshold views and per-100K-view revenue arithmetic.

If `outputs/results.csv` is missing or every row is LOW CONFIDENCE (n < 20),
it writes an "insufficient data" finding instead of a recommendation.

## Tests

```powershell
python -m pytest tests -q
```

All tests mock `urllib`; none call YouTube or need an API key.

## Quota costs (official YouTube Data API v3 table)

| endpoint | units/call |
|----------|-----------|
| search.list | 100 (planning figure used by this tool) |
| videos.list | 1 |
| channels.list | 1 |
| playlistItems.list | 1 |

Default daily quota: 10,000 units. Default `--unit-cap` is 9000. Estimate for
30 keywords: roughly 6,000-7,000 units (202/keyword worst case), actual usage
is usually lower because channels repeat across keywords. A warm-cache rerun
costs 0 units.

## Known limits

- `search.list(order=relevance)` is relevance-ordered, not a random sample;
  results are biased toward established channels.
- The API does not expose Shorts; `<= 180s` duration is used as a proxy for
  short-form, `>= 480s` for long-form, and mid-length videos are ignored.
- The API cannot show audience country; RPM is treated as niche-level only.
- Score = rpm_mid x small_win_rate is an unvalidated heuristic, not a revenue
  forecast. RPM values in `keywords.json` are vendor claims, unverified.
- Cached subscriber/view counts are non-authorized statistics; per developer
  policy they must be deleted or refreshed after 30 calendar days.
