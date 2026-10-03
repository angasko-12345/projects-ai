"""
Pipeline orchestration: idea -> script -> narration -> footage -> video.

Bugs fixed here:

* **Non-reproducible filenames.** Output names used ``abs(hash(idea))``, and
  Python randomizes string hashing per process unless ``PYTHONHASHSEED`` is
  set. Two runs of the same command produced different filenames, so caching
  never worked and duplicate videos piled up. Names are now derived from a
  stable SHA-256 digest plus a slug.
* **Hardcoded 60s duration.** The pipeline passed ``duration=60.0`` to both
  caption timing and the renderer regardless of the actual narration length,
  so subtitles drifted out of sync. The real audio duration is now probed.
* **One failure killed the batch.** An exception from idea generation aborted
  the whole run before any video was attempted. Failures are now isolated per
  idea and the run continues.
* **Stale intermediate files** accumulated forever; old ``.partial.mp4`` and
  orphan audio/caption files are cleaned up.
* ``datetime.utcnow()`` is deprecated and returns a naive datetime.
"""
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from . import captions as cap
from . import gemini, renderer, script as sc, tts, visuals
from .config import (
    ConfigError,
    get_ffmpeg_path,
    get_ffprobe_path,
    get_output_dir,
)


class PipelineError(Exception):
    pass


