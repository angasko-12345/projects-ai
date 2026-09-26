# Architecture Memory (canonical)

> Read before substantial work. Update when architecture changes. Never store secrets. Only verifiable facts; otherwise `Not yet established.`

## Current system architecture

- AgentOps v0.1.3 (`D:/admin/code/projects/agentops`): layered local-first orchestrator. Entry points (`cli.py`, `gui.py`+`gui_controller.py`) compose an injected `WorkflowEngine`; GUI never imports service modules directly — all Tk updates marshalled via `root.after()`, work runs on controller background threads. Audited 2026-09-13.

## Canonical instruction layout — 2026-09-20

- `.agents/AGENTS.md` is the canonical repository instruction file; `.agents/pi_AGENTS.md` is the canonical Pi-specific file.
- Root `AGENTS.md` and `pi_AGENTS.md` are compatibility shims for root-only discovery. They point readers to the canonical files and must not drift into separate instruction sets.

## A2 adapter boundary 2026-09-15

- New `agentops/agent_adapter.py` leaf: `Capability` (9 values) + extras passthrough, `AgentAdapter` Protocol (runtime_checkable), `CliAdapter` wrapping `AgentConfig`, `adapter_for` factory, role-derived honesty baseline. `AgentConfig.capabilities` additive tuple (default empty; positional construction preserved). Registry: cached `adapters()`/`adapter(name)` + `select(role, excluded, required_capabilities=())` (empty = legacy path). Runner `build_command` delegates, signature/output unchanged. Engine confirmed flag-free. Tests: `test_agent_adapter.py` (14) + config/registry/runner additions (8). Suite 278 OK.

## A8 persistence failure policy 2026-09-26

- New `agentops/persistence.py` leaf: `PersistencePolicy` (`SAFE_TO_DEGRADE` / `MUST_FAIL_CLOSED`), an exhaustive `PERSISTENCE_POLICIES` table covering every store write that has a fallback, `Degradation`, `DegradationRecorder` (never raises; degrades to an in-memory entry when the emitter is what broke), and `event_emitter` which adapts a `record_typed_event`-shaped callable. `policy_for` defaults unknown operations to `MUST_FAIL_CLOSED`.
- New `EventType.PERSISTENCE_DEGRADED` (additive, WARNING severity) so a lost write is visible on the timeline.
- `VerificationKernel` fails closed: a lost `finish_verification_check` or `start_verification_check` marks the run degraded, forces the report to FAILED (an existing CANCELLED/TIMED_OUT outcome wins as the stronger statement), and appends a `persistence degraded` transcript line. Per-run degradation is threaded explicitly through the private check methods rather than held on the instance, so concurrent runs cannot contaminate each other.
- `AgentRunner` observer notifications and `WorkflowEngine.record_failure` / `_record_routing_decision` are now `SAFE_TO_DEGRADE` with a recorded warning. `task.update` and `finalize_worktree`'s conflict task were already fail-closed (unguarded) and are now regression-locked.
- Only the previously-swallowed `(KeyError, ValueError)` is caught. A real SQLite error still propagates and aborts the run — that path was already fail-closed and was deliberately not widened.

## B7 capability router 2026-09-16

- New `agentops/routing.py`: `AgentProfile`, `AgentCapabilityResolver`, `AgentRouter`, explainable `RoutingDecision`/`RoutingAlternative`/`RoutingRejection`, and `normalize_requirements`. No SQLite or process execution.
- `Capability` extended with the ten task-level values; canonical role/task mapping is shared by adapters and the resolver; legacy transport values and extras passthrough remain.
- Registry builds profiles from detection plus adapters, with best-effort version detection; direct `select()` retains legacy semantics and now accepts scalar capability strings.
- Workflow selection uses router scoring with exclusions and historical success rates, persists `routing.decision` events, and falls back safely to static selection. `runtime.routing_enabled=false` preserves the old path.

## A1 execution state machine 2026-09-15

