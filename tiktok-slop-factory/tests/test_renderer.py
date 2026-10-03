"""Renderer tests. These run real FFmpeg against real files."""
import subprocess
from pathlib import Path

import pytest

from app import captions, renderer
from conftest import FFMPEG, FFPROBE, ffmpeg_required

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def _make_srt(tmp_path, words=6, duration=10.0):
    path = tmp_path / "cap.srt"
    captions.to_srt(captions.group_cues(captions.simple_timestamps(
        " ".join(f"w{i}" for i in range(words)), duration)), path)
    return path


def _make_audio(tmp_path, duration=10.0):
    out = tmp_path / "narr.wav"
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={duration}",
         "-c:a", "pcm_s16le", str(out)],
        check=True,
    )
    return out


@ffmpeg_required
def test_portrait_footage_renders_full_duration(mp4_factory, tmp_path):
    audio = _make_audio(tmp_path, 10.0)
    footage = mp4_factory(duration=6.0, size="1080x1920", audio=0)
    srt = _make_srt(tmp_path)
    out = tmp_path / "out.mp4"

    renderer.render_video(audio, footage, srt, out, duration=10.0)

    assert out.is_file()
    # The old -shortest truncated this to the 6s clip length.
    assert renderer.probe_duration(out) == pytest.approx(10.0, abs=0.6)
    assert renderer.probe_has_audio(out)


@ffmpeg_required
def test_landscape_footage_renders(mp4_factory, tmp_path):
    """The old `decrease`+`crop` chain aborted on landscape footage."""
    audio = _make_audio(tmp_path, 8.0)
    footage = mp4_factory(duration=5.0, size="1280x720", audio=0)
    srt = _make_srt(tmp_path, words=4, duration=8.0)
    out = tmp_path / "land.mp4"

    renderer.render_video(audio, footage, srt, out, duration=8.0)

    assert renderer.probe_duration(out) == pytest.approx(8.0, abs=0.6)
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,pix_fmt",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert "1080" in probe.stdout and "1920" in probe.stdout
    assert "yuv420p" in probe.stdout


@ffmpeg_required
def test_short_footage_loops_to_cover_narration(mp4_factory, tmp_path):
    """A 3s clip must still yield a full-length video."""
    audio = _make_audio(tmp_path, 12.0)
    footage = mp4_factory(duration=3.0, size="1080x1920", audio=0)
    srt = _make_srt(tmp_path, words=8, duration=12.0)
    out = tmp_path / "loop.mp4"

    renderer.render_video(audio, footage, srt, out, duration=12.0)
    assert renderer.probe_duration(out) == pytest.approx(12.0, abs=0.8)


@ffmpeg_required
def test_duration_probed_when_not_given(mp4_factory, tmp_path):
    """The pipeline no longer hardcodes 60s; duration comes from the audio."""
    audio = _make_audio(tmp_path, 9.0)
    footage = mp4_factory(duration=6.0, size="1080x1920", audio=0)
    srt = _make_srt(tmp_path, words=5, duration=9.0)
    out = tmp_path / "auto.mp4"

    renderer.render_video(audio, footage, srt, out)
    assert renderer.probe_duration(out) == pytest.approx(9.0, abs=0.6)


@ffmpeg_required
def test_no_partial_files_left_behind(mp4_factory, tmp_path):
    audio = _make_audio(tmp_path, 5.0)
    footage = mp4_factory(duration=4.0, size="1080x1920", audio=0)
    srt = _make_srt(tmp_path, words=3, duration=5.0)
    out = tmp_path / "clean.mp4"

    renderer.render_video(audio, footage, srt, out, duration=5.0)
    assert not list(tmp_path.glob("*.partial.mp4"))


@ffmpeg_required
def test_failed_render_leaves_no_partial_or_output(mp4_factory, tmp_path):
    """A broken input must not publish a corrupt MP4."""
    audio = _make_audio(tmp_path, 5.0)
    footage = mp4_factory(duration=4.0, size="1080x1920", audio=0)
    srt = tmp_path / "missing.srt"
    out = tmp_path / "never.mp4"

    with pytest.raises(renderer.RenderError):
        renderer.render_video(audio, footage, srt, out, duration=5.0)

    assert not out.exists()
    assert not list(tmp_path.glob("*.partial.mp4"))


@ffmpeg_required
def test_absolute_windows_path_in_srt_does_not_break_filter(mp4_factory, tmp_path):
    """An absolute D:/... path used to break the subtitles filter parser."""
    nested = tmp_path / "captions" / "sub dir"
    nested.mkdir(parents=True)
    srt = _make_srt(nested, words=3, duration=5.0)
    assert srt.is_absolute()  # the reproduction depends on this being absolute

    audio = _make_audio(tmp_path, 5.0)
    footage = mp4_factory(duration=4.0, size="1080x1920", audio=0)
    out = tmp_path / "winpath.mp4"

    renderer.render_video(audio, footage, srt, out, duration=5.0)
    assert out.is_file()


def test_missing_ffmpeg_gives_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setattr(renderer, "get_ffmpeg_path", lambda: "definitely-not-ffmpeg")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 100)
    footage = tmp_path / "f.mp4"
    footage.write_bytes(b"\0" * 100)
    srt = _make_srt(tmp_path)

    with pytest.raises(renderer.RenderError, match="not found"):
        renderer.render_video(audio, footage, srt, tmp_path / "x.mp4", duration=5.0)


def _renderer_code() -> str:
    """Return renderer.py with comments and docstrings stripped.

    Bug descriptions in the module docstring legitimately mention the old broken
    filter string, so prose must not be mistaken for live code.
    """
    import ast

    src = (APP_DIR / "renderer.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def test_subtitle_style_uses_libass_reference_units():
    """libass lays out in a 384x288 space, not output pixels.

    FontSize=64 renders at roughly 180px on a 1080x1920 frame and pushes text
    off screen. Values were tuned against real rendered frames.
    """
    code = _renderer_code()
    assert "FontSize=20" in code
    assert "MarginV=60" in code
    # The old oversized values must not come back.
    assert "FontSize=64" not in code
    assert "FontSize=16" not in code


def test_no_dead_filter_variants():
    """The old renderer built three filter strings and discarded two."""
    code = _renderer_code()
    assert "filter_complex_simple" not in code
    assert "zoompan" not in code
    # The broken decrease+crop order must not return.
    assert "force_original_aspect_ratio=decrease" not in code
    assert "force_original_aspect_ratio=increase" in code


def test_missing_inputs_rejected(tmp_path):
    srt = _make_srt(tmp_path)
    with pytest.raises(renderer.RenderError, match="audio"):
        renderer.render_video(tmp_path / "no.wav", tmp_path / "no.mp4", srt,
                              tmp_path / "o.mp4")


def test_verify_output_rejects_silent_video(tmp_path):
    tiny = tmp_path / "tiny.mp4"
    tiny.write_bytes(b"\0" * 5000)
    with pytest.raises(renderer.RenderError):
        renderer._verify_output(tiny, 5.0)