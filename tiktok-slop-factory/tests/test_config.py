"""Config loading."""
import pytest

from app import config


def test_config_gets_keys_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert config.get_gemini_api_key() == "g"


def test_config_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(config.ConfigError):
        config.get_gemini_api_key()


def test_no_pexels_key_is_required(monkeypatch):
    """Pexels was removed; only the Gemini key may be required."""
    assert not hasattr(config, "get_pexels_api_key")


def test_config_rejects_whitespace_only_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    with pytest.raises(config.ConfigError):
        config.get_gemini_api_key()


def test_config_strips_key_whitespace(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "  key  ")
    assert config.get_gemini_api_key() == "key"


def test_loads_dotenv_from_project_root(monkeypatch, tmp_path):
    """load_dotenv() with no args searched the CWD, not the project root."""
    monkeypatch.setattr(config, "get_project_root", lambda: tmp_path)
    monkeypatch.delenv("TTS_VOICE", raising=False)
    (tmp_path / ".env").write_text("TTS_VOICE=FromProjectEnv\n", encoding="utf-8")

    config._DOTENV_LOADED = False
    try:
        config._load_dotenv_once()
        assert config.get_tts_voice() == "FromProjectEnv"
    finally:
        config._DOTENV_LOADED = True
        monkeypatch.delenv("TTS_VOICE", raising=False)


def test_ffmpeg_and_ffprobe_defaults(monkeypatch):
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.delenv("FFPROBE_PATH", raising=False)
    assert config.get_ffmpeg_path() == "ffmpeg"
    assert config.get_ffprobe_path() == "ffprobe"


def test_ffmpeg_path_override(monkeypatch):
    monkeypatch.setenv("FFMPEG_PATH", r"C:\tools\ffmpeg.exe")
    assert config.get_ffmpeg_path() == r"C:\tools\ffmpeg.exe"