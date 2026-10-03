"""Tests for the Pexels client: field names, rendition choice, caching."""
import io
import urllib.request

import pytest

from app import pexels


def _file(width, height, link="https://cdn.invalid/v.mp4", file_type="video/mp4"):
    return {"width": width, "height": height, "link": link, "file_type": file_type}


def test_annotations_resolve():
    """`Any` was used but never imported, breaking get_type_hints."""
    import typing
    assert typing.get_type_hints(pexels._get)["return"] == typing.Dict[str, typing.Any]


def test_pick_file_prefers_portrait_1080():
    files = [_file(1920, 1080), _file(1080, 1920), _file(720, 1280)]
    best = pexels._pick_file(files)
    assert (best["width"], best["height"]) == (1080, 1920)


def test_pick_file_falls_back_to_any_portrait():
    best = pexels._pick_file([_file(1920, 1080), _file(720, 1280)])
    assert best["height"] > best["width"]


def test_pick_file_uses_largest_when_only_landscape():
    best = pexels._pick_file([_file(640, 360), _file(1920, 1080)])
    assert (best["width"], best["height"]) == (1920, 1080)


def test_pick_file_ignores_hls_manifest():
    """HLS entries have no dimensions and are not downloadable media."""
    hls = {"width": None, "height": None, "link": "https://x.invalid/i.m3u8",
           "file_type": "video/mp4"}
    assert pexels._pick_file([hls]) is None


def test_pick_file_requires_link_field():
    """Pexels uses `link`; entries without one are unusable."""
    assert pexels._pick_file([{"width": 1080, "height": 1920,
                              "file_type": "video/mp4"}]) is None


def test_search_empty_query_makes_no_request(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("should not call the API for an empty query")

    monkeypatch.setattr(pexels, "_get", fail)
    assert pexels.search_videos("   ") == []


def test_search_retries_without_orientation_on_empty_portrait(monkeypatch):
    calls = []

    def fake_get(path, params=None, timeout=60):
        calls.append(dict(params or {}))
        if params and params.get("orientation") == "portrait":
            return {"videos": []}
        return {"videos": [{"id": 1, "duration": 5, "url": "page",
                            "user": {"name": "Someone"},
                            "video_files": [_file(1080, 1920)]}]}

    monkeypatch.setattr(pexels, "_get", fake_get)
    out = pexels.search_videos("forest")
    assert len(out) == 1
    assert out[0]["id"] == 1
    assert calls[0].get("orientation") == "portrait"
    assert "orientation" not in calls[1]


def test_search_handles_user_missing(monkeypatch):
    """A video object without a `user` key must not raise."""
    res = {"videos": [{"id": 7, "video_files": [_file(1080, 1920)]}]}
    monkeypatch.setattr(pexels, "_get", lambda path, params=None, timeout=60: res)
    out = pexels.search_videos("x")
    assert out[0]["user"] is None
    assert out[0]["url"] == "https://cdn.invalid/v.mp4"


def test_search_propagates_rate_limit(monkeypatch):
    def always_429(path, params=None, timeout=60):
        raise pexels.PexelsError("rate limited", status=429, retryable=True)

    monkeypatch.setattr(pexels, "_get", always_429)
    with pytest.raises(pexels.PexelsError):
        pexels.search_videos("x")


def test_get_retries_429_then_succeeds(fake_env, monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"videos": []}'

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(b""))
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pexels.time, "sleep", lambda s: None)
    assert pexels._get("/search/videos", {"query": "x"}) == {"videos": []}
    assert calls["n"] == 3


def test_get_does_not_retry_401(fake_env, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pexels.time, "sleep", lambda s: None)
    with pytest.raises(pexels.PexelsError):
        pexels._get("/search/videos")
    assert calls["n"] == 1


def test_looks_like_mp4_rejects_html(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"<html><body>403 Forbidden</body></html>" * 100)
    assert pexels._looks_like_mp4(bad) is False


def test_download_requires_url():
    with pytest.raises(pexels.PexelsError):
        pexels.download_video({"id": 1, "url": None})


def test_download_does_not_cache_invalid_file(monkeypatch, tmp_path):
    """A failed download must not leave a truncated file that looks cached."""
    monkeypatch.setattr(pexels, "cache_dir", lambda: tmp_path)

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, size=-1):
            # Serve an HTML error page once, then EOF.
            if not hasattr(self, "_sent"):
                self._sent = True
                return b"<html>403 Forbidden</html>" * 500
            return b""

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    monkeypatch.setattr(pexels.time, "sleep", lambda s: None)

    with pytest.raises(pexels.PexelsError):
        pexels.download_video({"id": "123", "url": "https://x.invalid/a.mp4"})

    assert not (tmp_path / "123.mp4").exists()
    assert not list(tmp_path.glob("*.part"))