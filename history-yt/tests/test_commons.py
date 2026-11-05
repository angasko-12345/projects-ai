import io
import json
import os
import random
from unittest import mock

import pytest
from PIL import Image

from histyt import commons, scenes
from histyt.state import PipelineError

SEARCH_PAYLOAD = {
    "query": {
        "pages": {
            "1": {
                "index": 1,
                "title": "File:Legion.jpg",
                "imageinfo": [{
                    "thumburl": "https://upload.example/legion.jpg",
                    "descriptionurl": "https://commons.example/legion",
                    "extmetadata": {
                        "LicenseShortName": {"value": "Public domain"},
                        "Artist": {"value": "<a href='#'>Painter</a>"},
                    },
                }],
            },
            "2": {
                "index": 2,
                "title": "File:Modern.jpg",
                "imageinfo": [{
                    "thumburl": "https://upload.example/modern.jpg",
                    "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}},
                }],
            },
        }
    }
}


class FakeResponse:
    def __init__(self, payload=None, content=b"", content_type="image/jpeg"):
        self._payload = payload
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

    def iter_content(self, size):
        yield self.content


def test_normalize_license_only_allows_project_licenses():
    assert commons.normalize_license("Public domain") == "public domain"
    assert commons.normalize_license("CC0") == "cc0"
    assert commons.normalize_license("CC BY 4.0") == "cc-by"
    assert commons.normalize_license("CC BY-SA 4.0") is None
    assert commons.normalize_license("All rights reserved") is None


def test_search_filters_unlicensed_candidates():
    with mock_get(FakeResponse(payload=SEARCH_PAYLOAD)):
        candidates = commons.search_images("roman legion")
    assert len(candidates) == 1
    assert candidates[0]["license"] == "public domain"
    assert candidates[0]["attribution"] == "Painter"
    assert candidates[0]["url"].startswith("https://")


def test_download_rejects_non_image_content(tmp_path):
    response = FakeResponse(content=b"{}", content_type="application/json")
    with mock_get(response):
        with pytest.raises(PipelineError, match="not an image"):
            commons.download_image("https://upload.example/x", str(tmp_path / "x"))


def test_fetch_images_merges_into_assets(runs, make_video):
    vid = make_video(with_sources=False)
    search = FakeResponse(payload=SEARCH_PAYLOAD)
    image = FakeResponse(content=b"jpeg-bytes")

    def fake_get(url, *args, **kwargs):
        return search if url == commons.COMMONS_API else image

    with mock.patch("histyt.commons.requests.get", side_effect=fake_get):
        added = commons.fetch_images(runs, vid, "roman legion", limit=2)
    assert len(added) == 1
    path = os.path.join(runs, vid, "assets.json")
    with open(path, encoding="utf-8") as f:
        assets = json.load(f)
    assert len(assets) == 1
    assert assets[0]["license"] == "public domain"
    assert assets[0]["source_url"] == "https://commons.example/legion"
    assert os.path.isfile(assets[0]["path"])


