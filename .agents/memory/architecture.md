# Architecture Memory (canonical)

> Read before substantial work. Update when architecture changes. Never store secrets. Only verifiable facts; otherwise `Not yet established.`

## Current system architecture

- AgentOps v0.1.2 (`D:/admin/code/projects/agentops`): layered local-first orchestrator. Entry points (`cli.py`, `gui.py`+`gui_controller.py`) compose an injected `WorkflowEngine`; GUI never imports service modules directly — all Tk updates marshalled via `root.after()`, work runs on controller background threads. Audited 2026-09-13.

## A2 adapter boundary 2026-09-15

- New `agentops/agent_adapter.py` leaf: `Capability` (9 values) + extras passthrough, `AgentAdapter` Protocol (runtime_checkable), `CliAdapter` wrapping `AgentConfig`, `adapter_for` factory, role-derived honesty baseline. `AgentConfig.capabilities` additive tuple (default empty; positional construction preserved). Registry: cached `adapters()`/`adapter(name)` + `select(role, excluded, required_capabilities=())` (empty = legacy path). Runner `build_command` delegates, signature/output unchanged. Engine confirmed flag-free. Tests: `test_agent_adapter.py` (14) + config/registry/runner additions (8). Suite 278 OK.

## A1 execution state machine 2026-09-15

- New `agentops/execution_model.py` leaf (no I/O): authoritative matrix (AgentRun table mirroring `state._transition` exactly incl. the deliberate `pending → terminal` allowance; verification/task/workflow/recovery rules; success ladder process→agent→verification→review→merge→workflow), `StateTransitionError`, `AGENT_RUN_TRANSITIONS`, 5 pure validators. `state._transition` delegates to `assert_agent_run_transition` (ValueError contract preserved). Workflow wiring: `assert_report_consistent` after kernel runs, `assert_task_completion` on PASSED before persist, `assert_workflow_ready` before every READY return; `StateTransitionError` re-raised past the task-failure handler (fail-loud). `verification_evidence()` unified (kernel run OR legacy transcript). Exports in `__init__.py`. Tests: `test_execution_model.py` (27 tests).

## Phase 1 + Phase 3 delivery 2026-09-15

- Phase 1 Storage DTOs DONE: `tasks.Workflow` DTO; `StateStore.latest/list/get_workflow` return DTOs; `list_events` returns plain dicts; controller `serialize_workflow`/`serialize_task` emit dicts (tasks no longer leak dataclasses); `get/latest_workflow` include serialized failures + `worktree_ref`; CLI status uses attribute access. Tests: `test_storage_dtos_worktree_refs.py` (DTO mapping) + updated `test_state`/`test_review_regressions`/`test_events` (v6). Suite 224 OK, 3 skips.
- Phase 3 Persisted worktree refs DONE: `git.WorktreeRef` DTO; schema v6 `worktree_refs` table (FK-free, indexed by workflow/path); `record/get/find_by_path/list` CRUD; CLI + controller record provenance after workflow creation; `retry_merge` prefers stored base_branch/base_commit (`used_stored_provenance` flag); controller `get/list_worktree_refs`; CLI status prints provenance line. Survives restart (reopen test). GitHub `.agents/plans` synced locally (`chatgpt_*.md` restored from origin/main) — phase-3 kernel plan already DONE; ChatGPT audit/addition docs map to roadmap (AgentAdapter/ProcessRuntime/engine split deferred per stabilization-first principle).

## Components

