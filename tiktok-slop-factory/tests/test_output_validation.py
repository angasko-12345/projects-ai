"""Final-output validation: file checks + ffprobe + duration.

Covers the quick-win requirements:
  - exists, regular file, non-empty
  - ffprobe can read
  - contains video stream
  - positive finite duration
  - optional truncation guard via min_duration
Plus pipeline wiring: failure must not be reported as success and invalid
file must be left on disk for diagnosis.
"""
import math
import subprocess
import sys
from pathlib import Path

import pytest

from app import pipeline, renderer
from conftest import FFMPEG, FFPROBE, ffmpeg_required

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# validate_final_output — mocked ffprobe (unit, no FFmpeg needed)
# ---------------------------------------------------------------------------

def test_validate_missing_file_raises(tmp_path):
    missing = tmp_path / "no_such_file.mp4"
    with pytest.raises(renderer.OutputValidationError, match="does not exist"):
        renderer.validate_final_output(missing)


def test_validate_directory_not_regular_file(tmp_path):
    # tmp_path itself is a directory, is_file() is False
    with pytest.raises(renderer.OutputValidationError, match="not a regular file"):
        renderer.validate_final_output(tmp_path)


def test_validate_empty_file_raises(tmp_path):
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    with pytest.raises(renderer.OutputValidationError, match="empty"):
        renderer.validate_final_output(p)


def test_validate_corrupt_media_ffprobe_fails(monkeypatch, tmp_path):
    p = tmp_path / "corrupt.mp4"
    p.write_bytes(b"this is not a video" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: None)
    with pytest.raises(renderer.OutputValidationError, match="could not read|corrupt"):
        renderer.validate_final_output(p)


def test_validate_no_video_stream(monkeypatch, tmp_path):
    p = tmp_path / "audio_only.mp4"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: [
        {"codec_type": "audio", "codec_name": "aac"},
    ])
    with pytest.raises(renderer.OutputValidationError, match="no video stream"):
        renderer.validate_final_output(p)


def test_validate_duration_unreadable(monkeypatch, tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: [
        {"codec_type": "video", "codec_name": "h264"},
    ])
    monkeypatch.setattr(renderer, "probe_duration", lambda path: None)
    with pytest.raises(renderer.OutputValidationError, match="could not determine duration"):
        renderer.validate_final_output(p)


@pytest.mark.parametrize("bad_duration", [0, 0.0, -1, -0.1])
def test_validate_zero_or_negative_duration(monkeypatch, tmp_path, bad_duration):
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: [
        {"codec_type": "video"},
    ])
    monkeypatch.setattr(renderer, "probe_duration", lambda path: float(bad_duration))
    with pytest.raises(renderer.OutputValidationError, match="invalid duration"):
        renderer.validate_final_output(p)


def test_validate_nan_duration(monkeypatch, tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: [{"codec_type": "video"}])
    monkeypatch.setattr(renderer, "probe_duration", lambda path: float("nan"))
    with pytest.raises(renderer.OutputValidationError, match="invalid duration"):
        renderer.validate_final_output(p)


def test_validate_inf_duration(monkeypatch, tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: [{"codec_type": "video"}])
    monkeypatch.setattr(renderer, "probe_duration", lambda path: float("inf"))
    with pytest.raises(renderer.OutputValidationError, match="invalid duration"):
        renderer.validate_final_output(p)


def test_validate_truncated_when_min_duration_given(monkeypatch, tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: [{"codec_type": "video"}])
    monkeypatch.setattr(renderer, "probe_duration", lambda path: 5.0)
    # expected 10s, threshold is max(1.0, 10*0.9)=9.0 -> 5.0 < 9.0 should fail
    with pytest.raises(renderer.OutputValidationError, match="truncated|expected at least"):
        renderer.validate_final_output(p, min_duration=10.0)


def test_validate_valid_output_mocked(monkeypatch, tmp_path):
    p = tmp_path / "good.mp4"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: [
        {"codec_type": "video", "codec_name": "h264"},
        {"codec_type": "audio", "codec_name": "aac"},
    ])
    monkeypatch.setattr(renderer, "probe_duration", lambda path: 5.0)
    result = renderer.validate_final_output(p)
    assert result == pytest.approx(5.0)
    # with min_duration that is satisfied
    result2 = renderer.validate_final_output(p, min_duration=5.0)
    assert result2 == pytest.approx(5.0)


