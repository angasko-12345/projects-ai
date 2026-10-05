"""
Run the pipeline with the local text backend (no GEMINI_API_KEY needed).

    python make_videos_local.py --count 1

Swaps ``app.gemini`` for ``app.local_text`` BEFORE the pipeline imports it, so
``app/`` itself is untouched and the Gemini path still works normally.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import local_text  # noqa: E402
import app.gemini as _gemini  # noqa: E402

# Every name pipeline.py actually calls on the gemini module.
for _name in (
    "generate_ideas",
    "generate_script",
    "script_to_text",
    "generate_caption",
    "generate_tts",
    "get_gemini_api_key",
    "GeminiError",
    "AudioBlob",
):
    setattr(_gemini, _name, getattr(local_text, _name))

from make_videos import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
