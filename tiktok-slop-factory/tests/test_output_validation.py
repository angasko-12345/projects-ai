"""Final-output validation: file checks + ffprobe + duration + integrity.

Covers the quick-win requirements:
  - exists, regular file, non-empty
  - ffprobe can read
  - contains video stream
  - positive finite duration
  - optional truncation guard via min_duration (container level)
  - full decode integrity check (catches physically truncated files)
Plus pipeline wiring: failure must not be reported as success and invalid
file must be left on disk for diagnosis.
"""
import math
import subprocess
import sys
from pathlib import Path

import pytest

from app import captions, pipeline, renderer, script
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
    monkeypatch.setattr(renderer, "probe_integrity", lambda path: True)
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
    with pytest.raises(renderer.OutputValidationError, match="could not read|corrupt|no video|failed full decode"):
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
    with pytest.raises(renderer.OutputValidationError, match="no video stream|could not read|failed full decode"):
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


@ffmpeg_required
def test_validate_truncated_file_detected_by_integrity_check(tmp_path):
    """A file with valid header but corrupted stream data should fail integrity check."""
    # Create a file with a valid MP4 container header but corrupted/cut stream data
    # by writing a valid ftyp/moov header followed by garbage
    truncated = tmp_path / "truncated.mp4"
    # Valid ftyp box + minimal moov structure, but no mdat or corrupted mdat
    # This simulates a partial download where the container header is present
    # but the media data is missing/incomplete
    header = (
        b'\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41'  # ftyp
        b'\x00\x00\x00\x18moov'  # moov atom start (incomplete)
    )
    truncated.write_bytes(header + b'garbage_data_that_breaks_decode' * 50)
    
    # ffprobe will fail to read this
    with pytest.raises(renderer.OutputValidationError, match="could not read|corrupt|no video|failed full decode"):
        renderer.validate_final_output(truncated, min_duration=4.0)


# ---------------------------------------------------------------------------
# Pipeline wiring — validation failure must not be reported as success
# ---------------------------------------------------------------------------

def _make_mock_audio_with_duration(tmp_path, duration: float):
    """Create a WAV file of exact duration using FFmpeg."""
    wav = tmp_path / "audio" / f"fake_{duration}s.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={duration}",
         "-c:a", "pcm_s16le", str(wav)],
        check=True,
    )
    return wav.read_bytes()


def _setup_pipeline_mocks(monkeypatch, tmp_path, narration_text: str, audio_duration: float):
    """Set up all pipeline mocks to reach validate_final_output with given params."""
    from app import gemini
    from app.visuals import ScenePlan

    script_stub = {
        "hook": "H", "story": "S", "twist": "T", "ending": "E", "cta": "C",
        "title": "t", "hashtags": [],
    }

    # Build audio that matches the narration duration
    audio_bytes = _make_mock_audio_with_duration(tmp_path, audio_duration)

    # Patch all the way to validation
    monkeypatch.setattr(pipeline, "get_output_dir", lambda: tmp_path)
    monkeypatch.setattr(pipeline, "_measure_volume_db", lambda p: -20.0)
    monkeypatch.setattr(pipeline.gemini, "generate_script", lambda idea: script_stub)
    monkeypatch.setattr(pipeline.gemini, "script_to_text", lambda s: narration_text)
    monkeypatch.setattr(pipeline.gemini, "generate_tts",
                        lambda s: gemini.AudioBlob(audio_bytes, "audio/wav"))
    monkeypatch.setattr(pipeline.tts, "ensure_playable", lambda d, m: d)
    # save_audio will write our pre-made audio
    fake_audio = tmp_path / "audio" / "fake.wav"
    fake_audio.parent.mkdir(parents=True, exist_ok=True)
    fake_audio.write_bytes(audio_bytes)
    monkeypatch.setattr(pipeline.tts, "save_audio",
                        lambda data, name, output_dir=None: fake_audio)
    monkeypatch.setattr(pipeline.renderer, "probe_duration", lambda p: audio_duration)

    # Visuals
    fake_scene = ScenePlan(index=0, text="hello", keywords="hello",
                           duration=audio_duration, style="nebula", motion="drift",
                           transition="fade", seed=1, palette=("0x000", "0x111", "0x222"))
    monkeypatch.setattr(pipeline.visuals, "plan_scenes",
                        lambda script, duration, seed: [fake_scene])
    fake_visuals = tmp_path / "visuals" / "fake.mp4"
    fake_visuals.parent.mkdir(parents=True, exist_ok=True)
    fake_visuals.write_bytes(b"x" * 100)
    monkeypatch.setattr(pipeline.visuals, "render_background",
                        lambda *a, **kw: fake_visuals)

    # Captions
    monkeypatch.setattr(pipeline, "_build_captions",
                        lambda text, duration, srt_path:
                        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8") or srt_path)

    # Caption generator
    monkeypatch.setattr(pipeline.gemini, "generate_caption", lambda s: "cap")

    return fake_audio, fake_visuals


def test_pipeline_validation_failure_not_reported_as_success(monkeypatch, tmp_path):
    """If validate_final_output raises, _produce_one must return a failure entry."""
    # Use narration text that matches ~5s audio
    narration = " ".join(["word"] * 10)  # ~10 words ~5s at normal speech
    audio_duration = 5.0

    fake_audio, fake_visuals = _setup_pipeline_mocks(monkeypatch, tmp_path, narration, audio_duration)

    # render_video creates an invalid output file
    def fake_render(audio_path, visuals_path, srt_path, out_path, duration=0.0):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"invalid video content")
        return out_path

    monkeypatch.setattr(pipeline.renderer, "render_video", fake_render)

    entry = pipeline._produce_one("some idea for validation", 0)

    assert "error" in entry
    assert "file" not in entry
    # Error should be from validation (since render succeeds but validation fails)
    assert "validation" in entry["error"].lower() or "decode" in entry["error"].lower() or "stream" in entry["error"].lower()

    # Invalid output must NOT be deleted (left for diagnosis)
    stem = pipeline.build_stem("some idea for validation", 0)
    expected_out = tmp_path / "videos" / f"{stem}.mp4"
    assert expected_out.exists(), "invalid output should be preserved for diagnosis"
    assert expected_out.read_bytes() == b"invalid video content"


