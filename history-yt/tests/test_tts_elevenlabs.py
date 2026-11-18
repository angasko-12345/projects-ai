import os
import wave
from unittest import mock

import pytest

from histyt.providers_tts import (ElevenLabsProvider, PiperProvider,
                                  _clamp_speed, _length_scale)
from histyt.state import PipelineError


class FakeResponse:
    def __init__(self, status=200, content=b"mp3-bytes", text="", headers=None):
        self.status_code = status
        self.content = content
        self.text = text
        self.headers = headers or {}


def _write_wav(path, seconds=0.5, rate=16000):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))


def test_missing_key_raises_before_any_request():
    provider = ElevenLabsProvider("")
    with pytest.raises(PipelineError, match="ELEVENLABS_API_KEY"):
        provider.synthesize_chunk("Hello.", "out.wav", 1.0)


def test_success_sends_speed_and_writes_wav(tmp_path):
    out = str(tmp_path / "chunk.wav")

    def fake_ffmpeg(args, timeout=600):
        _write_wav(out)

    with mock.patch("histyt.providers_tts.requests.post",
                    return_value=FakeResponse()) as post, \
            mock.patch("histyt.providers_tts.run_ffmpeg", side_effect=fake_ffmpeg):
        duration = ElevenLabsProvider("test-key", voice_id="voice-1").synthesize_chunk(
            "Hello there.", out, 1.1)
    assert duration == pytest.approx(0.5)
    kwargs = post.call_args.kwargs
    assert kwargs["headers"]["xi-api-key"] == "test-key"
    assert kwargs["params"]["output_format"] == "mp3_44100_128"
    assert kwargs["json"]["voice_settings"]["speed"] == pytest.approx(1.1)
    assert kwargs["json"]["text"] == "Hello there."
    assert not os.path.exists(out + ".mp3")


def test_http_error_redacts_the_key(tmp_path):
    out = str(tmp_path / "chunk.wav")
    failing = FakeResponse(status=401, text="key test-key is invalid")
    with mock.patch("histyt.providers_tts.requests.post", return_value=failing):
        with pytest.raises(PipelineError) as exc:
            ElevenLabsProvider("test-key").synthesize_chunk("Hi.", out, 1.0)
    message = str(exc.value)
    assert "test-key" not in message
    assert "[redacted]" in message


def test_pace_is_wired_into_piper_length_scale(tmp_path):
    with mock.patch("histyt.providers_tts.subprocess.run") as run, \
            mock.patch("histyt.providers_tts._on_path", return_value=True), \
            mock.patch("histyt.providers_tts._wav_duration", return_value=1.0):
        PiperProvider("piper", "model.onnx").synthesize_chunk(
            "Hi.", str(tmp_path / "a.wav"), 0.8)
    command = run.call_args.args[0]
    index = command.index("--length_scale")
    assert float(command[index + 1]) == pytest.approx(1.25)


def test_pace_math_is_bounded():
    assert _length_scale(0.5) == 2.0
    assert _length_scale(2.0) == 0.5
    assert _clamp_speed(0.1) == 0.7
    assert _clamp_speed(5.0) == 1.2
