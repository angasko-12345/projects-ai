"""Assemble per-chunk segments, concat, captions, subtitle burn, loudness."""
import json
import math
import os
import shutil
import subprocess
import sys

from . import scenes as scenes_mod
from . import state as state_mod
from .media import get_streams, has_stream, run_ffmpeg, run_ffprobe
from .state import PipelineError

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
LOUDNESS_TARGET = -14.0


def _fmt_srt_time(seconds):
    seconds = max(seconds, 0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_chapter_time(seconds):
    seconds = max(int(seconds), 0)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def write_captions(vdir, durations):
    path = os.path.join(vdir, "captions.srt")
    lines = []
    cursor = 0.0
    for i, entry in enumerate(durations, 1):
        text = entry.get("text", "")
        start = cursor
        end = cursor + float(entry.get("duration") or 0)
        lines += [str(i), f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}", text, ""]
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


def _scene_assets(runs_dir, video_id, vdir):
    manifest = scenes_mod.load_manifest(runs_dir, video_id)
    mapping = {}
    for scene in (manifest or {}).get("scenes") or []:
        path = scene.get("asset")
        if not path:
            continue
        resolved = path if os.path.isabs(path) else os.path.join(vdir, path)
        if not os.path.isfile(resolved):
            raise PipelineError(
                f"scene {scene.get('scene_id')} asset missing: {resolved}; re-run visuals")
        mapping[scene.get("scene_id")] = resolved
    return mapping


def _segment_with_image(image, wav, duration, fps, out_path, width=1920, height=1080):
    """Loop image, pair with wav, zoompan + scale/crop to the configured size.

    The image input needs an explicit frame rate: `-loop 1` otherwise defaults
    to 25 fps, so a 0.6s segment renders 15 frames (0.5s) and every chunk loses
    time against the narration.

    Length is frame-exact. AAC pads each segment by its priming samples, so a
    concat of `-shortest` segments carries audio slightly longer than audio/
    frames and the joined file's average frame rate lands under target (29.907
    instead of 30 on a 28-chunk video). Each segment is therefore cut to a whole
    number of frames and its audio is padded and trimmed to that same length.
    """
    dur = max(float(duration), 0.1)
    frames = max(1, int(round(dur * fps)))
    seg_dur = frames / fps
    big_w, big_h = max(width, int(width * 4 / 3)), max(height, int(height * 4 / 3))
    filt = (
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
        f"crop={big_w}:{big_h},"
        f"zoompan=z='1+0.05*on/{int(fps * max(dur, 1))}':d=1:fps={fps}:"
        f"s={big_w}x{big_h},crop={width}:{height}"
    ) if image else f"scale={width}:{height},format=yuv420p"
    args = []
    if image:
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur}", "-i", image]
    else:
        # Black frame via color source avoids a missing-image failure.
        args += ["-f", "lavfi", "-t", f"{dur}",
                 "-i", f"color=c=black:s={width}x{height}:r={fps}"]
    args += ["-i", wav, "-vf", filt, "-frames:v", str(frames),
             "-af", "apad", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-r", str(fps), "-c:a", "aac", "-t", f"{seg_dur:.6f}", out_path]
    run_ffmpeg(args)


def build_chapters(manifest, min_seconds=10.0):
    """Section boundaries as YouTube chapters; empty when they cannot be valid.

    YouTube needs at least three chapters, the first at 00:00, and each at
    least 10 seconds long. Returning [] is honest; a broken chapter list is not.
    """
    scenes = (manifest or {}).get("scenes") or []
    if not scenes:
        return []
    starts, cursor, current = [], 0.0, None
    for scene in scenes:
        section = scene.get("section") or ""
        if section != current:
            starts.append((section or "Opening", cursor))
            current = section
        cursor += float(scene.get("duration") or 0)
    starts[0] = (starts[0][0], 0.0)
    valid = []
    for index, (name, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else cursor
        if index == 0 or end - start >= min_seconds:
            valid.append((name, start))
    if len(valid) < 3:
        return []
    return valid


def build_description(script_meta, sources, title, manifest=None, cfg=None):
    lines = [title or "", "", "Sources:"]
    for s in sources:
        lines.append(f"- {s.get('id')}: {s.get('title', '')} - {s.get('url', '')}")
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
    chapters = build_chapters(manifest)
    if chapters:
        lines += ["", "Chapters:"]
        for name, start in chapters:
            lines.append(f"{_fmt_chapter_time(start)} {name}")
    if ((cfg or {}).get("youtube") or {}).get("contains_synthetic_media") is True:
        lines += ["", "This video contains realistic AI-generated scenes."]
    lines += ["", "Corrections welcome in the comments."]
    return "\n".join(lines)


def _measure_loudnorm(path, target, ceiling, lra):
    """First loudnorm pass: measure the input so the second pass can correct it.

    Single-pass loudnorm guesses from the first seconds and lands off target
    (-15.3 LUFS and a -0.7 dBTP peak against a -14/-1 spec on the first real
    render). Measuring first is the documented two-pass usage.
    """
    if shutil.which("ffmpeg") is None:
        return None
    args = ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
            f"loudnorm=I={target}:TP={ceiling}:LRA={lra}:print_format=json",
            "-f", "null", "-"]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=300,
                              **({"creationflags": subprocess.CREATE_NO_WINDOW}
                                 if sys.platform == "win32" else {}))
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stderr or b"").decode("utf-8", "replace")
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def _measured_values(measured):
    """Parsed loudnorm measurement, or None when it is silence (-inf).

    A silent input measures -inf, which ffmpeg rejects as a filter argument, so
    the measurement pass is used only when every value is a real number.
    """
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    try:
        values = {key: float((measured or {})[key]) for key in keys}
    except (KeyError, TypeError, ValueError):
        return None
    return values if all(math.isfinite(v) for v in values.values()) else None


