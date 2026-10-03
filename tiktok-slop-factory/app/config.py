"""
Config loading for TikTok Slop Factory.

All settings come from environment variables, optionally seeded from a
``.env`` file that sits next to this package. Nothing here reads or writes
secrets to disk.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is optional
    load_dotenv = None


class ConfigError(Exception):
    pass


def get_project_root() -> Path:
    return Path(__file__).parent.parent


def _load_dotenv_once() -> None:
    """Load ``<project root>/.env`` exactly once.

    ``load_dotenv()`` with no argument walks up from the *current working
    directory*, so running the CLI from anywhere else silently skipped the
    project's own ``.env``. Point it at an explicit path instead.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    if load_dotenv is None:
        return
    env_file = get_project_root() / ".env"
    if env_file.is_file():
        load_dotenv(dotenv_path=env_file)


_DOTENV_LOADED = False


def _require_key(name: str) -> str:
    _load_dotenv_once()
    key = (os.getenv(name) or "").strip()
    if not key:
        raise ConfigError(f"{name} not set. Copy .env.example to .env and fill it in.")
    return key


def get_gemini_api_key() -> str:
    return _require_key("GEMINI_API_KEY")


def get_pexels_api_key() -> str:
    return _require_key("PEXELS_API_KEY")


def get_gemini_text_model() -> str:
    _load_dotenv_once()
    return os.getenv("GEMINI_TEXT_MODEL", "gemini-3.8-flash").strip()


def get_gemini_tts_model() -> str:
    _load_dotenv_once()
    return os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts").strip()


def get_tts_voice() -> str:
    _load_dotenv_once()
    return os.getenv("TTS_VOICE", "Kore").strip()


def get_output_dir() -> Path:
    return get_project_root() / "output"


def get_ffmpeg_path() -> str:
    _load_dotenv_once()
    return os.getenv("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg"


def get_ffprobe_path() -> str:
    _load_dotenv_once()
    return os.getenv("FFPROBE_PATH", "ffprobe").strip() or "ffprobe"
