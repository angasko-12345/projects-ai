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
Status: done

Add tests for capability matching, disabled/unavailable agents, fallback, explicit preferences, missing capabilities, deterministic scoring, explainability, disabled routing, and event persistence. Run focused tests, full suite, and read-only reviewer checks.

---

### Subtask 5 — Update memory, task output, and report
Agent: pi (sole writer)
Depends on: Subtask 4
Status: done

Update canonical memory and append the task output. Commit and push unless the user says otherwise; do not rebuild the executable because packaging is not affected.

---

## Output (Agent registry capability resolver and deterministic router)

Status: implementation and both requested reviews complete.

- Added `agentops/routing.py` with profiles, resolver, deterministic scoring router, explainable decisions, and input normalization.
- Extended task capabilities, config/profile metadata, registry version/profile support, workflow routing-event persistence with executed-agent tracking, routing switch, README documentation, and package exports.
- Added `tests/test_routing.py` with 25 routing tests for all requested coverage categories plus registry/config compatibility; added one adapter regression test.
- Full suite: 336 passing, 4 environment skips; `git diff --check` clean.
- Copilot snapshot review: fixed valid scalar-selector compatibility; rejected two snapshot-packaging artifacts with evidence.
- OpenCode architecture review: adjudicated all eight findings. Fixed non-ASCII version decoding, role-derived task-capability selection, legacy capability consolidation, executed-agent/event agreement, and single-profile input. Rejected stale bottom-import and stale route-exception observations with code evidence; retained full-vocabulary empty-role profiles by design; swept all `AppConfig` constructions for positional compatibility.
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

## Plan (A3 Shared ProcessRuntime — 2026-09-16)

### Objective
Agent: pi (sole writer)
Depends on: none
Status: done

Implement roadmap A3 as a behavior-preserving shared process layer used by `AgentRunner`, `VerificationKernel`, and legacy `Verifier`. Remove kernel/verifier dependence on runner internals while keeping all public APIs, outcomes, timeouts, cancellation behavior, logging, and persistence integration unchanged except for explicitly documented safety fixes.

---

### Subtask 1 — Audit current process paths
Agent: pi
Depends on: none
Status: done

Audit `runner.py`, `verification_kernel.py`, `verification.py`, environment handling, Windows spawn flags, POSIX process-group behavior, cancellation/timeout paths, injectable factories, and existing tests. Backup: `/tmp/agentops-backup-a3-runtime-20260916-203040`.

---

### Subtask 2 — Improve the A3 design
Agent: pi
Depends on: Subtask 1
Status: done

Improvements over the roadmap paragraph:

- Include legacy `Verifier` in the shared runtime so runner stops being the implicit process layer for all three execution paths.
- Give every default runtime spawn one cross-platform policy: Windows `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`; POSIX `start_new_session=True`.
- Route Windows `taskkill` cleanup through the same no-window spawn path.
- Preserve injectable process factories; custom factories retain their existing spawn behavior.
- Preserve `AgentRunner._environment`, `AgentRunner.terminate`, `AgentRunner._communicate_with_cancel`, `OperationCancelled`, and `VerificationKernel(process_factory=...)` as compatibility seams.
- Terminate the child process for cooperative cancellation, timeouts, and `asyncio.CancelledError` before propagating cancellation.

---

### Subtask 3 — Implement `ProcessRuntime`
Agent: pi
Depends on: Subtask 2
Status: done

Add `agentops/runtime.py` with environment construction, unified spawn flags, cancellation-aware communication, timeout handling, process-group termination, byte-level result capture, and an injectable spawn factory. Refactor runner, kernel, and legacy verifier to delegate without changing their public signatures.

---

### Subtask 4 — Add regression and parity tests
Agent: pi
Depends on: Subtask 3
Status: done

Add `tests/test_runtime.py` for environment policy, spawn flags, timeout/cancellation, process-group cleanup, custom factories, and real agent-plus-verification smoke coverage. Extend existing runner/kernel/legacy coverage only where needed to lock behavior.

---

