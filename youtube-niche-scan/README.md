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

# Purge cache entries older than N days before running (default 30 days,
# per the YouTube API Services developer policies)
python scan.py --purge-cache-older-than 30
```

Outputs: `outputs/results.csv` (per keyword/format aggregates) and
`outputs/report.md` (ranking by keyword, aggregate by niche, long-form vs
short-form side by side, excluded counts, units used).

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
