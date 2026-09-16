# Output — A4 Structured Failure Evidence

> Delivered 2026-09-16. Full suite 353 OK (4 skips). Copilot review closed.
> OpenCode architecture review pending.

## What was built

- **`FailureEvidence`** (`failure.py`): frozen dataclass with source,
  check_class, exit_code, timed_out, cancelled, terminated, command, and
  capped stderr_peek; total constructor/`to_dict()`; `is_empty()` fallback.
- **Structured-first classification:** `classify(..., evidence=)` consults
  structured signals (cancelled → timeout → terminated/negative-exit →
  check-class map → nonzero-exit) before substring heuristics, with identical
  outcome payloads.
- **Evidence construction:** `_record_agent_failure` (RunResult signals,
  executable-only command, redacted stderr tail),
  `_record_verification_failure` (failing-check class/exit/command, defensive
  against malformed checks), `_record_legacy_verification_failure` (exit
  code/command persisted, legacy text rules decide).
- **Persistence:** additive `structured_evidence` column, schema v7, repair
  loop for legacy DBs, `serialize_failure` exposure, `[1..7]` assertion.
- **Package exports:** `FailureEvidence` in `failure.py` and `agentops`.

## Verification

- 17 new tests; full suite green; `git diff --check` clean.
- Copilot adjudication: fixed legacy-path evidence, command/dict secret
  scrubbing, hostile `to_dict` fields, malformed-check defense, precedence
  coverage — all with regressions.
- No new module: no executable rebuild.

## Follow-ups

- Adjudicate the pending OpenCode architecture reply when it arrives.