def test_output_validation_error_is_render_error():
    assert issubclass(renderer.OutputValidationError, renderer.RenderError)


# ---------------------------------------------------------------------------
# validate_final_output — real FFmpeg fixtures
# ---------------------------------------------------------------------------

@ffmpeg_required
def test_validate_valid_real_video(mp4_factory, tmp_path):
    src = mp4_factory(duration=4.0, size="1080x1920", audio=4.0)
    dur = renderer.validate_final_output(src)
    assert dur == pytest.approx(4.0, abs=0.6)


@ffmpeg_required
def test_validate_valid_real_video_with_min_duration(mp4_factory, tmp_path):
    src = mp4_factory(duration=6.0, size="1080x1920", audio=0)
    # need audio? validate checks video stream only, so video-only is fine
    dur = renderer.validate_final_output(src, min_duration=6.0)
    assert dur == pytest.approx(6.0, abs=0.6)


@ffmpeg_required
def test_validate_corrupt_real_file(tmp_path):
    p = tmp_path / "corrupt.mp4"
    p.write_bytes(b"not a real mp4 file" * 100)
    with pytest.raises(renderer.OutputValidationError, match="could not read|corrupt|no video"):
        renderer.validate_final_output(p)


@ffmpeg_required
def test_validate_audio_only_has_no_video_stream(tmp_path):
    # Create a wav file (audio only) and try to validate it as video
    wav = tmp_path / "audio.wav"
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=400:duration=2",
         "-c:a", "pcm_s16le", str(wav)],
        check=True,
    )
    with pytest.raises(renderer.OutputValidationError, match="no video stream|could not read"):
        renderer.validate_final_output(wav)


@ffmpeg_required
def test_validate_truncated_real_video(mp4_factory, tmp_path):
    src = mp4_factory(duration=2.0, size="1080x1920", audio=0)
    with pytest.raises(renderer.OutputValidationError, match="truncated|expected at least"):
        renderer.validate_final_output(src, min_duration=10.0)


@ffmpeg_required
def test_validate_empty_real_file(tmp_path):
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    with pytest.raises(renderer.OutputValidationError, match="empty"):
        renderer.validate_final_output(p)


# ---------------------------------------------------------------------------
# Pipeline wiring — validation failure must not be reported as success
# ---------------------------------------------------------------------------

