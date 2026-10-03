"""
Render the final vertical MP4 with FFmpeg.

Bugs fixed here, each reproduced against a real FFmpeg build first:

* **Landscape footage failed outright.** The old chain used
  ``scale=...:force_original_aspect_ratio=decrease`` followed by
  ``crop=1080:1920``. ``decrease`` shrinks a 1280x720 clip to fit *inside*
  1080x1920, i.e. 1080x608, so the subsequent ``crop`` asks for more pixels
  than exist and FFmpeg aborts with ``Invalid too big or non positive size``.
  Correct order is ``increase`` (cover) then ``crop``.
* **Windows paths broke the subtitles filter.** Passing an absolute
  ``D:/...`` path into ``subtitles='...'`` makes FFmpeg parse ``D:`` as an
  option and fail with ``Unable to parse "original_size"``. We now run FFmpeg
  with ``cwd`` set to the caption directory and pass a bare filename, so no
  drive letter ever reaches the filter parser.
* **Output was truncated to the footage length.** ``-shortest`` plus a short
  Pexels clip produced a 5.4s video out of a 60s narration. The footage input
  is now looped with ``-stream_loop -1`` and the output is bounded by the
  audio duration.
* **Audio could be dropped**, because the map used ``1:a?``. The narration is
  mandatory, so the audio stream is mapped explicitly and the result is
  verified.
* **Dead code**: three filter strings were built and two were thrown away on
  every call.
* **``-pix_fmt yuv420p``** is now forced, since players reject other pix fmts.
* **Temp files** are cleaned up and the output is verified with ffprobe.
"""
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from .config import get_ffmpeg_path, get_ffprobe_path


WIDTH = 1080
HEIGHT = 1920
FPS = 30


class RenderError(Exception):
    pass


def _run(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 1800) -> str:
    """Run a subprocess, raising RenderError with the tail of stderr on failure."""
    creationflags = 0
    if os.name == "nt":
        # Avoid popping a black console window on Windows.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            creationflags=creationflags,
        )
        return proc.stderr or ""
    except subprocess.TimeoutExpired:
        raise RenderError(f"FFmpeg timed out after {timeout}s") from None
    except FileNotFoundError:
        raise RenderError(
            f"FFmpeg not found at {cmd[0]!r}. Install FFmpeg and put it on PATH, "
            "or set FFMPEG_PATH."
        ) from None
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or "").strip().splitlines()
        # The banner dominates the output; the useful error is at the end.
        detail = "\n".join(tail[-12:]) if tail else f"exit code {e.returncode}"
        raise RenderError(f"FFmpeg failed: {detail}") from None


def probe_duration(path: Path) -> Optional[float]:
    """Return the duration of ``path`` in seconds, or None if unknown."""
    cmd = [
        get_ffprobe_path(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            creationflags=creationflags,
        )
        return float(proc.stdout.strip())
    except Exception:
        return None


def probe_has_audio(path: Path) -> bool:
    cmd = [
        get_ffprobe_path(),
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            creationflags=creationflags,
        )
        return "audio" in proc.stdout
    except Exception:
        return False


def _verify_output(path: Path, min_duration: float) -> None:
    """Fail loudly rather than shipping a silent or truncated MP4."""
    if not path.is_file() or path.stat().st_size < 10_000:
        raise RenderError("Output video was not created or is suspiciously small")

    duration = probe_duration(path)
    if duration is None:
        raise RenderError("Output video could not be probed; it is likely corrupt")
    if duration < max(1.0, min_duration * 0.9):
        raise RenderError(
            f"Output video is {duration:.1f}s but narration is about "
            f"{min_duration:.1f}s; refusing a truncated result"
        )
    if not probe_has_audio(path):
        raise RenderError("Output video has no audio stream")

def render_video(
    audio_path: Path,
    footage_path: Path,
    srt_path: Path,
    out_path: Path,
    duration: float = 0.0,
) -> Path:
    """Burn subtitles over looping footage and mux the narration.

    ``duration`` is the narration length; when omitted it is probed from the
    audio file so the output always matches the voiceover.
    """
    # ``_run`` sets cwd to the caption directory so the bare SRT filename below
    # resolves. That makes relative input/output paths ambiguous, so pin every
    # path we hand to FFmpeg to an absolute one first.
    audio_path = Path(audio_path).resolve()
    footage_path = Path(footage_path).resolve()
    srt_path = Path(srt_path).resolve()
    out_path = Path(out_path).resolve()

    for label, p in (("audio", audio_path), ("footage", footage_path), ("srt", srt_path)):
        if not p.is_file():
            raise RenderError(f"Missing {label} input: {p}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if duration and duration > 0:
        target = float(duration)
    else:
        target = probe_duration(audio_path)
    if not target or target <= 0:
        raise RenderError("Could not determine narration duration")

    # A bare filename plus cwd sidesteps Windows drive-letter escaping entirely.
    srt_name = srt_path.name

    vf = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1,format=yuv420p[v0]"
    )
    # libass lays subtitles out against a 384x288 reference canvas by default and
    # then scales to the real frame. So FontSize/MarginV are in that small space,
    # not pixels: FontSize=64 renders at roughly 180px on a 1080x1920 frame and
    # pushes the text off screen. These values were tuned against real renders.
    style = (
        "FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
        "Alignment=2,MarginV=60"
    )
    vf += f";[v0]subtitles={srt_name}:force_style='{style}'[vout]"

    tmp_out = out_path.with_name(out_path.stem + ".partial.mp4")

    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        # Loop the stock clip so a short clip can cover the full narration.
        "-stream_loop", "-1",
        "-i", str(footage_path),
        "-i", str(audio_path),
        "-filter_complex", vf,
        "-map", "[vout]",
        # Narration is required; map it explicitly instead of the old 1:a?
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", "4.0",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        "-movflags", "+faststart",
        "-t", f"{target:.3f}",
        str(tmp_out),
    ]

    try:
        _run(cmd, cwd=srt_path.parent)
        if not tmp_out.is_file() or tmp_out.stat().st_size < 10_000:
            raise RenderError("FFmpeg produced no usable output file")
        # Verify before publishing so a bad render never lands in output/videos/.
        _verify_output(tmp_out, target)
    except BaseException:
        # Failed render: drop the partial file, never publish it.
        if tmp_out.exists():
            try:
                tmp_out.unlink()
            except OSError:
                pass
        raise

    tmp_out.replace(out_path)
    return out_path