def _normalize_loudness(final, cfg):
    audio_cfg = (cfg or {}).get("audio") or {}
    if not audio_cfg.get("normalize", True):
        return False
    target = float(audio_cfg.get("loudness_lufs", LOUDNESS_TARGET))
    qa_cfg = (cfg or {}).get("qa") or {}
    peak_max = float(qa_cfg.get("true_peak_max", -1.0))
    # Half a dB of headroom: a limiter at exactly the spec reads above it after
    # lossy encoding, and QA measures the encoded file, not the filtered stream.
    ceiling = round(peak_max - 0.5, 1)
    lra = 11.0
    measured = _measured_values(_measure_loudnorm(final, target, ceiling, lra))
    tmp = final + ".norm.mp4"
    af = f"loudnorm=I={target}:TP={ceiling}:LRA={lra}"
    if measured:
        af += (f":measured_I={measured['input_i']:.2f}"
               f":measured_TP={measured['input_tp']:.2f}"
               f":measured_LRA={measured['input_lra']:.2f}"
               f":measured_thresh={measured['input_thresh']:.2f}"
               f":offset={measured['target_offset']:.2f}:linear=true")
    run_ffmpeg(["-i", final, "-c:v", "copy", "-af", af,
                "-c:a", "aac", "-b:a", "192k", tmp])
    os.replace(tmp, final)
    _trim_to_target(final, target, peak_max)
    return True


def _measure_pair(path, target, peak_max):
    data = _measure_loudnorm(path, target, peak_max, 11.0)
    try:
        integrated = float((data or {}).get("input_i"))
        peak = float((data or {}).get("input_tp"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(integrated) and math.isfinite(peak)):
        return None
    return integrated, peak


def _trim_to_target(final, target, peak_max, tolerance=0.2, passes=2, max_gain=3.0):
    """Correct loudnorm's dynamic-mode undershoot, then honour the peak limit.

    With `linear=true` loudnorm cannot apply its full gain without breaking the
    true-peak ceiling, so it limits instead and lands about a dB low (-15.0
    against -14 on the first real render). A measured gain closes that gap.

    The limiter ceiling sits a full dB under the spec: AAC adds intersample
    overshoot on the way out, so a -1.5 dBFS sample ceiling still measured
    -0.7 dBTP and a -2.0 dBFS ceiling measured -1.4. Each pass re-measures the
    encoded file, which is what QA reads.
    """
    ceiling = round(peak_max - 1.0, 1)
    peak_ok = peak_max - 0.2
    for _ in range(passes):
        measured = _measure_pair(final, target, peak_max)
        if measured is None:
            return False
        integrated, peak = measured
        if abs(target - integrated) <= tolerance and peak <= peak_ok:
            return False
        gain = max(-max_gain, min(max_gain, target - integrated))
        if abs(gain) < 0.05:
            return False
        limit = 10 ** (ceiling / 20)
        tmp = final + ".gain.mp4"
        # level=disabled matters: alimiter auto-levels its output by default,
        # which added its own makeup gain on top of the correction (+2 dB).
        af = (f"volume={gain:.2f}dB,"
              f"alimiter=limit={limit:.4f}:level=disabled:attack=1:release=50")
        run_ffmpeg(["-i", final, "-c:v", "copy", "-af", af,
                    "-c:a", "aac", "-b:a", "192k", tmp])
        os.replace(tmp, final)
    return True


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
    scene_images = _scene_assets(runs_dir, video_id, vdir)
    seg_dir = os.path.join(vdir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    segments = []
    for i, entry in enumerate(durations, 1):
        wav = os.path.join(vdir, entry.get("file", ""))
        if not os.path.isfile(wav):
            raise PipelineError(f"audio chunk missing: {wav}")
        image = scene_images.get(entry.get("scene_id"))
        if image is None and images:
            image = images[(i - 1) % len(images)]
        out = os.path.join(seg_dir, f"seg_{i:03d}.mp4")
        _segment_with_image(image, wav, entry.get("duration") or 0.5, fps, out,
                            width=width, height=height)
        segments.append(out)
    concat_path = os.path.join(vdir, "video.mp4")
    list_path = os.path.join(vdir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(f"file '{seg}'\n")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_path,
                "-c", "copy", concat_path])
    write_captions(vdir, durations)
    final = os.path.join(vdir, "final.mp4")
    subtitle_ok = True
    try:
        # Run with cwd=captions dir so ffmpeg subtitles filter resolves the sidecar.
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", concat_path,
             "-vf", "subtitles=captions.srt",
             "-r", str(fps), "-fps_mode", "cfr", "-c:a", "copy", final],
            capture_output=True, timeout=600, check=True, cwd=vdir,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        subtitle_ok = False
        print(f"warning: subtitle burn failed, keeping sidecar only: {exc}")
        # Fallback: copy video.mp4 as final (sidecar captions stay in captions.srt).
        run_ffmpeg(["-i", concat_path, "-c", "copy", final])
    normalized = _normalize_loudness(final, cfg)
    # Validate with ffprobe after the audio pass.
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
            "loudness_normalized": normalized, "duration": duration}