### Subtask 5 — Reviews, full suite, packaging, and records
Agent: pi; reviewers: copilot (tests), opencode (architecture)
Depends on: Subtask 4
Status: done (both reviews closed)

Run read-only snapshot reviews, address valid findings, run the full suite from `agentops/`, rebuild/archive-inspect/smoke-test the Windows executable because the new runtime module affects the frozen bundle, update canonical memory and task output, then commit and push.

---

## Output (A3 Shared ProcessRuntime — 2026-09-16)

- New `agentops/runtime.py`: `ProcessRuntime` (reduced env, unified Windows/POSIX spawn policy, cancellation-aware wait, timeouts, process-group termination, byte-level results), `ProcessResult`, `OperationCancelled`, `SpawnFactory`.
- `AgentRunner`, `VerificationKernel`, and legacy `Verifier` delegate to the runtime; public APIs unchanged; `AgentRunner._environment`/`.terminate`/compat helpers and `VerificationKernel(process_factory=...)` preserved.
- Safety fixes beyond the roadmap paragraph: POSIX `start_new_session` for all default spawns (killpg can no longer target the parent group), no-window `taskkill` cleanup, child termination on `asyncio.CancelledError`, thread-free cooperative cancellation polling, direct-kill cleanup for custom factories, executed `on_running` lifecycle preserved.
- Kernel setup fix: duplicate check names fail before a persisted run is started.
- Kernel semantics fix: required FAILED/TIMED_OUT evidence dominates fail-fast sibling cancellation in the overall report.
- Tests: `tests/test_runtime.py` (13 tests) + `tests/test_runner.py` running-lifecycle regression + 2 kernel regressions; full suite 335 passing, 4 environment skips.
- Copilot snapshot review: 6 findings — 5 fixed with regressions (thread leak, custom-factory killpg, on_running orphan, stranded RUNNING run, fail-fast overall), 1 deferred to A8 (state-error swallowing) with rationale.
- OpenCode architecture review: adjudicated all 11 items. Fixed factory-alias duplication, dead TimeoutError branch, and custom-factory policy documentation; added cleanup-bound, returncode, and cancel/skip asymmetry comments; hardened the POSIX cleanup test. Rejected as stale: threadpool leak and POSIX-test inconsistency (both fixed before the review snapshot). Rebutted with baseline evidence: SIGKILL-first termination is unchanged from pre-A3. Accepted: synthetic cleanup stderr text. Deferred: state-error swallowing (A8), as_posix log paths (pre-existing, out of scope), SpawnFactory Protocol typing (follow-up).
- Full suite after adjudication: 336 passing, 4 environment skips.
- Packaging: `dist/AgentOps.exe` rebuilt (14,851,920 bytes), archive-inspected (`agentops.runtime` bundled), startup/shutdown smoke-tested with process-exit verification. Exe stays out of git.
- Backup: `/tmp/agentops-backup-a3-runtime-20260916-203040`; snapshot: `/tmp/agentops-review-a3-runtime`.

---

## Plan (A4 Structured Failure Evidence — 2026-09-16)

### Objective
Agent: pi (sole writer)
Depends on: none
Status: in progress

Implement roadmap A4: classification driven by structured `FailureEvidence` with substring matching demoted to fallback. Keep `classify()` signature compatible, persist evidence JSON in the `failures` row (additive schema v7), and prove with before/after tests that structured evidence beats string heuristics.

---

### Subtask 1 — Audit classifier and evidence points
Agent: pi
Depends on: none
Status: done

Audit `failure.py` classifier branches, workflow `_record_agent_failure` / `_record_verification_failure` / `record_failure`, state `failures` persistence, and existing kernel tests. Backup: `/tmp/agentops-backup-a4-evidence-20260916-211809`.

---

### Subtask 2 — Improve the A4 design
Agent: pi
Depends on: Subtask 1
Status: done

Improvements over the roadmap paragraph:

