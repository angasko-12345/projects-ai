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
    ts = captions.simple_timestamps("one two three", 6.0)
    assert len(ts) == 3
    assert ts[0][0] == 0.0
    assert ts[-1][1] == pytest.approx(6.0)


def test_timestamps_empty_text_returns_no_cues():
    """The old code emitted one blank cue, rendering an empty subtitle box."""
    assert captions.simple_timestamps("   ", 10.0) == []


# --- speech-aware timing -------------------------------------------------
#
# The narration duration is authoritative: cues must cover it exactly. These
# tests are pure functions of (text, duration) and need no network, TTS, or
# FFmpeg.

_NARRATION = (
    "The lighthouse keeper heard his name, after eleven silent winters. "
    "He counted every wave, and every wave answered."
)


def _spans(cues):
    return [(end - start, word) for start, end, word in cues]


def _durations(cues):
    """Map word -> cue duration, so a repeated word cannot mask a comparison."""
    return {word: duration for duration, word in _spans(cues)}


def test_comma_cue_gets_more_time_than_mid_sentence_cue():
    """Punctuation buys local pause budget, not just syllables."""
    plain = _durations(captions.speech_aware_timestamps("he counted waves now", 12.0))
    punctuated = _durations(
        captions.speech_aware_timestamps("he counted waves, now", 12.0))
    assert punctuated["waves,"] > plain["waves"] * 1.1


def test_sentence_end_gets_more_time_than_comma():
    stops = _durations(captions.speech_aware_timestamps(
        "waves, waves. waves! waves? waves; waves: waves...", 24.0))
    assert stops["waves."] > stops["waves,"]
    assert stops["waves;"] > stops["waves,"]
    assert stops["waves..."] == pytest.approx(stops["waves."])  # one pause


def test_longer_word_gets_more_weight_than_short_word():
    assert captions.word_weight("extraordinarily") > captions.word_weight("cat")
    assert captions.estimate_syllables("extraordinarily") > (
        captions.estimate_syllables("cat"))


def test_weights_match_actual_cue_durations():
    """The normalized cue durations must follow the weights, not just differ."""
    mid = _durations(captions.speech_aware_timestamps(
        "hello world goodbye world", 12.0))
    stopped = _durations(captions.speech_aware_timestamps(
        "hello world goodbye world.", 12.0))
    assert stopped["world."] > mid["world"]


def test_timestamps_cover_duration_without_gaps_or_overlap():
    duration = 17.5
    cues = captions.speech_aware_timestamps(_NARRATION, duration)
    assert cues[0][0] == 0.0
    for (_, prev_end, _), (start, _, _) in zip(cues, cues[1:]):
        assert prev_end == pytest.approx(start, abs=1e-9)
    assert cues[-1][1] == pytest.approx(duration, abs=1e-9)
    assert sum(end - start for start, end, _ in cues) == pytest.approx(
        duration, abs=1e-9)


def test_timestamps_are_deterministic():
    first = captions.speech_aware_timestamps(_NARRATION, 13.25)
    second = captions.speech_aware_timestamps(_NARRATION, 13.25)
    assert first == second


def test_short_audio_long_text_raises_instead_of_strobing():
    """A 1s narration over 40 words cannot be readable; it must fail clearly."""
    text = " ".join(["word"] * 40)
    with pytest.raises(captions.CaptionTimingError) as err:
        captions.speech_aware_timestamps(text, 1.0)
    message = str(err.value)
    assert "40" in message and "minimum cue" in message


def test_no_cue_is_shorter_than_minimum_when_it_succeeds():
    text = " ".join(["word"] * 12)
    cues = captions.speech_aware_timestamps(text, 12 * captions.MIN_CUE_SEC)
    assert min(end - start for start, end, _ in cues) == pytest.approx(
        captions.MIN_CUE_SEC, abs=1e-6)


def test_grouped_captions_floor_allows_dense_narration():
    """The floor is per caption, so 3-word captions read at 1/3 the cost."""
    text = " ".join(["word"] * 12)
    # Per-word the floor would demand 12 * 0.4s; per caption only 4 * 0.4s.
    cues = captions.speech_aware_timestamps(text, 2.0, max_words=3)
    phrases = captions.group_cues(cues, max_words=3)
    assert len(phrases) == 4
    assert min(end - start for start, end, _ in phrases) >= (
        captions.MIN_CUE_SEC - 1e-6)


def test_grouped_timing_still_covers_narration_exactly():
    duration = 21.0
    text = _NARRATION + " Nobody believed him, so he opened it?"
    cues = captions.speech_aware_timestamps(text, duration, max_words=3)
    assert cues[0][0] == 0.0
    for (_, prev_end, _), (start, _, _) in zip(cues, cues[1:]):
        assert prev_end == pytest.approx(start, abs=1e-9)
    assert cues[-1][1] == pytest.approx(duration, abs=1e-9)


def test_long_narration_few_words_stays_bounded():
    """120s over three words must not become one 40s caption."""
    cues = captions.speech_aware_timestamps("one two three", 120.0)
    assert max(end - start for start, end, _ in cues) <= captions.MAX_CUE_SEC


def test_max_cue_cap_still_covers_the_whole_narration():
    """Clamping must not shorten or lengthen the track."""
    text = "quick brown fox jumps over lazy dogs again and again tonight"
    duration = captions.MAX_CUE_SEC * len(text.split())
    cues = captions.speech_aware_timestamps(text, duration)
    assert max(end - start for start, end, _ in cues) <= (
        captions.MAX_CUE_SEC + 1e-9)
    assert sum(end - start for start, end, _ in cues) == pytest.approx(
        duration, abs=1e-9)


def test_cues_end_before_narration_when_words_cannot_fill_it():
    """A capped track must not pad the trailing silence with invented cues."""
    cues = captions.speech_aware_timestamps("one two three", 120.0)
    assert cues[-1][1] == pytest.approx(3 * captions.MAX_CUE_SEC, abs=1e-9)


def test_group_cues_does_not_merge_across_sentence_boundary():
    cues = captions.speech_aware_timestamps("he left. she stayed", 10.0)
    grouped = captions.group_cues(cues, max_words=3)
    assert [text for _, _, text in grouped] == ["he left.", "she stayed"]
    assert grouped[1][0] >= grouped[0][1]


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