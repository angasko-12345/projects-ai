"""Text-to-speech providers: piper (real) + fake (tests/dry-runs)."""
import os
import re
import subprocess
import wave
from abc import ABC, abstractmethod

from .state import PipelineError


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
        cmd = [self.piper_exe, "--model", self.model, "--output_file", out_wav]
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


class FakeTTSProvider(TTSProvider):
    """0.6s of 16kHz silence per chunk — for tests/dry-runs, no audio content."""

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


def make_tts_provider(cfg):
    tts = (cfg or {}).get("tts") or {}
    provider = (tts.get("provider") or "fake").lower()
    if provider == "piper":
        return PiperProvider(tts.get("piper_exe") or "piper", tts.get("model") or "")
    if provider == "fake":
        return FakeTTSProvider()
    if provider == "kokoro":
        raise PipelineError("kokoro not implemented")
    raise PipelineError(f"unknown TTS provider: {provider!r}")
