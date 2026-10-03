"""
Gemini client for ideas, script, captions, and TTS.

Uses the REST API directly over ``urllib`` so the project keeps a tiny
dependency surface.

Details that caused real bugs before and are now guarded by tests:

* Model IDs are configurable. ``gemini-2.5-flash-lite`` and
  ``gemini-2.5-flash-preview-tts`` were removed from the Gemini model list,
  so the defaults come from ``GEMINI_TEXT_MODEL`` / ``GEMINI_TTS_MODEL``.
* The API key travels in the ``x-goog-api-key`` header, not the URL query
  string, so it never lands in logs, tracebacks, or proxy access logs.
* Only transient failures (429, 5xx, network faults) are retried. Retrying a
  400/401/403 just burns quota and delays the real error.
* An empty or malformed response raises instead of silently returning ``{}``.
"""
import base64
import json
import random
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, NamedTuple, Optional

from .config import (
    get_gemini_api_key,
    get_gemini_text_model,
    get_gemini_tts_model,
    get_tts_voice,
)


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Status codes worth retrying: rate limits and transient server/transport faults.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

MAX_ATTEMPTS = 4

# Never sit in a sleep longer than this. A free-tier daily quota exhaustion
# replies with a retryDelay measured in hours; retrying that four times would
# stall the run for no benefit, so the caller gets the real error instead.
MAX_SLEEP_SECONDS = 60.0


