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
    """Collect candidate objects in their actual order in the output text."""
    candidates: list[tuple[int, dict[str, Any]]] = []
    fenced_ranges: list[tuple[int, int]] = []
    last_fenced: tuple[int, dict[str, Any] | None] | None = None
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE):
        content = match.group(1)
        fenced_ranges.append((match.start(1), match.end(1)))
        if not content.strip():
            last_fenced = (match.start(), None)
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        fenced_payload = parsed if isinstance(parsed, dict) else None
        last_fenced = (match.start(), fenced_payload)
        if fenced_payload is not None:
            position = match.start(1) + len(content) - len(content.lstrip())
            candidates.append((position, fenced_payload))

    stripped = text.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            # If the entire output is one JSON object, nested objects are part
            # of its schema and must not displace the top-level candidate.
            if isinstance(parsed, dict):
                return [parsed]

    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.strip().rstrip(",")
        position = offset + len(line) - len(line.lstrip())
        offset += len(line)
        if not content.startswith("{"):
            continue
        if any(start <= position < end for start, end in fenced_ranges):
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append((position, parsed))

    # Never fall back to an older approval when the output contains a later,
    # malformed fenced JSON response. The final review response is the trust
    # boundary, so malformed newer output must fail closed.
    if last_fenced is not None and last_fenced[1] is None:
        later_candidates = [position for position, _ in candidates if position > last_fenced[0]]
        if not later_candidates:
            return []

    candidates.sort(key=lambda item: item[0])
    return [payload for _position, payload in candidates]


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

    # Validate the schema version strictly; strings and booleans are not ints
    # in this contract, even though Python can coerce them to one.
    version = payload.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        return None
    version_int = version
    if version_int != VERDICT_SCHEMA_VERSION:
        return None

    # Validate verdict field
    verdict_value = payload.get("verdict")
    if verdict_value not in ("approve", "reject"):
        return None

    # These fields define the approval boundary. Missing or malformed lists
    # must never collapse to empty lists, as that would turn bad output into an
    # accidental approval.
    if any(field not in payload for field in ("blocking", "non_blocking", "confidence")):
        return None
    blocking_value = payload["blocking"]
    non_blocking_value = payload["non_blocking"]
    confidence_value = payload["confidence"]
    if not isinstance(blocking_value, list) or any(not isinstance(item, str) for item in blocking_value):
        return None
    if not isinstance(non_blocking_value, list) or any(not isinstance(item, str) for item in non_blocking_value):
        return None
    if isinstance(confidence_value, bool) or not isinstance(confidence_value, (int, float)):
        return None
    try:
        confidence_number = float(confidence_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not float("-inf") < confidence_number < float("inf"):
        return None

    blocking = tuple(blocking_value)
    non_blocking = tuple(non_blocking_value)
    confidence = _coerce_confidence(confidence_number)

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
