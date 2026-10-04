"""Narration coherence gate.

The gate runs after TTS and after the duration probe, before any visual
rendering, so an impossible text/audio pairing costs a fraction of a render
instead of a full 1080x1920 encode.

Audio fixtures are synthesized with the project's own FFmpeg via the existing
``ffmpeg_required`` convention from ``conftest``. Nothing here calls Gemini or
the real TTS, and no test needs network access.
"""
import subprocess

import pytest

from app import captions, pipeline, renderer
from conftest import FFMPEG, ffmpeg_required

_NARRATION = (
    "The lighthouse keeper heard his name, after eleven silent winters. "
    "He counted every wave, and every wave answered."
)


def _wav(tmp_path, name, seconds, *, silent=False):
    """Build a real WAV of ``seconds`` with FFmpeg (sine tone or silence)."""
    source = "anullsrc=r=44100:cl=mono" if silent else "sine=frequency=300"
    path = tmp_path / name
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"{source}:duration={seconds}",
         "-c:a", "pcm_s16le", str(path)],
        check=True,
    )
    return path


# --- silence ------------------------------------------------------------


@ffmpeg_required
def test_silent_audio_with_valid_duration_is_rejected(tmp_path):
    """A 6s silent WAV is probeable but has no voice; it must not render."""
    path = _wav(tmp_path, "silent.wav", 6.0, silent=True)
    assert renderer.probe_duration(path) == pytest.approx(6.0, abs=0.3)

    with pytest.raises(pipeline.PipelineError) as err:
        pipeline._assert_narration_coherent(_NARRATION, path, 6.0)
    assert "silent" in str(err.value).lower()


@ffmpeg_required
def test_silence_threshold_sits_below_real_audio(tmp_path):
    """The threshold must separate digital silence from quiet-but-audible."""
    silent = pipeline._measure_volume_db(_wav(tmp_path, "s.wav", 3.0, silent=True))
    tone = pipeline._measure_volume_db(_wav(tmp_path, "t.wav", 3.0))
    assert silent is not None and tone is not None
    assert silent <= pipeline.SILENCE_MAX_DB
    assert tone > pipeline.SILENCE_MAX_DB


@ffmpeg_required
def test_ordinary_quiet_narration_is_accepted(tmp_path):
    """Low amplitude is not silence: -45 dB of real tone must pass."""
    path = tmp_path / "quiet.wav"
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=8",
         "-af", "volume=-45dB", "-c:a", "pcm_s16le", str(path)],
        check=True,
    )
    measured = pipeline._measure_volume_db(path)
    assert measured is not None and measured > pipeline.SILENCE_MAX_DB

    low, high = captions.estimate_speech_range(_NARRATION)
    seconds = (low + high) / 2
    pipeline._assert_narration_coherent(_NARRATION, path, seconds)


# --- duration mismatch --------------------------------------------------


@ffmpeg_required
def test_two_hundred_words_in_three_seconds_is_rejected(tmp_path):
    """The headline case: 200 words cannot be spoken in 3 seconds."""
    text = " ".join(["word"] * 200)
    path = _wav(tmp_path, "tone.wav", 3.0)

    with pytest.raises(pipeline.PipelineError) as err:
        pipeline._assert_narration_coherent(text, path, 3.0)
    message = str(err.value)
    assert "too short" in message.lower()
    assert "3.0s" in message


@ffmpeg_required
def test_audio_far_longer_than_the_script_is_rejected(tmp_path):
    """A padded 90s track for a short script is the mirror-image failure."""
    path = _wav(tmp_path, "long.wav", 90.0)

    with pytest.raises(pipeline.PipelineError) as err:
        pipeline._assert_narration_coherent(_NARRATION, path, 90.0)
    assert "too long" in str(err.value).lower()


@ffmpeg_required
def test_plausible_pairing_is_accepted(tmp_path):
    low, high = captions.estimate_speech_range(_NARRATION)
    seconds = (low + high) / 2
    pipeline._assert_narration_coherent(_NARRATION, _wav(tmp_path, "ok.wav", seconds),
                                        seconds)


