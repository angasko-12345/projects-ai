"""Local secret screening for the optional security-review gate.

This leaf module (no I/O, no LLM, no subprocess) scans candidate text --
worktree diffs and redacted task transcripts -- for credential-like patterns
before a security agent is asked to look at the change. Findings name the
pattern and location only: secret *values* are never included, so the output
is safe to embed in prompts, logs, artifacts, and persistent memory.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


#: (pattern name, compiled regex). Every regex is deliberately narrow: a
#: false positive blocks a merge, so generic words like "password" alone do
#: not match -- only assignments and well-known token prefixes do.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws-secret-access-key", re.compile(
        r"(?i)\baws(.{0,20})?secret(.{0,20})?['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{32,}",
    )),
    ("github-token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("openai-api-key", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b")),
    ("private-key-header", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("credential-assignment", re.compile(
        r"(?i)\b(password|passwd|pwd|api[_-]?key|secret|client[_-]?secret|"
        r"auth[_-]?token|access[_-]?token|private[_-]?key)\b\s*[:=]\s*['\"]?"
        r"\S{4,}",
    )),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{12,}={0,2}\b")),
    ("connection-string-secret", re.compile(
        r"(?i)\b(password|pwd)=[^;\s]{4,}",
    )),
)

#: Long opaque blobs carry enough entropy to be tokens even without a known
#: prefix. Quoted strings of 32+ base64/hex characters outside normal prose
#: are flagged; the value itself is never reported.
_HIGH_ENTROPY_RE = re.compile(r"['\"]([A-Za-z0-9/+=]{32,}|[0-9a-fA-F]{32,})['\"]")

_ENTROPY_THRESHOLD_BITS_PER_CHAR = 4.0

_FINGERPRINT_CHARS = 120


@dataclass(frozen=True)
class SecretFinding:
    """One redacted secret sighting: where, and what shape -- never the value."""

    pattern: str
    location: str
    hint: str

    def to_dict(self) -> dict[str, Any]:
        return {"pattern": self.pattern, "location": self.location, "hint": self.hint}

    def describe(self) -> str:
        return f"{self.location}: possible {self.pattern} ({self.hint})"


def _shannon_entropy(text: str) -> float:
    try:
        if not text:
            return 0.0
        frequencies: dict[str, int] = {}
        for char in text:
            frequencies[char] = frequencies.get(char, 0) + 1
        length = len(text)
        return -sum(
            (count / length) * math.log2(count / length)
            for count in frequencies.values()
        )
    except Exception:
        return 0.0


def _fingerprint_path(location: str, match_start: int, text: str) -> str:
    """Describe the neighbourhood of a match without quoting the match."""
    try:
        line_start = text.rfind("\n", 0, match_start) + 1
        line_end = text.find("\n", match_start)
        if line_end < 0:
            line_end = len(text)
        line = text[line_start:line_end]
        stripped = line.strip()
        if len(stripped) > _FINGERPRINT_CHARS:
            stripped = stripped[:_FINGERPRINT_CHARS].rstrip() + "…"
        line_number = text.count("\n", 0, match_start) + 1
        return f"{location}:{line_number}"
    except Exception:
        return location


def scan_text(text: str | None, location: str = "diff") -> list[SecretFinding]:
    """Return redacted secret findings for one text blob. Never raises."""
    findings: list[SecretFinding] = []
    try:
        if not text or not isinstance(text, str):
            return []
        seen: set[tuple[str, str]] = set()
        for name, pattern in _SECRET_PATTERNS:
            try:
                for match in pattern.finditer(text):
                    where = _fingerprint_path(location, match.start(), text)
                    key = (name, where)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(SecretFinding(
                        pattern=name, location=where,
                        hint="known credential shape",
                    ))
            except Exception:
                continue
        try:
            for match in _HIGH_ENTROPY_RE.finditer(text):
                candidate = match.group(1)
                if len(candidate) < 32:
                    continue
                if _shannon_entropy(candidate) < _ENTROPY_THRESHOLD_BITS_PER_CHAR:
                    continue
                where = _fingerprint_path(location, match.start(), text)
                key = ("high-entropy-token", where)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(SecretFinding(
                    pattern="high-entropy-token", location=where,
                    hint="long opaque value worth checking",
                ))
        except Exception:
            pass
        return findings
    except Exception:
        return []


def scan_many(parts: list[tuple[str, str | None]]) -> list[SecretFinding]:
    """Scan several named blobs; ``parts`` is (location, text) pairs."""
    findings: list[SecretFinding] = []
    try:
        for location, text in parts:
            findings.extend(scan_text(text, location))
    except Exception:
        pass
    return findings


def findings_for_prompt(findings: list[SecretFinding], limit: int = 20) -> str:
    """Render findings as prompt context. Contains no secret values by construction."""
    try:
        if not findings:
            return "Local secret scan: no credential-like patterns found."
        lines = [f"Local secret scan found {len(findings)} credential-like pattern(s):"]
        for finding in findings[: max(1, limit)]:
            lines.append(f"- {finding.describe()}")
        if len(findings) > max(1, limit):
            lines.append(f"({len(findings) - max(1, limit)} more omitted)")
        lines.append("Treat every item as a real secret until proven otherwise.")
        return "\n".join(lines)
    except Exception:
        return "Local secret scan: unavailable."


__all__ = [
    "SecretFinding",
    "findings_for_prompt",
    "scan_many",
    "scan_text",
]
