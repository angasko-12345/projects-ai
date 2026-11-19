"""YouTube niche-scan tool (read-only, API-key only).

Ranks candidate YouTube niches by RPM tier x small-channel win rate,
separately for long-form and short-form video, using only read-only
YouTube Data API v3 endpoints (search.list, videos.list, channels.list,
playlistItems.list). No OAuth, no uploads, no scraping.

The API key must be supplied through the YOUTUBE_API_KEY environment
variable. It is never logged or written to disk; cache files store only
redacted URLs and raw API responses (video IDs, channel IDs, statistics,
snippets -- no secrets).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://www.googleapis.com/youtube/v3"

QUOTA_COSTS = {
    "search": 100,
    "videos": 1,
    "channels": 1,
    "playlistItems": 1,
}

API_KEY_ENV = "YOUTUBE_API_KEY"

UNIT_CAP_DEFAULT = 9000
SMALL_SUB_MAX_DEFAULT = 10_000
BIG_SUB_THRESHOLD = 100_000
OUTLIER_MIN_DEFAULT = 5.0
MIN_AGE_DAYS_DEFAULT = 14
PURGE_DEFAULT_DAYS = 30
SEARCH_WINDOW_DAYS = 365
MAX_RESULTS = 50
SHORT_MAX_SECONDS = 180
LONG_MIN_SECONDS = 480
LOW_CONFIDENCE_N = 20
FORMATS = ("long-form", "short-form")
SORT_OPTIONS = ("relevance", "viewCount", "rating")

_DURATION_RE = re.compile(
    r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
    r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$"
)


class QuotaExceeded(RuntimeError):
    """Raised when the next API call would exceed --unit-cap."""


class YouTubeError(RuntimeError):
    pass


def redact_url(url: str) -> str:
    """Return a copy of ``url`` with the API key value redacted."""
    return re.sub(r"(key=)[^&]+", r"\1REDACTED", url)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_duration_seconds(duration: str | None) -> int | None:
    """Parse an ISO 8601 duration (contentDetails.duration) into seconds.

    Calendar fields (years/months/weeks) cannot be converted reliably and
    return None. Returns None for unparseable input.
    """
    match = _DURATION_RE.match(duration or "")
    if not match:
        return None
    years, months, weeks, days, hours, minutes, seconds = match.groups()
    if years or months or weeks:
        return None
    values = [int(g) if g else 0 for g in (days, hours, minutes, seconds)]
    return ((values[0] * 24 + values[1]) * 60 + values[2]) * 60 + values[3]


def format_duration(seconds: int) -> str:
    """Render seconds as an ISO 8601 duration (PT#H#M#S)."""
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}H")
    if minutes or (not parts and not secs):
        parts.append(f"{minutes}M")
    if secs or not parts:
        parts.append(f"{secs}S")
    return "PT" + "".join(parts)


def classify_format(duration_s: int | None) -> str | None:
    """Classify a video by duration. Mid-length videos return None (ignored)."""
    if duration_s is None:
        return None
    if duration_s <= SHORT_MAX_SECONDS:
        return "short-form"
    if duration_s >= LONG_MIN_SECONDS:
        return "long-form"
    return None


