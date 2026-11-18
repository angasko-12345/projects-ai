import pytest

from histyt import visuals
from histyt.state import PipelineError


def test_unknown_license_rejected():
    with pytest.raises(PipelineError):
        visuals.validate_asset({"license": "proprietary", "path": "x", "source_url": "u"})


def test_allowed_license_passes():
    assert visuals.validate_asset({"license": "CC0", "path": "x", "source_url": "u"})
    assert visuals.validate_asset({"license": "Public Domain", "path": "x", "source_url": "u"})


def test_write_assets_validates_all(runs, make_video):
    vid = make_video()
    with pytest.raises(PipelineError):
        visuals.write_assets(runs, vid, [
            {"path": "a.png", "license": "cc-by", "source_url": "u", "attribution": "x"},
            {"path": "b.png", "license": "unknown", "source_url": "u", "attribution": "x"},
        ])
