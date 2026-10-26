"""clipcheck QC: post-text rules, FFmpeg-log parsing, and real-media checks.

Media fixtures are generated with lavfi into tmp_path at 270x480 and <= 4 s:
silent audio, a black opening, a frozen opening, a frozen middle, a drawn
"logo" overlaid for 1.5 s vs 2.5 s (and a small corner watermark that must not
count as the CTA), and tones at -30 / -16.5 / -15.4 / -14.5 LUFS. Nothing is
downloaded, nothing needs a key, and nothing is written outside tmp_path.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import qc
from app.campaign import Campaign
from conftest import FFMPEG, ffmpeg_required

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "clipcheck_pass.json"

TAGS = ("#brandpartner", "#gamedev", "#indiegame")

# Sine amplitudes measured once with ebur128 (stereo 440 Hz, AAC):
AMP_MINUS_14_5 = 0.287
AMP_MINUS_15_4 = 0.259
AMP_MINUS_16_5 = 0.228
AMP_MINUS_30 = 0.048

needs_numpy = pytest.mark.skipif(qc._numpy() is None, reason="numpy not installed")


def _campaign(tmp_path, **kw) -> Campaign:
    return Campaign(source=tmp_path / "campaign.toml", **kw)


def _by_id(report):
    return {c.id: c for c in report.checks}


# ---------------------------------------------------------------------------
# Post text (pure Python)
# ---------------------------------------------------------------------------

def _post_campaign(tmp_path, **kw):
    base = dict(required_hashtags=TAGS, hashtags_must_be_last=True,
                required_mentions=("BrandName",))
    base.update(kw)
    return _campaign(tmp_path, **base)


def _post_status(tmp_path, text, **kw):
    return {c.id: c.status for c in qc.check_post(text, _post_campaign(tmp_path, **kw))}


def test_post_correct_order_and_last_passes(tmp_path):
    text = "I built this with BrandName, what next? #brandpartner #gamedev #indiegame\n"
    assert _post_status(tmp_path, text) == {"post_hashtags": "pass", "post_mentions": "pass"}


def test_post_hashtags_case_insensitive(tmp_path):
    text = "made with brandname #BrandPartner #GameDev #IndieGame"
    assert _post_status(tmp_path, text)["post_hashtags"] == "pass"


def test_post_wrong_hashtag_order_fails(tmp_path):
    text = "BrandName rocks #gamedev #brandpartner #indiegame"
    res = qc.check_post(text, _post_campaign(tmp_path))
    tags = next(c for c in res if c.id == "post_hashtags")
    assert tags.status == "fail" and "order" in tags.hint


def test_post_missing_hashtag_fails_and_names_it(tmp_path):
    res = qc.check_post("BrandName #brandpartner #indiegame", _post_campaign(tmp_path))
    tags = next(c for c in res if c.id == "post_hashtags")
    assert tags.status == "fail" and "#gamedev" in tags.hint


def test_post_tag_between_required_ones_fails(tmp_path):
    text = "BrandName #brandpartner #fyp #gamedev #indiegame"
    assert _post_status(tmp_path, text)["post_hashtags"] == "fail"


def test_post_text_after_hashtags_fails_when_must_be_last(tmp_path):
    text = "BrandName #brandpartner #gamedev #indiegame what do you think?"
    assert _post_status(tmp_path, text)["post_hashtags"] == "fail"


def test_post_extra_tag_after_fails_when_must_be_last(tmp_path):
    text = "BrandName #brandpartner #gamedev #indiegame #fyp"
    assert _post_status(tmp_path, text)["post_hashtags"] == "fail"


def test_post_text_after_hashtags_ok_when_not_required_last(tmp_path):
    text = "BrandName #brandpartner #gamedev #indiegame what do you think?"
    assert _post_status(tmp_path, text, hashtags_must_be_last=False)["post_hashtags"] == "pass"


def test_post_missing_mention_fails(tmp_path):
    text = "no brand here #brandpartner #gamedev #indiegame"
    assert _post_status(tmp_path, text)["post_mentions"] == "fail"


def test_post_disclosure_line(tmp_path):
    camp = _post_campaign(tmp_path, disclosure_line="AI voice")
    ok = qc.check_post("BrandName (ai voice) #brandpartner #gamedev #indiegame", camp)
    bad = qc.check_post("BrandName #brandpartner #gamedev #indiegame", camp)
    assert {c.id: c.status for c in ok}["post_disclosure"] == "pass"
    assert {c.id: c.status for c in bad}["post_disclosure"] == "fail"


def test_no_post_text_skips_post_rules(tmp_path):
    res = qc.check_post(None, _post_campaign(tmp_path))
    assert {c.status for c in res} == {"skip"}


def test_no_post_rules_no_post_checks(tmp_path):
    assert qc.check_post("anything #x", _campaign(tmp_path)) == []


# ---------------------------------------------------------------------------
# Parsers and exit codes (pure Python)
# ---------------------------------------------------------------------------

EBUR128_TAIL = """
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -14.6 LUFS
    Threshold: -24.8 LUFS

  Loudness range:
    LRA:         3.1 LU

  True peak:
    Peak:       -1.3 dBFS