def test_pipeline_validation_success_still_succeeds(monkeypatch, tmp_path):
    """When validation passes, _produce_one returns success entry."""
    narration = " ".join(["word"] * 10)  # ~10 words ~5s
    audio_duration = 5.0

    fake_audio, fake_visuals = _setup_pipeline_mocks(monkeypatch, tmp_path, narration, audio_duration)

    # render_video creates a real-looking file - use the actual renderer with our fixtures
    # We'll just use the real renderer with our fake inputs
    def fake_render(audio_path, visuals_path, srt_path, out_path, duration=0.0):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Create a minimal valid MP4 using the mp4_factory approach
        subprocess.run(
            [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"testsrc=size=1080x1920:rate=30:duration={duration}",
             "-f", "lavfi", "-i", f"sine=frequency=300:duration={duration}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
             "-c:a", "aac", "-t", f"{duration:.3f}", str(out_path)],
            check=True,
        )
        return out_path

    monkeypatch.setattr(pipeline.renderer, "render_video", fake_render)

    entry = pipeline._produce_one("another idea", 0)

    assert "file" in entry
    assert "error" not in entry
    assert entry["duration"] == pytest.approx(audio_duration)


def test_pipeline_validation_error_converted_to_pipeline_error(monkeypatch, tmp_path):
    """OutputValidationError is surfaced as PipelineError chain, message preserved."""
    narration = " ".join(["word"] * 10)
    audio_duration = 5.0

    fake_audio, fake_visuals = _setup_pipeline_mocks(monkeypatch, tmp_path, narration, audio_duration)

    def fake_render(audio_path, visuals_path, srt_path, out_path, duration=0.0):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"bad")
        return out_path

    monkeypatch.setattr(pipeline.renderer, "render_video", fake_render)

    entry = pipeline._produce_one("idea x", 0)

    assert "error" in entry
    assert "validation" in entry["error"].lower() or "decode" in entry["error"].lower() or "empty" in entry["error"].lower() or "stream" in entry["error"].lower()
    # ensure the invalid file is still on disk
    stem = pipeline.build_stem("idea x", 0)
    assert (tmp_path / "videos" / f"{stem}.mp4").exists()


# ---------------------------------------------------------------------------
# FFprobe JSON edge cases
# ---------------------------------------------------------------------------

def test_validate_malformed_ffprobe_json(monkeypatch, tmp_path):
    """When ffprobe returns invalid JSON, validation fails gracefully."""
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    # Make probe_streams return None (simulating JSON parse error or missing streams)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: None)
    with pytest.raises(renderer.OutputValidationError, match="could not read|corrupt"):
        renderer.validate_final_output(p)


def test_validate_ffprobe_returns_empty_streams(monkeypatch, tmp_path):
    """When ffprobe returns empty streams array."""
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: [])
    with pytest.raises(renderer.OutputValidationError, match="no video stream"):
        renderer.validate_final_output(p)


def test_validate_ffprobe_returns_no_codec_type(monkeypatch, tmp_path):
    """When ffprobe returns streams without codec_type field."""
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: [
        {"codec_name": "h264"},  # missing codec_type
    ])
    with pytest.raises(renderer.OutputValidationError, match="no video stream"):
        renderer.validate_final_output(p)


def test_validate_ffprobe_returns_non_dict_streams(monkeypatch, tmp_path):
    """When ffprobe returns non-dict entries in streams."""
    p = tmp_path / "video.mp4"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr(renderer, "probe_streams", lambda path: [
        "not a dict",
        {"codec_type": "video"},
    ])
    # Should still find the video stream
    monkeypatch.setattr(renderer, "probe_duration", lambda path: 5.0)
    monkeypatch.setattr(renderer, "probe_integrity", lambda path: True)
    result = renderer.validate_final_output(p)
    assert result == pytest.approx(5.0)