- `FailureEvidence` is a frozen leaf dataclass (`source`, `check_class`, `exit_code`, `timed_out`, `cancelled`, `terminated`, `command`, `stderr_peek`) with `to_dict()` and `is_empty()`; empty evidence falls back to strings.
- Populate evidence at the two workflow failure-recording choke points (from `RunResult` and from the failing verification check) instead of threading new fields through runner/kernel output contracts — keeps A3 output contracts stable.
- `stderr_peek` is capped at 500 chars and redacted via `logging.redact_text` at construction, so evidence JSON never becomes a secret sink.
- New additive `structured_evidence TEXT` column (schema v7); `Failure.structured_evidence` appended field; `serialize_failure` exposes it; migration assertion bumped to `[1..7]`.
- Structured-first branch mirrors string-branch outcomes exactly, so behavior only changes where structured evidence wins.

---

### Subtask 3 — Implement evidence + structured-first classification
Agent: pi
Depends on: Subtask 2
Status: done

Add `FailureEvidence`, extend `classify()` with keyword-only `evidence=`, wire workflow recording paths, persist the evidence column.

---

### Subtask 4 — Add before/after tests
Agent: pi
Depends on: Subtask 3
Status: done

Extend `tests/test_failure_kernel.py`: ≥5 tests proving structured evidence beats string heuristics, plus evidence-empty parity, persistence round-trip, legacy-migration repair, and workflow integration coverage.

---

### Subtask 5 — Reviews, full suite, and records
Agent: pi; reviewers: copilot (tests), opencode (architecture)
Depends on: Subtask 4
Status: done (copilot closed; opencode async reply pending)

Run read-only snapshot reviews, address valid findings, run the full suite from `agentops/`, update canonical memory and task output, then commit and push. No new module is added, so no executable rebuild is required.

---

## Output (A4 Structured Failure Evidence — 2026-09-16)

- New `FailureEvidence` leaf dataclass (`source`, `check_class`, `exit_code`, `timed_out`, `cancelled`, `terminated`, `command`, capped `stderr_peek`) with total constructor/`to_dict()` and `is_empty()` fallback.
- `FailureClassifier.classify()` gains keyword-only `evidence=`; structured-first branch mirrors string-branch outcomes, so behavior changes only where structured evidence wins.
- Evidence populated at the two workflow recording choke points (from `RunResult`, from the failing check) plus the legacy verifier path; agent commands persist executable-only, stderr peeks and arbitrary mappings are redacted/scrubbed.
- Additive `structured_evidence TEXT` column (schema v7); `Failure.structured_evidence` appended field; `serialize_failure` exposes it; migration assertion bumped to `[1..7]`.
- Tests: 19 new failure-kernel tests plus one redaction test (structured-beats-strings, precedence combos, hostile fields, secret redaction, defensive malformed checks, legacy persistence, migration repair, workflow integration); full suite 356 passing, 4 environment skips.
- Copilot snapshot review: 5 findings, all fixed with regressions (legacy-path evidence, command/dict secret scrubbing, hostile `to_dict` fields, malformed-check defense, precedence coverage).
- OpenCode architecture review: adjudicated all items. Fixed unredacted legacy evidence (HIGH), specified timeout-over-dependency precedence, broadened `redact_text` (AKIA/Bearer/GitHub variants); structured commands already scrubbed via `_scrub_structured` (LOW rebutted with evidence).
- Full suite after adjudication: 356 passing, 4 environment skips.
- Backup: `/tmp/agentops-backup-a4-evidence-20260916-211809`; snapshot: `/tmp/agentops-review-a4-evidence`.
- No new module added, so no executable rebuild is required.

---

# USER NOTE TO AGENT: Put everything below this point into dedicated files in "D:\admin\code\projects\.agents\...". Use this as a replaceable, updated summary of recent projects/prompts.

## Recent summary (replaceable — details live in .agents/)

