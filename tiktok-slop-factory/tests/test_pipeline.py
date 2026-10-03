"""Pipeline behaviour: naming, isolation of failures, cleanup, CLI wiring."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from app import gemini, pipeline, renderer, tts
from conftest import ffmpeg_required

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_select_best_ideas():
    assert pipeline.select_best_ideas(["a", "b", "c", "d"], 2) == ["a", "b"]


def test_select_best_ideas_deduplicates():
    """Duplicate ideas produced duplicate filenames and duplicate videos."""
    out = pipeline.select_best_ideas(["A idea", "a idea", "B idea"], 3)
    assert out == ["A idea", "B idea"]


def test_select_best_ideas_skips_blanks():
    assert pipeline.select_best_ideas(["", "  ", "real"], 2) == ["real"]


def test_build_stem_is_deterministic_across_processes():
    """The old `abs(hash(idea))` was randomized per process, so caching broke."""
    idea = "A lighthouse keeper finds a message in a bottle"
    code = (
        f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r});"
        f"from app.pipeline import build_stem; print(build_stem({idea!r}))"
    )
    runs = [
        subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, check=True).stdout.strip()
        for _ in range(2)
    ]
    assert runs[0] == runs[1]
    assert pipeline.build_stem(idea) == runs[0]


def test_build_stem_is_filesystem_safe():
    stem = pipeline.build_stem('Weird/idea: with "quotes" & <chars>', 0)
    assert "/" not in stem and ":" not in stem
    assert stem.startswith("01-")


def test_build_stem_distinguishes_different_ideas():
    assert pipeline.build_stem("idea one") != pipeline.build_stem("idea two")


def test_run_pipeline_dry_run_reports_missing_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    result = pipeline.run_pipeline(1, dry_run=True)
    assert result["failed"]
    assert not result["success"]


def test_run_pipeline_dry_run_ok(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("PEXELS_API_KEY", "p")
    result = pipeline.run_pipeline(1, dry_run=True)
    if result["failed"]:
        # Only acceptable dry-run failure is a missing local FFmpeg install.
        assert all("FFmpeg" in f["error"] or "ffprobe" in f["error"]
                   for f in result["failed"]), result["failed"]
    else:
        assert result["success"]


def test_run_pipeline_reports_idea_failure_without_crashing(monkeypatch):
    """A Gemini outage must return a failure entry, not raise."""

    def boom(n):
        raise gemini.GeminiError("Gemini HTTP 429")

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("PEXELS_API_KEY", "p")
    monkeypatch.setattr(pipeline.gemini, "generate_ideas", boom)

    result = pipeline.run_pipeline(3)
    assert result["failed"]
    assert result["failed"][0]["step"] == "ideas"
    assert not result["success"]


def test_run_pipeline_continues_past_one_bad_idea(monkeypatch, tmp_path):
    """One failing video must not kill the whole batch."""
    monkeypatch.setattr(pipeline, "get_output_dir", lambda: tmp_path)
    monkeypatch.setattr(
        pipeline.gemini, "generate_ideas",
        lambda n: ["good one", "bad one", "good two"],
    )
    calls = {"n": 0}

    def fake_script(idea):
        calls["n"] += 1
        if idea == "bad one":
            raise gemini.GeminiError("script exploded")
        return {"hook": "H", "story": "S", "twist": "T", "ending": "E",
                "cta": "C", "title": idea, "hashtags": ["#x"]}

    monkeypatch.setattr(pipeline.gemini, "generate_script", fake_script)
    monkeypatch.setattr(
        pipeline.gemini, "generate_tts",
        lambda s: (_ for _ in ()).throw(gemini.GeminiError("tts unavailable")),
    )

    result = pipeline.run_pipeline(3)
    # All three were attempted despite the middle one failing.
    assert calls["n"] == 3
    assert len(result["failed"]) == 3
    assert not result["success"]


def test_cleanup_stale_removes_partial_files(tmp_path):
    (tmp_path / "a.partial.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"keep")
    removed = pipeline.cleanup_stale(tmp_path, ("*.partial.mp4",))
    assert removed == 1
    assert (tmp_path / "b.mp4").exists()
    assert not (tmp_path / "a.partial.mp4").exists()
def test_produce_one_cleans_up_intermediates_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "get_output_dir", lambda: tmp_path)
    monkeypatch.setattr(
        pipeline.gemini, "generate_script",
        lambda idea: {"hook": "H", "story": "S", "twist": "T",
                      "ending": "E", "cta": "C", "title": "t", "hashtags": []},
    )
    monkeypatch.setattr(
        pipeline.gemini, "generate_tts",
        lambda s: (_ for _ in ()).throw(gemini.GeminiError("tts down")),
    )

    entry = pipeline._produce_one("some idea", 0)
    assert "error" in entry
    assert not list((tmp_path / "audio").glob("*"))
    assert not list(tmp_path.glob("*.partial.mp4"))


def test_find_footage_falls_back_across_queries(monkeypatch, tmp_path):
    """An empty first search must not fail the video."""
    monkeypatch.setattr(pipeline.pexels, "cache_dir", lambda: tmp_path)
    queries = []

    def fake_search(query, per_page=3):
        queries.append(query)
        # Reject everything until the generic fallback query is used.
        if "fog forest" not in query:
            return []
        return [{"id": 9, "url": "https://x.invalid/v.mp4", "user": {"name": "P"}}]

    clip = tmp_path / "9.mp4"
    clip.write_bytes(b"\0" * 20000 + b"ftyp")

    monkeypatch.setattr(pipeline.pexels, "search_videos", fake_search)
    monkeypatch.setattr(pipeline.pexels, "download_video", lambda info: clip)

    path, info = pipeline._find_footage(
        {"hook": "The lighthouse keeper watched the fog roll in."}, "fog story"
    )
    assert path == clip
    assert info["id"] == 9
    assert len(queries) > 1


def test_find_footage_skips_download_failures(monkeypatch, tmp_path):
    """A clip that fails to download should not abort the video."""
    monkeypatch.setattr(pipeline.pexels, "cache_dir", lambda: tmp_path)
    attempted = []

    def fake_search(query, per_page=3):
        return [{"id": len(attempted) + 1, "url": "https://x.invalid/v.mp4"}]

    def fake_download(info):
        attempted.append(info["id"])
        if len(attempted) == 1:
            raise pipeline.pexels.PexelsError("download failed")
        return good

    good = tmp_path / "ok.mp4"
    good.write_bytes(b"\0" * 20000 + b"ftyp")

    monkeypatch.setattr(pipeline.pexels, "search_videos", fake_search)
    monkeypatch.setattr(pipeline.pexels, "download_video", fake_download)

    path, _ = pipeline._find_footage({"hook": "The abandoned lighthouse."}, "lighthouse")
    assert path == good
    assert len(attempted) >= 2


def test_find_footage_raises_when_nothing_found(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.pexels, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(pipeline.pexels, "search_videos", lambda q, per_page=3: [])
    with pytest.raises(pipeline.PipelineError):
        pipeline._find_footage({"hook": "A foggy lighthouse."}, "fog")


def test_check_dependencies_lists_missing_ffmpeg(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("PEXELS_API_KEY", "p")
    monkeypatch.setattr(pipeline, "get_ffmpeg_path", lambda: "no-such-ffmpeg")
    monkeypatch.setattr(pipeline, "get_ffprobe_path", lambda: "no-such-ffprobe")
    problems = pipeline.check_dependencies()
    assert any("FFmpeg" in p for p in problems)


def test_cli_rejects_zero_count(monkeypatch):
    import make_videos
    assert make_videos.main(["--count", "0"]) == 1


def test_cli_dry_run_exit_code(monkeypatch):
    import make_videos

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert make_videos.main(["--count", "1", "--dry-run"]) == 1


@ffmpeg_required
def test_headerless_pcm_tts_is_made_playable(monkeypatch, tmp_path):
    """Gemini can return headerless PCM; FFmpeg cannot read that as-is.

    Unwrapped it fails with "Invalid data found when processing input".
    """
    import subprocess

    raw = tmp_path / "raw.pcm"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
         "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "24000",
         str(raw)],
        check=True,
    )
    pcm = raw.read_bytes()
    assert not pcm.startswith(b"RIFF")

    wrapped = tts.ensure_playable(pcm, "audio/L16;codec=pcm;rate=24000")
    assert wrapped.startswith(b"RIFF")
    assert wrapped.endswith(pcm)

    path = tmp_path / "fixed.wav"
    path.write_bytes(wrapped)

    assert renderer.probe_duration(path) == pytest.approx(4.0, abs=0.3)
    assert renderer.probe_has_audio(path)


def test_has_wav_header_detection():
    assert tts.has_wav_header(tts.wav_header(100) + b"\0" * 200)
    assert not tts.has_wav_header(b"\0" * 500)


def test_ensure_playable_leaves_other_formats_alone():
    mp3ish = b"ID3\x04fake mp3 payload"
    assert tts.ensure_playable(mp3ish, "audio/mpeg") == mp3ish
    already = tts.wav_header(10) + b"\0" * 20
    assert tts.ensure_playable(already, "audio/wav") == already


def test_save_audio_rejects_empty():
    with pytest.raises(ValueError):
        tts.save_audio(b"", "x.wav", output_dir=Path(tempfile.gettempdir()))


@ffmpeg_required
def test_end_to_end_produces_vertical_video(monkeypatch, tmp_path, mp4_factory):
    """Full pipeline with mocked APIs and real FFmpeg.

    Asserts the acceptance criteria: valid vertical MP4s in output/videos/.
    """
    import base64
    import subprocess

    monkeypatch.setattr(pipeline, "get_output_dir", lambda: tmp_path)
    monkeypatch.setattr(pipeline.pexels, "cache_dir", lambda: tmp_path / "footage")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("PEXELS_API_KEY", "p")

    # Real 6-second WAV. gemini.generate_tts returns already-decoded audio
    # bytes, so the stub hands the pipeline the raw WAV payload.
    wav = tmp_path / "speech.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
         "-c:a", "pcm_s16le", str(wav)],
        check=True,
    )
    encoded = wav.read_bytes()
    assert encoded.startswith(b"RIFF")

    monkeypatch.setattr(
        pipeline.gemini, "generate_ideas",
        lambda n: [f"idea number {i}" for i in range(n)],
    )
    monkeypatch.setattr(
        pipeline.gemini, "generate_script",
        lambda idea: {
            "hook": "The lighthouse keeper heard his name.",
            "story": "He had been alone for eleven winters.",
            "twist": "The voice came from inside the wall.",
            "ending": "Nobody believed him, so he opened it.",
            "cta": "Would you have opened it?",
            "title": "The Lighthouse",
            "hashtags": ["#fiction", "#storytime"],
        },
    )
    monkeypatch.setattr(
        pipeline.gemini, "generate_tts",
        lambda s: gemini.AudioBlob(encoded, "audio/wav"),
    )

    clip = mp4_factory(duration=6.0, size="1280x720", audio=0)  # landscape on purpose
    monkeypatch.setattr(
        pipeline.pexels, "search_videos",
        lambda q, per_page=3: [{"id": 42, "url": "https://x.invalid/v.mp4",
                                "user": {"name": "Pexels Artist"},
                                "page_url": "https://www.pexels.com/video/42/"}],
    )
    monkeypatch.setattr(pipeline.pexels, "download_video", lambda info: clip)

    result = pipeline.run_pipeline(3)

    assert not result["failed"], result["failed"]
    assert len(result["success"]) == 3

    videos = sorted((tmp_path / "videos").glob("*.mp4"))
    assert len(videos) == 3
    assert not list((tmp_path / "videos").glob("*.partial.mp4"))

    for video in videos:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width,height,pix_fmt",
             "-show_entries", "format=duration",
             "-of", "json", str(video)],
            capture_output=True, text=True, check=True,
        )
        import json as _json
        info = _json.loads(probe.stdout)
        streams = {s["codec_type"]: s for s in info["streams"]}
        assert "video" in streams, f"{video.name} has no video stream"
        assert "audio" in streams, f"{video.name} is silent"
        assert streams["video"]["width"] == 1080
        assert streams["video"]["height"] == 1920
        assert streams["video"]["pix_fmt"] == "yuv420p"
        assert float(info["format"]["duration"]) == pytest.approx(6.0, abs=0.6)

    # Metadata is written for each video, with Pexels attribution.
    metas = sorted((tmp_path / "metadata").glob("*.json"))
    assert len(metas) == 3
    data = json.loads(metas[0].read_text(encoding="utf-8"))
    assert data["title"] == "The Lighthouse"
    assert data["hashtags"] == ["#fiction", "#storytime"]
    assert data["duration_seconds"] == pytest.approx(6.0, abs=0.6)
    assert data["source_footage_urls"] == ["https://x.invalid/v.mp4"]

    # Subtitles exist and are non-trivial.
    srt = sorted((tmp_path / "captions").glob("*.srt"))[0]
    assert srt.read_text(encoding="utf-8-sig").count("-->") >= 4


def test_cli_rejects_zero_count(monkeypatch):
    import make_videos
    assert make_videos.main(["--count", "0"]) == 1


def test_cli_dry_run_exit_code(monkeypatch):
    import make_videos

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert make_videos.main(["--count", "1", "--dry-run"]) == 1


def test_build_metadata_uses_timezone_aware_timestamp():
    meta = pipeline.build_metadata("t", {}, "c", [], [], "f.mp4", 12.3456)
    assert meta["creation_timestamp"].endswith("+00:00")
    assert meta["duration_seconds"] == 12.35