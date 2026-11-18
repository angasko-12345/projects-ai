"""Automated QA for a rendered video: provable checks, honest verdicts.

The research rubric has 11 human-scored categories. Most of them cannot be
computed from a file, so this module never pretends to score them. It runs the
checks a machine can prove, applies the research's auto-rejection rules where
they are provable, and leaves a null human scorecard for the release review.
"""
import json
import math
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

from .media import get_streams, run_ffprobe
from .state import PipelineError, load, set_stage
from .visuals import validate_asset

HUMAN_CATEGORIES = (
    "historical_accuracy",
    "script_quality",
    "opening_hook",
    "visual_quality",
    "historical_authenticity",
    "scene_continuity",
    "animation",
    "narration",
    "audio",
    "editing",
    "overall_cohesion",
)
PLACEHOLDER_TOKENS = ("lorem ipsum", "todo", "tbd", "placeholder", "sample text", "untitled")
PROFILES = ("structural", "release")


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _check(checks, check_id, status, detail, value=None):
    checks.append({"id": check_id, "status": status, "detail": detail, "value": value})


def _fraction(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fps(stream):
    raw = (stream or {}).get("avg_frame_rate") or (stream or {}).get("r_frame_rate") or "0/1"
    try:
        num, den = raw.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _ffmpeg_stderr(args, timeout=300):
    if shutil.which("ffmpeg") is None:
        return None
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args],
                              capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return (proc.stderr or b"").decode("utf-8", "replace")


def measure_loudness(path):
    """Return {'integrated': LUFS, 'true_peak': dBTP} or None when unmeasurable."""
    text = _ffmpeg_stderr(["-i", path, "-af", "loudnorm=print_format=json",
                           "-f", "null", "-"])
    if not text:
        return None
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return {"integrated": _fraction(data.get("input_i")),
                "true_peak": _fraction(data.get("input_tp"))}
    except ValueError:
        return None


def _max_event_duration(text, key):
    if not text:
        return None
    values = [float(m.group(1)) for m in re.finditer(rf"{key}:\s*([\d.]+)", text)]
    return max(values) if values else 0.0


def max_silence_seconds(path, min_seconds=2.0):
    text = _ffmpeg_stderr(["-i", path, "-af",
                           f"silencedetect=noise=-50dB:d={min_seconds}",
                           "-f", "null", "-"])
    return _max_event_duration(text, "silence_duration")


def max_black_seconds(path, min_seconds=2.0):
    text = _ffmpeg_stderr(["-i", path, "-vf",
                           f"blackdetect=d={min_seconds}:pix_th=0.10",
                           "-an", "-f", "null", "-"])
    return _max_event_duration(text, "black_duration")


def _srt_cues(path):
    if not os.path.isfile(path):
        return []
    cues = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)", line.strip())
            if m:
                start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
                end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
                cues.append((start, end))
    return cues


