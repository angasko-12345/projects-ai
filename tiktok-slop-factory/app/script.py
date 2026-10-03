"""
Script text helpers: scene splitting and Pexels search keywords.
"""
import re
from typing import Any, Dict, List

FIELDS = ("hook", "story", "twist", "ending", "cta")

# Words that make for useless Pexels searches.
_STOPWORDS = frozenset(
    """a an the and or but of to in on at for with from that this it he she they
    them his her their we you i my your our is are was were be been being do does
    did not no so if then as by into over under out up down about after before
    when where which who whom what how why very just too also would could should
    will can may might must shall here there now new one two three like said says
    get got go went come came make made take took see saw know knew think thought
    told tell told story thing time day night man woman people person""".split()
)


def script_text(script: Dict[str, Any]) -> str:
    """Join the narration fields into one block."""
    parts = [str(script.get(f, "") or "").strip() for f in FIELDS]
    return " ".join(p for p in parts if p).strip()


def split_into_scenes(script: Dict[str, Any], num_scenes: int = 3) -> List[str]:
    """Split narration into at most ``num_scenes`` sentence groups.

    Bug fixed: when the script had fewer sentences than requested scenes, the
    old code appended copies of the last sentence, so the same footage
    keywords were produced repeatedly and Pexels quota was burned on duplicate
    searches. It now returns only the sentences that actually exist.
    """
    text = script_text(script)
    if not text:
        return []
    num_scenes = max(1, int(num_scenes))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return []
    return sentences[:num_scenes]


def get_scene_keywords(scene_text: str, max_words: int = 3) -> str:
    """Pick concrete search terms from a scene for Pexels.

    Bug fixed: this used to return the first three whitespace-separated words
    verbatim, which produced queries like ``H`` or ``So I was`` and matched
    useless stock footage. It now drops stopwords and keeps the longest words,
    which are the concrete visual nouns.
    """
    words = re.findall(r"[A-Za-z][A-Za-z'-]+", scene_text or "")
    if not words:
        return ""

    candidates = [w for w in words if w.lower() not in _STOPWORDS and len(w) > 3]
    if not candidates:
        candidates = [w for w in words if len(w) > 2]
    if not candidates:
        return ""

    # Longer words are usually the visually specific ones; keep a stable order.
    seen: List[str] = []
    for w in sorted(candidates, key=len, reverse=True):
        if w.lower() not in {s.lower() for s in seen}:
            seen.append(w)
        if len(seen) >= max(1, int(max_words)):
            break
    return " ".join(seen)