@ffmpeg_required
def test_slow_but_valid_narration_is_accepted(tmp_path):
    """A deliberately slow read, still inside the band, must pass."""
    low, high = captions.estimate_speech_range(_NARRATION)
    slow = high * 0.95
    assert slow > (low + high) / 2, "slow case must actually be slower than midpoint"

    pipeline._assert_narration_coherent(_NARRATION, _wav(tmp_path, "slow.wav", slow),
                                        slow)


@ffmpeg_required
def test_fast_but_valid_narration_is_accepted(tmp_path):
    low, high = captions.estimate_speech_range(_NARRATION)
    fast = low * 1.05
    assert fast < (low + high) / 2

    pipeline._assert_narration_coherent(_NARRATION, _wav(tmp_path, "fast.wav", fast),
                                        fast)


# --- error quality ------------------------------------------------------


def test_mismatch_errors_state_duration_and_range(tmp_path):
    """The message must be actionable, not just a rejection."""
    low, high = captions.estimate_speech_range(_NARRATION)
    missing = tmp_path / "does-not-exist.wav"

    with pytest.raises(pipeline.PipelineError) as too_long:
        pipeline._assert_narration_coherent(_NARRATION, missing, 1e6)
    message = str(too_long.value)
    assert "1000000.0s" in message
    assert f"{low:.1f}-{high:.1f}s" in message
    assert "Regenerate" in message


def test_short_and_long_errors_are_distinguishable(tmp_path):
    missing = tmp_path / "does-not-exist.wav"
    long_text = " ".join(["word"] * 500)

    with pytest.raises(pipeline.PipelineError) as too_long:
        pipeline._assert_narration_coherent(_NARRATION, missing, 1e6)
    with pytest.raises(pipeline.PipelineError) as too_short:
        pipeline._assert_narration_coherent(long_text, missing, 1.0)

    assert "too long" in str(too_long.value).lower()
    assert "too short" in str(too_short.value).lower()
    assert str(too_long.value) != str(too_short.value)


# --- the estimator itself ----------------------------------------------


def test_estimate_returns_a_band_not_a_point_value():
    low, high = captions.estimate_speech_range(_NARRATION)
    assert 0 < low < high


def test_longer_text_predicts_a_longer_narration():
    low, high = captions.estimate_speech_range(_NARRATION)
    heavier = captions.estimate_speech_range(
        _NARRATION + " " + " ".join(["extraordinarily"] * 30)
    )
    assert heavier[0] > low and heavier[1] > high


def test_estimate_reuses_the_caption_weights():
    """One speech model, not two: punctuation must raise the prediction too."""
    plain = captions.estimate_speech_range("cat cat cat cat")[1]
    punctuated = captions.estimate_speech_range("cat, cat, cat, cat.")[1]
    assert punctuated > plain


def test_empty_text_yields_the_floor():
    low, high = captions.estimate_speech_range("   ")
    assert low == high == captions.MIN_PREDICTED_SEC


def test_estimate_is_deterministic():
    assert captions.estimate_speech_range(_NARRATION) == (
        captions.estimate_speech_range(_NARRATION)
    )


# --- pipeline placement -------------------------------------------------


def test_gate_runs_before_visual_rendering(monkeypatch, tmp_path):
    """A rejected narration must never reach the expensive render stage."""
    monkeypatch.setattr(pipeline, "get_output_dir", lambda: tmp_path)
    monkeypatch.setattr(
        pipeline.gemini, "generate_script",
        lambda idea: {
            "hook": "H", "story": "S", "twist": "T",
            "ending": "E", "cta": "C", "title": "t", "hashtags": [],
        },
    )

    audio = _wav(tmp_path, "speech.wav", 20.0)
    monkeypatch.setattr(pipeline.gemini, "generate_tts",
                        lambda s: pipeline.gemini.AudioBlob(b"RIFF", "audio/wav"))
    monkeypatch.setattr(pipeline.tts, "ensure_playable", lambda d, m: d)
    monkeypatch.setattr(pipeline.tts, "save_audio", lambda *a, **k: audio)

    def _boom(*args, **kwargs):
        raise AssertionError("visual rendering must not run")

    monkeypatch.setattr(pipeline.visuals, "plan_scenes", _boom)

    entry = pipeline._produce_one("some idea", 0)
    assert "error" in entry
    assert not list((tmp_path / "visuals").glob("*.mp4"))
    assert not list((tmp_path / "videos").glob("*.mp4"))