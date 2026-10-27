"""Structured agent-status and review-verdict helpers (Issue #12 gates)."""

from __future__ import annotations

import re

from ..agent_result import (
    AgentResultStatus,
    _coerce_status,
    coerce_agent_result,
    parse_agent_result,
)
from ..logging import redact_text
from ..verdict import _extract_candidate_objects, parse_verdict


def _requires_review_verdict(role: object) -> bool:
    """Whether a task role is required to end with an explicit review verdict."""
    normalized = " ".join(
        str(role or "").strip().lower().replace("_", " ").replace("-", " ").split()
    )
    return normalized in {"review", "security review", "security"}


def verdict_summary_for_task(task: object) -> dict[str, object] | None:
    """Small redacted verdict summary for a review/security task, if any."""
    try:
        role = getattr(task, "role", None)
        if not _requires_review_verdict(role):
            return None
        verdict = parse_verdict(getattr(task, "result", None))
        if verdict is None:
            return {
                "role": role,
                "verdict": "missing",
                "approved": False,
                "blocking": [],
                "non_blocking": [],
            }
        return {
            "role": role,
            "verdict": verdict.verdict,
            "approved": verdict.is_approved,
            "blocking": [redact_text(item)[:500] for item in verdict.blocking],
            "non_blocking": [redact_text(item)[:500] for item in verdict.non_blocking],
        }
    except Exception:
        return None


def _safe_workflow_structured(stdout: object) -> object | None:
    """Best-effort structured fallback for post-hoc run recording."""
    try:
        if not isinstance(stdout, str):
            return None
        parsed = parse_agent_result(stdout)
        result = parsed.result
        extra_warnings = tuple(dict.fromkeys((*result.warnings, *parsed.warnings)))
        metadata = dict(result.metadata)
        metadata.setdefault("parse_mode", parsed.parse_mode.value)
        from dataclasses import replace

        return replace(result, warnings=extra_warnings, metadata=metadata).to_dict()
    except Exception:
        return None


_SUCCESS_KEYS = frozenset({"success", "successful", "succeeded", "passed", "pass", "ok"})


_FALSE_LIKE = frozenset({"false", "0", "no", "n", "off", "fail", "failed", "error", "errored", ""})


def _is_verdict_only(payload: object) -> bool:
    """True for a review-verdict object, which is not an agent-status signal."""
    try:
        if not isinstance(payload, dict):
            return False
        if "verdict" not in payload or "status" in payload:
            return False
        return not (_SUCCESS_KEYS & frozenset(payload))
    except Exception:
        return False


def _payload_denies_success(payload: object) -> bool:
    """True when a structured payload explicitly reports non-success."""
    try:
        if not isinstance(payload, dict):
            return False
        for key in _SUCCESS_KEYS:
            if key not in payload:
                continue
            value = payload[key]
            if value is None:
                continue
            if isinstance(value, bool):
                if not value:
                    return True
            elif isinstance(value, (int, float)):
                if value == 0:
                    return True
            elif isinstance(value, str):
                if value.strip().lower() in _FALSE_LIKE:
                    return True
        return False
    except Exception:
        return False


def _stdout_candidate_dicts(stdout: object) -> list[dict]:
    """Best-effort JSON-object scan of agent stdout. Never raises.

    One pathological line must not abort the whole scan: every parse failure
    -- including ``RecursionError`` from pathologically nested JSON -- is
    skipped per candidate so an earlier structured signal is still seen,
    rather than collapsing the scan to "no candidates" (which the callers
    would read as the legacy plaintext path).
    """
    found: list[dict] = []
    try:
        import json as _json

        if not isinstance(stdout, str) or not stdout.strip():
            return found
        try:
            whole = _json.loads(stdout.strip())
        except (ValueError, TypeError, RecursionError):
            whole = None
        if isinstance(whole, dict):
            found.append(whole)
        for match in re.finditer(r"```(?:json)?\s*\n(.*?)```", stdout, re.DOTALL | re.IGNORECASE):
            try:
                parsed = _json.loads(match.group(1).strip())
            except (ValueError, TypeError, RecursionError):
                continue
            if isinstance(parsed, dict):
                found.append(parsed)
        for line in reversed(stdout.splitlines()):
            text = line.strip().rstrip(",")
            if not text or not text.startswith("{"):
                continue
            try:
                parsed = _json.loads(text)
            except (ValueError, TypeError, RecursionError):
                continue
            if isinstance(parsed, dict):
                found.append(parsed)
                break
    except Exception:
        pass
    return found


