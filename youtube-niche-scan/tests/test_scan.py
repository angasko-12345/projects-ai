"""Tests for the YouTube niche-scan tool. No live API calls; HTTP is mocked."""
import json
from datetime import datetime, timezone

import pytest

import scan
from scan import YouTubeClient

KEY = "test-key-12345"


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def fixed_now_payload(seconds: int, channel_id: str = "ch", video_id: str = "vid") -> dict:
    """One video item published 60 days before 2026-06-01."""
    return {
        "id": video_id,
        "snippet": {
            "channelId": channel_id,
            "channelTitle": "ch",
            "title": "t",
            "publishedAt": "2026-04-01T00:00:00Z",
        },
        "contentDetails": {"duration": scan.format_duration(seconds)},
        "statistics": {"viewCount": "100"},
    }


# --- duration parsing / format classification ---------------------------------


def test_duration_boundary_180s_is_short():
    assert scan.classify_format(scan.parse_duration_seconds("PT180S")) == "short-form"


def test_duration_181s_is_ignored():
    assert scan.classify_format(scan.parse_duration_seconds("PT181S")) is None


def test_duration_479s_is_ignored():
    assert scan.classify_format(scan.parse_duration_seconds("PT479S")) is None


def test_duration_boundary_480s_is_long():
    assert scan.classify_format(scan.parse_duration_seconds("PT480S")) == "long-form"


def test_duration_hms_and_invalid():
    assert scan.parse_duration_seconds("PT3M") == 180
    assert scan.parse_duration_seconds("PT8M") == 480
    assert scan.parse_duration_seconds("PT1H") == 3600
    assert scan.parse_duration_seconds("P1Y2M") is None
    assert scan.parse_duration_seconds("garbage") is None
    assert scan.parse_duration_seconds(None) is None


def test_format_duration_normalizes_minutes():
    assert scan.format_duration(180) == "PT3M"
    assert scan.format_duration(480) == "PT8M"
    assert scan.format_duration(3600) == "PT1H"


# --- pure scoring helpers ------------------------------------------------------


def test_outlier_ratio_excludes_scored_video_from_baseline():
    baselines = {"chA": [("v-scored", 1000), ("v1", 100), ("v2", 200), ("v3", 300), ("v4", 400), ("v5", 500)]}
    channels = {"chA": {"statistics": {"subscriberCount": "5000"}}}
    records = [dict(video_id="v-scored", channel_id="chA", views=2000, duration_s=600)]
    scored = scan.score_records(records, channels, baselines, outlier_min=5.0, small_sub_max=10_000)
    r = scored[0]
    assert r["baseline"] == 300  # median of [100, 200, 300, 400, 500]
    assert r["ratio"] == pytest.approx(2000 / 300)
    assert r["outlier"] is True
    assert r["small"] is True
    assert r["big"] is False


def test_outlier_ratio_missing_baseline_is_not_a_win():
    baselines = {"chA": []}
    channels = {"chA": {"statistics": {"subscriberCount": "5000"}}}
    records = [dict(video_id="v1", channel_id="chA", views=1_000_000, duration_s=600)]
    scored = scan.score_records(records, channels, baselines, outlier_min=5.0, small_sub_max=10_000)
    assert scored[0]["ratio"] is None
    assert scored[0]["outlier"] is False


def test_outlier_ratio_zero_baseline_is_not_a_win():
    baselines = {"chA": [("v1", 0), ("v2", 0)]}
    channels = {"chA": {"statistics": {"subscriberCount": "5000"}}}
    records = [dict(video_id="v9", channel_id="chA", views=100, duration_s=600)]
    scored = scan.score_records(records, channels, baselines, outlier_min=5.0, small_sub_max=10_000)
    assert scored[0]["ratio"] is None


def test_small_win_rate_and_big_channel_share():
    # 10 long-form videos: 8 small-channel outliers, 2 big channels
    records = []
    channels = {}
    baselines = {}
    for i in range(10):
        cid = "ch-big" if i >= 8 else f"ch-{i}"
        channels[cid] = {"statistics": {"subscriberCount": "200000" if i >= 8 else "5000"}}
        baselines[cid] = [("other", 100)]
        records.append(dict(video_id=f"v{i}", channel_id=cid, views=1000, duration_s=600))
    scored = scan.score_records(records, channels, baselines, outlier_min=5.0, small_sub_max=10_000)
    bucket = [r for r in scored if r["format"] == "long-form"]
    agg = scan.aggregate(bucket)
    assert agg["n"] == 10
    assert agg["small_wins"] == 8  # ch-0..ch-7 are small and outliers
    assert agg["small_win_rate"] == 0.8
    assert agg["big_channels"] == 2
    assert agg["big_channel_share"] == 0.2
    assert agg["low_confidence"] is True


