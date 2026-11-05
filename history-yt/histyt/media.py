"""ffmpeg/ffprobe wrappers. No console windows on Windows."""
import json
import subprocess
import sys

from .state import PipelineError


def _no_window():
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run_ffmpeg(args, timeout=600):
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                       capture_output=True, timeout=timeout, check=True, **_no_window())
    except FileNotFoundError as exc:
        raise PipelineError("ffmpeg not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"ffmpeg timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace")[-800:]
        raise PipelineError(f"ffmpeg failed: {err}") from exc


def run_ffprobe(path, timeout=60):
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, timeout=timeout, check=True, **_no_window())
    except FileNotFoundError as exc:
        raise PipelineError("ffprobe not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"ffprobe timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace")[-500:]
        raise PipelineError(f"ffprobe failed for {path}: {err}") from exc
    try:
        return json.loads((proc.stdout or b"{}").decode("utf-8", "replace"))
    except ValueError as exc:
        raise PipelineError(f"ffprobe output not JSON for {path}: {exc}") from exc


def get_duration(path):
    info = run_ffprobe(path)
    try:
        return float((info.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def get_streams(path):
    return run_ffprobe(path).get("streams") or []


def has_stream(path, codec_type):
    return any(s.get("codec_type") == codec_type for s in get_streams(path))