def parse_rfc3339(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def hidden_subscriber(stats: dict) -> bool:
    """True when the channel's subscriber count is hidden/not public."""
    return bool(stats.get("hiddenSubscriberCount")) or "subscriberCount" not in stats


def channel_subscribers(stats: dict) -> int:
    return int(stats.get("subscriberCount", 0) or 0)


class YouTubeClient:
    """Thin read-only client with quota accounting and a disk cache."""

    def __init__(self, api_key: str, unit_cap: int, cache_dir=None) -> None:
        self.api_key = api_key
        self.unit_cap = unit_cap
        self.units_used = 0
        self.calls = {name: 0 for name in QUOTA_COSTS}
        self.cache_hits = 0
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.json"

    def _read_cache(self, url: str):
        path = self._cache_path(url)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return entry.get("response")

    def _write_cache(self, url: str, data) -> None:
        entry = {"fetched_at": iso_now(), "url": redact_url(url), "response": data}
        self._cache_path(url).write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")

    def fetch(self, method: str, path: str, params: dict, use_cache: bool = True):
        cost = QUOTA_COSTS[method]
        query = dict(params)
        query["key"] = self.api_key
        url = f"{BASE_URL}{path}?{urlencode(query)}"

        if use_cache and self.cache_dir:
            cached = self._read_cache(url)
            if cached is not None:
                self.cache_hits += 1
                return cached

        self._check(cost, method)
        try:
            with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=30) as resp:
                body = resp.read()
        except HTTPError as exc:
            raise YouTubeError(f"{method} {redact_url(url)} failed: HTTP {exc.code}") from exc
        except URLError as exc:
            raise YouTubeError(f"{method} {redact_url(url)} failed: {exc}") from exc
        data = json.loads(body.decode("utf-8"))

        self.units_used += cost
        self.calls[method] += 1

        if use_cache and self.cache_dir:
            self._write_cache(url, data)
        return data

    def _check(self, cost: int, method: str = "") -> None:
        if self.units_used + cost > self.unit_cap:
            raise QuotaExceeded(
                f"quota guard: {method} would take units to "
                f"{self.units_used + cost}, cap is {self.unit_cap}"
            )