- **B7 Capability Router: DONE 2026-09-16.** `routing.py` + registry profiles + deterministic router + routing-decision events; 25 routing tests + 1 adapter regression; suite 336 OK; copilot + opencode reviews closed. Details: `.agents/outputs/b7-router.md`.
- **A3 Shared ProcessRuntime: DONE 2026-09-16.** `runtime.py` shared by runner/kernel/legacy verifier; unified spawn policy; 16 new tests; suite 336 OK; exe rebuilt + smoke-tested; copilot + opencode reviews closed. Details: `.agents/outputs/a3-runtime.md`.
- **Pending follow-ups:** A8 persistence failure policy (deferred kernel state-error handling); `SpawnFactory` Protocol typing; repository characteristics for routing; pending OpenCode architecture reply on A4 (see `## Output (A4 ...)` above).
- **Pi invocation fix 2026-09-16:** bundled `agents.yaml` passed `--prompt` (rejected by installed pi CLI); now `args: ["--print", "{prompt}"]` with a regression test locking the built command; verified with a real `agentops run pi` round-trip; suite 357 OK; copilot snapshot review clean.

---

## Plan (A8 Persistence Failure Policy — 2026-09-26)

### Subtask 1 — Audit every persistence fallback
Agent: pi (sole writer)
Depends on: none
Status: done

Enumerated every store write wrapped in a swallowing `except`. Classified each as
safe-to-degrade or must-fail-closed. Probed the deferred A3 item and confirmed it
was a real fabrication bug, not cosmetic.

### Subtask 2 — Leaf policy module and regression test
Agent: pi
Depends on: Subtask 1
Status: done

`agentops/persistence.py` (policy enum, exhaustive table, `Degradation`,
`DegradationRecorder`, `event_emitter`) + `EventType.PERSISTENCE_DEGRADED`.
Regression test written first: patching `finish_verification_check` to raise the
swallowed `KeyError` must not yield a `passed` report.

### Subtask 3 — Kernel fail-closed + visible degradation
Agent: pi
Depends on: Subtask 2
Status: done

Kernel fails closed on lost check start/finish (per-run state threaded, not
instance state). Runner observer notifications and workflow `failure.create` /
`routing.decision` degrade with a recorded warning.

### Subtask 4 — Full suite
Agent: pi
Depends on: Subtask 3
Status: done

---

## Output (A8 Persistence Failure Policy — 2026-09-26)

- **Real defect fixed, confirmed by probe before and after:** a swallowed
  `finish_verification_check` error produced `overall_status = passed` while the
  stored check was still `running`; the workflow then set `task.verified = True`
  on evidence no durable record supported. `assert_report_consistent` could not
  catch it — it validates a report against its own counters, not against what was
  actually stored. The same hole existed at `start_verification_check`.
- **New leaf** `agentops/persistence.py`: `PersistencePolicy`, an exhaustive
  `PERSISTENCE_POLICIES` table, `Degradation`, `DegradationRecorder` (never
  raises; falls back to an in-memory entry when the emitter is what broke),
  `event_emitter` (store injected, so the leaf stays SQLite-free). Unknown
  operations default to `MUST_FAIL_CLOSED` so a new write cannot silently degrade.
- **New event type** `PERSISTENCE_DEGRADED` (additive, WARNING severity).
- **Fail-closed set kept minimal, as the roadmap requires.** Only the
  previously-swallowed `(KeyError, ValueError)` is caught; a real SQLite error
  still propagates and aborts the run, which was already fail-closed. Verified
  end-to-end that a `failure.create` failure persists a WARNING event through a
  real `StateStore`.
- **Already-correct paths regression-locked:** `task.update` and
  `finalize_worktree`'s conflict task were already unguarded (fail-closed); both
  now have tests so a future refactor cannot quietly add a swallow.
- **Tests:** `tests/test_persistence.py`, 18 tests (recorder, policy table,
  fail-closed kernel paths, optional-check case, healthy-run non-regression,
  unclassified-error propagation, and the three degradation sites). Full suite
  **375 passing, 4 environment skips** (baseline was 357 + 4).
- **Files:** `agentops/persistence.py` (new), `tests/test_persistence.py` (new),
  `verification_kernel.py`, `runner.py`, `workflow.py`, `events.py`, `__init__.py`.
- **Not done:** no reviewer pass yet (opencode/copilot read-only snapshot review
  not run). No exe rebuild — packaging untouched.
- **Backup:** `/tmp/agentops-backup-a8-persistence-20260926-174814`.