class GeminiError(Exception):
    """Raised for any Gemini API or response-parsing failure."""

    def __init__(self, message: str, status: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = 0.0


class AudioBlob(NamedTuple):
    """Synthesized audio plus the mime type the API actually reported."""

    data: bytes
    mime_type: str

    @property
    def extension(self) -> str:
        """File extension implied by the returned mime type.

        Gemini TTS returns ``audio/wav`` (with a 44-byte RIFF header), so the
        old hardcoded ``.mp3`` name was simply wrong.
        """
        mime = self.mime_type.lower()
        if "mpeg" in mime or "mp3" in mime:
            return ".mp3"
        if "ogg" in mime:
            return ".ogg"
        if "flac" in mime:
            return ".flac"
        return ".wav"


def _redact(text: str) -> str:
    """Strip anything that looks like a credential out of an error body."""
    out = []
    for line in text.splitlines():
        low = line.lower()
        if "api key" in low or "api_key" in low or "key=" in low or "token" in low:
            out.append("<redacted>")
        else:
            out.append(line)
    return "\n".join(out)


def _json_request(url: str, payload: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    """POST JSON to Gemini and return the decoded body."""
    key = get_gemini_api_key()
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Header, not query string: keeps the key out of URLs and logs.
            "x-goog-api-key": key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            err = _redact(e.read().decode("utf-8", errors="replace"))
        except Exception:
            err = ""
        status = getattr(e, "code", None)
        error = GeminiError(
            f"Gemini HTTP {status}: {err}".strip(),
            status=status,
            retryable=status in _RETRYABLE_STATUS,
        )
        error.retry_after = max(
            _retry_after_seconds(e), _retry_delay_from_body(err)
        )
        raise error from None
    except urllib.error.URLError as e:
        raise GeminiError(f"Gemini network error: {e.reason}", retryable=True) from None
    except TimeoutError:
        raise GeminiError("Gemini request timed out", retryable=True) from None
    except OSError as e:
        raise GeminiError(f"Gemini network error: {e}", retryable=True) from None

    if not body.strip():
        raise GeminiError("Gemini returned an empty body", retryable=True)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        raise GeminiError(f"Gemini returned a non-JSON body: {e}") from None
    if not isinstance(parsed, dict):
        raise GeminiError("Gemini returned an unexpected JSON shape")
    return parsed


def _retry_after_seconds(err: urllib.error.HTTPError) -> float:
    raw = err.headers.get("Retry-After") if getattr(err, "headers", None) else None
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _backoff_seconds(attempt: int, retry_after: float = 0.0) -> float:
    """Exponential backoff with jitter, honouring an explicit Retry-After."""
    return max(retry_after, (2**attempt) + random.uniform(0, 1))


def _retry_delay_from_body(body: str) -> float:
    """Pull Gemini's ``retryDelay`` out of a RESOURCE_EXHAUSTED body.

    Gemini reports e.g. ``"retryDelay": "46558s"`` for quota exhaustion.
    """
    match = re.search(r'"retryDelay"\s*:\s*"?(\d+(?:\.\d+)?)s', body)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0
    return 0.0


def _call_with_retries(url: str, payload: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    """Call Gemini, retrying only transient failures with backoff."""
    last: Optional[GeminiError] = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _json_request(url, payload, timeout=timeout)
        except GeminiError as e:
            last = e
            if not e.retryable or attempt == MAX_ATTEMPTS - 1:
                raise
            delay = _backoff_seconds(attempt, e.retry_after)
            # A wait this long means the quota is gone for hours; fail fast so
            # the batch reports the real reason instead of hanging.
            if e.retry_after > MAX_SLEEP_SECONDS or delay > MAX_SLEEP_SECONDS:
                raise
            time.sleep(delay)
    raise last if last else GeminiError("Gemini call failed")


def _text_of(res: Dict[str, Any]) -> str:
    """Extract text from a generateContent response.

    Raises when the model was blocked or returned no parts, instead of
    silently producing an empty result.
    """
    feedback = res.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise GeminiError(f"Gemini blocked the prompt: {feedback['blockReason']}")

    candidates = res.get("candidates") or []
    if not candidates:
        raise GeminiError("Gemini returned no candidates")
    cand = candidates[0] or {}

    finish = cand.get("finishReason")
    parts = ((cand.get("content") or {}).get("parts")) or []
    text = "".join(
        p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")
    ).strip()

    if not text:
        detail = f" (finishReason={finish})" if finish else ""
        raise GeminiError(f"Gemini returned an empty response{detail}")
    return text


def _loads_lenient(raw: str) -> Dict[str, Any]:
    """Parse JSON that may be wrapped in a markdown code fence."""
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\s*", "", candidate)
        if candidate.rstrip().endswith("```"):
            candidate = candidate.rstrip()[:-3]
        candidate = candidate.strip()
    data = json.loads(candidate)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


IDEAS_SCHEMA = {
    "type": "OBJECT",
    "properties": {"ideas": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["ideas"],
}

SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "hook": {"type": "STRING"},
        "story": {"type": "STRING"},
        "twist": {"type": "STRING"},
        "ending": {"type": "STRING"},
        "cta": {"type": "STRING"},
        "title": {"type": "STRING"},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["hook", "story", "twist", "ending", "cta", "title", "hashtags"],
}


def generate_ideas(count: int = 10) -> List[str]:
    """Return exactly ``count`` distinct story ideas.

    Raises when fewer distinct ideas come back. The old implementation
    "padded" a short list with ``ideas + ideas * count``, which produced
    duplicate videos with identical filenames and content.
    """
    count = max(1, int(count))
    url = f"{GEMINI_BASE}/models/{get_gemini_text_model()}:generateContent"
    prompt = (
        "Generate short fictional TikTok story ideas about absurd, mystery, or weird events. "
        "Each must be clearly fictional, never presented as real news. "
        "Each idea 6-12 words. Return JSON with an 'ideas' array of exactly "
        f"{count} items, all different from each other."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": IDEAS_SCHEMA,
            "temperature": 0.9,
        },
    }
    ideas = _parse_json_ideas(_call_with_retries(url, payload))
    if not ideas:
        raise GeminiError("Gemini returned no usable ideas")

    unique: List[str] = []
    seen = set()
    for idea in ideas:
        low = idea.lower()
        if low not in seen:
            seen.add(low)
            unique.append(idea)
    if len(unique) < count:
        raise GeminiError(f"Gemini returned {len(unique)} unique ideas, wanted {count}")
    return unique[:count]


def _parse_json_ideas(res: Dict[str, Any]) -> List[str]:
    try:
        data = _loads_lenient(_text_of(res))
    except GeminiError:
        raise
    except Exception as e:
        raise GeminiError(f"Failed to parse ideas: {e}") from None
    ideas = data.get("ideas") or data.get("items") or []
    if not isinstance(ideas, list):
        return []
    return [str(i).strip() for i in ideas if str(i).strip()]


def generate_script(idea: str) -> Dict[str, Any]:
    """Return a structured script dict for ``idea``."""
    url = f"{GEMINI_BASE}/models/{get_gemini_text_model()}:generateContent"
    prompt = (
        "Write a 45-60s spoken TikTok script for a fictional absurd/mystery story. "
        "Structure: hook, story, twist, ending, cta. "
        "The hook must land in the first 10-15 words. "
        "Write natural spoken English, no stage directions, no markdown, no emoji. "
        "Keep it clearly fictional. "
        "story, twist and ending together should be 110-150 words so it narrates in about 45-60 seconds. "
        "Return JSON with keys: hook, story, twist, ending, cta, title, hashtags (array of 3-5 tags)."
        f" Idea: {idea}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": SCRIPT_SCHEMA,
            "temperature": 0.85,
        },
    }
    return _parse_script(_call_with_retries(url, payload))


def _parse_script(res: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = _loads_lenient(_text_of(res))
    except GeminiError:
        raise
    except Exception as e:
        raise GeminiError(f"Failed to parse script: {e}") from None

    raw_tags = data.get("hashtags") or []
    if not isinstance(raw_tags, list):
        raw_tags = []

    out: Dict[str, Any] = {
        field: str(data.get(field, "") or "").strip()
        for field in ("hook", "story", "twist", "ending", "cta")
    }
    out["title"] = str(data.get("title", "") or "").strip() or "Fictional story"
    out["hashtags"] = [str(t).strip() for t in raw_tags if str(t).strip()]

    if not any(out[field] for field in ("hook", "story", "twist", "ending", "cta")):
        raise GeminiError("Gemini returned a script with no narration text")
    return out


SCRIPT_FIELDS = ("hook", "story", "twist", "ending", "cta")


def script_to_text(script: Dict[str, Any]) -> str:
    """Join the narration fields into one spoken block."""
    parts = [str(script.get(f, "") or "").strip() for f in SCRIPT_FIELDS]
    return " ".join(p for p in parts if p).strip()


def generate_caption(script: Dict[str, Any]) -> str:
    """The narration doubles as the on-post caption text."""
    return script_to_text(script)


def generate_tts(script: Dict[str, Any]) -> AudioBlob:
    """Synthesize narration and return audio plus its real mime type.

    Gemini TTS returns ``audio/wav`` (with a 44-byte RIFF header), so callers
    must not assume an mp3 payload.
    """
    full = script_to_text(script)
    if not full:
        raise GeminiError("Refusing to synthesize empty narration")

    url = f"{GEMINI_BASE}/models/{get_gemini_tts_model()}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": full}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "temperature": 0.7,
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": get_tts_voice()}}
            },
        },
    }
    return _extract_audio(_call_with_retries(url, payload, timeout=300))


def _extract_audio(res: Dict[str, Any]) -> AudioBlob:
    candidates = res.get("candidates") or []
    if not candidates:
        raise GeminiError("TTS response contained no candidates")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    for part in parts:
        if not isinstance(part, dict):
            continue
        inline = part.get("inlineData") or part.get("inline_data") or {}
        mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
        data = inline.get("data")
        if data and mime.lower().startswith("audio/"):
            try:
                decoded = base64.b64decode(data, validate=False)
            except Exception as e:
                raise GeminiError(f"TTS audio was not valid base64: {e}") from None
            if decoded:
                return AudioBlob(decoded, mime)
    raise GeminiError("No audio data in TTS response")
