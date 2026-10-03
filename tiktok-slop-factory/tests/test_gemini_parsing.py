"""Tests for the Gemini client: model IDs, retries, and response parsing."""
import base64
import json
import urllib.request

import pytest

from app import gemini


def _response(payload):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}


def test_parse_ideas():
    assert gemini._parse_json_ideas(_response({"ideas": ["a", "b", "c"]})) == ["a", "b", "c"]


def test_parse_script():
    s = {
        "hook": "h", "story": "s", "twist": "t", "ending": "e", "cta": "c",
        "title": "T", "hashtags": ["#x"],
    }
    out = gemini._parse_script(_response(s))
    assert out["hook"] == "h"
    assert out["title"] == "T"


def test_default_models_are_not_retired_ids(monkeypatch):
    """gemini-2.5-flash-lite / gemini-2.5-flash-preview-tts were removed."""
    import app.config as config

    monkeypatch.delenv("GEMINI_TEXT_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_TTS_MODEL", raising=False)
    assert "2.5" not in config.get_gemini_text_model()
    assert "2.5" not in config.get_gemini_tts_model()


def test_api_key_sent_in_header_not_url(monkeypatch):
    """The key must never appear in the URL query string."""
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(_response({"ideas": ["ok"]})).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return FakeResp()

    monkeypatch.setenv("GEMINI_API_KEY", "SECRET-KEY-VALUE")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    gemini._json_request("https://example.invalid/models/x:generateContent", {})

    assert "SECRET-KEY-VALUE" not in captured["url"]
    assert captured["headers"].get("x-goog-api-key") == "SECRET-KEY-VALUE"


def test_non_retryable_error_is_not_retried(monkeypatch):
    """A 400 must fail fast instead of burning quota on retries."""
    calls = {"n": 0}

    def boom(url, payload, timeout=180):
        calls["n"] += 1
        raise gemini.GeminiError("Gemini HTTP 400", status=400, retryable=False)

    monkeypatch.setattr(gemini, "_json_request", boom)
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)

    with pytest.raises(gemini.GeminiError):
        gemini._call_with_retries("u", {})
    assert calls["n"] == 1


def test_retryable_error_is_retried_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(url, payload, timeout=180):
        calls["n"] += 1
        if calls["n"] < 3:
            raise gemini.GeminiError("Gemini HTTP 429", status=429, retryable=True)
        return {"ok": True}

    monkeypatch.setattr(gemini, "_json_request", flaky)
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)

    assert gemini._call_with_retries("u", {}) == {"ok": True}
    assert calls["n"] == 3


def test_empty_response_raises_instead_of_returning_empty_dict():
    """The old code silently returned {} and produced a blank video."""
    with pytest.raises(gemini.GeminiError):
        gemini._text_of({"candidates": [{"content": {"parts": []}}]})
    with pytest.raises(gemini.GeminiError):
        gemini._text_of({"candidates": []})


def test_blocked_prompt_raises():
    res = {"promptFeedback": {"blockReason": "SAFETY"},
           "candidates": [{"content": {"parts": [{"text": "x"}]}}]}
    with pytest.raises(gemini.GeminiError):
        gemini._text_of(res)


def test_script_with_no_narration_raises():
    with pytest.raises(gemini.GeminiError):
        gemini._parse_script(_response({"title": "t", "hashtags": []}))


def test_malformed_json_raises():
    res = {"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}
    with pytest.raises(gemini.GeminiError):
        gemini._parse_script(res)


def test_fenced_json_is_parsed():
    res = {"candidates": [{"content": {"parts": [
        {"text": '```json\n{"ideas": ["a", "b"]}\n```'}]}}]}
    assert gemini._parse_json_ideas(res) == ["a", "b"]


def test_tts_extension_follows_returned_mime_type():
    """Gemini TTS returns audio/wav; naming the file .mp3 was wrong."""
    assert gemini.AudioBlob(b"RIFF", "audio/wav").extension == ".wav"
    assert gemini.AudioBlob(
        b"x", "audio/L16;codec=pcm;rate=24000"
    ).extension == ".wav"


def test_extract_audio_requires_audio_part():
    res = {"candidates": [{"content": {"parts": [{"text": "no audio"}]}}]}
    with pytest.raises(gemini.GeminiError):
        gemini._extract_audio(res)


def test_extract_audio_decodes_base64():
    data = base64.b64encode(b"RIFFfake").decode()
    res = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "audio/wav", "data": data}}]}}]}
    blob = gemini._extract_audio(res)
    assert blob.data == b"RIFFfake"
    assert blob.mime_type == "audio/wav"


def test_generate_ideas_rejects_duplicate_padding(monkeypatch):
    """The old code padded short lists by repeating ideas, producing dupes."""
    monkeypatch.setattr(gemini, "_parse_json_ideas", lambda res: ["only one"])
    with pytest.raises(gemini.GeminiError):
        gemini.generate_ideas(3)


def test_script_to_text_skips_empty_fields():
    assert gemini.script_to_text({"hook": "Hi", "story": "", "cta": "Bye"}) == "Hi Bye"


def test_retry_delay_parsed_from_quota_body():
    """Gemini reports daily-quota waits like "retryDelay": "46558s"."""
    body = '{"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"retryDelay": "46558s"}]}}'
    assert gemini._retry_delay_from_body(body) == 46558.0
    assert gemini._retry_delay_from_body("no delay here") == 0.0


def test_quota_exhaustion_fails_fast_without_sleeping(monkeypatch):
    """Waiting hours for a daily quota is pointless; surface the error now."""
    slept = []
    calls = {"n": 0}

    def quota(url, payload, timeout=180):
        calls["n"] += 1
        err = gemini.GeminiError("Gemini HTTP 429", status=429, retryable=True)
        err.retry_after = 46_558.0
        raise err

    monkeypatch.setattr(gemini, "_json_request", quota)
    monkeypatch.setattr(gemini.time, "sleep", lambda s: slept.append(s))

    with pytest.raises(gemini.GeminiError):
        gemini._call_with_retries("u", {})
    assert calls["n"] == 1
    assert slept == []


def test_error_body_is_redacted():
    out = gemini._redact("API key: AIzaSECRET\nrate limit exceeded")
    assert "AIzaSECRET" not in out
    assert "rate limit exceeded" in out
