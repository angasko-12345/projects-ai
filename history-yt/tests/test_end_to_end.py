import json
import os
import shutil

import pytest

from histyt import pipeline, topics
from histyt.media import get_streams, has_stream
from histyt.research import add_source
from histyt.upload import MockYouTubeClient

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def test_full_dry_run(runs, cfg):
    vid = topics.add_topic(runs, "The Fall of Rome", "rise_fall")
    add_source(runs, vid, "https://example.com/one", "One", "history")
    add_source(runs, vid, "https://example.com/two", "Two", "history")
    pipeline.run_until_gate(runs, vid, cfg)
    st = pipeline.status(runs, vid)
    assert st["stage"] == "awaiting_script_approval"

    pipeline.approve_script(runs, vid)
    pipeline.run_until_gate(runs, vid, cfg)
    st = pipeline.status(runs, vid)
    assert st["stage"] == "awaiting_render_approval"

    vdir = os.path.join(runs, vid)
    final = os.path.join(vdir, "final.mp4")
    assert os.path.isfile(final)
    streams = get_streams(final)
    video = next(s for s in streams if s.get("codec_type") == "video")
    assert int(video.get("width")) == 1920 and int(video.get("height")) == 1080
    assert has_stream(final, "audio")
    assert os.path.isfile(os.path.join(vdir, "thumbnail.png"))
    assert os.path.isfile(os.path.join(vdir, "captions.srt"))

    # Scene manifest is built and linked to the audio chunks.
    with open(os.path.join(vdir, "scene_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    with open(os.path.join(vdir, "durations.json"), encoding="utf-8") as f:
        durations = json.load(f)
    assert len(manifest["scenes"]) > 0
    assert all(entry.get("scene_id") for entry in durations)
    assert all(scene.get("asset") for scene in manifest["scenes"])

    # QA ran and did not reject the structural profile.
    with open(os.path.join(vdir, "qa_report.json"), encoding="utf-8") as f:
        report = json.load(f)
    assert report["verdict"] in ("pass", "revise")
    assert report["auto_rejections"] == []

    pipeline.approve_render(runs, vid)
    client = MockYouTubeClient(video_id="e2e-1")
    pipeline.run_until_gate(runs, vid, cfg, uploader=client)
    assert pipeline.status(runs, vid)["stage"] == "uploaded"
    with open(os.path.join(vdir, "upload.json"), encoding="utf-8") as f:
        record = json.load(f)
    assert record["privacyStatus"] == "private"
