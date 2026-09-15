"""Versioned structured results for coding-agent executions.

Natural-language agent output must never be the authoritative signal of
success.  This leaf module (no I/O, no LLM) defines:

- :class:`AgentResult`: versioned schema with the required fields.
- :func:`parse_agent_result`: robust parser for structured JSON, plain text,
  and malformed/partial output.  It never raises for bad input: the worst
  case is a ``text``/``empty``/``malformed`` fallback result.
- :func:`coerce_agent_result`: backward-compatible loader for values already
  stored in ``agent_runs.structured_result`` (old raw dicts/lists, new
  envelopes, ``None``).
- :class:`ExecutionOutcome` / :func:`evaluate_execution`: the five-way
  distinction between process success, agent success, verification success,
  review approval, and merge eligibility.  These are deliberately NOT
  equivalent: merge eligibility requires every applicable signal.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


RESULT_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

_SUMMARY_MAX_LENGTH = 2000


class AgentResultStatus(StrEnum):
    """Authoritative agent-level outcome claimed inside structured output."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class ParseMode(StrEnum):
    """How the parser obtained the result."""

    STRUCTURED = "structured"
    PARTIAL = "partial"
    TEXT = "text"
    EMPTY = "empty"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class AgentResult:
    """Versioned structured result attached to an AgentRun.

    All fields have safe defaults so partial/malformed payloads still produce
    a usable object.  ``schema_version`` enables future format evolution:
    readers accept any version in ``SUPPORTED_SCHEMA_VERSIONS`` and coerce
    unknown versions to a partial result with a warning instead of failing.
    """

    schema_version: int = RESULT_SCHEMA_VERSION
    status: AgentResultStatus = AgentResultStatus.UNKNOWN
    summary: str | None = None
    files_changed: tuple[str, ...] = ()
    tests_run: int | None = None
    tests_passed: int | None = None
    tests_failed: int | None = None
    verification_results: tuple[dict[str, Any], ...] = ()
    review_findings: tuple[dict[str, Any], ...] = ()
    requested_followup: str | None = None
    confidence: float | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value if isinstance(self.status, AgentResultStatus) else str(self.status)
        data["files_changed"] = list(self.files_changed)
        data["verification_results"] = list(self.verification_results)
        data["review_findings"] = list(self.review_findings)
        data["errors"] = list(self.errors)
        data["warnings"] = list(self.warnings)
        return data


@dataclass(frozen=True)
class ParsedAgentResult:
    """Parser output: normalized result plus how it was obtained."""

    result: AgentResult
    parse_mode: ParseMode
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Coercion helpers (tolerant, never raise)
# ---------------------------------------------------------------------------

def _coerce_status(value: object) -> AgentResultStatus:
    if isinstance(value, AgentResultStatus):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        for member in AgentResultStatus:
            if normalized == member.value:
                return member
        if normalized in {"pass", "passed", "ok", "done", "complete", "completed"}:
            return AgentResultStatus.SUCCESS
        if normalized in {"fail", "failed", "error", "errored"}:
            return AgentResultStatus.FAILURE
    return AgentResultStatus.UNKNOWN


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value[:_SUMMARY_MAX_LENGTH]
    try:
        text = str(value)
    except Exception:
        return None
    return text[:_SUMMARY_MAX_LENGTH]


def _coerce_str_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value[:_SUMMARY_MAX_LENGTH],)
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for entry in value:
            text = _coerce_str(entry)
            if text is not None:
                items.append(text)
        return tuple(items)
    return ()


def _coerce_dict_list(value: object) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, dict):
        return (dict(value),)
    if isinstance(value, (list, tuple)):
        items: list[dict[str, Any]] = []
        for entry in value:
            if isinstance(entry, dict):
                items.append(dict(entry))
            elif entry is not None:
                items.append({"value": entry})
        return tuple(items)
    return ({"value": value},)


def _coerce_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else 0
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except (ValueError, AttributeError):
            return None
        return parsed if parsed >= 0 else 0
    return None


