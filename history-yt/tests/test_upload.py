import os

import pytest

from histyt import pipeline
from histyt.state import GateError
from histyt.upload import MockYouTubeClient, build_body, upload_stage


def _prepare(runs, cfg, make_video):
    vid = make_video()
    from histyt.script_stage import generate_script
    generate_script(runs, vid, cfg)
    pipeline.approve_script(runs, vid)
    # Minimal final artifacts for upload.
    vdir = os.path.join(runs, vid)
    open(os.path.join(vdir, "final.mp4"), "wb").write(b"fake")
    open(os.path.join(vdir, "thumbnail.png"), "wb").write(b"fake")
    return vid


def test_mock_asserts_private_and_description_has_sources(runs, cfg, make_video):
    vid = _prepare(runs, cfg, make_video)
    pipeline.approve_render(runs, vid)
    client = MockYouTubeClient(video_id="uploaded-42")
    record = upload_stage(runs, vid, cfg, client=client)
    assert record["privacyStatus"] == "private"
    body = client.calls[0]["body"]
    assert body["status"]["privacyStatus"] == "private"
    assert "example.com/a" in body["snippet"]["description"]
    assert "example.com/b" in body["snippet"]["description"]


def test_upload_refuses_without_gate(runs, cfg, make_video):
    vid = _prepare(runs, cfg, make_video)
    with pytest.raises(GateError):
        upload_stage(runs, vid, cfg, client=MockYouTubeClient())


def test_build_body_never_public():
    cfg = {"youtube": {"contains_synthetic_media": None}}
    body = build_body(cfg, "T", "D")
    assert body["status"]["privacyStatus"] == "private"
    assert "containsSyntheticMedia" not in body["status"]