- New `agentops/execution_model.py` leaf (no I/O): authoritative matrix (AgentRun table mirroring `state._transition` exactly incl. the deliberate `pending → terminal` allowance; verification/task/workflow/recovery rules; success ladder process→agent→verification→review→merge→workflow), `StateTransitionError`, `AGENT_RUN_TRANSITIONS`, 5 pure validators. `state._transition` delegates to `assert_agent_run_transition` (ValueError contract preserved). Workflow wiring: `assert_report_consistent` after kernel runs, `assert_task_completion` on PASSED before persist, `assert_workflow_ready` before every READY return; `StateTransitionError` re-raised past the task-failure handler (fail-loud). `verification_evidence()` unified (kernel run OR legacy transcript). Exports in `__init__.py`. Tests: `test_execution_model.py` (27 tests).

## Phase 1 + Phase 3 delivery 2026-09-15

- Phase 1 Storage DTOs DONE: `tasks.Workflow` DTO; `StateStore.latest/list/get_workflow` return DTOs; `list_events` returns plain dicts; controller `serialize_workflow`/`serialize_task` emit dicts (tasks no longer leak dataclasses); `get/latest_workflow` include serialized failures + `worktree_ref`; CLI status uses attribute access. Tests: `test_storage_dtos_worktree_refs.py` (DTO mapping) + updated `test_state`/`test_review_regressions`/`test_events` (v6). Suite 224 OK, 3 skips.
- Phase 3 Persisted worktree refs DONE: `git.WorktreeRef` DTO; schema v6 `worktree_refs` table (FK-free, indexed by workflow/path); `record/get/find_by_path/list` CRUD; CLI + controller record provenance after workflow creation; `retry_merge` prefers stored base_branch/base_commit (`used_stored_provenance` flag); controller `get/list_worktree_refs`; CLI status prints provenance line. Survives restart (reopen test). GitHub `.agents/plans` synced locally (`chatgpt_*.md` restored from origin/main) — phase-3 kernel plan already DONE; ChatGPT audit/addition docs map to roadmap (AgentAdapter/ProcessRuntime/engine split deferred per stabilization-first principle).

## Components

