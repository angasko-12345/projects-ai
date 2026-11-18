"""CLI wiring: upload --mock must not touch the network or need credentials."""
import json
import os

from histyt import cli, pipeline
from histyt.script_stage import generate_script


def test_upload_mock_flag_uses_mock_client(runs, cfg, make_video, capsys):
    vid = make_video()
    generate_script(runs, vid, cfg)
    pipeline.approve_script(runs, vid)
    vdir = os.path.join(runs, vid)
    with open(os.path.join(vdir, "final.mp4"), "wb") as f:
        f.write(b"fake")
    with open(os.path.join(vdir, "thumbnail.png"), "wb") as f:
        f.write(b"fake")
    with open(os.path.join(vdir, "qa_report.json"), "w", encoding="utf-8") as f:
        json.dump({"verdict": "pass", "auto_rejections": [], "checks": []}, f)
    pipeline.approve_render(runs, vid)
    rc = cli.main(["--runs", runs, "upload", vid, "--mock"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "uploaded (mock)" in out
    with open(os.path.join(vdir, "upload.json"), encoding="utf-8") as f:
        record = json.load(f)
    assert record["privacyStatus"] == "private"
    assert record["video_id"] == f"mock-{vid}"
