"""Backend selection: the local seam, the untouched Gemini path, and the CLI.

Proves the five things that make `--backend` a real switch rather than a
half-wired workaround:

* selecting local actually selects local, and vice versa;
* the local dry-run needs no GEMINI_API_KEY;
* missing or malformed local data fails with something actionable;
* an invalid backend name is rejected cleanly by the CLI;
* nothing is monkeypatched, so importing the pipeline leaves app.gemini alone.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import config, gemini, local_text, pipeline, providers

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_VALID = {
    "scripts": [
        {
            "idea": "A lighthouse keeper finds a message in a bottle",
            "hook": "The keeper found the bottle at dawn.",
            "story": "It was warm, and it was empty.",
            "twist": "His name was already scratched inside.",
            "ending": "He has kept it ever since.",
            "cta": "Would you have thrown it back?",
            "title": "The Bottle",
            "hashtags": ["mystery", "sea"],
        }
    ]
}


def _write_local(tmp_path, data):
    path = tmp_path / "local_text.json"
    path.write_text(
        data if isinstance(data, str) else json.dumps(data), encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------- selection


def test_backend_local_resolves_to_the_local_module():
    assert providers.resolve_backend("local") is local_text


def test_backend_gemini_resolves_to_the_gemini_module():
    assert providers.resolve_backend("gemini") is gemini


def test_backend_defaults_to_gemini(monkeypatch):
    """An unset TEXT_BACKEND must keep the normal path on Gemini."""
    monkeypatch.delenv("TEXT_BACKEND", raising=False)
    assert config.get_backend() == "gemini"
    assert providers.resolve_backend() is gemini


def test_backend_reads_text_backend_env(monkeypatch):
    monkeypatch.setenv("TEXT_BACKEND", "local")
    assert config.get_backend() == "local"
    assert providers.resolve_backend() is local_text


def test_backend_lookup_round_trips():
    assert providers.backend_name(providers.resolve_backend("local")) == "local"
    assert providers.backend_name(providers.resolve_backend("gemini")) == "gemini"


def test_both_backends_satisfy_the_same_contract():
    """The seam is exactly these five calls; both modules must provide them."""
    for name in config.BACKENDS:
        module = providers.resolve_backend(name)
        for fn in providers.CONTRACT:
            assert callable(getattr(module, fn, None)), f"{name}.{fn}"


def test_a_backend_missing_the_contract_is_rejected(monkeypatch):
    """A module that does not implement the seam is a config error, not a crash."""
    import types

    broken = types.ModuleType("broken_backend")
    broken.generate_ideas = lambda n: []  # type: ignore[attr-defined]
    # Repoint the real "local" name at a module that implements only one call.
    monkeypatch.setitem(providers._BACKEND_MODULES, "local", "broken_backend")
    monkeypatch.setitem(sys.modules, "broken_backend", broken)

    with pytest.raises(config.ConfigError) as exc:
        providers.resolve_backend("local")
    msg = str(exc.value)
    assert "generate_script" in msg
    assert "generate_tts" in msg


def test_resolution_does_not_monkeypatch_the_gemini_module():
    """The old design rebound 8 attributes on app.gemini; that must be gone."""
    before = {name: getattr(gemini, name, None) for name in
              ("generate_ideas", "generate_script", "script_to_text",
               "generate_tts", "generate_caption", "get_gemini_api_key")}
    providers.resolve_backend("local")
    after = {name: getattr(gemini, name, None) for name in before}
    assert before == after
    assert gemini.generate_ideas is not local_text.generate_ideas


# ---------------------------------------------------------------- validation


def test_unknown_backend_name_is_rejected():
    with pytest.raises(config.ConfigError) as exc:
        config.normalize_backend("openai")
    assert "openai" in str(exc.value)
    assert "gemini" in str(exc.value) and "local" in str(exc.value)


def test_backend_name_is_case_and_space_insensitive():
    assert config.normalize_backend("  LOCAL ") == "local"


def test_pipeline_reports_unknown_backend_as_a_failure_entry():
    result = pipeline.run_pipeline(1, dry_run=True, backend="nope")
    assert result["failed"] and result["failed"][0]["step"] == "backend"
    assert not result["success"]


# ---------------------------------------------------------------- dry-run


def test_local_dry_run_does_not_require_a_gemini_key(monkeypatch, tmp_path):
    """The whole point of the local backend: no key anywhere in the run."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("TEXT_PROVIDER_JSON", str(_write_local(tmp_path, _VALID)))

    result = pipeline.run_pipeline(1, dry_run=True, backend="local")
    assert result["backend"] == "local"
    assert not any("GEMINI_API_KEY" in f["error"] for f in result["failed"])
    assert not result["failed"] or all(
        "FFmpeg" in f["error"] or "ffprobe" in f["error"]
        for f in result["failed"]
    ), result["failed"]