- `tasks.py`: `Task` dataclass, `TaskStatus` StrEnum — leaf module, no I/O.
- `config.py`: `AgentConfig`/`AppConfig` + strict YAML/JSON validation; additive profile fields and routing switch; `agents/agents.yaml` default (JSON-valid YAML).
- `registry.py`: PATH discovery (`shutil.which`), UTF-8/replacement version detection, capability-rich profiles, `select(role, excluded)` with preference-list fallback; caches detection.
- `routing.py`: deterministic router and explainable decisions; no persistence or execution.
- `persistence.py`: leaf classification of every persistence fallback plus the degradation recorder; imports `events`/`logging` only, never SQLite (the store's `record_typed_event` is injected through `event_emitter`).
- `agent_run.py`: `AgentRun`/`AgentRunStatus` domain, prompt-hash metadata, redacted command metadata, structured-result extraction, optional Git metadata collection, and the runner lifecycle-observer protocol.
- `runner.py`: delegates spawn/wait/timeout/terminate to `ProcessRuntime`; keeps `run_agent` signature, observer lifecycle, logging, metadata, and structured results. `_environment`/`terminate`/`_communicate_with_cancel` remain as compatibility delegates.
- `verification_model.py`: leaf domain (`VerificationProfile`/`VerificationCheck`/`VerificationRun`/`VerificationReport`, deterministic outcome parsing, profile snapshots).
- `verification.py`: legacy allowlisted check commands over runner plumbing (preserved, still counts as verification evidence).
- `runtime.py`: shared process layer (reduced env, unified Windows/POSIX spawn policy, cancellation-aware wait, timeouts, process-group termination, byte results); no logging/SQLite/service imports. Custom factories keep spawn behavior, get direct-kill cleanup.
- `verification_kernel.py`: profile-driven executor (sequential/safe-parallel, fail-fast/continue-on-failure, per-check timeout, cancellation, contained working directories, redacted log artifacts); uses `ProcessRuntime` directly, never imports runner internals; persists runs/checks/reports via duck-typed state, never imports SQLite itself; fails closed when a check's terminal state cannot be persisted.
- `agent_result.py`: leaf domain (no I/O, no LLM) — `AgentResult` (schema v1, 12 required fields + `schema_version`), `AgentResultStatus`/`ParseMode` enums, total `parse_agent_result` (whole-doc/fenced/trailing-line JSON, truncated-JSON detection, plain-text + empty fallbacks, never raises), total `agent_result_from_dict` (safe repr/get, unknown versions downgraded to PARTIAL), `coerce_agent_result` (dicts, lists, JSON TEXT, None; legacy payloads preserved under `metadata.legacy_payload`), `evaluate_execution` (strict bool signals; merge requires all four). Runner/workflow persist envelopes via `_safe_*` helpers that fold parse warnings + `parse_mode` into the stored dict; `extract_structured_result` retained for backward compat; prompt seam advertises the optional JSON contract.
- `events.py`: leaf domain (no I/O, no SQLite) — `Event` (schema v1: id, timestamp, workflow/task/agent-run ids, type, severity, message, payload, schema_version), 29 `EventType` members including `routing.decision` and `persistence.degraded` (verify with `len(EventType)`; this number moves), total `event_from_dict`/`coerce_event` (legacy kinds → NOTE + `legacy_payload`), `EventBus` (thread-safe fan-out, subscriber failures isolated, delivery count). State notifies the bus best-effort on legacy + typed writes; GUI/API share the subscriber protocol.
- `artifacts.py`: `ArtifactKind` (10 families), `Artifact` + `ArtifactMetadata`, `ArtifactStore` (root containment with traversal rejection, 0o700/0o600 perms, sha256 verify on read, retention `prune`, missing/integrity errors, redaction on text writes). Metadata in SQLite (`artifacts`, schema v5), bytes in files; `typed_events` is schema v4. CLI `events`/`artifacts`, controller pass-throughs, GUI Artifacts tab (Logs-tab pattern).
- `failure.py`: leaf domain (no I/O, no LLM) — `FailureCategory` (16), `FailureSeverity`, `FailureSource`, `RepairAction` (6), `RecoveryState`, `InterruptionContext` (6 + unknown), `Failure` record (+`structured_evidence`), deterministic `FailureClassifier` (structured `FailureEvidence`-first, substring fallback; unknown stays non-retryable STOP), `RetryPolicy` (attempt/repair budgets, exponential backoff, cancellation-aware), `RepairPlan`, `build_retry_prompt` (inherited context), interruption classifier (never unknown/interrupted → success).
- `workflow.py`: `WorkflowEngine` (standard 4-task flow + custom DAG with cycle validation, atomic `claim_task`, repair cycles, concurrency semaphore, `retry_policy` from config backoff knobs). Agent selection uses `AgentRouter` when enabled and static preference/fallback when disabled; routing events record both selected and executed agents. Non-verification executions create linked AgentRuns; debugging repair runs link to their source run and task retries link to the previous attempt. Verification tasks run the kernel, link `verification_run_id`, and set `verified` only on a passed report; implementation `PASSED` never implies verified. Custom workflows are READY only with passed verification evidence. Every task outcome records a deterministic `Failure` row (agent/verification/no-agent/cancelled/exception paths); retries back off cancellation-aware and inherit previous failure + verification evidence via `_prompt_with_history`; `plan_repair_for_failure` + `recover_incomplete` expose repair planning and crash classification.
- `state.py`: SQLite (WAL, busy-timeout, RLock) — `workflows`/`tasks`/`events` plus additive `agent_runs` (schema v1), `verification_runs`/`verification_checks`/`verification_reports` (schema v2), `failures` (schema v3 + v7 `structured_evidence` column; run refs are plain TEXT, no FK, so no-agent failures record cleanly), `typed_events` (v4), `artifacts` (v5), `worktree_refs` (v6) migrations; `list_events` explicitly `ORDER BY rowid`. Tasks carry `verified` + `verification_run_id` (explicit-column inserts so legacy tables migrate). Recovery counts required-vs-optional interruptions correctly; `recover_tasks` marks stranded RUNNING tasks FAILED (never success without evidence); `recover_all` bundles all three passes.
- `git.py`: worktree create/commit/merge/remove + list/inspect/cleanup with merge guards (clean base, same branch/commit); porcelain parser for `worktree list`.
- `logging.py`: redacted file logs (600/700 perms), containment-checked `read_tail`.
- `finalize.py`: single shared commit→merge→conflict-task path used by CLI and GUI (added 2026-09-13).
- `gui_controller.py`: threaded facade emitting versioned event dicts with operation-ID staleness guard; one in-flight operation.
- Packaging: `AgentOps.spec` (one-file windowed) + package-aware `agentops_gui.py` launcher; bundled Tk/SQLite/default config.

## Data flow

- task/workflow → `manager.create` (isolated `agentops/<slug>-<rand>` worktree) → `run_high_level` (plan→implement→verify→review, repair loop) → `finalize_worktree` (commit, merge, or conflict-task + preserved worktree) → remove worktree on success.
- Direct run: `registry.get` → `runner.run_agent` + `StateAgentRunObserver` → result + run ID + log paths.
- AgentRun inspection: `agentops runs`, `agentops status`, controller `list_agent_runs`/`get_agent_run`/`get_workflow`, and GUI selected-task run history.
- Verification inspection: `agentops verify`, `agentops status` verification lines, controller `list_verification_runs`/`get_verification_run`/`get_workflow`, and GUI selected-task verification summaries.
- Failure inspection: `agentops failures`, `agentops recover`, `agentops status` failure lines, controller `list_failures`/`recover_interrupted`, and GUI selected-task failure summaries. Config gains `runtime.backoff_base_seconds`/`backoff_max_seconds`/`backoff_factor` (appended with defaults; positional construction preserved).
- GUI additionally: 1s SQLite polling during operations; History/Logs/Worktrees tabs read via controller pass-throughs.

## APIs

- No REST API. Service boundary is `AgentOpsController` (event-dict protocol); `GuiController` Protocol enables fake-controller UI tests.

## Important technical implementation details

- Constructor injection across engine/registry/runner/verifier/state — fully fakeable; latest recorded suite baseline is 357 passing with 4 environment skips, mocked subprocesses except one real-git lifecycle test plus real-process runtime smoke coverage; headless GUI tests.
- SQLite `claim_task` conditional UPDATE is the cross-process atomicity guarantee.
- Worktree base branch/commit metadata was memory-only (resolved by roadmap Phase 3: persisted worktree refs).
- Polling is the event bus (roadmap Phase 2: subscriptions); single in-flight op per controller (roadmap Phase 4: queue).

## Integration points

- **Agent Intercom:** working; all four sessions connected and verified 2026-09-12 (`pi-manager`, `codex-builder`, `opencode-arch`, `agy-reviewer`). Canonical message targets: `opencode-arch`, `codex-builder`, `agy-reviewer`. Must not be reinstalled/reconfigured.
- **Supermemory MCP:** Pi has 0 MCP servers / 0 tools (verified 2026-09-12 via `mcp` status + `supermemory` tool search) — not available to Pi. Availability to Codex / OpenCode / AGY: to be confirmed via Intercom (agents must report yes/no only, never send keys).

## Agent instruction hierarchy 2026-09-26

Layered so that universal rules live in exactly one place and product facts live only with
the product they describe. A change in one product is not a change in the other.

| Layer | File | Scope |
|---|---|---|
| Universal contract | `.agents/AGENTS.md` | Two-product identity, canonical locations, startup checklist, per-product command table, sole-writer rule, review-collaborator policy, secret-handling rule, leaf-module rule |
| Product-local | `agentops/AGENTS.md` | Entry points, architecture map, data flow, packaging + archive inspection, SQLite migration discipline, GUI/Tk threading, leaf-module rule, verification |
| Product-local | `universal-game-agent/AGENTS.md` | Layer dependency direction, toy vs external path, config/checkpoint handling, CLI entry points, known defects |
| Tool deltas | `.agents/pi_AGENTS.md`, `.agents/ohmypiagents.md` | What is different when driving this repo with Pi or Oh-My-Pi |
| Compatibility shims | root `AGENTS.md`, root `pi_AGENTS.md` | Pointers only; never an independent source of truth |
| Adjacent | `.github/copilot-instructions.md` | Kept as a real file, not reduced to a pointer; product-focus corrected |

Two structural rules the hierarchy depends on:

1. **Product facts must not live in the universal file.** Test commands, dependency sets, entry points, and packaging differ per product. AgentOps is stdlib-only at runtime (PyYAML optional, imported lazily) and ships a packaged exe; universal-game-agent requires `torch`, `gymnasium`, `numpy`, `pyyaml`, and `mss` and ships none.
2. **Rules that only one product has must be scoped to it.** Three conventions were originally filed as repo-wide but exist nowhere in universal-game-agent: Tk `root.after` threading, the Windows no-console spawn helpers, and `as_posix()` path normalization. They are now tagged `(agentops)`.

The Windows no-console spawn policy has a single owner: `agentops/runtime.py` (`spawn_options()`, `CREATE_NO_WINDOW`), consumed by `runner.py` and `verification_kernel.py`. `runner.py` contains no such helper of its own; it delegates to `ProcessRuntime`. `agent_run.py` and `registry.py` also carry spawn policy.

## Memory folder layout (split planned, not implemented)

`.agents/memory/` currently mixes universal project knowledge with agent-specific
runbooks. `.agents/memory/oh-my-pi/` and `.agents/memory/opencode/` are the first
agent-specific subfolders. The intended end state is a universal set plus one folder per
agent, but that split is **not implemented** — the two dedicated OpenCode runbooks
(`omp-opencode-free-tier-403.md`, `pi-opencode-free-tier-fix.md`) are still at the top
level and are the obvious candidates to move into `opencode/`. Until the split happens,
`opencode/README.md` records the map rather than duplicating that content.

## Update log

- 2026-09-12: Placeholder created during memory scaffolding; no architecture decided.
- 2026-09-12: Full-spec rewrite; OpenCode architecture review requested.
- 2026-09-13: AgentOps architecture recorded from code audit (v0.1.1, 14 modules, shared finalize.py, 10-phase roadmap proposed).
- 2026-09-14: Added first-class AgentRun persistence and lifecycle integration across runner, workflow, CLI, controller, and GUI; optional `AgentConfig.model`; redacted execution metadata; 95 tests passing.
- 2026-09-14: Added deterministic Verification Kernel (profiles, sequential/parallel policies, fail-fast/continue, per-check timeout/cancel, contained workdirs, kernel/legacy evidence rules, custom-workflow READY gate); 117 tests passing.
- 2026-09-14: Fixed relayed opencode review findings (fail-fast cancels only on terminal failures; `CancelledError` finalizes CANCELLED reports; enum rehydration; verification repair loop; duplicate-name and source-run validation); 122 tests passing.
- 2026-09-14: Added first-class Failure/Repair/Recovery Kernel (deterministic classification, bounded retries with backoff + inherited context, parent-linked retry/repair AgentRuns, crash recovery for 6 interruption contexts, failures/recover CLI, controller/GUI views); 146 tests passing (24 new).
- 2026-09-15: Added versioned Structured Results (`agent_result.py` leaf: schema v1, total parser/coercion, five-way outcome distinction); runner/workflow persist envelopes with warnings+parse_mode (no DB migration); 184 tests passing (34 new).
- 2026-09-16: Added B7 capability profiles and deterministic routing (`routing.py`); registry profile/version support; workflow routing-event persistence; routing switch; 25 routing tests plus one adapter regression test; suite 336 OK. OpenCode review adjudicated: canonical adapter task mapping, UTF-8 version decoding, executed-agent tracking, and single-profile input.
- 2026-09-16: Added A3 shared ProcessRuntime (`runtime.py`); runner/kernel/legacy-verifier delegation; unified spawn policy; CancelledError termination; thread-free cancel polling; duplicate-name validation before persistence; fail-fast evidence-dominated overall status; 16 new tests; suite 336 OK; exe rebuilt + smoke-tested.
- 2026-09-16: Added A4 structured failure evidence (`FailureEvidence`, structured-first classifier, evidence at agent/verification/legacy recording sites, executable-only agent commands, redacted/scrubbed evidence, schema v7 column); 19 new tests; suite 356 OK.
- 2026-09-26: Added the A8 persistence-failure policy (`persistence.py`): every store write with a fallback is classified safe-to-degrade or must-fail-closed, degrading writes emit a `persistence.degraded` WARNING event, and the verification kernel no longer lets a lost check-state write produce a `passed` report. 18 new tests; suite 375 OK (4 skips). No packaging change, so no exe rebuild.
- 2026-09-26: Added the layered agent-instruction hierarchy documented above, and the first agent-specific memory subfolders (`.agents/memory/oh-my-pi/`, `.agents/memory/opencode/`). Documentation only; no runtime code changed. Seven factually incorrect claims in the first draft were found by an independent read-only review and corrected before commit.
