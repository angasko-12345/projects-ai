"""
``clipcheck``: read-only QC of a finished vertical clip against a campaign brief.

Works on any MP4 (this product's renders, CapCut exports, anything FFmpeg can
read). It never modifies, re-encodes or uploads anything: every measurement is
an FFmpeg/ffprobe analysis pass whose output goes to the null muxer or a pipe.

Each check returns a :class:`CheckResult` with ``status`` one of:

* ``pass`` - measured and within the rule;
* ``warn`` - measured and borderline, or a retention heuristic was missed, or
  the check could not be measured (e.g. numpy absent for the logo match);
* ``fail`` - measured and outside a rule the campaign enforces;
* ``skip`` - the rule is not configured (or no post text was given). A skip
  never changes the exit code.

Exit codes (``exit_code_for``): 0 all pass/skip, 2 any warn, 1 any fail.

What is measured and how:

* container/streams: :func:`renderer.validate_final_output` (reused, including
  the full-decode integrity check), plus ffprobe stream geometry;
* opening: ``blackdetect`` + ``freezedetect`` over the whole clip, inspected
  over the first 1.0 s (payoff on frame 0);
* static: longest ``freezedetect`` run before the CTA (the end card is
  expected to be static, so it is excluded);
* loudness: ``ebur128=peak=true`` integrated LUFS and true peak;
* CTA logo: frames sampled at 4 fps over the clip's tail, grayscale normalized
  cross-correlation against the campaign logo at several scales (numpy,
  optional). Only matches at least ``min_width_fraction`` of the frame width
  count, so a small corner watermark is not mistaken for the call-to-action.
  Resolution is one sample (0.25 s); a result within one sample below the
  minimum is a ``warn``, not a ``fail``.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import renderer
from .campaign import Campaign
from .config import get_ffmpeg_path

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
_RANK = {SKIP: 0, PASS: 0, WARN: 1, FAIL: 2}

REPORT_SCHEMA = 1
OPENING_SECONDS = 1.0
ASPECT_TOLERANCE = 0.01          # 1% off 9:16 still counts as 9:16
BLACK_PIX_TH = 0.10              # blackdetect pixel threshold
BLACK_MIN_SECONDS = 0.1
FREEZE_NOISE_DB = -50            # freezedetect noise tolerance (as the builders used)
FREEZE_MIN_SECONDS = 0.5         # shortest frozen run freezedetect reports
SILENCE_LUFS = -69.0             # ebur128 reports -70.0 LUFS for digital silence
LOUDNESS_FAIL_MARGIN = 1.0       # LU outside the target range before warn becomes fail
LOGO_SAMPLE_FPS = 4
LOGO_TAIL_SECONDS = 5.0
LOGO_ANALYSIS_WIDTH = 270
LOGO_SCALE_STEP = 1.06
FFMPEG_TIMEOUT = 900


class QCError(Exception):
    """The check could not run at all (FFmpeg missing, timeout)."""


@dataclass
class CheckResult:
    id: str
    status: str
    measured: str = ""
    expected: str = ""
    hint: str = ""


@dataclass
class Report:
    video: str
    campaign: str
    post: str | None
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        worst = max((_RANK[c.status] for c in self.checks), default=0)
        return {0: PASS, 1: WARN, 2: FAIL}[worst]

    @property
    def exit_code(self) -> int:
        return exit_code_for(self.checks)

    def to_dict(self) -> dict:
        return {
            "tool": "clipcheck",
            "schema": REPORT_SCHEMA,
            "video": self.video,
            "campaign": self.campaign,
            "post": self.post,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "checks": [asdict(c) for c in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


def exit_code_for(checks: list[CheckResult]) -> int:
    statuses = {c.status for c in checks}
    if FAIL in statuses:
        return 1
    if WARN in statuses:
        return 2
    return 0


# ---------------------------------------------------------------------------
# FFmpeg plumbing
# ---------------------------------------------------------------------------

def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run_ffmpeg(args: list[str], *, binary: bool = False) -> subprocess.CompletedProcess:
    cmd = [get_ffmpeg_path(), "-hide_banner", "-nostats", *args]
    try:
        if binary:
            return subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT,
                                  creationflags=_creationflags())
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=FFMPEG_TIMEOUT,
                              creationflags=_creationflags())
    except FileNotFoundError:
        raise QCError(f"FFmpeg not found at {cmd[0]!r}. Install FFmpeg or set FFMPEG_PATH.") \
            from None
    except subprocess.TimeoutExpired:
        raise QCError(f"FFmpeg timed out after {FFMPEG_TIMEOUT}s") from None


def _thread_args(threads: int | None) -> list[str]:
    return ["-threads", str(threads)] if threads else []


def _filter_thread_args(threads: int | None) -> list[str]:
    return ["-filter_threads", str(threads)] if threads else []


def probe_video_geometry(path: Path) -> dict | None:
    """Width/height/has_audio of the first video stream, rotation-aware."""
    streams = renderer.probe_streams(path)
    if not isinstance(streams, list):
        return None
    video = next((s for s in streams if isinstance(s, dict)
                  and s.get("codec_type") == "video"), None)
    if not video:
        return None
    try:
        w, h = int(video["width"]), int(video["height"])
    except (KeyError, TypeError, ValueError):
        return None
    rotation = 0
    for sd in video.get("side_data_list") or []:
        if isinstance(sd, dict) and "rotation" in sd:
            try:
                rotation = int(float(sd["rotation"]))
            except (TypeError, ValueError):
                pass
    if abs(rotation) % 180 == 90:
        w, h = h, w
    has_audio = any(isinstance(s, dict) and s.get("codec_type") == "audio" for s in streams)
    return {"width": w, "height": h, "has_audio": has_audio}


_KV_RE = re.compile(r"lavfi\.(\w+?)_(start|end|duration)\s*:\s*(-?[\d.]+)")
_BLACK_RE = re.compile(r"black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)")


def parse_detect_log(stderr: str, duration: float) -> tuple[list, list]:
    """Return (black_intervals, freeze_intervals) as lists of (start, end)."""
    black = [(float(a), float(b)) for a, b in _BLACK_RE.findall(stderr)]
    freezes: list[tuple[float, float]] = []
    open_start: float | None = None
    for line in stderr.splitlines():
        if "freezedetect" not in line:
            continue
        m = re.search(r"freeze_start:\s*(-?[\d.]+)", line)
        if m:
            open_start = float(m.group(1))
            continue
        m = re.search(r"freeze_end:\s*(-?[\d.]+)", line)
        if m and open_start is not None:
            freezes.append((open_start, float(m.group(1))))
            open_start = None
    if open_start is not None:  # freeze ran to end of file without an end marker
        freezes.append((open_start, duration))
    return black, freezes


def detect_black_and_freeze(path: Path, duration: float, threads: int | None = None):
    args = [*_thread_args(threads), "-i", str(path), *_filter_thread_args(threads), "-an",
            "-vf",
            f"blackdetect=d={BLACK_MIN_SECONDS}:pix_th={BLACK_PIX_TH},"
            f"freezedetect=n={FREEZE_NOISE_DB}dB:d={FREEZE_MIN_SECONDS}",
            "-f", "null", "-"]
    proc = _run_ffmpeg(args)
    if proc.returncode != 0:
        return None
    return parse_detect_log(proc.stderr or "", duration)


def parse_ebur128_summary(stderr: str) -> tuple[float | None, float | None]:
    """Integrated loudness (LUFS) and true peak (dBFS) from ebur128's summary."""
    text = stderr or ""
    idx = text.rfind("Summary:")
    if idx < 0:
        return None, None
    summary = text[idx:]
    m = re.search(r"\bI:\s*(-?[\d.]+|-inf)\s*LUFS", summary)
    integrated = None
    if m:
        integrated = float("-inf") if m.group(1) == "-inf" else float(m.group(1))
    peak = None
    tp = re.search(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)", summary)
    if tp:
        peak = float("-inf") if tp.group(1) == "-inf" else float(tp.group(1))
    return integrated, peak


