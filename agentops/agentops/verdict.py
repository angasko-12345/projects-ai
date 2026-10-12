"""Structured verdict contract for review and security roles.

This leaf module (no I/O, no LLM) defines the verdict schema that review
and security agents MUST emit as their final output line. Only a well-formed
object with ``verdict == "approve"`` and empty ``blocking`` passes.
Everything else is NOT_APPROVED.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


VERDICT_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

_VERDICT_RE = re.compile(
    r"^\s*\{.*\"schema_version\"\s*:\s*1.*\"verdict\"\s*:\s*\"(approve|reject)\".*\}\s*$",
    re.DOTALL,
)


@dataclass(frozen=True)
class Verdict:
    """Parsed review/security verdict."""

    schema_version: int
    verdict: str  # "approve" | "reject"
    blocking: tuple[str, ...]
    non_blocking: tuple[str, ...]
    confidence: float

    def __post_init__(self) -> None:
        # Clamp confidence to [0, 1]
        try:
            c = float(self.confidence)
            if c != c:  # NaN
                c = 0.0
            c = max(0.0, min(1.0, c))
        except (TypeError, ValueError):
            c = 0.0
        object.__setattr__(self, "confidence", c)

    @property
    def is_approved(self) -> bool:
        """True only for approve with no blocking items."""
        return self.verdict == "approve" and not self.blocking

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verdict": self.verdict,
            "blocking": list(self.blocking),
            "non_blocking": list(self.non_blocking),
            "confidence": self.confidence,
        }


def _coerce_confidence(value: Any) -> float:
    try:
        if value is None or isinstance(value, bool):
            return 0.0
        number = float(value)
        if number != number:  # NaN
            return 0.0
        return max(0.0, min(1.0, number))
    except (TypeError, ValueError):
        return 0.0


def _coerce_str_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for entry in value:
            if entry is not None:
                items.append(str(entry))
        return tuple(items)
    return ()


def _extract_candidate_objects(text: str) -> list[dict[str, Any]]:
    """Collect candidate JSON objects from text, last one wins."""
    candidates: list[dict[str, Any]] = []
    # Try fenced JSON blocks first (most explicit)
    try:
        for match in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE):
            content = match.group(1).strip()
            if content:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    candidates.append(parsed)
    except Exception:
        pass
    # Try whole text as JSON
    try:
        stripped = text.strip()
        if stripped:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                candidates.append(parsed)
    except Exception:
        pass
    # Try trailing lines that look like JSON (one per line, last wins)
    try:
        for line in reversed(text.splitlines()):
            line = line.strip().rstrip(",")
            if not line or not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    candidates.append(parsed)
                    break  # Last valid line wins
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return candidates


def parse_verdict(stdout: str | None) -> Verdict | None:
    """Parse agent stdout for a structured verdict.

    Returns a Verdict if exactly one well-formed verdict object is found
    with schema_version 1 and valid fields. Returns None if no verdict
    is found or the payload is malformed/missing required fields.

    The verdict MUST be the last JSON object in the output to count.
    """
    if not stdout or not isinstance(stdout, str):
        return None

    candidates = _extract_candidate_objects(stdout)
    if not candidates:
        return None

    # Take the last candidate (final verdict)
    payload = candidates[-1]

    # Validate schema version
    version = payload.get("schema_version")
    try:
        version_int = int(version) if version is not None else 0
    except (TypeError, ValueError):
        return None
    if version_int != VERDICT_SCHEMA_VERSION:
        return None

    # Validate verdict field
    verdict_value = payload.get("verdict")
    if verdict_value not in ("approve", "reject"):
        return None

    # Extract fields with coercion
    blocking = _coerce_str_list(payload.get("blocking"))
    non_blocking = _coerce_str_list(payload.get("non_blocking"))
    confidence = _coerce_confidence(payload.get("confidence"))

    return Verdict(
        schema_version=version_int,
        verdict=verdict_value,
        blocking=blocking,
        non_blocking=non_blocking,
        confidence=confidence,
    )


def verdict_to_json(verdict: Verdict) -> str:
    """Serialize a Verdict to a compact JSON line."""
    return json.dumps(verdict.to_dict(), separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "VERDICT_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "Verdict",
    "parse_verdict",
    "verdict_to_json",
]