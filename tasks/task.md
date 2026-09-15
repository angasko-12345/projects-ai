# Task

Upgrade AgentOps agent execution to support structured results.

Do not trust natural-language agent output as the authoritative indication of success.

Define a versioned AgentResult schema containing:

status
summary
files_changed
tests_run
tests_passed
tests_failed
verification_results
review_findings
requested_followup
confidence
errors
warnings
metadata

Support agents that return:

structured JSON
plain text
malformed/partial output

Implement a robust parser.

Plain-text agents must continue working.

The parser must never cause an otherwise valid agent execution to crash solely because structured output is unavailable.

Store the structured result with the AgentRun.

Define a distinction between:

process success
agent success
verification success
review approval
merge eligibility

These must NOT be treated as equivalent.

Add schema versioning so future result formats can evolve.

Add comprehensive tests for:

valid JSON
malformed JSON
empty output
partial output
plain text
process failure
verification failure
reviewer rejection

Preserve backward compatibility.
Run the full test suite.

## Plan

### Subtask 1 — Versioned AgentResult schema + robust parser (leaf module)
Agent: pi (sole writer)
Depends on: none
Status: done

Create `agentops/agent_result.py` (leaf, no I/O): `AgentResultStatus`, `ParseMode`, `AgentResult` dataclass with all 12 required fields + `schema_version`, `ParsedAgentResult`, `parse_agent_result()` (structured JSON incl. fenced blocks + trailing-line scan, partial/malformed tolerance, plain-text + empty fallback, never raises), `coerce_agent_result()` for old-DB backward compat, `ExecutionOutcome` + `evaluate_execution()` distinguishing process/agent/verification/review/merge with `merge_eligible` requiring all applicable signals.

---

### Subtask 2 — Runner + workflow + state integration
Agent: pi (sole writer)
Depends on: Subtask 1
Status: done

Runner stores normalized `AgentResult.to_dict()` as `structured_result` (TEXT column unchanged, no migration); workflow post-hoc fallbacks use the new parser wrapped so they can never crash a valid run; `extract_structured_result` kept unchanged for backward compat; prompt seam gains an optional JSON contract line with plain-text fallback stated.

---

### Subtask 3 — Comprehensive tests + full suite
Agent: pi (sole writer)
Depends on: Subtask 2
Status: done

New `tests/test_agent_result.py`: valid JSON, malformed JSON, empty output, partial output, plain text, process failure, verification failure, reviewer rejection, versioning/migration, old-shape backward compat, never-crash fuzz, merge-eligibility non-equivalence. Then `python -m unittest discover -s tests` → 184 passing (1 pre-existing platform skip).

---

### Subtask 4 — Snapshot reviews (read-only)
Agent: copilot (tests) + opencode (architecture) + fcc-claude (security, if server up)
Depends on: Subtask 3
Status: done

Copy relevant files to `/tmp/agentops-review-structured-results/`, run each reviewer via `agentops run <agent>` with explicit READ-ONLY prompt or via Intercom only after `intercom_list` confirms liveness. Pi addresses findings, re-runs suite. → DONE 2026-09-15: copilot snapshot review completed (94s, 4 findings, all fixed + 6 regression tests); no live Intercom peers so opencode/fcc-claude reviews not requested.

---

### Subtask 5 — Memory + report
Agent: pi
Depends on: Subtask 4
Status: done

Update `.agents/memory/{project,architecture,decisions,lessons}.md`, append `## Output` summary to task.md, report to user. No exe rebuild (packaging untouched) unless review finds packaging impact. → DONE 2026-09-15, no packaging impact.

## Output

Structured-results upgrade complete. `agentops/agent_result.py` (leaf): versioned `AgentResult` schema v1 with all 12 required fields, `parse_agent_result` (structured/fenced/trailing-line JSON, truncated-JSON detection, plain-text + empty fallbacks, never raises), `coerce_agent_result` (dicts, lists, JSON TEXT, legacy payloads preserved), `evaluate_execution` (process/agent/verification/review/merge kept distinct; merge requires all four). Runner + workflow store normalized envelopes in the existing `structured_result` TEXT column (no migration); `extract_structured_result` retained; prompt seam advertises the optional JSON contract, plain text still valid. Tests: `tests/test_agent_result.py` (40 tests: valid/malformed/empty/partial/plain-text, process/verification/review failures, versioning, backward compat, never-crash, non-equivalence); full suite 184 passing, 1 pre-existing skip. Review: copilot read-only snapshot review, 4 findings fixed (+1 self-found repr crash via new regression test). Files changed: `agentops/agent_result.py` (new), `agentops/runner.py`, `agentops/workflow.py`, `agentops/__init__.py`, `tests/test_agent_result.py`. Backup: `/tmp/agentops-backup-structured-results/`. Review snapshot: `/tmp/agentops-review-structured-results/`.

---

# USER NOTE TO AGENT: Put everything below this point into dedicated files in "D:\admin\code\projects\.agent\...". Use this as a replaceable, updated summary of recent projects/prompts.

## Recent summary (replaceable — details live in .agent/)

- **Phase 2 — Verification Kernel: DONE.** Deterministic kernel (`verification_model.py`, `verification_kernel.py`, schema v2, config profiles, `verify` CLI, GUI summaries). 122 tests OK. Details: `.agent/outputs/phase-2-verification-kernel.md`.
- **Phase 3 — Failure, Repair, and Recovery Kernel: DONE.** First-class failure subsystem (`failure.py` leaf: 16 categories, deterministic classifier, RetryPolicy, RepairPlan, 6 interruption contexts), schema v3 `failures` table, workflow bounded-retry + inherited context + parent-linked AgentRuns, `failures`/`recover` CLI, controller/GUI views. 146 tests OK (1 pre-existing skip); exe rebuilt (14,756,616 bytes). Plan: `.agent/plans/phase-3-failure-recovery-kernel-plan.md`. Details: `.agent/outputs/phase-3-failure-recovery-kernel.md`.
- **Pending:** copilot/opencode review findings (requested, replies pending); commit strategy for accumulated uncommitted work (AgentRun + Verification + Failure kernels).
