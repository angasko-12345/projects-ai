import json
import os
from unittest import mock

import pytest

from histyt import commons
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
