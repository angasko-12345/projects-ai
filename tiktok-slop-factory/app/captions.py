"""
Subtitle (SRT) generation.

Two bugs are fixed here:

* ``fmt_time`` computed milliseconds as
  ``round((t - int(t)) * 1000)``, which can round up to exactly ``1000`` and
  emit an invalid timestamp such as ``00:00:01,1000``. Milliseconds must be
  derived from a single rounded total.
* Caption timing used to divide the narration duration evenly across words.
  Real speech does not spend equal time on every word, so cues drifted away
  from the audio. ``speech_aware_timestamps`` now shares out the *measured*
  narration duration using a text-only speech-rate heuristic.

Timing model (heuristic, NOT speech alignment)
----------------------------------------------
Each word gets a relative weight:

* ``BASE_WORD_WEIGHT`` for simply being spoken, plus
* ``SYLLABLE_WEIGHT`` for every estimated syllable beyond the first, plus
* a pause weight taken from the punctuation that ends the word.

The weights are normalized against the real probed narration duration, so the
cues always sum to the audio length. ``MAX_CUE_SEC`` is applied wherever the
budget allows it; ``MIN_CUE_SEC`` is a hard floor, and a narration too short to
give every word a readable cue fails loudly instead of emitting a strobe-like
subtitle track.

This is an approximation of speech rate, not a claim of linguistically exact
timing, and no single English speaking speed is baked in: every duration comes
from the narration that was actually rendered, and the heuristic only decides
how that known total is distributed. Its purpose is to reduce caption drift.
Deterministic, dependency-free, pure Python.
"""
import re
from pathlib import Path
from typing import List, Sequence, Tuple

Cue = Tuple[float, float, str]

# Relative cost of one word, before punctuation.
BASE_WORD_WEIGHT = 1.0
# Extra weight per syllable beyond the first: longer words take longer to say.
SYLLABLE_WEIGHT = 0.35

# Local pause budget granted to the word carrying the punctuation.
PAUSE_WEIGHTS = {
    ",": 0.50,
    ";": 0.70,
    ":": 0.70,
    ".": 0.90,
    "?": 0.90,
    "!": 0.90,
    "…": 0.90,  # ellipsis counts as one pause, not one per character
}

# Readability bounds for a single cue, in seconds. Short-form captions read
# badly below ~0.4s; a cue held much past ~7s stops being a caption.
MIN_CUE_SEC = 0.40
MAX_CUE_SEC = 7.00

_EPS = 1e-9
_VOWEL_GROUP = re.compile(r"[aeiouy]+")
_TRAILING_PAUSE = re.compile(r"(\.{2,}|…|[,;:!?.]+)$")
_SENTENCE_END = re.compile(r"[.!?…][\"')\]’”]*$")


class CaptionTimingError(ValueError):
    """Raised when a narration cannot produce readable cues."""


def estimate_syllables(word: str) -> int:
    """Cheap deterministic syllable estimate: vowel groups, minimum one."""
    letters = re.sub(r"[^a-z]", "", word.lower())
    if not letters:
        return 1
    groups = len(_VOWEL_GROUP.findall(letters))
    # A trailing silent 'e' is not its own syllable, except in "-le"/"-ee".
    if (
        groups > 1
        and letters.endswith("e")
        and not letters.endswith(("le", "ee", "ye", "oe"))
    ):
        groups -= 1
    return max(1, groups)


def _trailing_pause(word: str) -> float:
    """Pause weight for the punctuation closing ``word`` (0.0 when absent)."""
    trimmed = word.rstrip("\"')]}’”")  # closing quotes are not punctuation
    match = _TRAILING_PAUSE.search(trimmed)
    if not match:
        return 0.0
    mark = match.group(1)
    if mark[0] in ".…":
        return PAUSE_WEIGHTS["…"]
    return max(PAUSE_WEIGHTS[char] for char in mark)


def word_weight(word: str) -> float:
    """Relative speech-time weight of a single word, punctuation included."""
    syllables = estimate_syllables(word)
    return (
        BASE_WORD_WEIGHT
        + SYLLABLE_WEIGHT * (syllables - 1)
        + _trailing_pause(word)
    )


def _solve_scale(
    weights: Sequence[float], total_sec: float, lo: float = 0.0, hi: float = 1.0
) -> float:
    """Bisect the scale that makes the clamped weights sum to ``total_sec``.

    ``share(k) = clamp(k * w_i, MIN_CUE_SEC, MAX_CUE_SEC)`` is non-decreasing in
    ``k``, so the sum is too and a plain bisection converges. A bisection rather
    than an iterative clamp loop matters: re-scaling after a clamp sends the
    remaining cues below the floor and the budget walks off a cliff.
    """
    for _ in range(80):
        mid = (lo + hi) / 2.0
        total = 0.0
        for w in weights:
            share = mid * w
            total += min(max(share, MIN_CUE_SEC), MAX_CUE_SEC)
        if total < total_sec:
            lo = mid
        else:
            hi = mid
    return hi


