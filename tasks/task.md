# Task

Inspect the current agent registry and selection logic.

Preserve current explicit preferences and fallback behavior.

Upgrade the registry so every installed agent can advertise capabilities.

An agent profile should contain:

identifier
display name
executable path
detected version
availability
roles
capabilities
supported structured output
cancellation support
timeout support
interactive/noninteractive support
configured priority
optional metadata

Initial capabilities:

planning
architecture
implementation
debugging
refactoring
testing
code review
security review
documentation
repository exploration

Create an AgentCapabilityResolver.

Create an AgentRouter that accepts:

role
task description
repository characteristics
required capabilities
available agents
user preferences
historical performance if available

Initially use deterministic scoring.

The router must return an explainable decision:

selected agent
score
alternatives
reasons
rejected candidates
constraints

Example:

Selected: codex

Reasons:

implementation capability matched
preferred agent
available
fallback not required

Persist routing decisions as events.

Allow routing to be disabled.

When disabled, preserve existing static preference/fallback behavior.

Add tests for:

capability matching
disabled agents
unavailable agents
fallback
explicit user preference
missing capabilities
deterministic scoring
explainability

Run the entire test suite.

## Plan (Agent registry capability resolver and deterministic router — 2026-09-15)

### Subtask 1 — Audit registry and define compatible profile contract
Agent: pi (sole writer)
Depends on: none
Status: done

Map current `AgentConfig`, `DetectedAgent`, adapter capabilities, role gates, preference fallback, and event persistence. Define additive profile fields without breaking positional constructors or existing `select()` behavior.

---

### Subtask 2 — Implement capability resolver and agent profiles
Agent: pi (sole writer)
Depends on: Subtask 1
Status: done

Add the requested capability vocabulary, resolver, profile construction, configured priority/metadata support, and deterministic capability matching while preserving disabled/unavailable/role/exclusion behavior.

---

### Subtask 3 — Implement explainable deterministic router
Agent: pi (sole writer)
Depends on: Subtask 2
Status: done

Add `AgentRouter`, deterministic scoring, explicit preference/fallback handling, routing-disable switch, alternatives/rejections/reasons/constraints, and routing-decision event persistence. Integrate it with workflow selection without changing direct registry compatibility.

---

### Subtask 4 — Add coverage and run reviews
Agent: pi (sole writer)
Depends on: Subtask 3
Status: done (OpenCode architecture reply pending asynchronously)

Add tests for capability matching, disabled/unavailable agents, fallback, explicit preferences, missing capabilities, deterministic scoring, explainability, disabled routing, and event persistence. Run focused tests, full suite, and read-only reviewer checks.

---

### Subtask 5 — Update memory, task output, and report
Agent: pi (sole writer)
Depends on: Subtask 4
Status: done

Update canonical memory and append the task output. Commit and push unless the user says otherwise; do not rebuild the executable because packaging is not affected.

---

## Output (Agent registry capability resolver and deterministic router)

Status: implementation complete; architecture review pending.

- Added `agentops/routing.py` with profiles, resolver, deterministic scoring router, explainable decisions, and input normalization.
- Extended task capabilities, config/profile metadata, registry version/profile support, workflow routing-event persistence, routing switch, README documentation, and package exports.
- Added `tests/test_routing.py` with 20 tests for all requested coverage categories plus registry/config compatibility.
- Full suite: 309 passing, 3 environment skips; `git diff --check` clean.
- Copilot snapshot review: fixed valid scalar-selector compatibility; rejected two snapshot-packaging artifacts with evidence.
- OpenCode architecture review requested asynchronously; pending.
- Backup: `/tmp/agentops-backup-b7-router-20260916-182309`; snapshot: `/tmp/agentops-review-router`.
- No executable rebuild: packaging untouched.

---

## Plan (PHASE 3 re-audit + PHASE 4 build — 2026-09-15)

### Subtask 1 — Phase 3 re-audit (no code expected)
Agent: pi (sole writer)
Depends on: none
Status: done

