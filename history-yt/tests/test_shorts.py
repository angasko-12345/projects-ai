"""Phase 2 shorts: heuristic planning + landscape extraction, render-gate hash pin."""
import json
import os

import pytest

from histyt import pipeline
from histyt.media import get_duration, run_ffmpeg
from histyt.providers_tts import chunk_text
from histyt.script_stage import generate_script, parse_script
from histyt.shorts import extract_shorts, plan_shorts
from histyt.state import GateError, PipelineError, sha256_file


def _approved_video(runs, cfg, make_video):
    from histyt.voice_stage import voice_stage

    vid = make_video()
    generate_script(runs, vid, cfg)
    pipeline.approve_script(runs, vid)
    voice_stage(runs, vid, cfg)  # produces durations.json for the planner
    return vid


def _write_fake_final(runs, vid):
    vdir = os.path.join(runs, vid)
    with open(os.path.join(vdir, "final.mp4"), "wb") as f:
        f.write(b"fake-final")
    with open(os.path.join(vdir, "thumbnail.png"), "wb") as f:
        f.write(b"fake-thumb")
    return vdir


def _approved_render(runs, vid):
    vdir = _write_fake_final(runs, vid)
    pipeline.approve_render(runs, vid)
    return vdir


def _synthetic_video(runs, cfg, make_video, sentences):
    """Video with controlled script + durations (hook/entity scoring is testable)."""
    vid = make_video()
    vdir = os.path.join(runs, vid)
    script = "\n".join(
        f"[C{i + 1}] {text} (S1, S2)" for i, (text, _) in enumerate(sentences))
    with open(os.path.join(vdir, "script.md"), "w", encoding="utf-8") as f:
        f.write("# Synthetic\n\n" + script + "\n")
    pipeline.approve_script(runs, vid)
    _, claims, _ = parse_script("# Synthetic\n\n" + script + "\n")
    durations, idx = [], 0
    for claim, (_, seconds) in zip(claims, sentences, strict=True):
        for chunk in chunk_text(claim["text"]):
            idx += 1
            durations.append({"file": f"audio/chunk_{idx:03d}.wav", "text": chunk,
                              "duration": float(seconds), "pace": 1.0})
    with open(os.path.join(vdir, "durations.json"), "w", encoding="utf-8") as f:
        json.dump(durations, f, indent=2, ensure_ascii=False)
    return vid


def _shorts_cfg(cfg, **overrides):
    c = dict(cfg)
    shorts = dict(c.get("shorts") or {})
    shorts.update(overrides)
    c["shorts"] = shorts
    return c


def test_plan_requires_script_approval(runs, cfg, make_video):
    vid = make_video()
    generate_script(runs, vid, cfg)  # stage stays awaiting_script_approval
    with pytest.raises(GateError):
        plan_shorts(runs, vid, cfg)


def test_plan_windows_bounded_non_overlapping_and_deterministic(runs, cfg, make_video):
    sentences = [
        ("Why did the Roman Empire fall so quickly?", 8.0),
        ("The empire fractured under pressure from every border.", 8.0),
        ("The Rhine and Danube frontiers dissolved within decades.", 8.0),
        ("The legacy still shapes how we study the period.", 8.0),
    ]
    vid = _synthetic_video(runs, cfg, make_video, sentences)
    plan = plan_shorts(runs, vid, cfg, max_clips=2)
    again = plan_shorts(runs, vid, cfg, max_clips=2)
    assert [c["slug"] for c in plan["clips"]] == [c["slug"] for c in again["clips"]]
    clips = plan["clips"]
    assert clips, "expected at least one clip"
    assert len(clips) <= 2
    for clip in clips:
        assert 15.0 <= clip["duration"] <= 60.0
        assert clip["title_suggestions"]
        assert clip["slug"]
    ordered = sorted(clips, key=lambda c: c["start"])
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        assert prev["end"] <= nxt["start"] + 1e-6
    assert plan["relaxed_min_seconds"] is False
    assert any(c["hook"] for c in clips), "hook-led window should be selected"


def test_plan_relaxes_min_seconds_for_tiny_videos(runs, cfg, make_video):
    vid = _approved_video(runs, cfg, make_video)  # fake TTS: ~1.8s total
    plan = plan_shorts(runs, vid, cfg)
    assert plan["relaxed_min_seconds"] is True
    assert plan["clips"], "relaxed plan still yields a clip"


def test_plan_missing_durations_raises(runs, cfg, make_video):
    vid = _approved_video(runs, cfg, make_video)
    os.remove(os.path.join(runs, vid, "durations.json"))
    with pytest.raises(PipelineError, match="durations.json"):
        plan_shorts(runs, vid, cfg)


def test_extract_requires_render_approval(runs, cfg, make_video):
    vid = _approved_video(runs, cfg, make_video)
    plan_shorts(runs, vid, cfg)
    _write_fake_final(runs, vid)  # final present but not approved
    with pytest.raises(GateError):
        extract_shorts(runs, vid, cfg)


def test_extract_refuses_tampered_final_after_approval(runs, cfg, make_video):
    vid = _approved_video(runs, cfg, make_video)
    plan_shorts(runs, vid, cfg)
    _approved_render(runs, vid)
    with open(os.path.join(runs, vid, "final.mp4"), "ab") as f:
        f.write(b"tamper")
    with pytest.raises(GateError):
        extract_shorts(runs, vid, cfg)


def test_extract_cuts_real_video_and_writes_manifest(runs, cfg, make_video, tmp_path):
    sentences = [
        ("Why did the Roman Empire fall so quickly?", 2.0),
        ("The frontier broke within decades of the crisis.", 2.0),
        ("The legacy still shapes how we study the period.", 2.0),
    ]
    vid = _synthetic_video(runs, cfg, make_video, sentences)
    vdir = os.path.join(runs, vid)
    final = os.path.join(vdir, "final.mp4")
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", final,
    ])
    pipeline.approve_render(runs, vid)
    small = _shorts_cfg(cfg, max_clips=1, min_seconds=1, max_seconds=6)
    plan = plan_shorts(runs, vid, small)
    assert len(plan["clips"]) == 1
    manifest = extract_shorts(runs, vid, small)
    assert len(manifest["shorts"]) == 1
    out = os.path.join(vdir, "shorts", manifest["shorts"][0]["file"])
    assert os.path.isfile(out)
    assert get_duration(out) > 0
    assert manifest["shorts"][0]["sha256"] == sha256_file(out)
    with open(os.path.join(vdir, "shorts", "shorts_manifest.json"),
              encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["shorts"][0]["file"] == manifest["shorts"][0]["file"]