def test_gemini_dry_run_still_reports_a_missing_key(monkeypatch):
    """The Gemini path must not have been weakened by the local one."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    problems = pipeline.check_dependencies(pipeline.providers.resolve_backend("gemini"))
    assert any("GEMINI_API_KEY" in p for p in problems)


def test_gemini_dry_run_passes_with_a_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert not any(
        "GEMINI_API_KEY" in p
        for p in pipeline.check_dependencies(pipeline.providers.resolve_backend("gemini"))
    )


def test_local_dry_run_reports_missing_edge_tts_as_setup_problem(monkeypatch, tmp_path):
    """A missing edge-tts must be a named, installable problem, not a traceback."""
    import builtins

    monkeypatch.setenv("TEXT_PROVIDER_JSON", str(_write_local(tmp_path, _VALID)))
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "edge_tts":
            raise ImportError("No module named 'edge_tts'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    problems = local_text.check_setup()
    assert any("edge-tts" in p and "pip install" in p for p in problems)


# ------------------------------------------------------- local data errors


def test_missing_local_file_is_actionable(monkeypatch, tmp_path):
    missing = tmp_path / "absent.json"
    monkeypatch.setenv("TEXT_PROVIDER_JSON", str(missing))
    problems = local_text.check_setup()
    assert problems
    assert "not found" in problems[0]
    assert str(missing) in problems[0]
    assert "TEXT_PROVIDER_JSON" in problems[0]
    with pytest.raises(gemini.GeminiError):
        local_text.generate_ideas(1)


def test_invalid_json_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "TEXT_PROVIDER_JSON", str(_write_local(tmp_path, "{not json"))
    )
    problems = local_text.check_setup()
    assert problems
    assert "not valid JSON" in problems[0]


def test_missing_scripts_array_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT_PROVIDER_JSON", str(_write_local(tmp_path, {"x": 1})))
    problems = local_text.check_setup()
    assert any("scripts" in p for p in problems)


def test_empty_scripts_array_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT_PROVIDER_JSON", str(_write_local(tmp_path, {"scripts": []})))
    problems = local_text.check_setup()
    assert any("empty" in p for p in problems)


def test_script_without_narration_text_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "TEXT_PROVIDER_JSON", str(_write_local(tmp_path, {"scripts": [{"idea": "x"}]}))
    )
    with pytest.raises(gemini.GeminiError) as exc:
        local_text.generate_script("x")
    assert "narration text" in str(exc.value)


def test_scripts_entry_must_be_an_object(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "TEXT_PROVIDER_JSON", str(_write_local(tmp_path, {"scripts": ["a string"]}))
    )
    problems = local_text.check_setup()
    assert any("scripts[0]" in p for p in problems)


def test_valid_local_file_reports_no_setup_problems(monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT_PROVIDER_JSON", str(_write_local(tmp_path, _VALID)))
    assert local_text.check_setup() == []


def test_local_ideas_are_read_from_the_file(monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT_PROVIDER_JSON", str(_write_local(tmp_path, _VALID)))
    assert local_text.generate_ideas(1) == [_VALID["scripts"][0]["idea"]]


def test_local_text_path_defaults_to_project_root(monkeypatch):
    monkeypatch.delenv("TEXT_PROVIDER_JSON", raising=False)
    assert config.get_local_text_path() == config.get_project_root() / "local_text.json"
    assert config.get_local_text_path().is_file(), "the shipped local_text.json"


def test_local_voice_is_separate_from_the_gemini_voice(monkeypatch):
    """TTS_VOICE names a Gemini voice; Edge would reject it."""
    monkeypatch.setenv("TTS_VOICE", "Kore")
    assert config.get_edge_tts_voice() == "en-GB-SoniaNeural"
    monkeypatch.setenv("EDGE_TTS_VOICE", "en-US-JennyNeural")
    assert config.get_edge_tts_voice() == "en-US-JennyNeural"
    assert config.get_tts_voice() == "Kore"


# ---------------------------------------------------------------- the CLI


def _cli(args):
    return subprocess.run(
        [sys.executable, "make_videos.py", *args],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )


def test_cli_rejects_invalid_backend_value():
    proc = _cli(["--backend", "bogus", "--count", "1", "--dry-run"])
    assert proc.returncode != 0
    assert "bogus" in proc.stderr
    assert "gemini" in proc.stderr and "local" in proc.stderr


def test_cli_rejects_invalid_backend_from_the_environment():
    """A bad TEXT_BACKEND is reported as a failure entry, not a traceback."""
    import os

    saved = os.environ.get("TEXT_BACKEND")
    os.environ["TEXT_BACKEND"] = "bogus"
    try:
        result = pipeline.run_pipeline(1, dry_run=True, backend=None)
    finally:
        if saved is None:
            os.environ.pop("TEXT_BACKEND", None)
        else:
            os.environ["TEXT_BACKEND"] = saved
    assert result["failed"] and result["failed"][0]["step"] == "backend"
    assert "bogus" in result["failed"][0]["error"]


def test_cli_help_documents_the_backend_flag():
    proc = _cli(["--help"])
    assert proc.returncode == 0
    assert "--backend" in proc.stdout
    assert "gemini" in proc.stdout and "local" in proc.stdout


def test_cli_local_dry_run_needs_no_gemini_key():
    """The documented keyless command must actually run keyless."""
    import os

    assert (PROJECT_ROOT / "local_text.json").is_file()
    proc = subprocess.run(
        [sys.executable, "make_videos.py", "--backend", "local", "--count", "1",
         "--dry-run"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
        env={**os.environ, "GEMINI_API_KEY": ""},
    )
    assert "GEMINI_API_KEY not set" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr
    assert "backend 'local'" in proc.stdout


def test_cli_dry_run_uses_the_gemini_backend_by_default():
    """With no flag and no TEXT_BACKEND, the run is still a Gemini run."""
    import os

    proc = subprocess.run(
        [sys.executable, "make_videos.py", "--count", "1", "--dry-run"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "TEXT_BACKEND"},
    )
    # No key on this machine, so it fails; the point is WHICH backend it names.
    # No key on this machine, so it fails; the point is WHICH backend it names.
    assert "backend 'gemini'" in proc.stdout, proc.stdout + proc.stderr