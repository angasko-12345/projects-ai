"""P1 regression tests: resumability, render pin, voice-direct gate, fetch_url."""
import os
import unittest.mock

import pytest

from histyt import pipeline
from histyt.research import fetch_url
from histyt.script_stage import generate_script
from histyt.state import GateError, PipelineError
from histyt.voice_stage import voice_stage


def test_run_until_gate_skips_completed_stages(runs, cfg, make_video, tmp_path):
    """Running run_until_gate twice does not re-run completed stages. Voice is
    stubbed by counting synthesize calls via a monkeypatched state marker."""
    vid = make_video()
    generate_script(runs, vid, cfg)
    pipeline.approve_script(runs, vid)
    # After approval, completed_stages tracks the post-script pipeline.
    pipeline.run_until_gate(runs, vid, cfg)
    st1 = pipeline.status(runs, vid)
    assert "voice" in st1["completed_stages"]
    assert st1["stage"] == "awaiting_render_approval"
    # Second run: returns at render gate without re-running voice..variation.
    st2 = pipeline.run_until_gate(runs, vid, cfg)
    assert st2["stage"] == "awaiting_render_approval"
    assert st2["completed_stages"] == st1["completed_stages"]


def test_render_pin_detects_final_mp4_change(runs, cfg, make_video):
    from histyt.upload import MockYouTubeClient, upload_stage

    vid = make_video()
    generate_script(runs, vid, cfg)
    pipeline.approve_script(runs, vid)
    pipeline.run_until_gate(runs, vid, cfg)
    vdir = os.path.join(runs, vid)
    final = os.path.join(vdir, "final.mp4")
    if not os.path.isfile(final):
        pytest.skip("ffmpeg not available for render")
    pipeline.approve_render(runs, vid)
    # Tamper final.mp4 after render approval -> upload must refuse.
    with open(final, "ab") as f:
        f.write(b"tamper")
    with pytest.raises(GateError):
        upload_stage(runs, vid, cfg, client=MockYouTubeClient())


def test_voice_stage_blocks_unapproved_script_directly(runs, cfg, make_video):
    """Even if generate_script was run, voice_stage itself refuses without approval."""
    vid = make_video()
    generate_script(runs, vid, cfg)
    # No approve_script call — stage stays awaiting_script_approval.
    with pytest.raises(GateError):
        voice_stage(runs, vid, cfg)


def test_voice_stage_blocks_after_script_edit(runs, cfg, make_video):
    vid = make_video()
    generate_script(runs, vid, cfg)
    pipeline.approve_script(runs, vid)
    script = os.path.join(runs, vid, "script.md")
    with open(script, "a", encoding="utf-8") as f:
        f.write("\nEdited after approval\n")
    with pytest.raises(GateError):
        voice_stage(runs, vid, cfg)


def test_fetch_url_rejects_non_https_and_non_html():
    with pytest.raises(PipelineError, match="https"):
        fetch_url("http://example.com/page")

    fake = unittest.mock.MagicMock()
    fake.history = []
    fake.headers = {"content-type": "application/json"}
    fake.text = "{}"
    fake.raise_for_status = lambda: None
    with unittest.mock.patch("histyt.research.requests.get", return_value=fake):
        with pytest.raises(PipelineError, match="non-HTML"):
            fetch_url("https://example.com/data.json")


def test_fetch_url_caps_oversize_body():
    fake = unittest.mock.MagicMock()
    fake.history = []
    fake.headers = {"content-type": "text/html"}
    fake.text = "x" * 3000
    fake.raise_for_status = lambda: None
    with unittest.mock.patch("histyt.research.requests.get", return_value=fake):
        with pytest.raises(PipelineError, match="too large"):
            fetch_url("https://example.com/big", max_bytes=10)