def _tool_exists(name: str, version_flag: str = "-version") -> bool:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if _is_windows() else 0
    try:
        proc = subprocess.run(
            [name, version_flag],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            creationflags=creationflags,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _is_windows() -> bool:
    import os

    return os.name == "nt"


def _slug(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug[:max_len].strip("-") or "story")


def build_stem(idea: str, index: int | None = None) -> str:
    """Return a deterministic, filesystem-safe stem for an idea.

    Uses a SHA-256 digest instead of ``hash()`` so names are stable across
    processes and runs.
    """
    digest = hashlib.sha256((idea or "").strip().encode("utf-8")).hexdigest()[:8]
    prefix = f"{index + 1:02d}-" if index is not None else ""
    return f"{prefix}{_slug(idea, 32)}-{digest}"


def select_best_ideas(ideas: List[str], count: int = 3) -> List[str]:
    """Take the first ``count`` distinct ideas, preserving order."""
    seen = set()
    out: List[str] = []
    for idea in ideas or []:
        clean = str(idea).strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
        if len(out) >= max(1, int(count)):
            break
    return out


def check_dependencies() -> List[str]:
    """Return a list of human-readable setup problems, empty when all is well."""
    problems: List[str] = []
    try:
        gemini.get_gemini_api_key()
    except ConfigError as e:
        problems.append(str(e))
    if not _tool_exists(get_ffmpeg_path()):
        problems.append(
            f"FFmpeg not found ({get_ffmpeg_path()!r}). Install it and put it on PATH, "
            "or set FFMPEG_PATH."
        )
    if not _tool_exists(get_ffprobe_path()):
        problems.append(
            f"ffprobe not found ({get_ffprobe_path()!r}). It ships with FFmpeg."
        )
    return problems


def cleanup_stale(directory: Path, patterns: tuple) -> int:
    """Remove leftover files matching ``patterns``; returns the count removed."""
    removed = 0
    for pattern in patterns:
        for p in directory.glob(pattern):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _seed(idea: str, index: int) -> int:
    """Deterministic scene seed, so re-running the same idea looks the same."""
    digest = hashlib.sha256((idea or "").strip().encode("utf-8")).hexdigest()
    return int(digest[:12], 16) + int(index)


def build_metadata(
    title: str,
    script: Dict[str, Any],
    caption: str,
    hashtags: List[str],
    scenes: List[Any],
    filename: str,
    duration: float,
) -> Dict[str, Any]:
    return {
        "title": title,
        "script": script,
        "caption": caption,
        "hashtags": hashtags,
        "visual_source": "generated locally with FFmpeg (no stock API)",
        "scenes": [
            {
                "index": s.index,
                "duration_seconds": round(s.duration, 3),
                "style": s.style,
                "motion": s.motion,
                "transition": s.transition,
                "keywords": s.keywords,
            }
            for s in scenes
        ],
        "creation_timestamp": datetime.now(timezone.utc).isoformat(),
        "output_filename": filename,
        "duration_seconds": round(float(duration), 2),
        "disclaimer": "Fictional story content, not real news.",
    }


def _build_captions(text: str, duration: float, srt_path: Path) -> Path:
    cues = cap.simple_timestamps(text, duration)
    cues = cap.group_cues(cues, max_words=3)
    return cap.to_srt(cues, srt_path)


def run_pipeline(count: int = 3, dry_run: bool = False) -> Dict[str, Any]:
    """Generate up to ``count`` videos. Never raises for per-idea failures."""
    result: Dict[str, Any] = {"success": [], "failed": []}
    count = max(1, int(count))

    if dry_run:
        problems = check_dependencies()
        for problem in problems:
            result["failed"].append({"step": "config", "error": problem})
        if not problems:
            result["success"].append({"step": "dry-run", "msg": "config and deps OK"})
        return result

    out_dir = get_output_dir()
    for sub in ("videos", "audio", "captions", "metadata", "visuals"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
    # Clear half-written renders left behind by a crashed previous run.
    cleanup_stale(out_dir / "videos", ("*.partial.mp4",))
    cleanup_stale(out_dir / "visuals", ("*.partial.mp4",))

    try:
        ideas = select_best_ideas(gemini.generate_ideas(max(count * 3, 6)), count)
    except Exception as e:
        result["failed"].append({"step": "ideas", "error": str(e)})
        return result

    if not ideas:
        result["failed"].append({"step": "ideas", "error": "No usable ideas returned"})
        return result

    for index, idea in enumerate(ideas):
        entry = _produce_one(idea, index)
        if entry.get("file"):
            result["success"].append(entry)
        else:
            result["failed"].append(entry)

    return result


def _produce_one(idea: str, index: int) -> Dict[str, Any]:
    """Produce a single video, returning either a success or a failure entry."""
    stem = build_stem(idea, index)
    out_dir = get_output_dir()
    audio_path: Path | None = None
    srt_path: Path | None = None

    try:
        script = gemini.generate_script(idea)
        narration = gemini.script_to_text(script)
        if not narration:
            raise PipelineError("Script contained no narration text")

        blob = gemini.generate_tts(script)
        # Headerless PCM must be wrapped before it reaches FFmpeg.
        playable = tts.ensure_playable(blob.data, blob.mime_type)
        audio_path = tts.save_audio(
            playable, f"{stem}{blob.extension}", output_dir=out_dir
        )

        duration = renderer.probe_duration(audio_path)
        if not duration or duration < 1.0:
            raise PipelineError("Narration audio is empty or unreadable")

        # Visuals are generated locally from the script: no stock-media API.
        scenes = visuals.plan_scenes(script, duration=duration, seed=_seed(idea, index))
        if not scenes:
            raise PipelineError("Could not plan any scenes from the script")
        visuals_path = visuals.render_background(
            scenes, out_dir / "visuals" / f"{stem}.mp4",
            duration=duration, work_dir=out_dir / "visuals",
        )

        srt_path = out_dir / "captions" / f"{stem}.srt"
        _build_captions(narration, duration, srt_path)

        out_path = out_dir / "videos" / f"{stem}.mp4"
        renderer.render_video(
            audio_path, visuals_path, srt_path, out_path, duration=duration
        )

        meta = build_metadata(
            title=script.get("title") or "Fictional story",
            script=script,
            caption=gemini.generate_caption(script),
            hashtags=list(script.get("hashtags") or []),
            scenes=scenes,
            filename=out_path.name,
            duration=duration,
        )
        meta_dir = out_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / f"{stem}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return {
            "file": str(out_path),
            "duration": round(duration, 2),
            "idea": idea,
            "scenes": len(scenes),
            "visual_styles": [s.style for s in scenes],
        }
    except Exception as e:
        # Remove this video's intermediates so failures do not leave litter.
        for p in (srt_path, audio_path, locals().get("visuals_path")):
            if p is not None and p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        return {"idea": idea, "error": str(e)}
