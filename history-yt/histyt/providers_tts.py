"""Text-to-speech providers: piper, ElevenLabs, fake (tests/dry-runs)."""
import os
import re
import subprocess
import wave
from abc import ABC, abstractmethod

import requests

from .media import run_ffmpeg
from .state import PipelineError

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
ELEVENLABS_DEFAULT_MODEL = "eleven_multilingual_v2"
ELEVENLABS_DEFAULT_FORMAT = "mp3_44100_128"


class TTSProvider(ABC):
    @abstractmethod
    def synthesize_chunk(self, text, out_wav, pace):
        """Write chunk WAV; return duration in seconds."""
        raise NotImplementedError


def chunk_text(text):
    parts = re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text or "")
    return [p.strip() for p in parts if p.strip()]


def _wav_duration(path):
    with wave.open(path, "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate() or 1
    return frames / float(rate)


def _length_scale(pace):
    """Pace is a speed multiplier (1.0 = normal); Piper takes its inverse."""
    return min(2.0, max(0.5, 1.0 / float(pace or 1.0)))


def _clamp_speed(pace):
    return min(1.2, max(0.7, float(pace or 1.0)))


class PiperProvider(TTSProvider):
    def __init__(self, piper_exe, model, timeout=120):
        self.piper_exe = piper_exe or "piper"
        self.model = model or ""
        self.timeout = timeout

    def synthesize_chunk(self, text, out_wav, pace):
        if not self.piper_exe or not os.path.isfile(self.piper_exe) and not _on_path(self.piper_exe):
            raise PipelineError(f"piper executable not found: {self.piper_exe!r}")
        if not self.model or not _on_path(self.model) and not os.path.isfile(self.model):
            raise PipelineError(f"piper model not found: {self.model!r} (set tts.model)")
        os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
        cmd = [self.piper_exe, "--model", self.model,
               "--length_scale", f"{_length_scale(pace):.3f}",
               "--output_file", out_wav]
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True,
                           timeout=self.timeout, check=True)
        except FileNotFoundError as exc:
            raise PipelineError(f"piper executable not found: {self.piper_exe!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PipelineError(f"piper timed out after {self.timeout}s") from exc
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or b"").decode("utf-8", "replace")[:500]
            raise PipelineError(f"piper failed: {err}") from exc
        return _wav_duration(out_wav)


class ElevenLabsProvider(TTSProvider):
    """ElevenLabs TTS over the REST API; returns a WAV converted with ffmpeg."""

    def __init__(self, api_key, voice_id=None, model=None, output_format=None,
                 timeout=180):
        self.api_key = api_key or ""
        self.voice_id = voice_id or ELEVENLABS_DEFAULT_VOICE
        self.model = model or ELEVENLABS_DEFAULT_MODEL
        self.output_format = output_format or ELEVENLABS_DEFAULT_FORMAT
        self.timeout = timeout

    def synthesize_chunk(self, text, out_wav, pace):
        if not self.api_key:
            raise PipelineError(
                "ELEVENLABS_API_KEY is not set; set it or switch tts.provider to fake")
        url = f"{ELEVENLABS_BASE}/{self.voice_id}"
        payload = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": round(_clamp_speed(pace), 3),
            },
        }
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json",
                   "Accept": "audio/mpeg"}
        try:
            resp = requests.post(url, params={"output_format": self.output_format},
                                 json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise PipelineError(f"ElevenLabs request failed: {exc}") from exc
        if resp.status_code >= 400:
            text_err = (resp.text or "")[:500].replace(self.api_key, "[redacted]")
            raise PipelineError(f"ElevenLabs HTTP {resp.status_code}: {text_err}")
        if not resp.content:
            raise PipelineError("ElevenLabs returned an empty audio body")
        os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
        mp3_path = out_wav + ".mp3"
        try:
            with open(mp3_path, "wb") as f:
                f.write(resp.content)
            run_ffmpeg(["-i", mp3_path, "-ar", "44100", "-ac", "1",
                        "-c:a", "pcm_s16le", out_wav])
        finally:
            if os.path.isfile(mp3_path):
                os.remove(mp3_path)
        return _wav_duration(out_wav)


class FakeTTSProvider(TTSProvider):
    """0.6s of 16kHz silence per chunk - for tests/dry-runs, no audio content."""

    def synthesize_chunk(self, text, out_wav, pace):
        os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
        rate = 16000
        with wave.open(out_wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(b"\x00\x00" * int(rate * 0.6))
        return 0.6


def _on_path(exe):
    import shutil
    return bool(shutil.which(exe))


def make_tts_provider(cfg, env=None):
    env = os.environ if env is None else env
    tts = (cfg or {}).get("tts") or {}
    provider = (tts.get("provider") or "fake").lower()
    if provider == "piper":
        return PiperProvider(tts.get("piper_exe") or "piper", tts.get("model") or "")
    if provider == "elevenlabs":
        return ElevenLabsProvider(
            env.get("ELEVENLABS_API_KEY", ""),
            voice_id=tts.get("elevenlabs_voice_id") or ELEVENLABS_DEFAULT_VOICE,
            model=tts.get("elevenlabs_model") or ELEVENLABS_DEFAULT_MODEL,
            output_format=tts.get("elevenlabs_output_format") or ELEVENLABS_DEFAULT_FORMAT,
        )
    if provider == "fake":
        return FakeTTSProvider()
    if provider == "kokoro":
        raise PipelineError("kokoro not implemented")
    raise PipelineError(f"unknown TTS provider: {provider!r}")
