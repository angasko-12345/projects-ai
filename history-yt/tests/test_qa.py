import json
import os

import pytest

from histyt import pipeline, qa
from histyt.script_stage import generate_script
from histyt.state import GateError


def _run_to_gate(runs, cfg, make_video):
    vid = make_video()
    generate_script(runs, vid, cfg)
    pipeline.approve_script(runs, vid)
    pipeline.run_until_gate(runs, vid, cfg)
    return vid


def test_structural_report_and_release_rejection(runs, cfg, make_video):
    vid = _run_to_gate(runs, cfg, make_video)
    report = qa.load_report(runs, vid)
    assert report["profile"] == "structural"
    assert report["verdict"] in ("pass", "revise")
    assert report["auto_rejections"] == []
    assert set(report["human_scorecard"]) == set(qa.HUMAN_CATEGORIES)
    assert all(value is None for value in report["human_scorecard"].values())
    checks = {c["id"]: c["status"] for c in report["checks"]}
    assert checks["duration_matches_narration"] == "pass"
    assert checks["export_specs"] == "pass"
    assert checks["loudness"] == "not_available"

    release_cfg = dict(cfg)
    release_cfg["qa"] = {"profile": "release"}
    rejected = qa.qa_stage(runs, vid, release_cfg)
    assert rejected["verdict"] == "reject"
    assert "loudness" in rejected["auto_rejections"]
    assert "long_silence" in rejected["auto_rejections"]
    with pytest.raises(GateError, match="QA rejected"):
        pipeline.approve_render(runs, vid)


def test_approve_render_requires_a_report(runs, cfg, make_video):
    vid = make_video()
    generate_script(runs, vid, cfg)
    pipeline.approve_script(runs, vid)
    vdir = os.path.join(runs, vid)
    with open(os.path.join(vdir, "final.mp4"), "wb") as f:
        f.write(b"fake")
    with pytest.raises(GateError, match="QA report missing"):
        pipeline.approve_render(runs, vid)


def test_approve_render_accepts_revise(runs, cfg, make_video):
    vid = make_video()
    generate_script(runs, vid, cfg)
    pipeline.approve_script(runs, vid)
    vdir = os.path.join(runs, vid)
    with open(os.path.join(vdir, "final.mp4"), "wb") as f:
        f.write(b"fake")
    with open(os.path.join(vdir, "qa_report.json"), "w", encoding="utf-8") as f:
        json.dump({"verdict": "revise", "auto_rejections": [], "checks": []}, f)
    state = pipeline.approve_render(runs, vid)
    assert state["render_approved"] is True


def test_format_report_names_the_verdict_and_failures():
    report = {"verdict": "reject", "profile": "release",
              "checks": [{"id": "loudness", "status": "fail", "detail": "silence"}],
              "auto_rejections": ["loudness"], "notes": ["set disclosure"]}
    text = qa.format_report(report)
    assert "REJECT" in text
    assert "loudness" in text
    assert "set disclosure" in text