def test_pipeline_validation_failure_not_reported_as_success(monkeypatch, tmp_path):
    """If validate_final_output raises, _produce_one must return a failure entry."""
    monkeypatch.setattr(pipeline, "get_output_dir", lambda: tmp_path)
    # Need to get past narration coherence; stub helpers
    monkeypatch.setattr(pipeline, "_measure_volume_db", lambda p: -20.0)

    from app import gemini

    script_stub = {
        "hook": "H",
        "story": "S",
        "twist": "T",
        "ending": "E",
        "cta": "C",
        "title": "t",
        "hashtags": [],
    }
    monkeypatch.setattr(pipeline.gemini, "generate_script", lambda idea: script_stub)
    monkeypatch.setattr(pipeline.gemini, "script_to_text",
                        lambda s: " ".join([s["hook"], s["story"], s["twist"], s["ending"], s["cta"]]))

    # Create a tiny real wav so probe_duration returns something
    # Instead mock probe_duration to avoid needing real file
    fake_audio = tmp_path / "audio" / "fake.wav"
    fake_audio.parent.mkdir(parents=True, exist_ok=True)
    fake_audio.write_bytes(b"RIFF____WAVEfake")

    fake_blob = gemini.AudioBlob(b"RIFF____WAVEfake", "audio/wav")
    monkeypatch.setattr(pipeline.gemini, "generate_tts", lambda s: fake_blob)
    monkeypatch.setattr(pipeline.tts, "ensure_playable", lambda data, mime: data)
    monkeypatch.setattr(pipeline.tts, "save_audio", lambda data, name, output_dir=None: fake_audio)
    monkeypatch.setattr(pipeline.renderer, "probe_duration", lambda p: 5.0)

    # visuals
    from app.visuals import ScenePlan
    fake_scene = ScenePlan(index=0, text="hello world", keywords="hello",
                           duration=5.0, style="nebula", motion="drift",
                           transition="fade", seed=1, palette=("0x000000","0x111111","0x222222"))
    monkeypatch.setattr(pipeline.visuals, "plan_scenes", lambda script, duration, seed: [fake_scene])
    fake_visuals = tmp_path / "visuals" / "fake.mp4"
    fake_visuals.parent.mkdir(parents=True, exist_ok=True)
    fake_visuals.write_bytes(b"x" * 100)
    monkeypatch.setattr(pipeline.visuals, "render_background", lambda scenes, out, duration, work_dir=None: fake_visuals)

    # captions
    monkeypatch.setattr(pipeline, "_build_captions", lambda text, duration, srt_path: srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8") or srt_path)

    # render_video creates the output file (invalid on purpose)
    def fake_render(audio_path, visuals_path, srt_path, out_path, duration=0.0):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"invalid video content")
        return out_path

    monkeypatch.setattr(pipeline.renderer, "render_video", fake_render)

    # Make validation fail
    def boom(path, min_duration=None):
        raise renderer.OutputValidationError(
            f"Final output validation failed: file contains no video stream: {path}"
        )
    monkeypatch.setattr(pipeline.renderer, "validate_final_output", boom)

    # Also need generate_caption (called after render) — but validation happens before it,
    # so this won't be reached when validation fails. Still stub to be safe.
    monkeypatch.setattr(pipeline.gemini, "generate_caption", lambda s: "cap")

    entry = pipeline._produce_one("some idea for validation", 0)
    assert "error" in entry
    assert "file" not in entry
    # error message should preserve validation detail
    assert "no video stream" in entry["error"].lower() or "validation" in entry["error"].lower()

    # Invalid output must NOT be deleted (left for diagnosis)
    # The stem is deterministic
    stem = pipeline.build_stem("some idea for validation", 0)
    expected_out = tmp_path / "videos" / f"{stem}.mp4"
    assert expected_out.exists(), "invalid output should be preserved for diagnosis"
    assert expected_out.read_bytes() == b"invalid video content"


def test_pipeline_validation_success_still_succeeds(monkeypatch, tmp_path):
    """When validation passes, _produce_one returns success entry."""
    monkeypatch.setattr(pipeline, "get_output_dir", lambda: tmp_path)
    monkeypatch.setattr(pipeline, "_measure_volume_db", lambda p: -20.0)

    from app import gemini

    script_stub = {
        "hook": "H",
        "story": "S",
        "twist": "T",
        "ending": "E",
        "cta": "C",
        "title": "t",
        "hashtags": [],
    }
    monkeypatch.setattr(pipeline.gemini, "generate_script", lambda idea: script_stub)
    monkeypatch.setattr(pipeline.gemini, "script_to_text",
                        lambda s: " ".join([s["hook"], s["story"], s["twist"], s["ending"], s["cta"]]))

    fake_audio = tmp_path / "audio" / "fake.wav"
    fake_audio.parent.mkdir(parents=True, exist_ok=True)
    fake_audio.write_bytes(b"RIFF____WAVEfake")
    fake_blob = gemini.AudioBlob(b"RIFF____WAVEfake", "audio/wav")
    monkeypatch.setattr(pipeline.gemini, "generate_tts", lambda s: fake_blob)
    monkeypatch.setattr(pipeline.tts, "ensure_playable", lambda data, mime: data)
    monkeypatch.setattr(pipeline.tts, "save_audio", lambda data, name, output_dir=None: fake_audio)
    monkeypatch.setattr(pipeline.renderer, "probe_duration", lambda p: 5.0)

    from app.visuals import ScenePlan
    fake_scene = ScenePlan(index=0, text="hello world", keywords="hello",
                           duration=5.0, style="nebula", motion="drift",
                           transition="fade", seed=1, palette=("0x000000","0x111111","0x222222"))
    monkeypatch.setattr(pipeline.visuals, "plan_scenes", lambda script, duration, seed: [fake_scene])
    fake_visuals = tmp_path / "visuals" / "fake.mp4"
    fake_visuals.parent.mkdir(parents=True, exist_ok=True)
    fake_visuals.write_bytes(b"x" * 100)
    monkeypatch.setattr(pipeline.visuals, "render_background", lambda scenes, out, duration, work_dir=None: fake_visuals)
    monkeypatch.setattr(pipeline, "_build_captions", lambda text, duration, srt_path: srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8") or srt_path)

    def fake_render(audio_path, visuals_path, srt_path, out_path, duration=0.0):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"valid fake mp4" * 100)
        return out_path
    monkeypatch.setattr(pipeline.renderer, "render_video", fake_render)
    monkeypatch.setattr(pipeline.renderer, "validate_final_output", lambda path, min_duration=None: 5.0)
    monkeypatch.setattr(pipeline.gemini, "generate_caption", lambda s: "cap")

    entry = pipeline._produce_one("another idea", 0)
    assert "file" in entry
    assert "error" not in entry
    assert entry["duration"] == pytest.approx(5.0)