def _placeholder_hits(vdir):
    hits = []
    for name in ("script.md", "captions.srt", "assets.json", "scene_manifest.json"):
        path = os.path.join(vdir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read().lower()
        except OSError:
            continue
        for token in PLACEHOLDER_TOKENS:
            if token in text:
                hits.append(f"{name}:{token}")
    return hits


def qa_stage(runs_dir, video_id, cfg):
    state = load(runs_dir, video_id)
    vdir = os.path.join(runs_dir, video_id)
    final = os.path.join(vdir, "final.mp4")
    if not os.path.isfile(final):
        raise PipelineError(f"final.mp4 missing for {video_id}; run assemble first")
    qa_cfg = (cfg or {}).get("qa") or {}
    profile = (qa_cfg.get("profile") or "release").lower()
    if profile not in PROFILES:
        raise PipelineError(f"unknown qa profile {profile!r} (allowed: {', '.join(PROFILES)})")
    release = profile == "release"
    checks = []
    videos_cfg = (cfg or {}).get("videos") or {}
    width = int(videos_cfg.get("width", 1920))
    height = int(videos_cfg.get("height", 1080))
    fps_target = float(videos_cfg.get("fps", 30))

    # 1. Required files.
    thumb = os.path.join(vdir, "thumbnail.png")
    captions = os.path.join(vdir, "captions.srt")
    missing = [os.path.basename(p) for p in (final, thumb, captions) if not os.path.isfile(p)]
    _check(checks, "files", "fail" if missing else "pass",
           f"missing: {', '.join(missing)}" if missing else "final, thumbnail and captions present")

    # 2. Streams.
    streams = get_streams(final)
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video_stream or not audio_stream:
        _check(checks, "streams", "fail", "final.mp4 needs both a video and an audio stream")
    else:
        _check(checks, "streams", "pass", "video and audio streams present")

    # 3. Export specs.
    if video_stream:
        got = (int(video_stream.get("width") or 0), int(video_stream.get("height") or 0))
        codec = video_stream.get("codec_name")
        fps_got = _fps(video_stream)
        problems = []
        if got != (width, height):
            problems.append(f"{got[0]}x{got[1]}, expected {width}x{height}")
        if codec != "h264":
            problems.append(f"codec {codec}, expected h264")
        if abs(fps_got - fps_target) > 0.01:
            problems.append(f"fps {fps_got:.3f}, expected {fps_target}")
        _check(checks, "export_specs", "fail" if problems else "pass",
               "; ".join(problems) or f"{width}x{height} h264 {fps_target:g}fps",
               value={"width": got[0], "height": got[1], "fps": fps_got, "codec": codec})

    info = run_ffprobe(final)
    duration = _fraction((info.get("format") or {}).get("duration")) or 0.0
    _check(checks, "duration", "fail" if duration <= 0 else "pass",
           f"final.mp4 duration {duration:.3f}s", value=duration)

    # 4. Render length matches the narration track.
    durations_path = os.path.join(vdir, "durations.json")
    narration = None
    if os.path.isfile(durations_path):
        narration = sum(float(d.get("duration") or 0) for d in _load_json(durations_path))
    if narration is None:
        _check(checks, "duration_matches_narration", "warn", "durations.json missing")
    else:
        tolerance = max(1.0, narration * 0.03)
        delta = abs(duration - narration)
        _check(checks, "duration_matches_narration", "fail" if delta > tolerance else "pass",
               f"render {duration:.2f}s vs narration {narration:.2f}s (delta {delta:.2f}s)",
               value={"render": duration, "narration": narration})

    # 5. Target length, informational in structural and enforcing in release.
    target_minutes = float((cfg or {}).get("target_length_minutes") or 0)
    if target_minutes <= 0:
        _check(checks, "target_length", "warn", "target_length_minutes is not set")
    else:
        target_seconds = target_minutes * 60
        ratio = duration / target_seconds
        status = "pass" if 0.8 <= ratio <= 1.2 else ("fail" if release else "warn")
        _check(checks, "target_length", status,
               f"{duration / 60:.2f} min vs {target_minutes:g} min target (ratio {ratio:.2f})",
               value=ratio)

    # 6. Captions.
    cues = _srt_cues(captions)
    if not cues:
        _check(checks, "captions", "fail", "captions.srt missing or has no cues")
    else:
        last_end = max(end for _, end in cues)
        first_start = min(start for start, _ in cues)
        problems = []
        if first_start > 0.5:
            problems.append(f"first cue starts at {first_start:.2f}s")
        if duration and last_end > duration + 2.0:
            problems.append(f"last cue ends at {last_end:.2f}s beyond {duration:.2f}s")
        _check(checks, "captions", "fail" if problems else "pass",
               "; ".join(problems) or f"{len(cues)} cues, last ends {last_end:.2f}s")

    # 7. Asset licenses.
    assets_path = os.path.join(vdir, "assets.json")
    try:
        assets = _load_json(assets_path) if os.path.isfile(assets_path) else []
        if not assets:
            raise PipelineError("assets.json missing or empty")
        for asset in assets:
            validate_asset(asset)
        _check(checks, "assets", "pass", f"{len(assets)} licensed asset(s)")
    except (PipelineError, ValueError) as exc:
        _check(checks, "assets", "fail", str(exc))

    # 8. Placeholder text.
    hits = _placeholder_hits(vdir)
    _check(checks, "placeholder_text", "fail" if hits else "pass",
           f"found: {', '.join(hits)}" if hits else "no placeholder tokens")

    # 9. Claim grounding (release rejects numbers with no support in the sources).
    meta_path = os.path.join(vdir, "script_meta.json")
    meta = _load_json(meta_path) if os.path.isfile(meta_path) else {}
    claims = meta.get("claims") or []
    missing_numbers = sorted({n for c in claims
                              for n in (c.get("support") or {}).get("missing_numbers", [])})
    if release and missing_numbers:
        _check(checks, "claim_numbers", "fail",
               "numbers absent from cited source text: " + ", ".join(missing_numbers))
    elif missing_numbers:
        _check(checks, "claim_numbers", "warn",
               "numbers absent from cited source text: " + ", ".join(missing_numbers))
    else:
        _check(checks, "claim_numbers", "pass", "no unsupported numbers detected")
    weak = [c.get("id") for c in claims
            if (c.get("support") or {}).get("status") in ("weak", "unverified")]
    unchecked = [c.get("id") for c in claims
                 if (c.get("support") or {}).get("status") == "unchecked"]
    if weak:
        _check(checks, "claim_grounding", "warn",
               f"{len(weak)} claim(s) with weak or unverified source overlap: {', '.join(weak[:10])}")
    else:
        _check(checks, "claim_grounding", "pass",
               "all checked claims meet the lexical overlap threshold")

    # 10. Release-only audio and frame checks.
    if release:
        loudness = measure_loudness(final)
        if loudness is None:
            _check(checks, "loudness", "not_available", "could not measure loudness")
        else:
            target_lufs = float(qa_cfg.get("loudness_lufs", -14.0))
            tolerance = float(qa_cfg.get("loudness_tolerance", 1.0))
            peak_max = float(qa_cfg.get("true_peak_max", -1.0))
            integrated = loudness["integrated"]
            peak = loudness["true_peak"]
            problems = []
            if integrated is None or not math.isfinite(integrated):
                problems.append("integrated loudness is silence/undefined")
            elif abs(integrated - target_lufs) > tolerance:
                problems.append(f"integrated {integrated:.1f} LUFS, target {target_lufs:g}")
            if peak is not None and math.isfinite(peak) and peak > peak_max:
                problems.append(f"true peak {peak:.1f} dBTP above {peak_max:g}")
            _check(checks, "loudness", "fail" if problems else "pass",
                   "; ".join(problems) or f"{integrated:.1f} LUFS, peak {peak:.1f} dBTP",
                   value=loudness)
        silence = max_silence_seconds(final)
        if silence is None:
            _check(checks, "long_silence", "not_available", "could not measure silence")
        else:
            _check(checks, "long_silence", "fail" if silence > 2.0 else "pass",
                   f"longest detected silence {silence:.2f}s", value=silence)
        black = max_black_seconds(final)
        if black is None:
            _check(checks, "black_frames", "not_available", "could not measure black frames")
        else:
            _check(checks, "black_frames", "fail" if black > 2.0 else "pass",
                   f"longest black run {black:.2f}s", value=black)
    else:
        _check(checks, "loudness", "not_available", "structural profile: release checks skipped")
        _check(checks, "long_silence", "not_available", "structural profile: release checks skipped")
        _check(checks, "black_frames", "not_available", "structural profile: release checks skipped")

    failed = [c["id"] for c in checks if c["status"] == "fail"]
    warned = [c["id"] for c in checks if c["status"] == "warn"]
    verdict = "reject" if failed else ("revise" if warned else "pass")
    notes = []
    if (cfg or {}).get("youtube", {}).get("contains_synthetic_media") is None:
        notes.append("synthetic/AI disclosure is unset: set the YouTube Studio toggle manually")
    report = {
        "video_id": video_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "verdict": verdict,
        "checks": checks,
        "auto_rejections": failed,
        "warnings": warned,
        "unchecked_claims": unchecked,
        "human_scorecard": {category: None for category in HUMAN_CATEGORIES},
        "human_scorecard_note": (
            "Automated checks only. Score these 11 categories by hand against "
            "workflows/quality-assurance-checklist.md before release."),
        "notes": notes,
    }
    with open(os.path.join(vdir, "qa_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    state["qa_verdict"] = verdict
    set_stage(runs_dir, video_id, "qa")
    return report


def load_report(runs_dir, video_id):
    path = os.path.join(runs_dir, video_id, "qa_report.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def format_report(report):
    lines = [f"QA {report.get('verdict', '?').upper()} (profile: {report.get('profile')})"]
    for check in report.get("checks") or []:
        lines.append(f"  {check['status'].upper():<13} {check['id']}: {check['detail']}")
    if report.get("auto_rejections"):
        lines.append("auto-rejections: " + ", ".join(report["auto_rejections"]))
    for note in report.get("notes") or []:
        lines.append(f"NOTE: {note}")
    return "\n".join(lines)