def test_low_confidence_at_n_19_and_ok_at_n_20():
    def mk(n):
        records = []
        channels = {}
        baselines = {}
        for i in range(n):
            cid = f"ch-{i}"
            channels[cid] = {"statistics": {"subscriberCount": "5000"}}
            baselines[cid] = [("other", 100)]
            records.append(dict(video_id=f"v{i}", channel_id=cid, views=1000, duration_s=600))
        scored = scan.score_records(records, channels, baselines, outlier_min=5.0, small_sub_max=10_000)
        return scan.aggregate([r for r in scored if r["format"] == "long-form"])

    assert mk(19)["low_confidence"] is True
    assert mk(20)["low_confidence"] is False


def test_empty_bucket_rates_are_zero():
    agg = scan.aggregate([])
    assert agg["n"] == 0
    assert agg["small_win_rate"] == 0.0
    assert agg["big_channel_share"] == 0.0
    assert agg["low_confidence"] is True


# --- hidden subscriber / min-age exclusions ------------------------------------


def test_hidden_subscriber_channel_excluded():
    records = [dict(video_id="v1", channel_id="ch-hidden", views=100, duration_s=600)]
    channels = {"ch-hidden": {"statistics": {}}}  # no subscriberCount -> hidden
    baselines = {}
    scored = scan.score_records(records, channels, baselines, outlier_min=5.0, small_sub_max=10_000)
    assert scored == []


def test_hidden_subscriber_flag_excluded():
    records = [dict(video_id="v1", channel_id="ch-hidden", views=100, duration_s=600)]
    channels = {"ch-hidden": {"statistics": {"hiddenSubscriberCount": True, "subscriberCount": "50"}}}
    baselines = {}
    scored = scan.score_records(records, channels, baselines, outlier_min=5.0, small_sub_max=10_000)
    assert scored == []


def test_min_age_exclusion(monkeypatch):
    fixed_now = datetime(2026, 6, 1, tzinfo=timezone.utc)

    videos_by_id = {
        "v-old": fixed_now_payload(600, channel_id="ch", video_id="v-old"),
        "v-young": {
            **fixed_now_payload(600, channel_id="ch", video_id="v-young"),
            "snippet": {**fixed_now_payload(600)["snippet"], "publishedAt": "2026-05-28T00:00:00Z"},
        },
    }

    def fake_fetch(self, method, path, params, use_cache=True):
        if path == "/search":
            return {"items": [{"id": {"videoId": "v-old"}}, {"id": {"videoId": "v-young"}}]}
        if path == "/videos":
            ids = [i.strip() for i in params["id"].split(",")]
            return {"items": [videos_by_id[i] for i in ids if i in videos_by_id]}
        if path == "/channels":
            return {"items": [{"id": "ch", "statistics": {"subscriberCount": "5000"}, "contentDetails": {"relatedPlaylists": {"uploads": "pl"}}}]}
        if path == "/playlistItems":
            return {"items": []}
        raise AssertionError(f"unexpected {path}")

    monkeypatch.setattr(YouTubeClient, "fetch", fake_fetch)
    client = YouTubeClient(KEY, unit_cap=10_000)
    records, channels, baselines, stats = scan.scan_keyword(
        client, "kw", "2025-06-01T00:00:00Z", min_age_days=14, now=fixed_now
    )
    assert stats["excluded_young"] == 1
    assert [r["video_id"] for r in records] == ["v-old"]


# --- quota accounting ----------------------------------------------------------


def _patch_urlopen(monkeypatch, payload):
    def fake_urlopen(req, timeout=None):
        return FakeResponse(payload)

    monkeypatch.setattr(scan, "urlopen", fake_urlopen)


def test_quota_accounting_counts_units(monkeypatch):
    _patch_urlopen(monkeypatch, {"items": []})
    client = YouTubeClient(KEY, unit_cap=10_000)
    client.fetch("search", "/search", {"q": "x"})
    client.fetch("videos", "/videos", {"id": "v1"})
    client.fetch("channels", "/channels", {"id": "c1"})
    client.fetch("playlistItems", "/playlistItems", {"playlistId": "p1"})

    assert client.units_used == 103
    assert client.calls == {"search": 1, "videos": 1, "channels": 1, "playlistItems": 1}