- `tasks.py`: `Task` dataclass, `TaskStatus` StrEnum — leaf module, no I/O.
- `config.py`: `AgentConfig`/`AppConfig` + strict YAML/JSON validation; `agents/agents.yaml` default (JSON-valid YAML).
- `registry.py`: PATH discovery (`shutil.which`), `select(role, excluded)` with preference-list fallback; caches detection.
- `agent_run.py`: `AgentRun`/`AgentRunStatus` domain, prompt-hash metadata, redacted command metadata, structured-result extraction, optional Git metadata collection, and the runner lifecycle-observer protocol.
- `runner.py`: asyncio subprocess exec, env allowlist, timeout + `OperationCancelled` token, taskkill/killpg process-group cleanup, TOCTOU-safe resolved executables. Every agent subprocess can now notify an `AgentRunObserver`; persistence failures never prevent the configured agent from running.
- `verification_model.py`: leaf domain (`VerificationProfile`/`VerificationCheck`/`VerificationRun`/`VerificationReport`, deterministic outcome parsing, profile snapshots).
- `verification.py`: legacy allowlisted check commands over runner plumbing (preserved, still counts as verification evidence).
- `verification_kernel.py`: profile-driven executor (sequential/safe-parallel, fail-fast/continue-on-failure, per-check timeout, cancellation, contained working directories, redacted log artifacts); persists runs/checks/reports via duck-typed state, never imports SQLite itself.
- `agent_result.py`: leaf domain (no I/O, no LLM) — `AgentResult` (schema v1, 12 required fields + `schema_version`), `AgentResultStatus`/`ParseMode` enums, total `parse_agent_result` (whole-doc/fenced/trailing-line JSON, truncated-JSON detection, plain-text + empty fallbacks, never raises), total `agent_result_from_dict` (safe repr/get, unknown versions downgraded to PARTIAL), `coerce_agent_result` (dicts, lists, JSON TEXT, None; legacy payloads preserved under `metadata.legacy_payload`), `evaluate_execution` (strict bool signals; merge requires all four). Runner/workflow persist envelopes via `_safe_*` helpers that fold parse warnings + `parse_mode` into the stored dict; `extract_structured_result` retained for backward compat; prompt seam advertises the optional JSON contract.
- `events.py`: leaf domain (no I/O, no SQLite) — `Event` (schema v1: id, timestamp, workflow/task/agent-run ids, type, severity, message, payload, schema_version), 27 `EventType` members, total `event_from_dict`/`coerce_event` (legacy kinds → NOTE + `legacy_payload`), `EventBus` (thread-safe fan-out, subscriber failures isolated, delivery count). State notifies the bus best-effort on legacy + typed writes; GUI/API share the subscriber protocol.
- `artifacts.py`: `ArtifactKind` (10 families), `Artifact` + `ArtifactMetadata`, `ArtifactStore` (root containment with traversal rejection, 0o700/0o600 perms, sha256 verify on read, retention `prune`, missing/integrity errors, redaction on text writes). Metadata in SQLite (`artifacts`, schema v5), bytes in files; `typed_events` is schema v4. CLI `events`/`artifacts`, controller pass-throughs, GUI Artifacts tab (Logs-tab pattern).
- `failure.py`: leaf domain (no I/O, no LLM) — `FailureCategory` (16), `FailureSeverity`, `FailureSource`, `RepairAction` (6), `RecoveryState`, `InterruptionContext` (6 + unknown), `Failure` record, deterministic `FailureClassifier` (flags + substring rules; unknown stays non-retryable STOP), `RetryPolicy` (attempt/repair budgets, exponential backoff, cancellation-aware), `RepairPlan`, `build_retry_prompt` (inherited context), interruption classifier (never unknown/interrupted → success).
- `workflow.py`: `WorkflowEngine` (standard 4-task flow + custom DAG with cycle validation, atomic `claim_task`, repair cycles, concurrency semaphore, `retry_policy` from config backoff knobs). Non-verification executions create linked AgentRuns; debugging repair runs link to their source run and task retries link to the previous attempt. Verification tasks run the kernel, link `verification_run_id`, and set `verified` only on a passed report; implementation `PASSED` never implies verified. Custom workflows are READY only with passed verification evidence. Every task outcome records a deterministic `Failure` row (agent/verification/no-agent/cancelled/exception paths); retries back off cancellation-aware and inherit previous failure + verification evidence via `_prompt_with_history`; `plan_repair_for_failure` + `recover_incomplete` expose repair planning and crash classification.
- `state.py`: SQLite (WAL, busy-timeout, RLock) — `workflows`/`tasks`/`events` plus additive `agent_runs` (schema v1), `verification_runs`/`verification_checks`/`verification_reports` (schema v2), and `failures` (schema v3; run refs are plain TEXT, no FK, so no-agent failures record cleanly) migrations; `list_events` explicitly `ORDER BY rowid`. Tasks carry `verified` + `verification_run_id` (explicit-column inserts so legacy tables migrate). Recovery counts required-vs-optional interruptions correctly; `recover_tasks` marks stranded RUNNING tasks FAILED (never success without evidence); `recover_all` bundles all three passes.
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

- Constructor injection across engine/registry/runner/verifier/state — fully fakeable; 122 tests, mocked subprocesses except one real-git lifecycle test; headless GUI tests.
- SQLite `claim_task` conditional UPDATE is the cross-process atomicity guarantee.
- Worktree base branch/commit metadata is memory-only (known risk — roadmap Phase 3: persist worktree refs).
- Polling is the event bus (roadmap Phase 2: subscriptions); single in-flight op per controller (roadmap Phase 4: queue).

## Integration points

- **Agent Intercom:** working; all four sessions connected and verified 2026-09-12 (`pi-manager`, `codex-builder`, `opencode-arch`, `agy-reviewer`). Canonical message targets: `opencode-arch`, `codex-builder`, `agy-reviewer`. Must not be reinstalled/reconfigured.
- **Supermemory MCP:** Pi has 0 MCP servers / 0 tools (verified 2026-09-12 via `mcp` status + `supermemory` tool search) — not available to Pi. Availability to Codex / OpenCode / AGY: to be confirmed via Intercom (agents must report yes/no only, never send keys).

## Update log

- 2026-09-12: Placeholder created during memory scaffolding; no architecture decided.
- 2026-09-12: Full-spec rewrite; OpenCode architecture review requested.
- 2026-09-13: AgentOps architecture recorded from code audit (v0.1.1, 14 modules, shared finalize.py, 10-phase roadmap proposed).
- 2026-09-14: Added first-class AgentRun persistence and lifecycle integration across runner, workflow, CLI, controller, and GUI; optional `AgentConfig.model`; redacted execution metadata; 95 tests passing.
- 2026-09-14: Added deterministic Verification Kernel (profiles, sequential/parallel policies, fail-fast/continue, per-check timeout/cancel, contained workdirs, kernel/legacy evidence rules, custom-workflow READY gate); 117 tests passing.
- 2026-09-14: Fixed relayed opencode review findings (fail-fast cancels only on terminal failures; `CancelledError` finalizes CANCELLED reports; enum rehydration; verification repair loop; duplicate-name and source-run validation); 122 tests passing.
- 2026-09-14: Added first-class Failure/Repair/Recovery Kernel (deterministic classification, bounded retries with backoff + inherited context, parent-linked retry/repair AgentRuns, crash recovery for 6 interruption contexts, failures/recover CLI, controller/GUI views); 146 tests passing (24 new).
- 2026-09-15: Added versioned Structured Results (`agent_result.py` leaf: schema v1, total parser/coercion, five-way outcome distinction); runner/workflow persist envelopes with warnings+parse_mode (no DB migration); 184 tests passing (34 new).