def _coerce_confidence(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def _coerce_metadata(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        try:
            return dict(value)
        except Exception:
            return {}
    return {}


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        try:
            return f"<unrepresentable {type(value).__name__}>"
        except Exception:
            return "<unrepresentable>"


def _safe_get(payload: Any, key: str) -> Any:
    try:
        return payload.get(key)
    except Exception:
        return None


def _coerce_bool_signal(value: object) -> bool:
    """Strictly normalize a process-success signal.

    Plain ``bool`` values pass through.  Common string spellings
    ("true"/"false", "1"/"0", "yes"/"no", "on"/"off") are honoured so a
    truthy non-empty string such as ``"false"`` can never count as success.
    Other types fall back to truthiness, except ``None`` which is False.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "y", "on", "pass", "passed", "ok", "success"):
            return True
        if normalized in ("false", "0", "no", "n", "off", "fail", "failed", "error", ""):
            return False
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return bool(value)


def agent_result_from_dict(data: dict[str, Any]) -> tuple[AgentResult, tuple[str, ...]]:
    """Build an AgentResult from a raw dict, tolerating missing/bad fields.

    Total function: never raises for hostile payloads (custom mapping
    subclasses, raising __str__ entries, NaN, etc.).  Worst case is an
    UNKNOWN result with warnings describing the coercion."""
    warnings: list[str] = []
    try:
        payload: Any = dict(data)
    except Exception:
        return (
            AgentResult(status=AgentResultStatus.UNKNOWN, warnings=("unparseable payload",)),
            ("payload was not a mapping",),
        )
    try:
        version = payload.get("schema_version", RESULT_SCHEMA_VERSION)
    except Exception:
        warnings.append("unreadable schema_version; defaulting to " f"{RESULT_SCHEMA_VERSION}")
        version = RESULT_SCHEMA_VERSION
    try:
        version_int = int(version)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        warnings.append(f"invalid schema_version {_safe_repr(version)}; defaulting to {RESULT_SCHEMA_VERSION}")
        version_int = RESULT_SCHEMA_VERSION
    if version_int not in SUPPORTED_SCHEMA_VERSIONS:
        warnings.append(
            f"unsupported schema_version {version_int}; "
            f"coerced as v{RESULT_SCHEMA_VERSION} partial result"
        )
    try:
        status = _coerce_status(payload.get("status"))
    except Exception:
        status = AgentResultStatus.UNKNOWN
        warnings.append("unreadable status; coerced to unknown")
    try:
        status_raw = payload.get("status")
    except Exception:
        status_raw = None
    if "status" in payload and status is AgentResultStatus.UNKNOWN and status_raw not in (None, "unknown"):
        warnings.append(f"unrecognized status {_safe_repr(status_raw)}; coerced to unknown")
    try:
        result = AgentResult(
            schema_version=RESULT_SCHEMA_VERSION,
            status=status,
            summary=_coerce_str(_safe_get(payload, "summary")),
            files_changed=_coerce_str_list(_safe_get(payload, "files_changed")),
            tests_run=_coerce_int(_safe_get(payload, "tests_run")),
            tests_passed=_coerce_int(_safe_get(payload, "tests_passed")),
            tests_failed=_coerce_int(_safe_get(payload, "tests_failed")),
            verification_results=_coerce_dict_list(_safe_get(payload, "verification_results")),
            review_findings=_coerce_dict_list(_safe_get(payload, "review_findings")),
        requested_followup=_coerce_str(_safe_get(payload, "requested_followup")),
        confidence=_coerce_confidence(_safe_get(payload, "confidence")),
        errors=_coerce_str_list(_safe_get(payload, "errors")),
        warnings=_coerce_str_list(_safe_get(payload, "warnings")),
        metadata=_coerce_metadata(_safe_get(payload, "metadata")),
    )
    except Exception:
        return (
            AgentResult(status=AgentResultStatus.UNKNOWN, warnings=("payload coercion fallback",)),
            tuple(warnings + ["payload coercion fallback"]),
        )
    if version_int not in SUPPORTED_SCHEMA_VERSIONS:
        # Unknown future versions are never trusted at face value: any
        # claimed status is downgraded to PARTIAL with the original version
        # preserved in metadata, so readers stay total as formats evolve.
        try:
            merged_metadata = dict(result.metadata)
        except Exception:
            merged_metadata = {}
        merged_metadata["original_schema_version"] = version_int
        try:
            result = AgentResult(
                schema_version=result.schema_version,
                status=AgentResultStatus.PARTIAL,
                summary=result.summary,
                files_changed=result.files_changed,
                tests_run=result.tests_run,
                tests_passed=result.tests_passed,
                tests_failed=result.tests_failed,
                verification_results=result.verification_results,
                review_findings=result.review_findings,
                requested_followup=result.requested_followup,
                confidence=result.confidence,
                errors=result.errors,
                warnings=result.warnings,
                metadata=merged_metadata,
            )
        except Exception:
            result = AgentResult(
                status=AgentResultStatus.PARTIAL,
                warnings=("unsupported schema_version fallback",),
                metadata={"original_schema_version": version_int},
            )
    return result, tuple(warnings)


def coerce_agent_result(value: object) -> AgentResult | None:
    """Backward-compatible loader for stored ``structured_result`` values.

    Accepts new envelopes (dicts with ``schema_version``), old raw dicts
    (e.g. ``{"changed": True}`` stored before this schema existed), old raw
    lists, JSON-text payloads (as persisted in the ``structured_result`` TEXT
    column), and ``None``.  Never raises; returns ``None`` only when there is
    genuinely no structured payload to normalize.
    """
    try:
        if value is None:
            return None
        if isinstance(value, AgentResult):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                decoded: object = json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
            return coerce_agent_result(decoded)
        if isinstance(value, dict):
            result, _ = agent_result_from_dict(value)
            # Old raw dicts carry no schema fields: keep their content visible
            # in metadata so the upgrade loses nothing.
            known = {
                "schema_version", "status", "summary", "files_changed",
                "tests_run", "tests_passed", "tests_failed",
                "verification_results", "review_findings", "requested_followup",
                "confidence", "errors", "warnings", "metadata",
            }
            extra = {key: item for key, item in value.items() if key not in known}
            if extra and not result.metadata:
                return AgentResult(
                    schema_version=result.schema_version,
                    status=result.status,
                    summary=result.summary,
                    files_changed=result.files_changed,
                    tests_run=result.tests_run,
                    tests_passed=result.tests_passed,
                    tests_failed=result.tests_failed,
                    verification_results=result.verification_results,
                    review_findings=result.review_findings,
                    requested_followup=result.requested_followup,
                    confidence=result.confidence,
                    errors=result.errors,
                    warnings=result.warnings,
                    metadata={"legacy_payload": extra},
                )
            if extra:
                merged = dict(result.metadata)
                merged.setdefault("legacy_payload", extra)
                return AgentResult(
                    schema_version=result.schema_version,
                    status=result.status,
                    summary=result.summary,
                    files_changed=result.files_changed,
                    tests_run=result.tests_run,
                    tests_passed=result.tests_passed,
                    tests_failed=result.tests_failed,
                    verification_results=result.verification_results,
                    review_findings=result.review_findings,
                    requested_followup=result.requested_followup,
                    confidence=result.confidence,
                    errors=result.errors,
                    warnings=result.warnings,
                    metadata=merged,
                )
            return result
        if isinstance(value, (list, tuple)):
            return AgentResult(
                status=AgentResultStatus.UNKNOWN,
                summary=None,
                metadata={"legacy_payload": list(value)},
            )
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Robust output parser (never raises)
# ---------------------------------------------------------------------------

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _try_json_loads(candidate: str) -> object | None:
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _extract_candidate_objects(stdout: str) -> list[Any]:
    """Collect candidate JSON values without ever raising."""
    candidates: list[Any] = []
    try:
        stripped = stdout.strip()
        if stripped:
            whole = _try_json_loads(stripped)
            if whole is not None:
                candidates.append(whole)
        for match in _FENCED_JSON_RE.finditer(stdout or ""):
            parsed = _try_json_loads(match.group(1).strip())
            if parsed is not None:
                candidates.append(parsed)
        # Backward-compatible trailing-line scan (matches the historical
        # extract_structured_result behaviour for plain ``{"a": 1}`` tails).
        for line in reversed((stdout or "").splitlines()):
            text = line.strip().rstrip(",")
            if not text or not text.startswith(("{", "[")):
                continue
            parsed = _try_json_loads(text)
            if parsed is not None:
                candidates.append(parsed)
                break
    except Exception:
        pass
    return candidates


def _looks_like_truncated_json(stdout: str) -> bool:
    try:
        text = (stdout or "").strip()
        if not text:
            return False
        opens = text.count("{") + text.count("[")
        closes = text.count("}") + text.count("]")
        return opens > closes and ("schema_version" in text or '"status"' in text or "'status'" in text)
    except Exception:
        return False


def parse_agent_result(stdout: object) -> ParsedAgentResult:
    """Parse raw agent stdout into a normalized AgentResult.

    Never raises: malformed/partial/plain-text output yields a fallback
    result with an appropriate :class:`ParseMode` instead of an exception,
    so a missing structured payload can never crash a valid execution.
    """
    try:
        if stdout is None:
            return ParsedAgentResult(
                result=AgentResult(status=AgentResultStatus.UNKNOWN),
                parse_mode=ParseMode.EMPTY,
                warnings=("empty output",),
            )
        text = stdout if isinstance(stdout, str) else str(stdout)
        if not text.strip():
            return ParsedAgentResult(
                result=AgentResult(status=AgentResultStatus.UNKNOWN),
                parse_mode=ParseMode.EMPTY,
                warnings=("empty output",),
            )
        for candidate in _extract_candidate_objects(text):
            if isinstance(candidate, dict):
                result, warnings = agent_result_from_dict(candidate)
                mode = ParseMode.STRUCTURED if not warnings else ParseMode.PARTIAL
                return ParsedAgentResult(result=result, parse_mode=mode, warnings=tuple(warnings))
            if isinstance(candidate, list):
                # A bare list is not the schema, but it is structured evidence.
                return ParsedAgentResult(
                    result=AgentResult(
                        status=AgentResultStatus.UNKNOWN,
                        summary=None,
                        metadata={"legacy_payload": candidate},
                    ),
                    parse_mode=ParseMode.PARTIAL,
                    warnings=("top-level list is not the AgentResult schema",),
                )
        if _looks_like_truncated_json(text):
            return ParsedAgentResult(
                result=AgentResult(
                    status=AgentResultStatus.UNKNOWN,
                    summary=text.strip()[:_SUMMARY_MAX_LENGTH],
                    warnings=("truncated or malformed JSON payload",),
                ),
                parse_mode=ParseMode.MALFORMED,
                warnings=("truncated or malformed JSON payload",),
            )
        # Plain-text agent: keep working, record a text summary.
        return ParsedAgentResult(
            result=AgentResult(
                status=AgentResultStatus.UNKNOWN,
                summary=text.strip()[:_SUMMARY_MAX_LENGTH],
            ),
            parse_mode=ParseMode.TEXT,
            warnings=("no structured JSON found; plain-text fallback",),
        )
    except Exception as error:  # absolute last resort: never crash the run
        try:
            fallback = str(stdout)[:_SUMMARY_MAX_LENGTH]
        except Exception:
            fallback = None
        return ParsedAgentResult(
            result=AgentResult(status=AgentResultStatus.UNKNOWN, summary=fallback),
            parse_mode=ParseMode.TEXT,
            warnings=(f"parser internal fallback: {type(error).__name__}",),
        )


# ---------------------------------------------------------------------------
# Five-way outcome distinction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutionOutcome:
    """The five signals that must NOT be treated as equivalent."""

    process_success: bool
    agent_success: bool | None
    verification_success: bool | None
    review_approved: bool | None
    merge_eligible: bool
    reasons: tuple[str, ...] = ()


def evaluate_execution(
    *,
    process_success: bool,
    agent_status: AgentResultStatus | str | None = None,
    verification_success: bool | None = None,
    review_approved: bool | None = None,
) -> ExecutionOutcome:
    """Combine the four evidence signals into a merge-eligibility verdict.

    ``merge_eligible`` is True only when the process succeeded AND the agent
    claimed success AND verification passed AND review approved (when those
    signals are present).  An unknown agent status or a missing verification
    / review signal is never counted as success.
    """
    try:
        if isinstance(agent_status, AgentResultStatus):
            normalized = agent_status
        elif isinstance(agent_status, str):
            normalized = _coerce_status(agent_status)
        else:
            normalized = None
        process_ok = _coerce_bool_signal(process_success)
        agent_success = True if normalized is AgentResultStatus.SUCCESS else (
            False if normalized in (AgentResultStatus.FAILURE, AgentResultStatus.PARTIAL) else None
        )
        # UNKNOWN / missing agent evidence is not success.
        if normalized is AgentResultStatus.UNKNOWN:
            agent_success = False
        reasons: list[str] = []
        if not process_ok:
            reasons.append("process did not succeed")
        if agent_success is not True:
            reasons.append("agent did not report success")
        if verification_success is not True:
            reasons.append("verification did not pass")
        if review_approved is not True:
            reasons.append("review did not approve")
        merge_eligible = bool(
            process_ok
            and agent_success is True
            and verification_success is True
            and review_approved is True
        )
        return ExecutionOutcome(
            process_success=process_ok,
            agent_success=agent_success,
            verification_success=verification_success,
            review_approved=review_approved,
            merge_eligible=merge_eligible,
            reasons=tuple(reasons),
        )
    except Exception:
        try:
            fallback_process = _coerce_bool_signal(process_success)
        except Exception:
            fallback_process = False
        return ExecutionOutcome(
            process_success=fallback_process,
            agent_success=None,
            verification_success=verification_success,
            review_approved=review_approved,
            merge_eligible=False,
            reasons=("outcome evaluation fallback",),
        )


__all__ = [
    "RESULT_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "AgentResult",
    "AgentResultStatus",
    "ExecutionOutcome",
    "ParseMode",
    "ParsedAgentResult",
    "agent_result_from_dict",
    "coerce_agent_result",
    "evaluate_execution",
    "parse_agent_result",
]