def test_pipeline_validation_error_converted_to_pipeline_error(monkeypatch, tmp_path):
    """OutputValidationError is surfaced as PipelineError chain, message preserved."""
    monkeypatch.setattr(pipeline, "get_output_dir", lambda: tmp_path)
    monkeypatch.setattr(pipeline, "_measure_volume_db", lambda p: -20.0)
    from app import gemini
    script_stub = {"hook": "H", "story": "S", "twist": "T", "ending": "E", "cta": "C", "title": "t", "hashtags": []}
    monkeypatch.setattr(pipeline.gemini, "generate_script", lambda idea: script_stub)
    monkeypatch.setattr(pipeline.gemini, "script_to_text", lambda s: "text")
    fake_audio = tmp_path / "audio" / "fake.wav"
    fake_audio.parent.mkdir(parents=True, exist_ok=True)
    fake_audio.write_bytes(b"RIFF")
    monkeypatch.setattr(pipeline.gemini, "generate_tts", lambda s: gemini.AudioBlob(b"RIFF", "audio/wav"))
    monkeypatch.setattr(pipeline.tts, "ensure_playable", lambda d, m: d)
    monkeypatch.setattr(pipeline.tts, "save_audio", lambda d, n, output_dir=None: fake_audio)
    monkeypatch.setattr(pipeline.renderer, "probe_duration", lambda p: 5.0)
    from app.visuals import ScenePlan
    fake_scene = ScenePlan(index=0, text="hi", keywords="hi", duration=5.0, style="nebula", motion="drift", transition="fade", seed=1, palette=("0x000","0x111","0x222"))
    monkeypatch.setattr(pipeline.visuals, "plan_scenes", lambda s, duration, seed: [fake_scene])
    fake_visuals = tmp_path / "visuals" / "fake.mp4"
    fake_visuals.parent.mkdir(parents=True, exist_ok=True)
    fake_visuals.write_bytes(b"x")
    monkeypatch.setattr(pipeline.visuals, "render_background", lambda *a, **kw: fake_visuals)
    monkeypatch.setattr(pipeline, "_build_captions", lambda *a, **kw: tmp_path / "cap.srt")
    (tmp_path / "cap.srt").write_text("x")
    monkeypatch.setattr(pipeline.renderer, "render_video", lambda *a, **kw: (Path(kw.get("out_path") or a[3]).write_bytes(b"bad") or Path(kw.get("out_path") or a[3])) if len(a) >=4 else Path(tmp_path / "videos" / "out.mp4"))
    # simpler: render_video that takes positional args
    def fake_render2(audio_path, visuals_path, srt_path, out_path, duration=0.0):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"bad")
        return out_path
    monkeypatch.setattr(pipeline.renderer, "render_video", fake_render2)
    # validation fails with specific message
    monkeypatch.setattr(pipeline.renderer, "validate_final_output", lambda path, min_duration=None: (_ for _ in ()).throw(renderer.OutputValidationError("Final output validation failed: file is empty (0 bytes): /tmp/x.mp4")))
    monkeypatch.setattr(pipeline.gemini, "generate_caption", lambda s: "cap")

    entry = pipeline._produce_one("idea x", 0)
    assert "error" in entry
    assert "empty" in entry["error"].lower()
    # ensure the invalid file is still on disk
    stem = pipeline.build_stem("idea x", 0)
    assert (tmp_path / "videos" / f"{stem}.mp4").exists()