def test_unit_cap_stops_before_exceeding(monkeypatch):
    _patch_urlopen(monkeypatch, {"items": []})
    client = YouTubeClient(KEY, unit_cap=100)
    client.fetch("search", "/search", {"q": "x"})  # 100 units, exactly at cap
    assert client.units_used == 100
    with pytest.raises(scan.QuotaExceeded):
        client.fetch("videos", "/videos", {"id": "v1"})  # would be 101 > 100
    assert client.units_used == 100  # the rejected call consumed nothing


# --- cache ---------------------------------------------------------------------


def test_warm_cache_costs_zero_units(tmp_path, monkeypatch):
    http_calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        http_calls["n"] += 1
        if http_calls["n"] > 1:
            raise AssertionError("HTTP must not be called on a warm cache")
        return FakeResponse({"items": []})

    monkeypatch.setattr(scan, "urlopen", fake_urlopen)

    client = YouTubeClient(KEY, unit_cap=10_000, cache_dir=tmp_path)
    client.fetch("search", "/search", {"q": "x"}, use_cache=True)
    assert client.units_used == 100
    assert http_calls["n"] == 1

    client2 = YouTubeClient(KEY, unit_cap=10_000, cache_dir=tmp_path)
    client2.fetch("search", "/search", {"q": "x"}, use_cache=True)
    assert client2.units_used == 0
    assert client2.cache_hits == 1
    assert http_calls["n"] == 1  # still one total HTTP call


