# Plan — A4 Structured Failure Evidence (P2)

> Owner: pi (writer); copilot (test review); opencode (architecture review, async).
> Status: implementation and copilot review complete; suite 353 OK; opencode reply pending.

## Objective

Make classification driven by structured `FailureEvidence` with substring
matching demoted to fallback, without changing `classify()` compatibility or
existing outcomes except where structured evidence wins.

## Improvements over the roadmap paragraph

- `FailureEvidence` is a frozen leaf dataclass with total constructor,
  JSON-safe `to_dict()`, and `is_empty()` so empty evidence preserves legacy
  behavior exactly.
- Evidence is populated at the two workflow failure-recording choke points
  (from `RunResult`, from the failing verification check) plus the legacy
  verifier path — no new fields threaded through runner/kernel contracts.
- Secret safety: agent commands persist executable-only (argv embeds the raw
  prompt); stderr peeks are capped at 500 chars and redacted; arbitrary
  mappings passed to `record_failure` are deep-scrubbed.
- Persistence is a new additive `structured_evidence TEXT` column (schema v7)
  with a legacy repair loop; `Failure.structured_evidence` is an appended
  field; migration assertion bumped to `[1..7]`.
- Structured-first branch mirrors string-branch outcomes; legacy verification
  commands (exit code, no check class) persist evidence but keep legacy text
  rules instead of inventing an agent error.

## Verification

- 17 new failure-kernel tests: 6 structured-beats-strings before/after pairs,
  precedence combinations, hostile fields, secret redaction, defensive
  malformed checks, legacy persistence, migration repair, workflow integration.
- Full suite: 353 passing, 4 environment skips.
- Copilot review: 5 findings, all fixed with regressions.
- Backup: `/tmp/agentops-backup-a4-evidence-20260916-211809`.
