"""Procedural visual stage: filtergraph construction and real FFmpeg renders."""
import subprocess

import pytest

from app import renderer, visuals
from conftest import FFMPEG, FFPROBE, ffmpeg_required

_SCRIPT = {
    "hook": "The lighthouse keeper heard his name.",
    "story": "He had been alone for eleven winters on the rocks.",
    "twist": "The voice came from inside the wall behind him.",
    "ending": "Nobody believed him, so he opened it himself.",
    "cta": "Would you have opened it?",
}


def test_filtergraph_defines_every_scene(tmp_path):
    scenes = visuals.plan_scenes(_SCRIPT, duration=30.0, seed=5)
    graph = visuals.build_filtergraph(scenes, tmp_path, visuals.find_font())
    for scene in scenes:
        assert f"[s{scene.index}_bg]" in graph
        assert f"[s{scene.index}_out]" in graph
    assert graph.endswith("[vout]")


def test_filtergraph_chains_one_xfade_per_boundary(tmp_path):
    scenes = visuals.plan_scenes(_SCRIPT, duration=30.0, seed=5)
    graph = visuals.build_filtergraph(scenes, tmp_path, visuals.find_font())
    assert graph.count("xfade=") == len(scenes) - 1


def test_filtergraph_xfade_offsets_are_increasing(tmp_path):
    """A non-monotonic offset makes FFmpeg abort on the second transition."""
    import re
    scenes = visuals.plan_scenes(_SCRIPT, duration=45.0, seed=9)
    graph = visuals.build_filtergraph(scenes, tmp_path, visuals.find_font())
    offsets = [float(m) for m in re.findall(r"offset=([\d.]+)", graph)]
    assert len(offsets) == len(scenes) - 1
    assert offsets == sorted(offsets)


def test_filtergraph_single_scene_skips_xfade(tmp_path):
    scenes = visuals.plan_scenes({"hook": "One short line."}, duration=10.0, seed=2)
    graph = visuals.build_filtergraph(scenes, tmp_path, visuals.find_font())
    assert "xfade=" not in graph


def test_filtergraph_omits_drawtext_without_a_font(tmp_path):
    scenes = visuals.plan_scenes(_SCRIPT, duration=30.0, seed=5)
    graph = visuals.build_filtergraph(scenes, tmp_path, None)
    assert "drawtext" not in graph
    # The rest of the chain must still be intact without text.
    assert "gradients=" in graph and "xfade=" in graph


def test_scene_text_files_have_no_bom(tmp_path):
    """A UTF-8 BOM makes FFmpeg's drawtext render nothing at all."""
    scenes = visuals.plan_scenes(_SCRIPT, duration=30.0, seed=5)
    visuals.build_filtergraph(scenes, tmp_path, r"C:\Windows\Fonts\arialbd.ttf")
    for path in tmp_path.glob("scene*.txt"):
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_find_font_returns_none_when_nothing_exists(monkeypatch):
    monkeypatch.setenv("VISUAL_FONT", "")
    monkeypatch.setattr(visuals, "_FONT_CANDIDATES", ())
    assert visuals.find_font() is None


@ffmpeg_required
def test_render_background_produces_portrait_video(tmp_path):
    scenes = visuals.plan_scenes(_SCRIPT, duration=8.0, seed=4)
    out = visuals.render_background(scenes, tmp_path / "bg.mp4", duration=8.0,
                                   work_dir=tmp_path)
    assert out.is_file()
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,pix_fmt",
         "-show_entries", "format=duration", "-of", "json", str(out)],
        capture_output=True, text=True, check=True,
    )
    import json
    info = json.loads(probe.stdout)
    stream = info["streams"][0]
    assert (stream["width"], stream["height"]) == (1080, 1920)
    assert stream["pix_fmt"] == "yuv420p"
    assert float(info["format"]["duration"]) == pytest.approx(8.0, abs=0.5)


@ffmpeg_required
def test_render_background_covers_full_narration(tmp_path):
    """Scenes are padded for transitions; the track must still land on length."""
    duration = 12.0
    scenes = visuals.plan_scenes(_SCRIPT, duration=duration, seed=6)
    out = visuals.render_background(scenes, tmp_path / "bg.mp4", duration=duration,
                                   work_dir=tmp_path)
    assert renderer.probe_duration(out) == pytest.approx(duration, abs=0.5)


@ffmpeg_required
def test_render_background_leaves_no_partial_or_temp_files(tmp_path):
    scenes = visuals.plan_scenes(_SCRIPT, duration=6.0, seed=8)
    visuals.render_background(scenes, tmp_path / "bg.mp4", duration=6.0,
                              work_dir=tmp_path)
    assert not list(tmp_path.glob("*.partial.mp4"))
    assert not (tmp_path / "text").exists()


@ffmpeg_required
def test_render_background_rejects_empty_scene_list(tmp_path):
    with pytest.raises(visuals.VisualError):
        visuals.render_background([], tmp_path / "bg.mp4", duration=5.0,
                                  work_dir=tmp_path)


@ffmpeg_required
def test_generated_track_muxes_into_final_video(tmp_path):
    """The visuals stage must drop straight into the existing renderer."""
    from app import captions
    duration = 8.0
    scenes = visuals.plan_scenes(_SCRIPT, duration=duration, seed=12)
    bg = visuals.render_background(scenes, tmp_path / "bg.mp4",
                                   duration=duration, work_dir=tmp_path)

    audio = tmp_path / "narr.wav"
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={duration}",
         "-c:a", "pcm_s16le", str(audio)],
        check=True,
    )
    cues = captions.group_cues(
        captions.simple_timestamps(" ".join(f"w{i}" for i in range(9)), duration))
    srt = captions.to_srt(cues, tmp_path / "cap.srt")

    out = renderer.render_video(audio, bg, srt, tmp_path / "final.mp4",
                                duration=duration)
    assert out.is_file()
    assert renderer.probe_duration(out) == pytest.approx(duration, abs=0.6)
    assert renderer.probe_has_audio(out)