def _allocate(
    weights: Sequence[float], total_sec: float, min_cue_sec: float = MIN_CUE_SEC
) -> List[float]:
    """Turn relative weights into durations summing to ``total_sec``.

    ``MIN_CUE_SEC`` is a hard floor: a narration too short to give every word a
    readable cue raises ``CaptionTimingError`` rather than emitting a strobe.
    ``MAX_CUE_SEC`` caps every cue, so a long narration over a couple of words
    can never become one absurd caption. The cap can only be honoured if the
    narration is no longer than ``count * MAX_CUE_SEC``; past that the audio
    outlasts the words, so the track is capped and simply ends early instead of
    inventing captions over the trailing silence. The probed narration duration
    stays authoritative everywhere the model applies.
    """
    count = len(weights)
    if count == 0:
        return []

    floor_total = count * MIN_CUE_SEC
    if total_sec < floor_total - _EPS:
        raise CaptionTimingError(
            f"narration of {total_sec:.2f}s cannot be captioned readably: "
            f"{count} words need at least {floor_total:.2f}s "
            f"({count} x {MIN_CUE_SEC:.2f}s minimum cue)"
        )

    cap_total = count * MAX_CUE_SEC
    if total_sec > cap_total:
        return [MAX_CUE_SEC] * count

    scale = _solve_scale(weights, total_sec, lo=0.0, hi=total_sec / max(weights))
    durations = [
        min(max(scale * w, MIN_CUE_SEC), MAX_CUE_SEC) for w in weights
    ]
    # Absorb the bisection residual in the cue with the most headroom, so the
    # track still covers the narration without breaching either bound.
    residual = total_sec - sum(durations)
    if residual:
        index = max(range(count), key=lambda i: MAX_CUE_SEC - durations[i])
        durations[index] += residual
    return durations


def speech_aware_timestamps(
    text: str, duration_sec: float, max_words: int = 1
) -> List[Cue]:
    """One cue per word, weighted by speech rate, normalized to the audio.

    ``max_words`` declares how many words will share one on-screen caption.
    The weight budget is resolved per *caption*, not per word, so ``MIN_CUE_SEC``
    describes what a viewer actually sees; words inside a caption then share its
    time in proportion to their own weight. ``max_words=1`` (the default) times
    single words and keeps the historical one-cue-per-word output.

    Raises ``CaptionTimingError`` when ``duration_sec`` is too short to give
    every caption a readable hold.
    """
    words = re.findall(r"\S+", text or "")
    if duration_sec <= 0:
        duration_sec = 45.0
    if not words:
        return []

    phrases = _group_words(words, max_words)
    phrase_weights = [sum(word_weight(w) for w in phrase) for phrase in phrases]
    spans = _allocate(phrase_weights, float(duration_sec))

    cues: List[Cue] = []
    start = 0.0
    for phrase, span in zip(phrases, spans):
        weights = [word_weight(w) for w in phrase]
        word_sum = sum(weights)
        cursor = start
        for word, weight in zip(phrase, weights):
            share = span * weight / word_sum
            cues.append((cursor, cursor + share, word))
            cursor += share
        start = cursor
    return cues


def simple_timestamps(text: str, duration_sec: float) -> List[Cue]:
    """Backwards-compatible name for ``speech_aware_timestamps``."""
    return speech_aware_timestamps(text, duration_sec)


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.rstrip()))


def _group_words(words: List[str], max_words: int) -> List[List[str]]:
    """Split words into caption phrases, never merging across a sentence end.

    Timing and grouping must agree on where a caption ends, so both go through
    this one function: the pause at a sentence boundary is real time, and
    swallowing it into the previous cue hides it.
    """
    max_words = max(1, int(max_words))
    phrases: List[List[str]] = []
    for word in words:
        if phrases:
            previous = phrases[-1]
            if len(previous) < max_words and not _ends_sentence(previous[-1]):
                previous.append(word)
                continue
        phrases.append([word])
    return phrases


def group_cues(cues: List[Cue], max_words: int = 3) -> List[Cue]:
    """Merge single-word cues into short readable phrases.

    One word per subtitle is unreadable on a phone. TikTok-style captions use
    a few words at a time. Grouping never merges across a sentence boundary.
    """
    if not cues:
        return []
    grouped: List[Cue] = []
    index = 0
    for phrase in _group_words([text for _, _, text in cues], max_words):
        start = cues[index][0]
        index += len(phrase)
        grouped.append((start, cues[index - 1][1], " ".join(phrase)))
    return grouped


def to_srt(timestamps: List[Cue], path: Path) -> Path:
    """Write cues to an SRT file as UTF-8 (with BOM, which libass accepts)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    for idx, (start, end, text) in enumerate(timestamps, 1):
        if end <= start:
            end = start + 0.001
        lines.append(str(idx))
        lines.append(f"{fmt_time(start)} --> {fmt_time(end)}")
        lines.append(text)
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8-sig")
    return path


def fmt_time(t: float) -> str:
    """Format seconds as an SRT timestamp.

    Rounding happens once, on the total milliseconds, so the millisecond field
    can never overflow past 999.
    """
    t = max(0.0, float(t))
    total_ms = int(round(t * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    return f"{total_s // 3600:02d}:{(total_s % 3600) // 60:02d}:{total_s % 60:02d},{ms:03d}"