def _structured_agent_status(result: object) -> AgentResultStatus | None:
    """Authoritative agent-level outcome for a runner result, if any.

    Prefers the runner-attached ``structured_result`` envelope (what the
    runner parsed from stdout); falls back to parsing ``stdout`` directly
    so runner doubles that skip the envelope are still honoured. Returns
    ``None`` when no structured payload is available -- the legacy /
    plaintext path -- so callers keep the historical exit-code behaviour.
    Review-verdict objects (``{"verdict": ...}`` without a ``status``) are
    not agent-status signals and are skipped, so a pure verdict line can
    never masquerade as agent success or failure. Never raises.
    """
    try:
        stdout_candidates = _stdout_candidate_dicts(getattr(result, "stdout", None))
        candidates = [item for item in stdout_candidates if not _is_verdict_only(item)]
        if candidates:
            coerced = coerce_agent_result(candidates[-1])
            if coerced is not None:
                return coerced.status
            return AgentResultStatus.UNKNOWN
        verdict_only_candidates = [item for item in stdout_candidates if _is_verdict_only(item)]
        if verdict_only_candidates:
            # The only structured JSON in stdout is a review verdict
            # object, which is not an agent-status signal. The real runner
            # attaches an envelope parsed from that same stdout; its
            # UNKNOWN status is the verdict misread as an agent report,
            # not a claimed outcome. Keep it out of the gate: return None
            # (legacy path) exactly as the no-envelope doubles do.
            # 2026-10-18 regression: a compliant reviewer output (prose +
            # final verdict line, what the review prompt mandates) FAILED
            # the status gate live through workflows b901b8ac reviews.
            return None
        coerced = coerce_agent_result(getattr(result, "structured_result", None))
        if coerced is not None:
            envelope = getattr(result, "structured_result", None)
            if isinstance(envelope, dict) and _is_verdict_only(envelope):
                return None
            return coerced.status
    except Exception:
        pass
    return None


def _explicit_success_denied(result: object) -> bool:
    """True when any structured payload explicitly denies success.

    Catches ``{"success": false}``-shaped output that carries no ``status``
    field and would otherwise coerce to UNKNOWN. Verdict-only objects are
    not success signals either way and are ignored here; the verdict gate
    judges them. Never raises.
    """
    try:
        payloads: list[object] = []
        envelope = getattr(result, "structured_result", None)
        if isinstance(envelope, str):
            try:
                import json as _json

                envelope = _json.loads(envelope.strip())
            except (ValueError, TypeError, AttributeError):
                envelope = None
        if isinstance(envelope, dict):
            payloads.append(envelope)
            try:
                legacy = envelope.get("metadata", {}).get("legacy_payload")
            except (AttributeError, TypeError):
                legacy = None
            if isinstance(legacy, dict):
                payloads.append(legacy)
        payloads.extend(
            item
            for item in _stdout_candidate_dicts(getattr(result, "stdout", None))
            if not _is_verdict_only(item)
        )
        return any(_payload_denies_success(payload) for payload in payloads)
    except Exception:
        return False


#: Verdict values that refuse the change, normalised to lower snake_case.
_DENYING_VERDICTS = frozenset(
    {
        "reject",
        "rejected",
        "changes_requested",
        "request_changes",
        "needs_changes",
        "deny",
        "denied",
        "fail",
        "failed",
    }
)


def _review_payload_denies_success(payload: object) -> bool:
    """True when one top-level review-output object refuses the change.

    Only the object's own top-level keys are read: a success flag, a status,
    a verdict, or a non-empty ``blocking`` list next to a verdict. Nested
    values (quoted tool output, sub-results) are never inspected, so a
    reviewer reporting someone else's ``{"success": false}`` inside a field
    does not deny its own verdict. Never raises.
    """
    try:
        if not isinstance(payload, dict):
            return False
        if _payload_denies_success(payload):
            return True
        if "status" in payload and _coerce_status(payload["status"]) in (
            AgentResultStatus.FAILURE,
            AgentResultStatus.PARTIAL,
        ):
            return True
        if "verdict" in payload:
            verdict = payload["verdict"]
            if isinstance(verdict, str):
                normalized = "_".join(verdict.strip().lower().replace("-", " ").split())
                if normalized in _DENYING_VERDICTS:
                    return True
            blocking = payload.get("blocking")
            if isinstance(blocking, (list, tuple)) and blocking:
                return True
        return False
    except Exception:
        return False


def _review_output_denies_success(stdout: object) -> bool:
    """True when ANY structured object in review output refuses the change.

    The review gate otherwise trusts only the final verdict object, so
    ``{"success": false}`` or a REJECT followed by a final APPROVE used to
    pass. This scans the same top-level objects the verdict parser reads
    (whole-output object, ``json``/unlabelled fences, bare ``{...}`` lines;
    other-language fences are illustrative and skipped) and fails closed if
    any of them denies success, whatever follows it. On an internal error it
    also fails closed (returns True): this helper only ever runs once an
    approval has been parsed, so a crash must not read as "no denial".
    """
    try:
        if not isinstance(stdout, str) or not stdout.strip():
            return False
        return any(
            _review_payload_denies_success(payload)
            for payload in _extract_candidate_objects(stdout)
        )
    except Exception:
        return True
