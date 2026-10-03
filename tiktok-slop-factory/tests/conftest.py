"""Shared pytest fixtures and path setup."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _tool(name: str) -> bool:
    return shutil.which(name) is not None


ffmpeg_required = pytest.mark.skipif(
    not (_tool("ffmpeg") and _tool("ffprobe")),
    reason="FFmpeg/ffprobe not installed on PATH",
)


@pytest.fixture
def fake_env(monkeypatch):
    """Provide dummy API keys without touching real ones."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("PEXELS_API_KEY", "test-pexels-key")


@pytest.fixture
def mp4_factory(tmp_path):
    """Create small real MP4 files with FFmpeg for renderer tests."""
    counter = {"n": 0}

    def _make(duration: float = 6.0, size: str = "1080x1920", audio: float = 5.0):
        counter["n"] += 1
        out = tmp_path / f"src{counter['n']}.mp4"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={duration}",
        ]
        if audio > 0:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=300:duration={audio}"]
        cmd += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        ]
        if audio > 0:
            cmd += ["-c:a", "aac", "-shortest"]
        cmd.append(str(out))
        subprocess.run(cmd, check=True)
        return out

    return _make