def measure_loudness(path: Path, threads: int | None = None):
    args = [*_thread_args(threads), "-i", str(path), "-vn",
            "-af", "ebur128=peak=true", "-f", "null", "-"]
    proc = _run_ffmpeg(args)
    if proc.returncode != 0:
        return None, None
    return parse_ebur128_summary(proc.stderr or "")


# ---------------------------------------------------------------------------
# Logo template matching (numpy optional)
# ---------------------------------------------------------------------------

def _numpy():
    try:
        import numpy  # noqa: F401
    except ImportError:
        return None
    return numpy


def _gray_frames(path: Path, start: float, width: int, height: int, fps: int,
                 threads: int | None):
    np = _numpy()
    args = [*_thread_args(threads), "-ss", f"{max(0.0, start):.3f}", "-i", str(path),
            *_filter_thread_args(threads), "-an",
            "-vf", f"fps={fps},scale={width}:{height}:flags=area,format=gray",
            "-f", "rawvideo", "-"]
    proc = _run_ffmpeg(args, binary=True)
    if proc.returncode != 0:
        return None
    data = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = data.size // (width * height)
    return data[: n * width * height].reshape(n, height, width).astype(np.float64)


def _logo_templates(logo: Path, widths: list[int], max_height: int) -> list | None:
    """The logo in grayscale at each width (aspect kept), from one FFmpeg call.

    Returns ``[(width, array)]`` for every scale whose height fits ``max_height``,
    or None when the logo cannot be read.
    """
    np = _numpy()
    streams = renderer.probe_streams(logo)
    video = next((st for st in streams or [] if isinstance(st, dict)
                  and st.get("codec_type") == "video"), None)
    try:
        lw, lh = int(video["width"]), int(video["height"])
    except (TypeError, KeyError, ValueError):
        return None
    sizes = [(w, max(1, int(round(w * lh / lw)))) for w in widths]
    sizes = [(w, h) for w, h in sizes if h <= max_height]
    if not sizes:
        return []
    pad_w = max(w for w, _ in sizes)
    parts = [f"[0:v]format=gray,split={len(sizes)}" + "".join(f"[s{i}]" for i in range(len(sizes)))
             if len(sizes) > 1 else "[0:v]format=gray[s0]"]
    for i, (w, h) in enumerate(sizes):
        parts.append(f"[s{i}]scale={w}:{h}:flags=area,pad={pad_w}:{h}[p{i}]")
    if len(sizes) > 1:
        parts.append("".join(f"[p{i}]" for i in range(len(sizes)))
                     + f"vstack=inputs={len(sizes)}[out]")
    else:
        parts.append("[p0]null[out]")
    proc = _run_ffmpeg(["-i", str(logo), "-frames:v", "1", "-filter_complex", ";".join(parts),
                        "-map", "[out]", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       binary=True)
    total_h = sum(h for _, h in sizes)
    if proc.returncode != 0 or len(proc.stdout) != pad_w * total_h:
        return None
    sheet = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(total_h, pad_w)
    out, y = [], 0
    for w, h in sizes:
        out.append((w, sheet[y:y + h, :w].astype(np.float64)))
        y += h
    return out


def _fast_len(n: int) -> int:
    """Smallest 2^a*3^b*5^c >= n, so the FFTs stay fast."""
    best = 1 << max(0, (n - 1).bit_length())
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            m = p35
            while m < n:
                m *= 2
            best = min(best, m)
            p35 *= 3
        p5 *= 5
    return best


class _Matcher:
    """Zero-mean normalized cross-correlation of fixed templates against frames.

    The frame spectrum, integral images and template spectra are each computed
    once, so cost per (frame, scale) is one product and one inverse FFT.
    Flat windows (no texture) score 0 rather than dividing by zero.
    """

    def __init__(self, frame_shape, templates):
        np = _numpy()
        fh, fw = frame_shape
        self.fh, self.fw = fh, fw
        usable = [t for t in templates if t.shape[0] <= fh and t.shape[1] <= fw]
        maxh = max((t.shape[0] for t in usable), default=1)
        maxw = max((t.shape[1] for t in usable), default=1)
        self.shape = (_fast_len(fh + maxh - 1), _fast_len(fw + maxw - 1))
        self.entries = []
        for t in usable:
            z = t - t.mean()
            energy = float((z * z).sum())
            if energy <= 1e-9:
                continue
            spec = np.fft.rfft2(z[::-1, ::-1], self.shape)
            self.entries.append((t.shape, spec, energy))

    def scores(self, frame) -> list[float]:
        np = _numpy()
        if not self.entries:
            return []
        spec = np.fft.rfft2(frame, self.shape)
        ii = np.pad(frame, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        ii2 = np.pad(frame * frame, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        out = []
        for (th, tw), tspec, energy in self.entries:
            full = np.fft.irfft2(spec * tspec, self.shape)
            num = full[th - 1:self.fh, tw - 1:self.fw]

            def window_sum(a, th=th, tw=tw):
                return a[th:, tw:] - a[:-th, tw:] - a[th:, :-tw] + a[:-th, :-tw]

            n = th * tw
            s1 = window_sum(ii)
            var = np.maximum(window_sum(ii2) - s1 * s1 / n, 0.0)
            denom = np.sqrt(var * energy)
            with np.errstate(divide="ignore", invalid="ignore"):
                score = np.where(denom > 1e-6 * n, num / denom, 0.0)
            out.append(float(score.max()) if score.size else 0.0)
        return out


def ncc_max(frame, template) -> float:
    """Peak zero-mean normalized cross-correlation of ``template`` inside ``frame``."""
    scores = _Matcher(frame.shape, [template]).scores(frame)
    return scores[0] if scores else 0.0


def measure_logo_run(path: Path, duration: float, geometry: dict, logo: Path,
                     min_width_fraction: float, threshold: float,
                     window: float, threads: int | None = None) -> dict | None:
    """Longest run of tail samples in which the logo is matched, or None if not measurable."""
    np = _numpy()
    if np is None:
        return None
    aw = min(LOGO_ANALYSIS_WIDTH, geometry["width"])
    aw -= aw % 2
    ah = int(round(aw * geometry["height"] / geometry["width"] / 2.0)) * 2
    start = max(0.0, duration - window)
    frames = _gray_frames(path, start, aw, ah, LOGO_SAMPLE_FPS, threads)
    if frames is None or len(frames) == 0:
        return None
    widths = []
    w = max(8, int(math.ceil(min_width_fraction * aw)))
    while w <= aw:
        widths.append(w)
        w = max(w + 1, int(round(w * LOGO_SCALE_STEP)))
    templates = _logo_templates(logo, widths, ah)
    if templates is None:
        return None
    if not templates:
        return {"run_seconds": 0.0, "run_start": None, "best_score": 0.0,
                "best_width_fraction": None, "samples": len(frames)}

    matcher = _Matcher((ah, aw), [t for _, t in templates])
    by_shape = {t.shape: w for w, t in templates}
    hits = []
    best_score, best_w = 0.0, None
    for frame in frames:
        frame_best, frame_w = 0.0, None
        for (shape, _, _), s in zip(matcher.entries, matcher.scores(frame), strict=True):
            if s > frame_best:
                frame_best, frame_w = s, by_shape[shape]
        hits.append(frame_best >= threshold)
        if frame_best > best_score:
            best_score, best_w = frame_best, frame_w

    longest = cur = 0
    longest_end = -1
    for i, hit in enumerate(hits):
        cur = cur + 1 if hit else 0
        if cur > longest:
            longest, longest_end = cur, i
    run_start = None
    if longest:
        run_start = start + (longest_end - longest + 1) / LOGO_SAMPLE_FPS
    return {
        "run_seconds": longest / LOGO_SAMPLE_FPS,
        "run_start": run_start,
        "best_score": round(best_score, 3),
        "best_width_fraction": round(best_w / aw, 3) if best_w else None,
        "samples": len(frames),
    }


# ---------------------------------------------------------------------------
# Post text
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"#\w+", re.UNICODE)


def check_post(text: str | None, campaign: Campaign) -> list[CheckResult]:
    results: list[CheckResult] = []
    tags_rule = bool(campaign.required_hashtags)
    if text is None:
        reason = "no --post file given"
        if tags_rule:
            results.append(CheckResult("post_hashtags", SKIP, hint=reason))
        if campaign.required_mentions:
            results.append(CheckResult("post_mentions", SKIP, hint=reason))
        if campaign.disclosure_line:
            results.append(CheckResult("post_disclosure", SKIP, hint=reason))
        return results

    body = text.strip()
    if tags_rule:
        required = [t.lower() for t in campaign.required_hashtags]
        expected = " ".join(campaign.required_hashtags)
        if campaign.hashtags_must_be_last:
            expected += " (last, nothing after)"
        found = _TAG_RE.findall(body)
        found_l = [t.lower() for t in found]
        missing = [t for t, tl in zip(campaign.required_hashtags, required, strict=True)
                   if tl not in found_l]
        measured = " ".join(found) or "(no hashtags)"
        # Required tags must appear as one consecutive block in the given order.
        k = len(required)
        block_at = next((i for i in range(len(found_l) - k + 1)
                         if found_l[i:i + k] == required), None)
        if missing:
            results.append(CheckResult("post_hashtags", FAIL, measured, expected,
                                       f"missing: {' '.join(missing)}"))
        elif block_at is None:
            results.append(CheckResult("post_hashtags", FAIL, measured, expected,
                                       "required hashtags are present but not in the required "
                                       "order (or something sits between them)"))
        elif campaign.hashtags_must_be_last:
            tail = re.sub(r"\s+", " ", body).rstrip()
            pattern = r"\s+".join(re.escape(t) for t in campaign.required_hashtags) + r"$"
            if re.search(pattern, tail, flags=re.IGNORECASE):
                results.append(CheckResult("post_hashtags", PASS, measured, expected))
            else:
                results.append(CheckResult("post_hashtags", FAIL, measured, expected,
                                           "put the required hashtags at the very end of "
                                           "the post with nothing after them"))
        else:
            results.append(CheckResult("post_hashtags", PASS, measured, expected))

    if campaign.required_mentions:
        low = body.lower()
        missing = [m for m in campaign.required_mentions if m.lower() not in low]
        expected = ", ".join(campaign.required_mentions)
        if missing:
            results.append(CheckResult("post_mentions", FAIL,
                                       f"missing {', '.join(missing)}", expected,
                                       "mention it naturally in the description"))
        else:
            results.append(CheckResult("post_mentions", PASS, "all present", expected))

    if campaign.disclosure_line:
        ok = campaign.disclosure_line.lower() in body.lower()
        results.append(CheckResult("post_disclosure", PASS if ok else FAIL,
                                   "present" if ok else "absent",
                                   repr(campaign.disclosure_line),
                                   "" if ok else "add the disclosure line to the post"))
    return results


# ---------------------------------------------------------------------------
# The whole check
# ---------------------------------------------------------------------------

def _fmt(x: float, unit: str = "s", nd: int = 2) -> str:
    return f"{x:.{nd}f}{unit}" if x is not None and math.isfinite(x) else str(x)


def run_checks(video, campaign: Campaign, post_text: str | None = None,
               *, post_path=None, threads: int | None = 1) -> Report:
    video = Path(video)
    report = Report(video=str(video), campaign=str(campaign.source),
                    post=str(post_path) if post_path else None)
    add = report.checks.append

    # 1. container: reuse the renderer's final-output validation.
    duration = None
    try:
        duration = renderer.validate_final_output(video)
        add(CheckResult("container", PASS, f"readable, {duration:.2f}s",
                        "decodable video file"))
    except renderer.OutputValidationError as e:
        add(CheckResult("container", FAIL, "unreadable", "decodable video file", str(e)))

    geometry = probe_video_geometry(video) if duration else None
    media_ids = ["aspect", "duration", "opening", "static", "loudness", "true_peak", "cta_logo"]
    if duration is None or geometry is None:
        for cid in media_ids:
            add(CheckResult(cid, SKIP, hint="container check failed; nothing else measured"))
        report.checks.extend(check_post(post_text, campaign))
        return report

    # 2. aspect 9:16
    w, h = geometry["width"], geometry["height"]
    ratio = w / h if h else 0
    target = 9 / 16
    ok = h > w and abs(ratio - target) / target <= ASPECT_TOLERANCE
    add(CheckResult("aspect", PASS if ok else FAIL, f"{w}x{h}", "9:16 vertical",
                    "" if ok else "export as a vertical 9:16 video (e.g. 1080x1920)"))

    # 3. duration
    if campaign.length_range:
        lo, hi = campaign.length_range
        ok = lo <= duration <= hi
        add(CheckResult("duration", PASS if ok else FAIL, _fmt(duration),
                        f"{lo:g}-{hi:g}s", "" if ok else
                        ("too short" if duration < lo else "too long") + " for the campaign"))
    else:
        add(CheckResult("duration", SKIP, _fmt(duration), "", "no [video] length_range"))

    # CTA logo first: its start bounds the body for the static check.
    logo = None
    cta_note = ""
    if campaign.cta_logo_path and campaign.cta_min_seconds:
        window = max(LOGO_TAIL_SECONDS, campaign.cta_min_seconds + 2.0)
        logo = measure_logo_run(video, duration, geometry, campaign.cta_logo_path,
                                campaign.cta_logo_min_width_fraction,
                                campaign.cta_logo_match_threshold, window, threads)
        expected = (f">= {campaign.cta_min_seconds:g}s visible "
                    f"(logo >= {campaign.cta_logo_min_width_fraction:.0%} of width)")
        if logo is None:
            add(CheckResult("cta_logo", WARN, "not measured", expected,
                            "install numpy (pip install -r requirements-qc.txt) to measure "
                            "the logo, or the tail frames could not be read"))
        else:
            run = logo["run_seconds"]
            step = 1.0 / LOGO_SAMPLE_FPS
            measured = (f"~{run:.2f}s (best match {logo['best_score']:.2f}"
                        + (f" at {logo['best_width_fraction']:.0%} width"
                           if logo["best_width_fraction"] else "") + ")")
            if run >= campaign.cta_min_seconds:
                status, hint = PASS, ""
            elif run + step >= campaign.cta_min_seconds:
                status, hint = WARN, (f"within one {step:.2f}s sample of the minimum; "
                                      "hold the logo a little longer to be safe")
            else:
                status = FAIL
                hint = ("logo not found at CTA size in the last "
                        f"{window:g}s" if run == 0 else
                        "hold the logo end card longer")
            add(CheckResult("cta_logo", status, measured, expected, hint))
    else:
        add(CheckResult("cta_logo", SKIP, hint="no [cta] logo_path/min_seconds"))

    if logo and logo.get("run_start") is not None:
        body_end, cta_note = logo["run_start"], "before the detected CTA"
    elif campaign.cta_min_seconds:
        body_end = max(0.0, duration - campaign.cta_min_seconds)
        cta_note = f"excluding the last {campaign.cta_min_seconds:g}s (assumed CTA)"
    else:
        body_end, cta_note = duration, "whole clip"

    # 4/5. opening + static from one detection pass.
    detected = detect_black_and_freeze(video, duration, threads)
    if detected is None:
        add(CheckResult("opening", WARN, "not measured", "", "FFmpeg detection pass failed"))
        add(CheckResult("static", WARN, "not measured", "", "FFmpeg detection pass failed"))
    else:
        black, freezes = detected
        black_open = [b for b in black if b[0] <= 0.05]
        frozen_open = [f for f in freezes if f[0] <= 0.05 and f[1] >= OPENING_SECONDS - 0.05]
        expected = f"moving picture from frame 0 (first {OPENING_SECONDS:g}s)"
        if black_open:
            b = black_open[0]
            add(CheckResult("opening", FAIL, f"black {b[0]:.2f}-{b[1]:.2f}s", expected,
                            "open on the payoff, not a black frame"))
        elif frozen_open:
            f = frozen_open[0]
            add(CheckResult("opening", WARN, f"static {f[0]:.2f}-{min(f[1], duration):.2f}s",
                            expected, "the first second does not move; open on the payoff"))
        else:
            add(CheckResult("opening", PASS, "moving", expected))

        longest, at = 0.0, None
        for s, e in freezes:
            overlap = min(e, body_end) - max(s, 0.0)
            if overlap > longest:
                longest, at = overlap, s
        if campaign.max_static_seconds:
            exp = f"<= {campaign.max_static_seconds:g}s ({cta_note})"
            if longest <= campaign.max_static_seconds:
                meas = f"{longest:.2f}s" if longest else f"< {FREEZE_MIN_SECONDS:g}s"
                add(CheckResult("static", PASS, meas, exp))
            else:
                add(CheckResult("static", WARN, f"{longest:.2f}s from {at:.2f}s", exp,
                                "nothing should sit still this long; cut or add motion"))
        else:
            add(CheckResult("static", SKIP, f"{longest:.2f}s" if longest else "",
                            "", "no [video] max_static_seconds"))

    # 6/7. loudness and true peak.
    if campaign.loudness_target or campaign.max_true_peak is not None:
        if not geometry["has_audio"]:
            add(CheckResult("loudness", FAIL, "no audio stream", "audio track",
                            "the clip has no audio at all"))
            add(CheckResult("true_peak", SKIP, hint="no audio stream"))
        else:
            integrated, peak = measure_loudness(video, threads)
            if campaign.loudness_target:
                lo, hi = campaign.loudness_target
                exp = f"{lo:g} to {hi:g} LUFS"
                if integrated is None:
                    add(CheckResult("loudness", WARN, "not measured", exp,
                                    "ebur128 produced no summary"))
                elif integrated <= SILENCE_LUFS:
                    add(CheckResult("loudness", FAIL, _fmt(integrated, " LUFS", 1), exp,
                                    "the audio is silent"))
                elif lo <= integrated <= hi:
                    add(CheckResult("loudness", PASS, _fmt(integrated, " LUFS", 1), exp))
                else:
                    off = lo - integrated if integrated < lo else integrated - hi
                    status = WARN if off <= LOUDNESS_FAIL_MARGIN else FAIL
                    add(CheckResult("loudness", status, _fmt(integrated, " LUFS", 1), exp,
                                    ("too quiet" if integrated < lo else "too loud")
                                    + f" by {off:.1f} LU; normalise to the target"))
            else:
                add(CheckResult("loudness", SKIP, hint="no [audio] loudness_target"))
            if campaign.max_true_peak is not None:
                exp = f"<= {campaign.max_true_peak:g} dBTP"
                if peak is None:
                    add(CheckResult("true_peak", WARN, "not measured", exp))
                elif peak <= campaign.max_true_peak:
                    add(CheckResult("true_peak", PASS, _fmt(peak, " dBTP", 1), exp))
                else:
                    add(CheckResult("true_peak", WARN, _fmt(peak, " dBTP", 1), exp,
                                    "risk of clipping after the platform re-encodes; "
                                    "use a true-peak limiter"))
            else:
                add(CheckResult("true_peak", SKIP, hint="no [audio] max_true_peak"))
    else:
        add(CheckResult("loudness", SKIP, hint="no [audio] loudness_target"))
        add(CheckResult("true_peak", SKIP, hint="no [audio] max_true_peak"))

    # Keep a stable check order regardless of the measuring order above.
    order = {cid: i for i, cid in enumerate(["container", *media_ids])}
    report.checks.sort(key=lambda c: order.get(c.id, 99))
    report.checks.extend(check_post(post_text, campaign))
    return report


def format_table(report: Report) -> str:
    rows = [("STATUS", "CHECK", "MEASURED", "EXPECTED")]
    for c in report.checks:
        rows.append((c.status.upper(), c.id, c.measured or "-", c.expected or "-"))
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    lines = []
    for r in rows:
        lines.append("  ".join(r[i].ljust(widths[i]) for i in range(3)) + "  " + r[3])
    hints = [c for c in report.checks if c.hint and c.status in (WARN, FAIL)]
    if hints:
        lines.append("")
        for c in hints:
            lines.append(f"{c.status.upper()} {c.id}: {c.hint}")
    lines.append("")
    lines.append(f"verdict: {report.verdict.upper()} (exit {report.exit_code})")
    return "\n".join(lines)