Phase 3 code untouched since the 2026-09-15 audit (only tasks/*.md modified at plan time). Suite re-verified green in Subtask 5.

---

### Subtask 2 — Versioned event model + store + query + subscription
Agent: pi (sole writer)
Depends on: Subtask 1
Status: done

New `agentops/events.py` leaf: `EVENT_SCHEMA_VERSION`, 26 `EventType` members, `EventSeverity`, `Event` dataclass (id, timestamp, workflow/task/agent-run ids, type, severity, message, payload, schema_version), tolerant `to_dict`/`from_dict`/`coerce_event`, `EventSubscriber` protocol + thread-safe `EventBus` (never raises into publishers). State: additive `typed_events` table (schema v4, explicit-column inserts, legacy-DB repair loop), `record_typed_event` + `query_events` (chronological, pagination, task/agent-run/type filters) returning `Event` objects; legacy `event`/`list_events` untouched; store notifies bus on write (best-effort). Payloads never carry secrets (redaction reuse + tests).

---

### Subtask 3 — Artifact store + metadata + retention
Agent: pi (sole writer)
Depends on: Subtask 2
Status: done

New `agentops/artifacts.py`: `ArtifactKind` (plans, diffs, patches, execution_logs, verification_reports, reviews, failure_analysis, risk_reports, workflow_summaries, custom), `Artifact` + `ArtifactMetadata`, `ArtifactStore` (root containment incl. traversal rejection, 0o700/0o600 perms, sha256 on write + verify on read, retention `prune`, missing-artifact sentinel, redaction on text writes). State: additive `artifacts` table (schema v5) with create/get/list/delete. Large content stays in files, metadata in SQLite.

---

### Subtask 4 — CLI + controller + GUI exposure
Agent: pi (sole writer)
Depends on: Subtask 3
Status: done

CLI `events` (workflow/task/run/type filters + limit/offset) and `artifacts` (list/show/prune) mirroring existing styles; controller pass-throughs; GUI artifacts visibility via the Logs-tab pattern (read-only, Tk-thread safe).

---

### Subtask 5 — Tests + full suite
Agent: pi (sole writer)
Depends on: Subtask 4
Status: done

`tests/test_events.py` (14 tests) + `tests/test_artifacts.py` (11 tests). Full suite 209 passing (184 prior + 25 new), 2 skips (1 pre-existing platform skip + 1 Windows perms skip per standing precedent). Two self-found bugs fixed during testing (FK-constrained refs, repr-quoted legacy_type).

---

### Subtask 6 — Snapshot review (read-only)
Agent: copilot (tests) + opencode (architecture, DONE)
Depends on: Subtask 5
Status: done (opencode) / done (copilot — findings received and fixed, see below)

opencode findings received and fixed (adjudication follows); copilot findings received 2026-09-15 (2 issues + 3 test gaps), both fixed: (1) artifact crash-window — true cross-system atomicity is impossible, so narrowed it: temp-write + atomic rename (no partial files), crash-window docstring, `scan_files`/`find_orphans` helpers + orphan test; (2) typed-event legacy compat — bridged by mirroring typed events into the legacy table in the same transaction (workflow-less events stay typed-only, documented). Gap tests added: crash-window/orphan, legacy-mirror compat, symlink + absolute-path traversal. While stress-testing, a real flake surfaced (concurrent first-open SQLITE_LOCKED at WAL pragma + leaked handles) — fixed with whole-open retry (8 attempts, backoff, close-on-failure) + test-handle hygiene; 10/10 stress runs clean. Suite 217 OK (3 environment skips).

**opencode adjudication (15 items). Fixed before merge:** T1 (bus notify moved outside store RLock), M1 (INSERT OR IGNORE on all 5 migration version inserts), A1 (`_jsonable` deep sanitizer for payloads/metadata). **Contract hardening fixed:** L1 (docstring — GUI reads via controller queries; bus is the shared live/API protocol), L4 (public `redact_text`, `_redact` kept as alias), A3 (uniform ValueError pagination, matching existing list_*), A4 (hash-covers-stored-bytes documented), A5 (`EventBus(error_handler)`), T2 (publisher-thread contract documented), T4 (reentrant emits drained iteratively), M4 (store/registry pairing documented). **Noted, not changed:** T3 (pre-existing stateless-fallback, out of scope), L2 (pre-existing Task leak — roadmap Phase 1), M2 (invalid — single atomic commit, no partial shape possible), M3 (by design — global events have no workflow), L3 (typed timeline authoritative; legacy retained for old readers), A2 (reader-side tolerance documented). 5 new regression tests; suite 214 OK.

---

### Subtask 7 — Memory + report + commit + push
Agent: pi
Depends on: Subtask 6
Status: done

Committed `5742b2a`, pushed to origin main. No exe rebuild (packaging untouched).

---

## Output (PHASE 3 re-audit + PHASE 4 build)

**Phase 3:** no gaps (reaffirmed — code untouched since the prior audit; suite green).

**Phase 4 delivered** (`5742b2a`, pushed to origin main):

- `agentops/events.py` (leaf): `Event` schema v1 (id, timestamp, workflow/task/agent-run ids, type, severity, message, payload, schema_version), 27 `EventType` members covering all 26 specified + NOTE, total `event_from_dict`/`coerce_event` (legacy kinds → NOTE with `legacy_type` preserved), `EventBus` (thread-safe fan-out, per-subscriber isolation, error handler, iterative reentrancy drain, delivery count).
- `agentops/artifacts.py`: `ArtifactKind` (10 families covering all 9 specified + custom), `Artifact` + `ArtifactMetadata`, `ArtifactStore` (containment + traversal rejection, 0o700/0o600 perms, sha256 verify-on-read, retention prune, missing/integrity errors, redaction on text writes). Metadata in SQLite, bytes in files.
- State: additive schema v4 (`typed_events`) + v5 (`artifacts`), plain-TEXT refs (no FK — recovery evidence), race-safe version inserts, `record_typed_event` + `query_events` (chronological, pagination with uniform ValueError contract, workflow/task/run/type filters), artifact CRUD, bus notification outside the lock. Legacy `events` table + `list_events` untouched.
- CLI `events` / `artifacts` (list/show/prune-keep); controller `query_events`/`list_artifacts`/`read_artifact`; GUI Artifacts tab (Logs-tab pattern, Tk-safe).
- Tests: `test_events.py` (19) + `test_artifacts.py` (11); full suite 214 OK (2 environment skips). No secrets in payloads (redaction + test).
- Review: opencode architecture review — 15 findings, 3 blockers fixed (T1/M1/A1) + 8 hardening, 4 noted with rationale (see Subtask 6). Copilot test review pending at push; follow-up if needed. No exe rebuild (packaging untouched).
- Files: `events.py`, `artifacts.py` (new); `state.py`, `cli.py`, `gui.py`, `gui_controller.py`, `logging.py` (`redact_text`), `__init__.py`; `test_events.py`, `test_artifacts.py` (new). Backup: `/tmp/agentops-backup-phase4/diff.patch`. Snapshots: `/tmp/agentops-review-phase4/`.

---

## Plan (SUPERSEDED — Failure Analysis audit, kept for history)

### Subtask 1 — Spec-to-code audit
Agent: pi (sole writer)
Depends on: none
Status: done

Verified every header bullet against `agentops/agentops/failure.py`, `workflow.py`, `cli.py`, `gui_controller.py`: Failure object carries all 14 required fields (verification linkage as `verification_run_id`, suggestion as `recommended_action` — functionally equivalent, noted in Output); all 16 categories present; deterministic classifier + repair planner with all 6 decisions; bounded retries (max attempts, backoff, category rules, inherited context + verification evidence); parent-linked repair AgentRuns; persisted decisions; repair chain in `failures`/`recover` CLI and GUI failure views; failed worktrees preserved per `finalize.py` conflict-task path.

---

### Subtask 2 — Test verification + full suite
Agent: pi (sole writer)
Depends on: Subtask 1
Status: done

`tests/test_failure_kernel.py`: 28 tests OK (every category + repair decisions + recovery). Full suite: 184 passing, 1 pre-existing platform skip. No code changes required — implementation predates this task header (Phase 3, committed `935f4dc`, copilot-reviewed with 4 findings fixed).

---

### Subtask 3 — Memory + report
Agent: pi
Depends on: Subtask 2
Status: done

## Output (Failure Analysis audit)

No gaps found — the subsystem as specified already exists and is tested:

- Failure record (`failure.py:110`): id, workflow_id, task_id, agent_run_id, source, category, severity, retryable, repairable, evidence, primary_error, verification_run_id (= related verification checks, via the linked run), recommended_action (= suggested action), created_at (+ updated_at, attempt, repair_cycle, recovery_state).
- All 16 categories (`failure.py:20-35`): AGENT_ERROR, PROCESS_ERROR, TIMEOUT, CANCELLATION, TEST_FAILURE, LINT_FAILURE, TYPECHECK_FAILURE, BUILD_FAILURE, VERIFICATION_FAILURE, ENVIRONMENT_FAILURE, DEPENDENCY_FAILURE, GIT_CONFLICT, DIRTY_WORKTREE, POLICY_VIOLATION, REVIEW_REJECTION, UNKNOWN.
- Deterministic classifier first; repair planner decides all 6 (`failure.py:59-65`): retry same/different agent, repair implementation, rerun verification, request human approval, stop permanently. No blind retry: `RetryPolicy` (max_attempts, backoff base/max/factor), category retry rules, inherited context + prior verification evidence (`workflow.py:461-462`, `_prompt_with_history`).
- Repair attempts mint new parent-linked AgentRuns (`workflow.py:557-573`); decisions persisted in `failures` table; repair chain exposed via `failures`/`recover` CLI (`cli.py:53-59`) and GUI (`gui_controller.py:serialize_failure`).
- Tests: 28 failure-kernel tests (every category + repair decision + recovery paths); full suite 184 OK. Failed worktrees stay inspectable via the conflict-task + preserved-worktree path.
- Prior review coverage: copilot snapshot review of this exact code fixed 4 findings (see `.agents/memory/lessons.md` 2026-09-14 entry); no re-review needed as no lines changed.

Note: the previous Plan/Output below (structured-results work) is retained for history but belonged to an earlier task header; the header has since been replaced twice by an external process. Backup of pre-audit tree: `/tmp/agentops-backup-failure-audit/diff.patch`.

---

## Plan (SUPERSEDED — structured-results work, kept for history)

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

# USER NOTE TO AGENT: Put everything below this point into dedicated files in "D:\admin\code\projects\.agents\...". Use this as a replaceable, updated summary of recent projects/prompts.

## Recent summary (replaceable — details live in .agents/)

- **Phase 2 — Verification Kernel: DONE.** Deterministic kernel (`verification_model.py`, `verification_kernel.py`, schema v2, config profiles, `verify` CLI, GUI summaries). 122 tests OK. Details: `.agents/outputs/phase-2-verification-kernel.md`.
- **Phase 3 — Failure, Repair, and Recovery Kernel: DONE.** First-class failure subsystem (`failure.py` leaf: 16 categories, deterministic classifier, RetryPolicy, RepairPlan, 6 interruption contexts), schema v3 `failures` table, workflow bounded-retry + inherited context + parent-linked AgentRuns, `failures`/`recover` CLI, controller/GUI views. 146 tests OK (1 pre-existing skip); exe rebuilt (14,756,616 bytes). Plan: `.agents/plans/phase-3-failure-recovery-kernel-plan.md`. Details: `.agents/outputs/phase-3-failure-recovery-kernel.md`.
- **Pending:** copilot/opencode review findings (requested, replies pending); commit strategy for accumulated uncommitted work (AgentRun + Verification + Failure kernels).