def purge_cache(cache_dir, days: int) -> int:
    """Delete cache files whose fetched_at is older than ``days`` days.

    Returns the number of files removed. YouTube API Services developer
    policies require non-authorized statistics to be deleted or refreshed
    after 30 calendar days.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed = 0
    cache_dir = Path(cache_dir)
    if not cache_dir.is_dir():
        return 0
    for path in cache_dir.glob("*.json"):
        fetched_at = None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = parse_rfc3339(entry.get("fetched_at", "")) if entry.get("fetched_at") else None
        except (OSError, json.JSONDecodeError, ValueError):
            fetched_at = None
        if fetched_at is None:
            try:
                fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
        if fetched_at < cutoff:
            path.unlink()
            removed += 1
    return removed


def video_fields(item: dict) -> dict:
    stats = item.get("statistics", {})
    return {
        "video_id": item.get("id"),
        "channel_id": item.get("snippet", {}).get("channelId"),
        "channel_title": item.get("snippet", {}).get("channelTitle", ""),
        "title": item.get("snippet", {}).get("title", ""),
        "published_at": item.get("snippet", {}).get("publishedAt", ""),
        "views": int(stats.get("viewCount", 0) or 0),
        "duration_s": parse_duration_seconds(item.get("contentDetails", {}).get("duration", "")),
    }


def scan_keyword(client: YouTubeClient, keyword: str, published_after: str, min_age_days: int, now=None, sorts=("relevance",), pages: int = 1, regions=("US",)):
    """Fetch one keyword's search results plus channel baselines.

    Runs the search pipeline once per (sort, region, page) combination,
    collecting all video IDs across combinations before downstream calls.
    Defaults (sort=relevance, region=US, 1 page) preserve existing behavior.

    Returns (records, channels, baselines, stats) where stats holds
    exclusion counts. May raise QuotaExceeded mid-way.
    """
    now = now or datetime.now(timezone.utc)
    stats = {"excluded_young": 0, "excluded_hidden_channels": 0, "ignored_mid_length": 0}

    all_video_ids = []
    for sort in sorts:
        for region in regions:
            page_token = None
            for _page in range(pages):
                params = {
                    "part": "snippet",
                    "type": "video",
                    "regionCode": region,
                    "relevanceLanguage": "en",
                    "order": sort,
                    "publishedAfter": published_after,
                    "maxResults": MAX_RESULTS,
                    "q": keyword,
                }
                if page_token:
                    params["pageToken"] = page_token
                search = client.fetch(
                    "search",
                    "/search",
                    params,
                )
                video_ids = [
                    item["id"]["videoId"]
                    for item in search.get("items", [])
                    if item.get("id", {}).get("videoId")
                ]
                all_video_ids.extend(video_ids)
                page_token = search.get("nextPageToken")
                if not page_token:
                    break

    # Dedupe video IDs while preserving first-seen order.
    seen = set()
    video_ids = []
    for vid in all_video_ids:
        if vid not in seen:
            seen.add(vid)
            video_ids.append(vid)

    videos = {}
    for batch in chunks(video_ids, MAX_RESULTS):
        data = client.fetch(
            "videos",
            "/videos",
            {"part": "statistics,snippet,contentDetails", "id": ",".join(batch)},
        )
        for item in data.get("items", []):
            videos[item["id"]] = item

    records = []
    channel_ids = []
    for item in videos.values():
        fields = video_fields(item)
        if not fields["published_at"]:
            continue
        published = parse_rfc3339(fields["published_at"])
        if (now - published).days < min_age_days:
            stats["excluded_young"] += 1
            continue
        records.append(fields)
        if fields["channel_id"] and fields["channel_id"] not in channel_ids:
            channel_ids.append(fields["channel_id"])

    channels = {}
    for batch in chunks(channel_ids, MAX_RESULTS):
        data = client.fetch(
            "channels",
            "/channels",
            {"part": "statistics,snippet,contentDetails", "id": ",".join(batch)},
        )
        for item in data.get("items", []):
            channels[item["id"]] = item

    for rec in records:
        channel = channels.get(rec["channel_id"])
        if channel is None or hidden_subscriber(channel.get("statistics", {})):
            continue
        if classify_format(rec["duration_s"]) is None:
            stats["ignored_mid_length"] += 1

    baselines = {}
    for channel_id, channel in channels.items():
        if hidden_subscriber(channel.get("statistics", {})):
            stats["excluded_hidden_channels"] += 1
            continue
        uploads = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads:
            continue
        playlist = client.fetch(
            "playlistItems",
            "/playlistItems",
            {"part": "snippet", "playlistId": uploads, "maxResults": 20},
        )
        ids = [
            item["snippet"]["resourceId"]["videoId"]
            for item in playlist.get("items", [])
            if item.get("snippet", {}).get("resourceId", {}).get("videoId")
        ]
        views = {}
        for batch in chunks(ids, MAX_RESULTS):
            data = client.fetch("videos", "/videos", {"part": "statistics", "id": ",".join(batch)})
            for item in data.get("items", []):
                views[item["id"]] = int(item.get("statistics", {}).get("viewCount", 0) or 0)
        baselines[channel_id] = [(vid, views.get(vid, 0)) for vid in ids]

    return records, channels, baselines, stats


def score_records(records, channels, baselines, outlier_min: float, small_sub_max: int):
    """Score records: format, baseline (median excluding the scored video),
    outlier ratio, small-channel and big-channel flags."""
    scored = []
    for rec in records:
        channel = channels.get(rec["channel_id"])
        if channel is None or hidden_subscriber(channel.get("statistics", {})):
            continue  # channel excluded (hidden subs or missing)
        fmt = classify_format(rec["duration_s"])
        if fmt is None:
            continue  # mid-length video, ignored

        subs = channel_subscribers(channel.get("statistics", {}))
        baseline_views = [
            views for vid, views in baselines.get(rec["channel_id"], []) if vid != rec["video_id"]
        ]
        baseline = median(baseline_views) if baseline_views else None
        ratio = rec["views"] / baseline if baseline and baseline > 0 else None
        small = subs < small_sub_max
        big = subs >= BIG_SUB_THRESHOLD
        scored.append(
            {
                **rec,
                "format": fmt,
                "subs": subs,
                "baseline": baseline,
                "ratio": ratio,
                "small": small,
                "big": big,
                "outlier": ratio is not None and ratio >= outlier_min,
            }
        )
    return scored


def aggregate(bucket, n_key: str = "n"):
    n = len(bucket)
    small_wins = sum(1 for r in bucket if r["small"] and r["outlier"])
    big_channels = sum(1 for r in bucket if r["big"])
    return {
        "n": n,
        "small_wins": small_wins,
        "small_win_rate": (small_wins / n) if n else 0.0,
        "big_channels": big_channels,
        "big_channel_share": (big_channels / n) if n else 0.0,
        "low_confidence": n < LOW_CONFIDENCE_N,
    }


def load_keywords(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def estimate_units(n_keywords: int, sorts=("relevance",), pages: int = 1, regions=("US",)) -> dict:
    """Estimated units per stage for ``n_keywords`` (worst case: 50 unique
    channels per keyword, each needing one playlistItems + one videos call).

    The search cost scales by len(sorts) * pages * len(regions); downstream
    calls are deduplicated by ID so they stay roughly constant.
    """
    multiplier = len(sorts) * pages * len(regions)
    per_keyword = {
        "search.list (N calls x 100 units)": 100 * multiplier,
        "videos.list for up to 50 search results (1 call x 1 unit)": 1,
        "channels.list for up to 50 channels (1 call x 1 unit)": 1,
        "playlistItems.list + videos.list per channel (50 x 2 units)": 100,
    }
    per_keyword_total = sum(per_keyword.values())
    return {
        "n_keywords": n_keywords,
        "sorts": sorts,
        "pages": pages,
        "regions": regions,
        "combination_multiplier": multiplier,
        "per_keyword": per_keyword,
        "per_keyword_total": per_keyword_total,
        "estimated_total": n_keywords * per_keyword_total,
    }


def dry_run(args) -> int:
    data = load_keywords(Path(__file__).resolve().parent / "keywords.json")
    keywords = [kw for niche in data["niches"].values() for kw in niche["keywords"]]
    n = args.limit_keywords if args.limit_keywords else len(keywords)
    keywords = keywords[:n]

    sorts = tuple(args.sort) if args.sort else ("relevance",)
    regions = tuple(args.region) if args.region else ("US",)
    pages = args.pages if args.pages and args.pages > 0 else 1
    multiplier = len(sorts) * pages * len(regions)

    est = estimate_units(n, sorts=sorts, pages=pages, regions=regions)

    print("YouTube niche-scan --dry-run (no API calls made)")
    print(f"Keywords to scan: {n} (of {len(keywords)})")
    print(f"Search combos: {len(sorts)} sort(s) x {pages} page(s) x {len(regions)} region(s) = {multiplier} combos/keyword")
    print(f"Unit cap: {args.unit_cap}")
    print()
    print("Estimated unit cost per stage (worst case per keyword):")
    running = 0
    for stage, units in est["per_keyword"].items():
        stage_total = units * n
        running += stage_total
        print(f"  {stage}: {units} units/keyword x {n} = {stage_total} units")
    print(f"  Estimated total: ~{est['estimated_total']} units (cap {args.unit_cap})")
    if est["estimated_total"] > args.unit_cap:
        print("  NOTE: estimate exceeds --unit-cap; the run will stop early and report partial results.")
    print()
    print("Notes:")
    print("  - Estimate assumes up to 50 unique channels per keyword; actual usage")
    print("    is usually lower because channels repeat across keywords.")
    print("  - search.list = 100 units/call per the task's planning figure; the")
    print("    official quota page currently phrases search.list as a 100-call/day")
    print("    bucket at 1 unit/call -- the 100-unit figure is the conservative one.")
    print("  - channels.list = 1 unit, playlistItems.list = 1 unit, videos.list = 1 unit.")
    print("  - Warm-cache rerun costs 0 units. Cache entries older than 30 days are")
    print("    purged per the YouTube API Services developer policies.")
    return 0


def run_scan(client: YouTubeClient, niches: dict, args) -> dict:
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=SEARCH_WINDOW_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = []
    totals = {"excluded_young": 0, "excluded_hidden_channels": 0, "ignored_mid_length": 0}
    scanned = 0
    stopped_early = False

    sorts = tuple(args.sort) if args.sort else ("relevance",)
    regions = tuple(args.region) if args.region else ("US",)
    pages = args.pages if args.pages and args.pages > 0 else 1

    for niche_key, niche in niches.items():
        keywords = niche["keywords"]
        if args.limit_keywords:
            remaining = args.limit_keywords - scanned
            if remaining <= 0:
                break
            keywords = keywords[:remaining]
        for keyword in keywords:
            try:
                records, channels, baselines, stats = scan_keyword(
                    client, keyword, published_after, args.min_age_days,
                    sorts=sorts, pages=pages, regions=regions
                )
            except QuotaExceeded as exc:
                stopped_early = True
                print(f"WARNING: {exc}")
                print("Stopping scan early; reporting partial results.")
                break
            for key in ("excluded_young", "excluded_hidden_channels", "ignored_mid_length"):
                totals[key] += stats[key]
            # mid-length ignored = records fetched but not classifiable
            scored = score_records(records, channels, baselines, args.outlier_min, args.small_sub_max)
            scanned += 1

            rpm_mid = (niche["rpm_low"] + niche["rpm_high"]) / 2
            for fmt in FORMATS:
                agg = aggregate([r for r in scored if r["format"] == fmt])
                rows.append(
                    {
                        "keyword": keyword,
                        "niche": niche["name"],
                        "niche_key": niche_key,
                        "format": fmt,
                        "rpm_low": niche["rpm_low"],
                        "rpm_high": niche["rpm_high"],
                        "rpm_mid": rpm_mid,
                        "score": rpm_mid * agg["small_win_rate"],
                        **agg,
                    }
                )
            if stopped_early:
                break
        if stopped_early:
            break

    return {
        "rows": rows,
        "totals": totals,
        "scanned": scanned,
        "stopped_early": stopped_early,
        "units_used": client.units_used,
        "calls": dict(client.calls),
        "cache_hits": client.cache_hits,
    }


def write_csv(rows, path: Path) -> None:
    columns = [
        "keyword", "niche", "format", "n", "small_wins", "small_win_rate",
        "big_channels", "big_channel_share", "rpm_low", "rpm_high", "rpm_mid",
        "score", "low_confidence",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: r["score"], reverse=True):
            writer.writerow({col: row[col] for col in columns})


def fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def write_markdown(result: dict, niches: dict, args, path: Path) -> None:
    rows = result["rows"]
    totals = result["totals"]
    sorts = tuple(args.sort) if args.sort else ("relevance",)
    regions = tuple(args.region) if args.region else ("US",)

    lines = []
    lines.append("# YouTube niche-scan report")
    lines.append("")
    lines.append(f"Generated: {iso_now()}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Keywords scanned: {result['scanned']}")
    lines.append(f"- Unit cap: {args.unit_cap}; units used: {result['units_used']}")
    lines.append(f"- API cache hits: {result['cache_hits']} (warm-cache reruns cost 0 units)")
    lines.append(
        f"- Search: sort={', '.join(sorts)}, pages={args.pages}, region={', '.join(regions)}"
    )
    lines.append("- RPM ranges: vendor claims, unverified (see keywords.json)")
    lines.append("")
    if result["stopped_early"]:
        lines.append("> The run hit --unit-cap before all keywords were scanned; results are partial.")
        lines.append("")

    lines.append("## Ranking by keyword")
    lines.append("")
    lines.append("Score = rpm_mid x small_win_rate. `n` is videos classified into that format.")
    lines.append("Rows with `n < 20` are flagged LOW CONFIDENCE.")
    lines.append("")
    lines.append(
        "| rank | keyword | niche | format | n | small_win_rate | big_channel_share | rpm_mid | score | confidence |"
    )
    lines.append(
        "|------|---------|-------|--------|---|----------------|-------------------|---------|-------|------------|"
    )
    for rank, row in enumerate(sorted(rows, key=lambda r: r["score"], reverse=True), start=1):
        conf = "LOW CONFIDENCE" if row["low_confidence"] else "ok"
        lines.append(
            f"| {rank} | {row['keyword']} | {row['niche']} | {row['format']} | "
            f"{row['n']} | {fmt_pct(row['small_win_rate'])} | {fmt_pct(row['big_channel_share'])} | "
            f"{row['rpm_mid']:g} | {row['score']:.4f} | {conf} |"
        )
    lines.append("")

    lines.append("## Aggregate by niche")
    lines.append("")
    lines.append(
        "| niche | rpm range (vendor claim) | n long | small_win_rate long | score long | n short | small_win_rate short | score short |"
    )
    lines.append(
        "|-------|--------------------------|--------|---------------------|------------|---------|----------------------|-------------|"
    )
    for niche_key, niche in niches.items():
        long_row = next(
            (r for r in rows if r["niche_key"] == niche_key and r["format"] == "long-form"), None
        )
        short_row = next(
            (r for r in rows if r["niche_key"] == niche_key and r["format"] == "short-form"), None
        )

        def cell(row):
            if row is None:
                return "n/a", "n/a", "n/a"
            return row["n"], fmt_pct(row["small_win_rate"]), f"{row['score']:.4f}"

        ln, lw, ls = cell(long_row)
        sn, sw, ss = cell(short_row)
        lines.append(
            f"| {niche['name']} | {niche['rpm_low']}-{niche['rpm_high']} | {ln} | {lw} | {ls} | "
            f"{sn} | {sw} | {ss} |"
        )
    lines.append("")

    lines.append("## Long-form vs short-form (side by side)")
    lines.append("")
    lines.append(
        "| keyword | niche | long n | long win rate | long score | short n | short win rate | short score |"
    )
    lines.append(
        "|---------|-------|--------|---------------|------------|---------|----------------|-------------|"
    )
    by_keyword: dict[str, dict] = {}
    for row in rows:
        by_keyword.setdefault(row["keyword"], {"niche": row["niche"]})[row["format"]] = row
    for keyword, data in sorted(by_keyword.items()):
        long_row = data.get("long-form")
        short_row = data.get("short-form")

        def col(row, key):
            return "" if row is None else row[key]

        lines.append(
            f"| {keyword} | {data['niche']} | {col(long_row, 'n')} | "
            f"{fmt_pct(col(long_row, 'small_win_rate')) if long_row else ''} | "
            f"{col(long_row, 'score'):.4f} | {col(short_row, 'n')} | "
            f"{fmt_pct(col(short_row, 'small_win_rate')) if short_row else ''} | "
            f"{col(short_row, 'score'):.4f} |"
        )
    lines.append("")

    lines.append("## Excluded counts")
    lines.append(f"- Videos skipped (younger than {args.min_age_days} days): {totals['excluded_young']}")
    lines.append(f"- Channels skipped (hidden subscriber count): {totals['excluded_hidden_channels']}")
    lines.append(f"- Videos ignored (mid-length, {SHORT_MAX_SECONDS + 1}-{LONG_MIN_SECONDS - 1}s): {totals['ignored_mid_length']}")
    lines.append("")

    lines.append("## Units used")
    lines.append("")
    lines.append("| endpoint | cost/unit | calls | units |")
    lines.append("|----------|-----------|-------|-------|")
    unit_total = 0
    call_total = 0
    for method in ("search", "videos", "channels", "playlistItems"):
        calls = result["calls"][method]
        units = calls * QUOTA_COSTS[method]
        unit_total += units
        call_total += calls
        lines.append(f"| {method}.list | {QUOTA_COSTS[method]} | {calls} | {units} |")
    lines.append(f"| **total** | | **{call_total}** | **{unit_total}** |")
    lines.append("")

    lines.append("## Known limits")
    lines.append(
        f"- Search results use order={', '.join(sorts)}, region={', '.join(regions)}, "
        f"and at most {args.pages} page(s) per keyword. This is a bounded, non-random sample."
    )
    if len(sorts) > 1 or len(regions) > 1 or args.pages > 1:
        lines.append(
            "- Results from query combinations are deduplicated by video ID before scoring; "
            "this expands coverage but does not create independent or representative samples."
        )
    lines.append(
        "- Search ordering and pagination favor some videos/channels over others; rankings can "
        "change with a different order, region, or page depth."
    )
    lines.append(
        "- Shorts are not exposed by the API; `<= 180s` duration is used as a proxy for short-form."
    )
    lines.append("- The API cannot show audience country; RPM is treated as niche-level only.")
    lines.append("- Score = rpm_mid x small_win_rate is an unvalidated heuristic, not a revenue forecast.")
    lines.append("- Cache entries are raw API responses; subscriber/view counts are refreshed at most every 30 days per policy.")
    lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YouTube niche-scan tool (read-only, API-key only)."
    )
    parser.add_argument("--dry-run", action="store_true", help="print estimated unit cost, make no API calls")
    parser.add_argument("--limit-keywords", type=int, default=None, help="scan only the first N keywords")
    parser.add_argument("--unit-cap", type=int, default=UNIT_CAP_DEFAULT, help="hard unit cap (default 9000)")
    parser.add_argument("--small-sub-max", type=int, default=SMALL_SUB_MAX_DEFAULT, help="channel is small below this subscriber count (default 10000)")
    parser.add_argument("--outlier-min", type=float, default=OUTLIER_MIN_DEFAULT, help="outlier ratio threshold (default 5)")
    parser.add_argument("--min-age-days", type=int, default=MIN_AGE_DAYS_DEFAULT, help="skip videos younger than N days (default 14)")
    parser.add_argument("--purge-cache-older-than", type=int, default=PURGE_DEFAULT_DAYS, help="purge cache entries older than N days (default 30; set 0 to disable)")
    parser.add_argument(
        "--sort",
        action="append",
        choices=SORT_OPTIONS,
        default=None,
        help="search order (default: relevance); repeatable",
    )
    parser.add_argument("--pages", type=int, default=1, help="number of search result pages per keyword (default 1)")
    parser.add_argument("--region", action="append", default=None, help="regionCode (default US); repeatable")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.dry_run:
        return dry_run(args)

    api_key = os.getenv(API_KEY_ENV)
    if not api_key:
        print(f"ERROR: {API_KEY_ENV} environment variable is not set.", file=sys.stderr)
        print("Example (current PowerShell session only): $env:YOUTUBE_API_KEY='...'", file=sys.stderr)
        return 2

    project_root = Path(__file__).resolve().parent
    cache_dir = project_root / "data" / "cache"
    outputs_dir = project_root / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    if args.purge_cache_older_than and args.purge_cache_older_than > 0:
        removed = purge_cache(cache_dir, args.purge_cache_older_than)
        print(f"Purged {removed} cache entry/entries older than {args.purge_cache_older_than} days.")

    client = YouTubeClient(api_key, args.unit_cap, cache_dir)
    data = load_keywords(project_root / "keywords.json")
    result = run_scan(client, data["niches"], args)

    rows = result["rows"]
    write_csv(rows, outputs_dir / "results.csv")
    write_markdown(result, data["niches"], args, outputs_dir / "report.md")

    print(f"Scan complete: {result['scanned']} keywords, {len(rows)} keyword/format rows.")
    print(f"Units used: {result['units_used']} / cap {args.unit_cap} (calls: {result['calls']})")
    print(f"Cache hits: {result['cache_hits']}")
    print(f"Wrote {outputs_dir / 'results.csv'} and {outputs_dir / 'report.md'}")
    if result["stopped_early"]:
        print("WARNING: run stopped early by --unit-cap; results are partial.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
