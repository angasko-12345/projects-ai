"""Captions and script helpers."""
import pytest

from app import captions, script


def test_split_scenes_returns_requested_count():
    s = {"hook": "h", "story": "s.", "twist": "t.", "ending": "e.", "cta": "c"}
    scenes = script.split_into_scenes(s, num_scenes=3)
    assert len(scenes) == 3


def test_split_scenes_does_not_duplicate_sentences():
    """The old code padded by repeating the last sentence, wasting quota."""
    scenes = script.split_into_scenes({"hook": "Only one sentence here"}, num_scenes=3)
    assert scenes == ["Only one sentence here"]


def test_split_scenes_empty_script():
    assert script.split_into_scenes({}, num_scenes=3) == []


def test_timestamps():
    ts = captions.simple_timestamps("one two three", 60.0)
    assert len(ts) == 3
    assert ts[0][0] == 0.0
    assert ts[-1][1] == pytest.approx(60.0)


def test_timestamps_empty_text_returns_no_cues():
    """The old code emitted one blank cue, rendering an empty subtitle box."""
    assert captions.simple_timestamps("   ", 10.0) == []


@pytest.mark.parametrize("seconds", [1.9999, 9.9999999, 0.0006, 61.0005, 3599.9999])
def test_fmt_time_millisecond_field_never_overflows(seconds):
    """Rounding must not produce an invalid ",1000" millisecond field."""
    out = captions.fmt_time(seconds)
    ms = int(out.split(",")[1])
    assert ms <= 999, f"{seconds} produced {out}"


def test_fmt_time_exact_values():
    assert captions.fmt_time(0) == "00:00:00,000"
    assert captions.fmt_time(1.5) == "00:00:01,500"
    assert captions.fmt_time(3661.25) == "01:01:01,250"


def test_fmt_time_handles_negative():
    assert captions.fmt_time(-5) == "00:00:00,000"


def test_group_cues_produces_readable_phrases():
    """One word per subtitle is unreadable; group into short phrases."""
    cues = captions.simple_timestamps("one two three four five six", 12.0)
    grouped = captions.group_cues(cues, max_words=3)
    assert len(grouped) == 2
    assert grouped[0][2] == "one two three"
    assert grouped[0][1] <= grouped[1][0]


def test_to_srt_writes_valid_numbered_blocks(tmp_path):
    path = tmp_path / "out.srt"
    cues = captions.group_cues(captions.simple_timestamps("a b c d", 8.0))
    captions.to_srt(cues, path)
    text = path.read_text(encoding="utf-8-sig")
    assert text.startswith("1\n")
    assert text.count("-->") == len(cues)


def test_to_srt_creates_parent_directory(tmp_path):
    path = tmp_path / "deep" / "nested" / "out.srt"
    captions.to_srt(captions.simple_timestamps("a b", 4.0), path)
    assert path.is_file()


def test_to_srt_handles_non_positive_duration_cue(tmp_path):
    """A zero-length cue would produce an invalid SRT timestamp pair."""
    path = tmp_path / "zero.srt"
    captions.to_srt([(1.0, 1.0, "x")], path)
    text = path.read_text(encoding="utf-8-sig")
    start, end = text.splitlines()[1].split(" --> ")
    assert start != end


def test_get_scene_keywords_drops_filler_words():
    """The old version returned the first three words, e.g. 'H' or 'So I was'."""
    kw = script.get_scene_keywords("So I was walking through the abandoned lighthouse")
    assert kw
    assert "So" not in kw
    assert len(kw.split()) <= 3


def test_get_scene_keywords_returns_empty_for_useless_input():
    """A one-character scene yields no usable search term.

    The pipeline skips empty keywords, which is better than rendering the word
    "H" across the screen.
    """
    assert script.get_scene_keywords("") == ""
    assert script.get_scene_keywords("H") == ""