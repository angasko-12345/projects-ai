import json
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
    for name in ("final.mp4", "thumbnail.png"):
        with open(os.path.join(vdir, name), "wb") as handle:
            handle.write(b"fake")
    return vid


def _write_qa_pass(runs, vid, verdict="pass"):
    vdir = os.path.join(runs, vid)
    with open(os.path.join(vdir, "qa_report.json"), "w", encoding="utf-8") as f:
        json.dump({"verdict": verdict, "auto_rejections": [], "checks": []}, f)


def test_mock_asserts_private_and_description_has_sources(runs, cfg, make_video):
    vid = _prepare(runs, cfg, make_video)
    _write_qa_pass(runs, vid)
    pipeline.approve_render(runs, vid)
    client = MockYouTubeClient(video_id="uploaded-42")
    record = upload_stage(runs, vid, cfg, client=client)
    assert record["privacyStatus"] == "private"
    body = client.calls[0]["body"]
    assert body["status"]["privacyStatus"] == "private"
    assert "example.com/a" in body["snippet"]["description"]
    assert "example.com/b" in body["snippet"]["description"]


def test_description_includes_chapters_when_the_manifest_is_long_enough(
        runs, cfg, make_video):
    vid = _prepare(runs, cfg, make_video)
    vdir = os.path.join(runs, vid)
    scenes = []
    for name in ("rise", "peak", "fall", "legacy"):
        for _ in range(3):
            scenes.append({"scene_id": f"SC{len(scenes) + 1:03d}",
                           "section": name, "duration": 10.0})
    with open(os.path.join(vdir, "scene_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"scenes": scenes}, f)
    _write_qa_pass(runs, vid)
    pipeline.approve_render(runs, vid)
    client = MockYouTubeClient(video_id="chapters-1")
    upload_stage(runs, vid, cfg, client=client)
    description = client.calls[0]["body"]["snippet"]["description"]
    assert "Chapters:" in description
    assert "00:00 rise" in description


def test_upload_refuses_without_gate(runs, cfg, make_video):
    vid = _prepare(runs, cfg, make_video)
    with pytest.raises(GateError):
        upload_stage(runs, vid, cfg, client=MockYouTubeClient())


def test_build_body_never_public():
    cfg = {"youtube": {"contains_synthetic_media": None}}
    body = build_body(cfg, "T", "D")
    assert body["status"]["privacyStatus"] == "private"
    assert "containsSyntheticMedia" not in body["status"]
