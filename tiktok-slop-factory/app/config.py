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


# Text-generation backends. "gemini" is the default and the only one that
# needs an API key; "local" reads its text from a JSON file and synthesizes
# narration with Edge TTS, which is keyless.
BACKENDS = ("gemini", "local")
DEFAULT_BACKEND = "gemini"


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


def get_gemini_text_model() -> str:
    _load_dotenv_once()
    return os.getenv("GEMINI_TEXT_MODEL", "gemini-3.8-flash").strip()


def get_gemini_tts_model() -> str:
    _load_dotenv_once()
    return os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts").strip()


def get_tts_voice() -> str:
    _load_dotenv_once()
    return os.getenv("TTS_VOICE", "Kore").strip()


def normalize_backend(name: str | None) -> str:
    """Return a validated backend name, or raise ConfigError.

    An unset value falls back to ``DEFAULT_BACKEND`` so the Gemini path is
    what runs when nothing is configured. Anything else is a typo the user
    needs to see, not a silent fallback.
    """
    raw = (name or "").strip().lower()
    if not raw:
        return DEFAULT_BACKEND
    if raw not in BACKENDS:
        raise ConfigError(
            f"Unknown backend {name!r}. Choose one of: {', '.join(BACKENDS)}."
        )
    return raw


def get_backend() -> str:
    """The backend named by ``TEXT_BACKEND``, defaulting to Gemini."""
    _load_dotenv_once()
    return normalize_backend(os.getenv("TEXT_BACKEND"))


def get_local_text_path() -> Path:
    """Path to the local backend's text file.

    Defaults to ``<project root>/local_text.json`` so the value does not
    depend on the current working directory, and is read per call rather than
    frozen at import time.
    """
    _load_dotenv_once()
    raw = os.getenv("TEXT_PROVIDER_JSON", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (get_project_root() / "local_text.json").resolve()


def get_edge_tts_voice() -> str:
    """Edge TTS voice for the local backend.

    Deliberately a separate variable from ``TTS_VOICE``: that names a Gemini
    prebuilt voice (``Kore``), which Edge TTS would reject outright. One
    variable per backend keeps either setting unambiguous.
    """
    _load_dotenv_once()
    return os.getenv("EDGE_TTS_VOICE", "en-GB-SoniaNeural").strip()


def get_edge_tts_rate() -> str:
    """Edge TTS speaking rate for the local backend."""
    _load_dotenv_once()
    return os.getenv("EDGE_TTS_RATE", "+8%").strip()


def get_output_dir() -> Path:
    return get_project_root() / "output"


def get_ffmpeg_path() -> str:
    _load_dotenv_once()
    return os.getenv("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg"


def get_ffprobe_path() -> str:
    _load_dotenv_once()
    return os.getenv("FFPROBE_PATH", "ffprobe").strip() or "ffprobe"