def test_purge_cache_removes_old_entries(tmp_path):
    (tmp_path / "old.json").write_text(
        json.dumps({"fetched_at": "2020-01-01T00:00:00Z", "response": {}}), encoding="utf-8"
    )
    (tmp_path / "new.json").write_text(
        json.dumps({"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "response": {}}),
        encoding="utf-8",
    )
    removed = scan.purge_cache(tmp_path, days=30)
    assert removed == 1
    assert (tmp_path / "new.json").is_file()


# --- dry-run -------------------------------------------------------------------


def test_dry_run_makes_no_http_call(monkeypatch, capsys):
    def fake_urlopen(req, timeout=None):
        raise AssertionError("dry-run must not call the API")

    monkeypatch.setattr(scan, "urlopen", fake_urlopen)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)

    code = scan.main(["--dry-run", "--limit-keywords", "30"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Estimated total:" in out
    assert "units" in out


def test_dry_run_respects_limit_keywords(capsys):
    scan.main(["--dry-run", "--limit-keywords", "1"])
    out = capsys.readouterr().out
    assert "Keywords to scan: 1" in out
    assert "Estimated total: ~202 units" in out


# --- scan_keyword end-to-end with canned responses -----------------------------


def test_scan_keyword_scoring_end_to_end(monkeypatch):
    """Canned pipeline: long video from small channel is an outlier win; big
    channel video is not; short video counts; mid-length ignored."""
    fixed_now = datetime(2026, 6, 1, tzinfo=timezone.utc)

    videos_by_id = {
        "v-long": fixed_now_payload(600, channel_id="ch-small", video_id="v-long"),
        "v-short": fixed_now_payload(120, channel_id="ch-small", video_id="v-short"),
        "v-mid": fixed_now_payload(300, channel_id="ch-small", video_id="v-mid"),
        "v-big": fixed_now_payload(600, channel_id="ch-big", video_id="v-big"),
        "b1": fixed_now_payload(100, channel_id="ch-small", video_id="b1"),
        "b2": fixed_now_payload(100, channel_id="ch-small", video_id="b2"),
        "b3": fixed_now_payload(100, channel_id="ch-small", video_id="b3"),
        "b4": fixed_now_payload(100, channel_id="ch-small", video_id="b4"),
        "g1": fixed_now_payload(100, channel_id="ch-big", video_id="g1"),
    }
    # Override view counts per video
    view_counts = {"v-long": "5000", "v-short": "5000", "v-mid": "5000", "v-big": "100"}
    for vid, count in view_counts.items():
        videos_by_id[vid]["statistics"]["viewCount"] = count

    def fake_fetch(self, method, path, params, use_cache=True):
        if path == "/search":
            return {
                "items": [
                    {"id": {"videoId": "v-long"}},
                    {"id": {"videoId": "v-short"}},
                    {"id": {"videoId": "v-mid"}},
                    {"id": {"videoId": "v-big"}},
                ]
            }
        if path == "/videos":
            ids = [i.strip() for i in params["id"].split(",")]
            return {"items": [videos_by_id[i] for i in ids if i in videos_by_id]}
        if path == "/channels":
            return {
                "items": [
                    {
                        "id": "ch-small",
                        "statistics": {"subscriberCount": "5000"},
                        "contentDetails": {"relatedPlaylists": {"uploads": "pl-small"}},
                    },
                    {
                        "id": "ch-big",
                        "statistics": {"subscriberCount": "200000"},
                        "contentDetails": {"relatedPlaylists": {"uploads": "pl-big"}},
                    },
                ]
            }
        if path == "/playlistItems":
            pid = params["playlistId"]
            items = (
                [
                    {"snippet": {"resourceId": {"videoId": "v-long"}}},
                    {"snippet": {"resourceId": {"videoId": "b1"}}},
                    {"snippet": {"resourceId": {"videoId": "b2"}}},
                    {"snippet": {"resourceId": {"videoId": "b3"}}},
                    {"snippet": {"resourceId": {"videoId": "b4"}}},
                ]
                if pid == "pl-small"
                else [
                    {"snippet": {"resourceId": {"videoId": "v-big"}}},
                    {"snippet": {"resourceId": {"videoId": "g1"}}},
                ]
            )
            return {"items": items}
        raise AssertionError(f"unexpected {path} {params}")

    monkeypatch.setattr(YouTubeClient, "fetch", fake_fetch)
    client = YouTubeClient(KEY, unit_cap=10_000)
    records, channels, baselines, stats = scan.scan_keyword(
        client, "kw", "2025-06-01T00:00:00Z", min_age_days=14, now=fixed_now
    )
    assert stats["excluded_young"] == 0
    assert stats["excluded_hidden_channels"] == 0
    assert stats["ignored_mid_length"] == 1  # v-mid (300s)

    scored = scan.score_records(records, channels, baselines, outlier_min=5.0, small_sub_max=10_000)
    long_bucket = [r for r in scored if r["format"] == "long-form"]
    short_bucket = [r for r in scored if r["format"] == "short-form"]

    assert len(long_bucket) == 2  # v-long, v-big
    assert len(short_bucket) == 1  # v-short

    long_v = next(r for r in long_bucket if r["video_id"] == "v-long")
    # baseline of ch-small excluding v-long: median([100,100,100,100]) = 100
    assert long_v["baseline"] == 100
    assert long_v["ratio"] == 50.0
    assert long_v["outlier"] is True
    assert long_v["small"] is True

    big_v = next(r for r in long_bucket if r["video_id"] == "v-big")
    assert big_v["big"] is True
    assert big_v["small"] is False
    assert big_v["outlier"] is False  # ratio 1.0 < 5

    assert short_bucket[0]["outlier"] is True


def test_scan_keyword_stops_on_quota(monkeypatch):
    """Mid-scan QuotaExceeded propagates so run_scan can report partial results."""
    calls = {"n": 0}

    def fake_fetch(self, method, path, params, use_cache=True):
        calls["n"] += 1
        if calls["n"] > 1:
            raise scan.QuotaExceeded("cap hit")
        return {"items": [{"id": {"videoId": "v1"}}]}

    monkeypatch.setattr(YouTubeClient, "fetch", fake_fetch)
    client = YouTubeClient(KEY, unit_cap=100)
    with pytest.raises(scan.QuotaExceeded):
        scan.scan_keyword(client, "kw", "2025-06-01T00:00:00Z", min_age_days=14)
    assert calls["n"] == 2  # search (call 1) consumed the cap; call 2 is rejected


# --- new sort / page / region parameters ---------------------------------------


def test_scan_keyword_defaults_single_search(monkeypatch):
    """Default behavior: single search call, order=relevance, regionCode=US,
    no pageToken."""
    calls = []

    def fake_fetch(self, method, path, params, use_cache=True):
        calls.append((method, path, dict(params)))
        return {"items": []}

    monkeypatch.setattr(YouTubeClient, "fetch", fake_fetch)
    client = YouTubeClient(KEY, unit_cap=10_000)
    scan.scan_keyword(
        client, "kw", "2025-06-01T00:00:00Z", min_age_days=14
    )
    search_calls = [c for c in calls if c[1] == "/search"]
    assert len(search_calls) == 1
    _, _, params = search_calls[0]
    assert params["order"] == "relevance"
    assert params["regionCode"] == "US"
    assert "pageToken" not in params


def test_scan_keyword_multiple_sorts(monkeypatch):
    calls = []

    def fake_fetch(self, method, path, params, use_cache=True):
        calls.append(dict(params))
        return {"items": []}

    monkeypatch.setattr(YouTubeClient, "fetch", fake_fetch)
    client = YouTubeClient(KEY, unit_cap=10_000)
    scan.scan_keyword(
        client, "kw", "2025-06-01T00:00:00Z", min_age_days=14,
        sorts=("relevance", "viewCount"), regions=("US",), pages=1
    )
    search_calls = [c for c in calls if "order" in c]
    assert len(search_calls) == 2
    orders = {c["order"] for c in search_calls}
    assert orders == {"relevance", "viewCount"}
    for c in search_calls:
        assert c["regionCode"] == "US"


def test_scan_keyword_multiple_pages_uses_page_token(monkeypatch):
    calls = []

    def fake_fetch(self, method, path, params, use_cache=True):
        calls.append(dict(params))
        if "pageToken" in params:
            return {"items": [], "nextPageToken": None}
        return {"items": [], "nextPageToken": "token2"}

    monkeypatch.setattr(YouTubeClient, "fetch", fake_fetch)
    client = YouTubeClient(KEY, unit_cap=10_000)
    scan.scan_keyword(
        client, "kw", "2025-06-01T00:00:00Z", min_age_days=14,
        sorts=("relevance",), regions=("US",), pages=2
    )
    search_calls = [c for c in calls if "order" in c]
    assert len(search_calls) == 2
    assert "pageToken" not in search_calls[0]
    assert search_calls[1]["pageToken"] == "token2"


def test_scan_keyword_stops_pagination_without_next_page_token(monkeypatch):
    calls = []

    def fake_fetch(self, method, path, params, use_cache=True):
        calls.append(dict(params))
        return {"items": [], "nextPageToken": None}

    monkeypatch.setattr(YouTubeClient, "fetch", fake_fetch)
    client = YouTubeClient(KEY, unit_cap=10_000)
    scan.scan_keyword(
        client, "kw", "2025-06-01T00:00:00Z", min_age_days=14,
        sorts=("relevance",), regions=("US",), pages=5
    )
    search_calls = [c for c in calls if "order" in c]
    assert len(search_calls) == 1  # stops after first page when no nextPageToken


def test_scan_keyword_multiple_regions(monkeypatch):
    calls = []

    def fake_fetch(self, method, path, params, use_cache=True):
        calls.append(dict(params))
        return {"items": []}

    monkeypatch.setattr(YouTubeClient, "fetch", fake_fetch)
    client = YouTubeClient(KEY, unit_cap=10_000)
    scan.scan_keyword(
        client, "kw", "2025-06-01T00:00:00Z", min_age_days=14,
        sorts=("relevance",), regions=("US", "UK"), pages=1
    )
    search_calls = [c for c in calls if "order" in c]
    assert len(search_calls) == 2
    regions = {c["regionCode"] for c in search_calls}
    assert regions == {"US", "UK"}


def test_estimate_units_multiplier():
    est = scan.estimate_units(
        30, sorts=("relevance", "viewCount"), pages=2, regions=("US", "UK")
    )
    assert est["combination_multiplier"] == 8  # 2 sorts x 2 pages x 2 regions
    assert est["per_keyword"]["search.list (N calls x 100 units)"] == 800
    assert est["estimated_total"] == 30 * (800 + 1 + 1 + 100)


def test_estimate_units_default_multiplier_is_one():
    est = scan.estimate_units(30)
    assert est["combination_multiplier"] == 1
    assert est["per_keyword"]["search.list (N calls x 100 units)"] == 100


def test_dry_run_shows_combo_multiplier(monkeypatch, capsys):
    def fake_urlopen(req, timeout=None):
        raise AssertionError("dry-run must not call the API")

    monkeypatch.setattr(scan, "urlopen", fake_urlopen)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    scan.main(
        [
            "--dry-run",
            "--limit-keywords",
            "1",
            "--sort",
            "relevance",
            "--sort",
            "viewCount",
            "--pages",
            "2",
            "--region",
            "US",
            "--region",
            "UK",
        ]
    )
    out = capsys.readouterr().out
    assert "Search combos: 2 sort(s) x 2 page(s) x 2 region(s) = 8 combos/keyword" in out
    assert "800 units/keyword x 1 = 800 units" in out
