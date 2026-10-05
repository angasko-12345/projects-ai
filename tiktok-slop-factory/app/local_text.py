"""
Offline text provider for tiktok-slop-factory.

WHY THIS EXISTS
---------------
The pipeline's only external dependency is the Gemini API: story ideas, the
script, the caption, and the narration audio. There is no GEMINI_API_KEY on this
machine, so the pipeline has never produced a video.

Four of those five calls are plain text generation, which needs no key at all --
only ``generate_tts`` needs a real speech API. This module supplies the text from
a local ``TEXT_PROVIDER_JSON`` file and synthesizes audio with Microsoft Edge
TTS, which is free and keyless.

So the split is:
  ideas / script / caption  -> TEXT_PROVIDER_JSON (written by the agent)
  narration audio           -> edge-tts  (free, no key)

Nothing in ``app/`` is modified. Point the pipeline at this module with:

    TEXT_BACKEND=local python make_videos.py --count 1

The contract is unchanged: ``generate_ideas`` -> list[str], ``generate_script``
-> dict with hook/story/twist/ending/cta/title/hashtags, ``generate_tts`` ->
AudioBlob(data, mime_type). The pipeline cannot tell the difference.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from .gemini import AudioBlob, GeminiError, SCRIPT_FIELDS, script_to_text  # re-export

TEXT_PROVIDER_JSON = Path(
    os.environ.get("TEXT_PROVIDER_JSON", "local_text.json")
).resolve()


def get_gemini_api_key() -> str:
    """Sentinel so ``check_dependencies()`` passes without a real key.

    The pipeline calls this only to verify a key exists. Nothing in the local
    path ever sends it anywhere -- it is not a credential.
    """
    return "local-text-backend (no key required)"

# Edge voice: en-GB-SoniaNeural (female, British). Matches the README's Kore
# default in spirit -- a natural, non-robotic narrator.
EDGE_VOICE = os.environ.get("TTS_VOICE", "en-GB-SoniaNeural")
EDGE_RATE = os.environ.get("TTS_RATE", "+8%")


def _load() -> Dict[str, Any]:
    if not TEXT_PROVIDER_JSON.exists():
        raise GeminiError(
            f"Text provider file not found: {TEXT_PROVIDER_JSON}. "
            "The agent must write ideas + scripts here before running the pipeline."
        )
    try:
        return json.loads(TEXT_PROVIDER_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        raise GeminiError(f"Text provider file is not valid JSON: {e}") from None


def _scripts() -> List[Dict[str, Any]]:
    data = _load()
    scripts = data.get("scripts")
    if not isinstance(scripts, list) or not scripts:
        raise GeminiError("Text provider file contains no 'scripts' array")
    return scripts


def generate_ideas(count: int = 10) -> List[str]:
    """Return ideas from the local file (repeating if fewer than ``count``)."""
    ideas: List[str] = [_s.get("idea", "") for _s in _scripts() if _s.get("idea")]
    if not ideas:
        raise GeminiError("No ideas in text provider file")
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
    out["hashtags"] = [str(t).strip() for t in tags if str(t).strip()] if isinstance(tags, list) else []
    if not any(out[f] for f in SCRIPT_FIELDS):
        raise GeminiError("Local script has no narration text")
    return out


def generate_caption(script: Dict[str, Any]) -> str:
    """The narration doubles as the on-post caption text (unchanged behaviour)."""
    return script_to_text(script)


def generate_tts(script: Dict[str, Any]) -> AudioBlob:
    """Synthesize narration with Edge TTS. Returns WAV, matching Gemini's mime."""
    import edge_tts

    text = script_to_text(script)
    if not text:
        raise GeminiError("Refusing to synthesize empty narration")

    out_path = TEXT_PROVIDER_JSON.parent / "_narration.mp3"

    async def _run() -> None:
        comm = edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE)
        await comm.save(str(out_path))

    try:
        asyncio.run(_run())
    except Exception as e:
        raise GeminiError(f"Edge TTS failed: {e}") from None

    data = out_path.read_bytes()
    if not data:
        raise GeminiError("Edge TTS produced an empty audio file")
    out_path.unlink(missing_ok=True)
    # Edge returns MP3; AudioBlob.extension keys off the mime type.
    return AudioBlob(data, "audio/mpeg")