def test_fetch_images_fails_when_no_allowed_license(runs, make_video):
    vid = make_video(with_sources=False)
    payload = {"query": {"pages": {"1": {
        "index": 1, "title": "File:Modern.jpg",
        "imageinfo": [{"thumburl": "https://upload.example/modern.jpg",
                       "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}}}]}}}}
    with mock_get(FakeResponse(payload=payload)):
        with pytest.raises(PipelineError, match="no allowed-license images"):
            commons.fetch_images(runs, vid, "modern")


def mock_get(response):
    return mock.patch("histyt.commons.requests.get", return_value=response)


# --- per-scene resolution (visuals-fetch --all) -----------------------------


def _png_bytes(seed):
    """Noise image: distinct seeds give distinct average hashes."""
    rnd = random.Random(seed)
    img = Image.new("L", (16, 16))
    img.putdata([rnd.randrange(256) for _ in range(16 * 16)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _candidate(key, width=2000, height=1200, license_id="public domain"):
    return {
        "title": f"File:{key}.jpg",
        "url": f"https://upload.example/{key}.jpg",
        "page_url": f"https://commons.example/{key}",
        "license": license_id,
        "license_name": "Public domain" if license_id == "public domain" else "CC0",
        "attribution": "Painter",
        "width": width,
        "height": height,
        "file_sha1": f"sha1-{key}",
    }


def _download(url, dest_prefix, timeout=60):
    os.makedirs(os.path.dirname(os.path.abspath(dest_prefix)), exist_ok=True)
    path = dest_prefix + ".png"
    with open(path, "wb") as f:
        f.write(_png_bytes(url))
    return path


def _manifest(scene_queries):
    return {
        "video_id": "vid",
        "style_lock": "engraved documentary",
        "total_duration": 0,
        "scenes": [
            {"scene_id": f"SC{index:03d}", "section": "rise",
             "visual_type": "archive", "visual_query": queries[0],
             "visual_queries": queries, "subject": "legion forest",
             "period_note": "Roman: lorica segmentata",
             "authenticity_risk": "low", "camera": "slow_push",
             "asset": None, "asset_status": "pending"}
            for index, queries in enumerate(scene_queries, 1)
        ],
    }


def _fetch(runs, vid, searches, cfg=None, **kwargs):
    def fake_search(query, limit=6):
        return searches.get(query, [])

    with mock.patch("histyt.commons.search_images", side_effect=fake_search), \
         mock.patch("histyt.commons.download_image", side_effect=_download):
        return commons.fetch_scene_images(runs, vid, cfg, **kwargs)


def test_fetch_scene_images_maps_each_asset_to_its_scene(runs, make_video):
    vid = make_video(with_sources=False)
    scenes.save_manifest(runs, vid, _manifest([["roman legion"], ["german forest"]]))
    result = _fetch(runs, vid, {"roman legion": [_candidate("legion")],
                                "german forest": [_candidate("forest", 2400, 1400)]})
    assert [asset["scene_id"] for asset in result["selected"]] == ["SC001", "SC002"]
    assert result["missing"] == []
    with open(os.path.join(runs, vid, "assets.json"), encoding="utf-8") as f:
        assets = json.load(f)
    assert len(assets) == 2
    for asset in assets:
        assert asset["provider"] == "wikimedia-commons"
        assert asset["phash"] and asset["sha256"]
        assert asset["credit_line"]
        assert os.path.isfile(asset["path"])
    manifest = scenes.load_manifest(runs, vid)
    assert [scene["asset_status"] for scene in manifest["scenes"]] == ["selected", "selected"]
    assert all(os.path.isfile(scene["asset"]) for scene in manifest["scenes"])


def test_fetch_scene_images_leaves_selected_scenes_alone(runs, make_video):
    vid = make_video(with_sources=False)
    scenes.save_manifest(runs, vid, _manifest([["roman legion"]]))
    first = _fetch(runs, vid, {"roman legion": [_candidate("legion")]})
    second = _fetch(runs, vid, {"roman legion": [_candidate("other")]})
    assert len(first["selected"]) == 1
    assert second["selected"] == []
    assert second["missing"] == []


def test_fetch_scene_images_rejects_small_or_portrait_candidates(runs, make_video):
    vid = make_video(with_sources=False)
    scenes.save_manifest(runs, vid, _manifest([["roman legion"], ["squareish"]]))
    result = _fetch(runs, vid, {
        "roman legion": [_candidate("small", 800, 600)],
        "squareish": [_candidate("tall", 1800, 1800)],
    })
    assert result["selected"] == []
    assert result["missing"] == ["SC001", "SC002"]


def test_fetch_scene_images_does_not_reuse_the_same_image(runs, make_video):
    vid = make_video(with_sources=False)
    scenes.save_manifest(runs, vid, _manifest([["roman legion"], ["german forest"]]))
    shared = [_candidate("legion")]
    result = _fetch(runs, vid, {"roman legion": shared, "german forest": shared})
    assert [asset["scene_id"] for asset in result["selected"]] == ["SC001"]
    assert result["missing"] == ["SC002"]


def test_fetch_scene_images_picks_deterministically(runs, make_video):
    vid = make_video(with_sources=False)
    scenes.save_manifest(runs, vid, _manifest([["roman legion"]]))
    unordered = [_candidate("zulu"), _candidate("alpha")]
    result = _fetch(runs, vid, {"roman legion": unordered})
    assert result["selected"][0]["source_url"].endswith("/alpha")


def test_fetch_scene_images_survives_a_failed_download(runs, make_video):
    vid = make_video(with_sources=False)
    scenes.save_manifest(runs, vid, _manifest([["roman legion"]]))
    calls = []

    def flaky_download(url, dest_prefix, timeout=60):
        calls.append(url)
        if url.endswith("/bad.jpg"):
            raise PipelineError("HTTP 429")
        return _download(url, dest_prefix)

    bad = _candidate("bad")
    good = _candidate("good")
    with mock.patch("histyt.commons.search_images", return_value=[bad, good]), \
         mock.patch("histyt.commons.download_image", side_effect=flaky_download):
        result = commons.fetch_scene_images(runs, vid, None)
    assert len(calls) >= 2
    assert [asset["scene_id"] for asset in result["selected"]] == ["SC001"]
    assert result["selected"][0]["source_url"].endswith("/good")


def test_rank_candidates_filters_and_orders_by_subject_overlap():
    matching = dict(_candidate("matching"), title="File:Roman legion relief.jpg")
    unrelated = _candidate("unrelated")
    ranked = commons.rank_candidates([unrelated, matching], subject="legion relief")
    assert [c["file_sha1"] for c in ranked] == ["sha1-matching", "sha1-unrelated"]
    assert commons.rank_candidates([_candidate("tall", 900, 1800)]) == []
