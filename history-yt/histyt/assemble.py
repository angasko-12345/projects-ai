"""Assemble per-chunk segments, concat, captions, sidecar/burned subtitle."""
import json
import os
import subprocess
import sys

from . import state as state_mod
from .media import get_streams, has_stream, run_ffmpeg, run_ffprobe
from .state import PipelineError

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _fmt_srt_time(seconds):
    seconds = max(seconds, 0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_captions(vdir, durations, claims):
    path = os.path.join(vdir, "captions.srt")
    lines = []
    cursor = 0.0
    for i, entry in enumerate(durations, 1):
        text = entry.get("text", "")
        start = cursor
        end = cursor + float(entry.get("duration") or 0)
        lines += [str(i), f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}",
                  text, ""]
        cursor = end  # cumulative offset: each start = sum of all previous durations
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _image_list(vdir):
    candidates = []
    assets_path = os.path.join(vdir, "assets.json")
    if os.path.isfile(assets_path):
        with open(assets_path, encoding="utf-8") as f:
            for asset in json.load(f):
                p = asset.get("path", "")
                if p.lower().endswith(IMAGE_EXTS):
                    ap = p if os.path.isabs(p) else os.path.join(vdir, p)
                    candidates.append(ap)
    images_dir = os.path.join(vdir, "images")
    for name in ("title.png", "timeline.png"):
        p = os.path.join(images_dir, name)
        if os.path.isfile(p) and p not in candidates:
            candidates.append(p)
    return candidates


def _segment_with_image(vdir, image, wav, duration, fps, out_path):
    """Loop image, pair with wav, zoompan + scale/crop to 1920x1080."""
    dur = max(float(duration), 0.1)
    filt = f"scale=2560:1440,zoompan=z='1+0.05*on/{int(fps * max(dur, 1))}':d=1:fps={fps}:s=2560x1440,crop=1920:1080" if image else ""
    args = []
    if image:
        args += ["-loop", "1", "-t", f"{dur}", "-i", image]
    else:
        # Black frame via color source avoids a missing-image failure.
        args += ["-f", "lavfi", "-t", f"{dur}", "-i", "color=c=black:s=1920x1080:r=30"]
    args += ["-i", wav, "-vf", filt or "format=yuv420p",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
             "-c:a", "aac", "-shortest", out_path]
    run_ffmpeg(args)


def build_description(script_meta, sources, title):
    lines = [title or "", "", "Sources:"]
    for s in sources:
        lines.append(f"- {s.get('id')}: {s.get('title', '')} — {s.get('url', '')}")
    claims = (script_meta or {}).get("claims") or []
    if claims:
        lines += ["", "Claims & citations:"]
        for c in claims:
            lines.append(f"- {c.get('id')}: {c.get('text', '')} ({', '.join(c.get('sources', []))})")
    needs = (script_meta or {}).get("needs_check") or []
    if needs:
        lines += ["", "Needs human check:"]
        for item in needs:
            lines.append(f"- {item}")
    return "\n".join(lines)


def assemble_stage(runs_dir, video_id, cfg):
    state = state_mod.load(runs_dir, video_id)
    state_mod.require_script_approval(state, runs_dir)
    vdir = os.path.join(runs_dir, video_id)
    durations_path = os.path.join(vdir, "durations.json")
    if not os.path.isfile(durations_path):
        raise PipelineError(f"durations.json missing for {video_id}; run voice stage first")
    with open(durations_path, encoding="utf-8") as f:
        durations = json.load(f)
    if not durations:
        raise PipelineError("no audio chunks to assemble")
    videos_cfg = cfg.get("videos") or {}
    width, height = int(videos_cfg.get("width", 1920)), int(videos_cfg.get("height", 1080))
    fps = int(videos_cfg.get("fps", 30))
    images = _image_list(vdir)
    seg_dir = os.path.join(vdir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    segments = []
    for i, entry in enumerate(durations, 1):
        wav = os.path.join(vdir, entry.get("file", ""))
        if not os.path.isfile(wav):
            raise PipelineError(f"audio chunk missing: {wav}")
        image = images[i % len(images)] if images else None
        out = os.path.join(seg_dir, f"seg_{i:03d}.mp4")
        _segment_with_image(vdir, image, entry and wav, entry.get("duration") or 0.5, fps, out)
        segments.append(out)
    concat_path = os.path.join(vdir, "video.mp4")
    list_path = os.path.join(vdir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(f"file '{seg}'\n")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_path,
                "-c", "copy", concat_path])
    write_captions(vdir, durations, [])
    final = os.path.join(vdir, "final.mp4")
    subtitle_ok = True
    try:
        # Run with cwd=captions dir so ffmpeg subtitles filter resolves the sidecar.
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", concat_path,
             "-vf", "subtitles=captions.srt", "-c:a", "copy", final],
            capture_output=True, timeout=600, check=True, cwd=vdir,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        subtitle_ok = False
        print(f"warning: subtitle burn failed, keeping sidecar only: {exc}")
        # Fallback: copy video.mp4 as final (sidecar captions stay in captions.srt).
        run_ffmpeg(["-i", concat_path, "-c", "copy", final])
    # Validate with ffprobe.
    info = run_ffprobe(final)
    duration = float((info.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise PipelineError("final.mp4 has no duration")
    if not has_stream(final, "video") or not has_stream(final, "audio"):
        raise PipelineError("final.mp4 must have both video and audio streams")
    for stream in get_streams(final):
        if stream.get("codec_type") == "video":
            if int(stream.get("width") or 0) != width or int(stream.get("height") or 0) != height:
                raise PipelineError(
                    f"final.mp4 is {stream.get('width')}x{stream.get('height')}, expected {width}x{height}")
    state_mod.set_stage(runs_dir, video_id, "assemble")
    return {"video": concat_path, "final": final, "subtitle_burned": subtitle_ok,
            "duration": duration}
