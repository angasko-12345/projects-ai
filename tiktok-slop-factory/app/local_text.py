"""
Keyless text backend for tiktok-slop-factory.

WHY THIS EXISTS
---------------
The default backend's only external dependency is the Gemini API: story ideas,
the script, the caption, and the narration audio. Four of those five calls are
plain text generation, which needs no key at all -- only ``generate_tts`` needs
a real speech API. This module supplies the text from a local JSON file and
synthesizes audio with Microsoft Edge TTS, which is free and keyless.

So the split is:
  ideas / script / caption  -> TEXT_PROVIDER_JSON (local_text.json)
  narration audio           -> edge-tts  (free, no key)

Select it with:

    python make_videos.py --backend local --count 1

The contract is identical to ``app.gemini``: ``generate_ideas`` -> list[str],
``generate_script`` -> dict with hook/story/twist/ending/cta/title/hashtags,
``generate_tts`` -> AudioBlob(data, mime_type). The pipeline cannot tell the
difference. Every setting is read per call, never frozen at import time, so a
test or a caller can change it before use.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from .config import (
    get_edge_tts_rate,
    get_edge_tts_voice,
    get_local_text_path,
)
from .gemini import AudioBlob, GeminiError, SCRIPT_FIELDS, script_to_text  # re-export

__all__ = [
    "AudioBlob",
    "GeminiError",
    "check_setup",
    "generate_caption",
    "generate_ideas",
    "generate_script",
    "generate_tts",
    "script_to_text",
]


def check_setup() -> List[str]:
    """Return human-readable setup problems, empty when this backend can run.

    Runs without touching the network, so ``--dry-run`` stays meaningful
    offline.
    """
    problems: List[str] = []
    path = get_local_text_path()
    if not path.is_file():
        problems.append(
            f"Local text file not found: {path}. Point TEXT_PROVIDER_JSON at a "
            "JSON file containing a non-empty 'scripts' array, or run with "
            "--backend gemini."
        )
    else:
        try:
            if not _scripts_from(path):
                problems.append(f"Local text file has no usable scripts: {path}.")
        except GeminiError as e:
            problems.append(f"Local text file is unusable: {path}: {e}")

    try:
        import edge_tts  # noqa: F401
    except ImportError:
        problems.append(
            "edge-tts is not installed, and the local backend needs it for "
            "narration. Install it with: pip install -r requirements.txt"
        )
    return problems


def _scripts_from(path: Path) -> List[Dict[str, Any]]:
    """Load and validate the text provider file, with an actionable error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise GeminiError(
            f"not valid JSON ({e}). Fix the JSON syntax, or run with "
            "--backend gemini instead."
        ) from None
    except OSError as e:
        raise GeminiError(f"could not be read ({e}).") from None

    if not isinstance(data, dict):
        raise GeminiError("top level must be a JSON object with a 'scripts' array.")

    scripts = data.get("scripts")
    if not isinstance(scripts, list):
        raise GeminiError(
            "missing a 'scripts' array at the top level. Expected "
            '{"scripts": [{"idea": ..., "hook": ..., "story": ..., "twist": '
            '..., "ending": ..., "cta": ..., "title": ..., "hashtags": [...]}]}.'
        )
    if not scripts:
        raise GeminiError(
            "the 'scripts' array is empty. Add at least one script with an "
            "'idea' and narration text."
        )
    bad = [i for i, s in enumerate(scripts) if not isinstance(s, dict)]
    if bad:
        raise GeminiError(
            f"scripts[{bad[0]}] must be a JSON object, not a string or array "
            f"({len(bad)} such entries)."
        )
    return scripts


def _scripts() -> List[Dict[str, Any]]:
    path = get_local_text_path()
    if not path.is_file():
        raise GeminiError(
            f"Local text file not found: {path}. Point TEXT_PROVIDER_JSON at a "
            "JSON file containing a 'scripts' array, or run with "
            "--backend gemini."
        )
    return _scripts_from(path)


def generate_ideas(count: int = 10) -> List[str]:
    """Return ideas from the local file (repeating if fewer than ``count``)."""
    ideas = [str(s.get("idea", "")).strip() for s in _scripts()]
    ideas = [i for i in ideas if i]
    if not ideas:
        raise GeminiError(
            "No ideas in the local text file: every 'scripts' entry needs a "
            "non-empty 'idea' string."
        )
    count = max(1, int(count))
    out = ideas[:count]
    while len(out) < count:  # never hand back a short batch
        out.extend(ideas[: count - len(out)])
    return out


def generate_script(idea: str) -> Dict[str, Any]:
    """Return the stored script matching ``idea`` (or the first one)."""
    scripts = _scripts()
    match = next((s for s in scripts if s.get("idea") == idea), None) or scripts[0]
    out: Dict[str, Any] = {
        field: str(match.get(field, "") or "").strip() for field in SCRIPT_FIELDS
    }
    out["title"] = str(match.get("title", "") or "").strip() or "Fictional story"
    tags = match.get("hashtags") or []
    out["hashtags"] = (
        [str(t).strip() for t in tags if str(t).strip()]
        if isinstance(tags, list)
        else []
    )
    if not any(out[f] for f in SCRIPT_FIELDS):
        raise GeminiError(
            "Local script has no narration text: fill in at least one of "
            f"{', '.join(SCRIPT_FIELDS)} for this idea."
        )
    return out


def generate_caption(script: Dict[str, Any]) -> str:
    """The narration doubles as the on-post caption text (unchanged behaviour)."""
    return script_to_text(script)


def generate_tts(script: Dict[str, Any]) -> AudioBlob:
    """Synthesize narration with Edge TTS. Returns MP3 audio for the pipeline."""
    try:
        import edge_tts
    except ImportError:
        raise GeminiError(
            "edge-tts is not installed, and the local backend needs it for "
            "narration. Install it with: pip install -r requirements.txt"
        ) from None

    text = script_to_text(script)
    if not text:
        raise GeminiError("Refusing to synthesize empty narration")

    voice = get_edge_tts_voice()
    rate = get_edge_tts_rate()

    async def _run(dest: Path) -> None:
        comm = edge_tts.Communicate(text, voice, rate=rate)
        await comm.save(str(dest))

    # Named temp file outside the project: edge_tts writes it directly, and a
    # fixed name in the repo root would leave litter behind on a crash.
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        out_path = Path(tmp.name)
    try:
        try:
            asyncio.run(_run(out_path))
        except Exception as e:
            raise GeminiError(
                f"Edge TTS failed for voice {voice!r} (no network, or the voice "
                f"name is not one Edge offers): {e}"
            ) from None
        data = out_path.read_bytes()
    finally:
        out_path.unlink(missing_ok=True)

    if not data:
        raise GeminiError(
            "Edge TTS produced an empty audio file. Check EDGE_TTS_VOICE and "
            "the network connection."
        )
    return AudioBlob(data, "audio/mpeg")