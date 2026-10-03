"""
Saving synthesized audio to disk.

Gemini TTS normally returns ``audio/wav`` including a 44-byte RIFF header, but
the API can also return headerless linear PCM (``audio/L16;codec=pcm;rate=...``).
Headerless PCM is unreadable by ffprobe and FFmpeg, which produced the error
"Narration audio is empty or unreadable". Headerless payloads are wrapped in a
WAV container here before being written.
"""
import struct
from pathlib import Path

from . import config


def wav_header(num_samples: int, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """Build a canonical 44-byte RIFF/WAVE header for 16-bit PCM."""
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = num_samples * block_align
    return b"".join([
        b"RIFF",
        struct.pack("<I", 36 + data_size),
        b"WAVEfmt ",
        struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate,
                    block_align, bits_per_sample),
        b"data",
        struct.pack("<I", data_size),
    ])


def has_wav_header(data: bytes) -> bool:
    return len(data) >= 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def ensure_playable(data: bytes, mime_type: str = "") -> bytes:
    """Wrap headerless PCM in a WAV container so FFmpeg can read it.

    Non-PCM payloads (mp3, ogg, and genuine wav) are returned untouched.
    """
    if not data:
        return data
    if has_wav_header(data):
        return data
    mime = (mime_type or "").lower()
    is_pcm = "pcm" in mime or "l16" in mime
    if not is_pcm:
        return data
    rate = 24000
    if "rate=" in mime:
        try:
            rate = int(mime.split("rate=")[1].split(";")[0])
        except (IndexError, ValueError):
            rate = 24000
    channels = 1
    if "channels=" in mime:
        try:
            channels = int(mime.split("channels=")[1].split(";")[0])
        except (IndexError, ValueError):
            channels = 1
    block_align = channels * 2
    return wav_header(len(data) // block_align, rate, channels) + data


def save_audio(audio_bytes: bytes, filename: str, output_dir: Path | None = None) -> Path:
    """Write audio bytes into ``output/audio`` under ``filename``.

    ``output_dir`` lets the pipeline control the destination explicitly
    instead of every module independently resolving the project output path.
    """
    if not audio_bytes:
        raise ValueError("Refusing to save empty audio")
    out = Path(output_dir) if output_dir is not None else config.get_output_dir()
    out = out / "audio"
    out.mkdir(parents=True, exist_ok=True)
    path = out / filename
    path.write_bytes(audio_bytes)
    return path
