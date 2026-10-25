"""One table of credential shapes, shared by screening and redaction.

`security.scan_text()` reports the shapes it finds; `logging.redact_text()`
removes them from text that reaches logs, artifacts, and prompts. Both read
this module so the two layers cannot drift into disagreeing about what a
secret looks like.

Regexes are deliberately narrow: a false positive blocks a merge, so generic
words like "password" alone never match -- only assignments and well-known
token prefixes do. The value group marks the only span a redactor may
replace, which keeps surrounding context (`api_key=`) readable.

Secrets are never stored here, only shapes. Test fixtures must be obviously
fake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Replacement text for a redacted value.
REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class SecretPattern:
    """One credential shape: its name, its regex, and the span holding the value."""

    name: str
    regex: re.Pattern[str]
    value_group: str | int = 0


def _pattern(name: str, regex: str, value_group: str | int = 0) -> SecretPattern:
    return SecretPattern(name, re.compile(regex), value_group)


SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    _pattern("aws-access-key-id", r"\bAKIA[0-9A-Z]{16}\b"),
    _pattern(
        "aws-secret-access-key",
        r"(?i)\baws(.{0,20})?secret(.{0,20})?['\"]?\s*[:=]\s*['\"]?"
        r"(?P<value>[A-Za-z0-9/+=]{32,})",
        "value",
    ),
    _pattern("github-token", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    _pattern(
        "github-fine-grained-pat",
        r"\bgithub_pat_[A-Za-z0-9_]{20,}(?![A-Za-z0-9_])",
    ),
    # Anthropic keys carry a hyphen ("sk-ant-api03-"), so they need their own
    # shape ahead of the generic "sk-" family.
    _pattern(
        "anthropic-api-key",
        r"\bsk-ant-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])",
    ),
    _pattern(
        "openai-api-key",
        r"\bsk-(?!ant-)[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])",
    ),
    _pattern("stripe-key", r"\b(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}\b"),
    _pattern("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"),
    _pattern(
        "jwt",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
        r"(?![A-Za-z0-9_-])",
    ),
    _pattern("slack-token", r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b"),
    _pattern("private-key-header", r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    # The optional closing quote before the separator is what makes JSON
    # (`"api_key": "value"`) match as well as dotenv (`API_KEY=value`).
    #
    # The value itself must look credential-like, or ordinary log lines lose
    # their numbers and model names: a value is a candidate when it runs at
    # least 12 characters (`FAKEvalue1234`) or at least 8 and carries a symbol
    # (`sk-abc123`, `abcd1234!`). "token: 4096" and "token = GPT-4" stay
    # readable; real keys are long or punctuated, counts and names are not.
    _pattern(
        "credential-assignment",
        r"(?i)\b(password|passwd|pwd|api[_-]?key|secret|client[_-]?secret|"
        r"auth[_-]?token|access[_-]?token|refresh[_-]?token|private[_-]?key|token)"
        r"\b['\"]?\s*[:=]\s*['\"]?(?P<value>"
        r"(?=[^\s'\"]{8})"
        r"(?=[^\s'\"]{12,}|[^\s'\"]*[^A-Za-z0-9\s'\"])"
        r"[^\s'\"]{4,})",
        "value",
    ),
    # The `{20,}` floor is what keeps "bearer of bad news" intact: bearer
    # tokens are long by construction, ordinary English is not.
    _pattern(
        "bearer-token",
        r"(?i)\bbearer\s+(?P<value>[A-Za-z0-9\-._~+/]{20,}={0,2})",
        "value",
    ),
    _pattern(
        "connection-string-secret",
        r"(?i)\b(password|pwd)=(?P<value>[^;\s]{4,})",
        "value",
    ),
)


def secret_spans(text: str) -> list[tuple[int, int]]:
    """Return merged (start, end) spans of secret values in ``text``."""
    spans: list[tuple[int, int]] = []
    for spec in SECRET_PATTERNS:
        try:
            for match in spec.regex.finditer(text):
                start, end = match.span(spec.value_group)
                if start >= 0 and end > start:
                    spans.append((start, end))
        except Exception:
            continue
    if not spans:
        return []
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def redact_secrets(text: str) -> str:
    """Replace every credential shape in ``text`` with ``[REDACTED]``."""
    if not isinstance(text, str) or not text:
        return text
    try:
        spans = secret_spans(text)
        if not spans:
            return text
        pieces: list[str] = []
        cursor = 0
        for start, end in spans:
            pieces.append(text[cursor:start])
            pieces.append(REDACTED)
            cursor = end
        pieces.append(text[cursor:])
        return "".join(pieces)
    except Exception:
        return text


__all__ = [
    "REDACTED",
    "SECRET_PATTERNS",
    "SecretPattern",
    "redact_secrets",
    "secret_spans",
]
