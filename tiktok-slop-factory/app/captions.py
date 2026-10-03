"""
Subtitle (SRT) generation.

Bug fixed: ``fmt_time`` computed milliseconds as
``round((t - int(t)) * 1000)``, which can round up to exactly ``1000`` and
emit an invalid timestamp such as ``00:00:01,1000``. Milliseconds must be
derived from a single rounded total.
"""
import re
from pathlib import Path
from typing import List, Tuple

Cue = Tuple[float, float, str]


def simple_timestamps(text: str, duration_sec: float) -> List[Cue]:
    """Split text into one cue per word, spread evenly across the duration."""
    words = re.findall(r"\S+", text or "")
    if duration_sec <= 0:
        duration_sec = 45.0
    if not words:
        return []

    per_word = duration_sec / len(words)
    return [(i * per_word, (i + 1) * per_word, w) for i, w in enumerate(words)]


def group_cues(cues: List[Cue], max_words: int = 3) -> List[Cue]:
    """Merge single-word cues into short readable phrases.

    One word per subtitle is unreadable on a phone. TikTok-style captions use
    a few words at a time.
    """
    if not cues:
        return []
    max_words = max(1, int(max_words))
    grouped: List[Cue] = []
    for start, end, text in cues:
        if grouped and len(grouped[-1][2].split()) < max_words:
            prev_start, _, prev_text = grouped[-1]
            grouped[-1] = (prev_start, end, f"{prev_text} {text}".strip())
        else:
            grouped.append((start, end, text))
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