"""


def test_parse_ebur128_summary():
    assert qc.parse_ebur128_summary("t: 1 M: -20 I: -99.0 LUFS\n" + EBUR128_TAIL) == (-14.6, -1.3)


def test_parse_ebur128_without_summary():
    assert qc.parse_ebur128_summary("nothing here") == (None, None)


def test_parse_detect_log_open_freeze_runs_to_end():
    log = (
        "[blackdetect @ 0x1] black_start:0 black_end:0.5 black_duration:0.5\n"
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 1.0\n"
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 2.0\n"
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 3.0\n"
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 3.5\n"
    )
    black, freezes = qc.parse_detect_log(log, duration=4.0)
    assert black == [(0.0, 0.5)]
    assert freezes == [(1.0, 3.0), (3.5, 4.0)]


@pytest.mark.parametrize("statuses, code", [
    (["pass", "skip"], 0), (["pass", "warn"], 2), (["warn", "fail"], 1), ([], 0),
])
def test_exit_code_for(statuses, code):
    assert qc.exit_code_for([qc.CheckResult(str(i), s) for i, s in enumerate(statuses)]) == code


@needs_numpy
def test_ncc_finds_exact_patch_and_ignores_flat_frame():
    import numpy as np
    rng = np.random.default_rng(0)
    frame = rng.random((60, 80)) * 255
    patch = frame[10:30, 20:50].copy()
    assert qc.ncc_max(frame, patch) == pytest.approx(1.0, abs=1e-6)
    assert qc.ncc_max(np.full((60, 80), 7.0), patch) == 0.0
    assert qc.ncc_max(frame, np.zeros((5, 5))) == 0.0


# ---------------------------------------------------------------------------
# Real-media fixtures (FFmpeg)
# ---------------------------------------------------------------------------

def _logo(tmp_path) -> Path:
    """A chunky two-tone mark, like a real wordmark, drawn with lavfi."""
    out = tmp_path / "logo.png"
    if not out.exists():
        vf = ("drawbox=x=10:y=10:w=50:h=70:c=black:t=fill,"
              "drawbox=x=80:y=18:w=70:h=20:c=black:t=fill,"
              "drawbox=x=80:y=52:w=40:h=20:c=0x2050ff:t=fill")
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=white:s=160x90", "-frames:v", "1", "-vf", vf,
                        str(out)], check=True)
    return out


def _clip(tmp_path, name, *, dur=4.0, size="270x480", video="moving", amp=AMP_MINUS_14_5,
          audio=True, logo_from=None, logo_w=120, corner_logo=False) -> Path:
    out = tmp_path / f"{name}.mp4"
    w, h = size.split("x")
    src = f"testsrc2=s={size}:r=30:d={dur}"
    if video == "moving":
        graph = f"{src}[base]"
    elif video == "black_start":
        graph = (f"color=c=black:s={size}:r=30:d=0.5[blk];testsrc2=s={size}:r=30:d={dur - 0.5}"
                 "[mv];[blk][mv]concat=n=2:v=1:a=0[base]")
    elif video == "frozen_start":  # frame 0 held for 1.5 s, then motion
        graph = f"{src},loop=loop=44:size=1:start=0,setpts=N/30/TB,trim=end={dur}[base]"
    elif video == "frozen_mid":  # motion 0-1 s, frame 30 held 1-3 s, motion 3-4 s
        graph = f"{src},loop=loop=59:size=1:start=30,setpts=N/30/TB,trim=end={dur}[base]"
    else:
        raise AssertionError(video)

    inputs = []
    vlabel = "base"
    if logo_from is not None or corner_logo:
        inputs += ["-loop", "1", "-t", f"{dur}", "-i", str(_logo(tmp_path))]
        if corner_logo and logo_from is not None:
            graph += f";[0:v]split[l1][l2];[l1]scale=40:-1[cl];[l2]scale={logo_w}:-1[lg]"
        elif corner_logo:
            graph += ";[0:v]scale=40:-1[cl]"
        else:
            graph += f";[0:v]scale={logo_w}:-1[lg]"
        if corner_logo:
            graph += f";[{vlabel}][cl]overlay=x=8:y=8[wm]"
            vlabel = "wm"
        if logo_from is not None:
            graph += (f";[{vlabel}][lg]overlay=x=(W-w)/2:y=(H-h)/2:"
                      f"enable='gte(t,{logo_from})'[v]")
            vlabel = "v"
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *inputs]
    if audio:
        expr = f"{amp}*sin(2*PI*440*t)" if amp else "0"
        cmd += ["-f", "lavfi", "-i", f"aevalsrc={expr}:s=44100:d={dur}"]
    cmd += ["-filter_complex", graph, "-map", f"[{vlabel}]"]
    if audio:
        cmd += ["-map", f"{1 if inputs else 0}:a", "-ac", "2", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-t", f"{dur}", str(out)]
    subprocess.run(cmd, check=True)
    return out


def _media_campaign(tmp_path, **kw):
    base = dict(length_range=(3.0, 5.0), max_static_seconds=1.5,
                loudness_target=(-15.0, -14.0), max_true_peak=-1.0)
    base.update(kw)
    return _campaign(tmp_path, **base)


@ffmpeg_required
def test_good_clip_passes_media_checks(tmp_path):
    report = qc.run_checks(_clip(tmp_path, "good"), _media_campaign(tmp_path))
    st = {c.id: c.status for c in report.checks}
    assert st == {"container": "pass", "aspect": "pass", "duration": "pass",
                  "opening": "pass", "static": "pass", "loudness": "pass",
                  "true_peak": "pass", "cta_logo": "skip"}
    assert report.exit_code == 0


@ffmpeg_required
def test_silent_clip_fails_loudness(tmp_path):
    report = qc.run_checks(_clip(tmp_path, "silent", amp=None), _media_campaign(tmp_path))
    c = _by_id(report)["loudness"]
    assert c.status == "fail" and "silent" in c.hint
    assert report.exit_code == 1


@ffmpeg_required
def test_clip_without_audio_fails_loudness(tmp_path):
    report = qc.run_checks(_clip(tmp_path, "noaudio", audio=False), _media_campaign(tmp_path))
    assert _by_id(report)["loudness"].status == "fail"
    assert _by_id(report)["true_peak"].status == "skip"


@ffmpeg_required
@pytest.mark.parametrize("amp, status", [
    (AMP_MINUS_14_5, "pass"),    # inside -15..-14
    (AMP_MINUS_15_4, "warn"),    # 0.4 LU too quiet: borderline
    (AMP_MINUS_16_5, "fail"),    # 1.5 LU too quiet
    (AMP_MINUS_30, "fail"),      # far off target
])
def test_loudness_window(tmp_path, amp, status):
    report = qc.run_checks(_clip(tmp_path, f"tone{amp}", amp=amp), _media_campaign(tmp_path))
    assert _by_id(report)["loudness"].status == status, _by_id(report)["loudness"]


@ffmpeg_required
def test_black_opening_fails(tmp_path):
    report = qc.run_checks(_clip(tmp_path, "black", video="black_start"),
                           _media_campaign(tmp_path))
    c = _by_id(report)["opening"]
    assert c.status == "fail" and c.measured.startswith("black 0.00")


@ffmpeg_required
def test_frozen_opening_warns(tmp_path):
    report = qc.run_checks(_clip(tmp_path, "frozen", video="frozen_start"),
                           _media_campaign(tmp_path))
    assert _by_id(report)["opening"].status == "warn"


@ffmpeg_required
def test_static_middle_warns_and_reports_length(tmp_path):
    report = qc.run_checks(_clip(tmp_path, "midfreeze", video="frozen_mid"),
                           _media_campaign(tmp_path))
    c = _by_id(report)["static"]
    assert c.status == "warn"
    assert c.measured.startswith(("1.9", "2.0")), c.measured
    # The same 2 s freeze is fine for a campaign that allows 2.5 s.
    lax = qc.run_checks(tmp_path / "midfreeze.mp4", _media_campaign(
        tmp_path, max_static_seconds=2.5))
    assert _by_id(lax)["static"].status == "pass"


@ffmpeg_required
def test_landscape_and_out_of_range_duration_fail(tmp_path):
    report = qc.run_checks(_clip(tmp_path, "wide", size="480x270"),
                           _media_campaign(tmp_path, length_range=(15.0, 28.0)))
    st = {c.id: c.status for c in report.checks}
    assert st["aspect"] == "fail" and st["duration"] == "fail"


@ffmpeg_required
def test_unreadable_file_fails_container_and_skips_media(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video" * 200)
    report = qc.run_checks(bad, _media_campaign(tmp_path, required_hashtags=TAGS),
                           "x #brandpartner #gamedev #indiegame")
    st = {c.id: c.status for c in report.checks}
    assert st["container"] == "fail"
    assert {st[k] for k in ("aspect", "duration", "opening", "static", "loudness",
                            "true_peak", "cta_logo")} == {"skip"}
    assert st["post_hashtags"] == "pass"
    assert report.exit_code == 1


@ffmpeg_required
@needs_numpy
@pytest.mark.parametrize("logo_from, status", [
    (2.5, "fail"),   # logo for 1.5 s, campaign needs 2.0 s (like a 1.1 s end card)
    (1.5, "pass"),   # logo for 2.5 s
])
def test_cta_logo_duration(tmp_path, logo_from, status):
    camp = _media_campaign(tmp_path, cta_logo_path=_logo(tmp_path), cta_min_seconds=2.0)
    report = qc.run_checks(_clip(tmp_path, f"logo{logo_from}", logo_from=logo_from), camp)
    c = _by_id(report)["cta_logo"]
    assert c.status == status, c
    expected_run = 4.0 - logo_from
    assert c.measured.startswith(f"~{expected_run:.2f}s"), c.measured


@ffmpeg_required
@needs_numpy
def test_cta_logo_borderline_warns(tmp_path):
    # 1.75 s visible vs a 2.0 s minimum: within one 0.25 s sample -> warn, not fail.
    camp = _media_campaign(tmp_path, cta_logo_path=_logo(tmp_path), cta_min_seconds=2.0)
    report = qc.run_checks(_clip(tmp_path, "logo_border", logo_from=2.25), camp)
    assert _by_id(report)["cta_logo"].status == "warn"


@ffmpeg_required
@needs_numpy
def test_small_corner_watermark_is_not_the_cta(tmp_path):
    camp = _media_campaign(tmp_path, cta_logo_path=_logo(tmp_path), cta_min_seconds=2.0)
    report = qc.run_checks(_clip(tmp_path, "corner", corner_logo=True), camp)
    c = _by_id(report)["cta_logo"]
    assert c.status == "fail" and c.measured.startswith("~0.00s")
    # The static check then assumes the last cta_min_seconds are the end card.
    assert "assumed CTA" in _by_id(report)["static"].expected


@ffmpeg_required
def test_cta_logo_without_numpy_warns_not_measured(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "_numpy", lambda: None)
    camp = _media_campaign(tmp_path, cta_logo_path=_logo(tmp_path), cta_min_seconds=2.0)
    report = qc.run_checks(_clip(tmp_path, "nonumpy", logo_from=1.5), camp)
    c = _by_id(report)["cta_logo"]
    assert c.status == "warn" and c.measured == "not measured"


# ---------------------------------------------------------------------------
# CLI + golden JSON report
# ---------------------------------------------------------------------------

def _write_campaign_toml(tmp_path, logo: Path | None) -> Path:
    lines = [
        "[campaign]", 'name = "fixture"',
        "[video]", "length_range = [3.0, 5.0]", "max_static_seconds = 1.5",
        "[audio]", "loudness_target = [-15.0, -14.0]", "max_true_peak = -1.0",
        "[cta]", "min_seconds = 2.0",
    ]
    if logo is not None:
        lines.append(f'logo_path = "{logo.name}"')
    lines += ["[post]", f"required_hashtags = {json.dumps(list(TAGS))}",
              "hashtags_must_be_last = true", 'required_mentions = ["BrandName"]',
              'disclosure_line = "AI voice"']
    path = tmp_path / "fixture campaign.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _cli(*args):
    return subprocess.run([sys.executable, str(PROJECT_ROOT / "clipcheck.py"), *map(str, args)],
                          capture_output=True, text=True, timeout=600)


def _normalize(report: dict) -> dict:
    """Drop machine-specific values (paths, measured numbers) for the golden compare."""
    return {
        "tool": report["tool"], "schema": report["schema"],
        "verdict": report["verdict"], "exit_code": report["exit_code"],
        "checks": [{"id": c["id"], "status": c["status"], "expected": c["expected"]}
                   for c in report["checks"]],
    }


@ffmpeg_required
@needs_numpy
def test_cli_json_report_matches_golden(tmp_path):
    logo = _logo(tmp_path)
    video = _clip(tmp_path, "golden clip", logo_from=1.5)
    camp = _write_campaign_toml(tmp_path, logo)
    post = tmp_path / "post.txt"
    post.write_text("made with BrandName (AI voice) what next? "
                    "#brandpartner #gamedev #indiegame\n", encoding="utf-8")
    proc = _cli(video, "--campaign", camp, "--post", post, "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["video"] == str(video) and report["post"] == str(post)
    assert _normalize(report) == json.loads(GOLDEN.read_text(encoding="utf-8"))


@ffmpeg_required
def test_cli_table_and_warn_exit_code(tmp_path):
    video = _clip(tmp_path, "quiet", amp=AMP_MINUS_15_4)
    proc = _cli(video, "--campaign", _write_campaign_toml(tmp_path, None))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "WARN    loudness" in proc.stdout
    assert "verdict: WARN (exit 2)" in proc.stdout


def test_cli_bad_campaign_exits_1_with_clear_message(tmp_path):
    bad = tmp_path / "c.toml"
    bad.write_text("[video]\nlenght_range = [1, 2]\n", encoding="utf-8")
    proc = _cli(tmp_path / "x.mp4", "--campaign", bad)
    assert proc.returncode == 1
    assert "lenght_range" in proc.stderr and "Traceback" not in proc.stderr


def test_cli_usage_error_exits_1_not_2(tmp_path):
    proc = _cli(tmp_path / "x.mp4")  # --campaign missing
    assert proc.returncode == 1
    assert "--campaign" in proc.stderr


def test_cli_missing_post_file_exits_1(tmp_path):
    camp = tmp_path / "c.toml"
    camp.write_text("", encoding="utf-8")
    proc = _cli(tmp_path / "x.mp4", "--campaign", camp, "--post", tmp_path / "nope.txt")
    assert proc.returncode == 1 and "cannot read post file" in proc.stderr
