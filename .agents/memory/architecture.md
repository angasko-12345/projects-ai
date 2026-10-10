# Architecture Memory (canonical)

> Read before substantial work. Update when architecture changes. Never store secrets. Only verifiable facts; otherwise `Not yet established.`

## Current system architecture

- AgentOps v0.1.3 (`D:/admin/code/projects/agentops`): layered local-first orchestrator. Entry points (`cli.py`, `gui/`+`gui_controller.py`) compose an injected `WorkflowEngine`; GUI never imports service modules directly — all Qt widget updates happen on the GUI thread via signals from `gui/bridge.py`, and controller reads run on a worker pool. Work runs on controller background threads. Audited 2026-09-13; GUI re-audited 2026-10-04 after the Tk→PySide6 migration.

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
- Packaging: `AgentOps.spec` (one-file windowed) + package-aware `agentops_gui.py` launcher; bundled Qt/SQLite/default config. **Stale as of 2026-10-04:** the spec has not been rebuilt for PySide6 and still lacks it in `hiddenimports`. Treat the packaged exe as unverified since the Qt migration (commit `5b80d9b`).
- **Qt desktop client (2026-10-04).** `agentops/gui/` replaced the deleted single-module `agentops/gui.py`. Ten registered views (Dashboard, Tasks, Workflows, Agents, Runs, Verification, Failures, Worktrees, Artifacts, Settings) behind a persistent shell with sidebar, command palette (Ctrl+K), tray icon, and toasts. Layering: `shell.py` owns navigation/operation lifecycle/settings; `views/` own data loading; `detail.py` owns the drill-down panels; `bridge.py` is the only thread boundary. `context.py` holds `ViewContext` + `AsyncMixin` outside `views/` specifically so `detail.py` can be imported without loading the view registry — putting them in `views/base.py` created a circular import (`detail` → `views/__init__` → every view → `detail`). PySide6 is an optional `desktop` extra; the orchestration core stays stdlib-only.

- **Visual layer (2026-10-04, commits `2159af6`/`c753e3e`).** `gui/tokens.py` is the styling contract: page/section/group label roles, 33px single-line controls, unified radii, exactly one accent (#4c8dff), nav checked accent bar with variant rules ordered before `:checked`; `ASSET_DIR` points at `gui/assets/` (bundled `chevron-down.svg` for combo arrows - QSS cannot draw CSS-style border triangles). `gui/widgets.PageHeader` is the mandatory header on all ten views (title, subtitle, action row). Sidebar `_NAV_GROUPS` mirrors `VIEW_SPECS` (drift-guarded by `tests/test_gui_visual_states.py`); every dashboard card keeps one Expanding occupant so Qt's spare-height split cannot inflate titles; `_fade_stack` removes its `QGraphicsOpacityEffect` on `finished` (a retained effect left stale previous-view regions).

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

- Constructor injection across engine/registry/runner/verifier/state — fully fakeable; current suite baseline (re-run 2026-10-02) is 425 tests with 4 environment skips, mocked subprocesses except one real-git lifecycle test plus real-process runtime smoke coverage; headless GUI tests via the test-only `@requires_display` policy.
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
| Universal contract | `.agents/AGENTS.md` | Three-product identity, canonical locations, startup checklist, per-product command table, memory-hierarchy rule, sole-writer rule, review-collaborator policy, secret-handling rule, leaf-module rule |
| Product-local | `agentops/AGENTS.md` | Entry points, architecture map, data flow, packaging + archive inspection, SQLite migration discipline, GUI/Qt threading, leaf-module rule, verification |
| Product-local | `universal-game-agent/AGENTS.md` | Layer dependency direction, toy vs external path, config/checkpoint handling, CLI entry points, known defects |
| Product-local | `small-projects/mini-llm/AGENTS.md` | Model identity, tests, conventions, checkpoint/tokenizer path contract |
| Tool deltas | `.agents/pi_AGENTS.md`, `.agents/ohmypiagents.md` | What is different when driving this repo with Pi or Oh-My-Pi |
| Compatibility shims | root `AGENTS.md`, root `pi_AGENTS.md` | Pointers only; never an independent source of truth |
| Adjacent | `.github/copilot-instructions.md` | Kept as a real file, not reduced to a pointer; product-focus corrected |

Two structural rules the hierarchy depends on:

1. **Product facts must not live in the universal file.** Test commands, dependency sets, entry points, and packaging differ per product. AgentOps is stdlib-only at runtime (PyYAML and PySide6 optional, imported lazily) and ships a packaged exe; universal-game-agent requires `torch`, `gymnasium`, `numpy`, `pyyaml`, and `mss` and ships none; mini-llm requires `torch`, `tokenizers`, and `numpy` and ships none.
2. **Rules that only one product has must be scoped to it.** Three conventions were originally filed as repo-wide but exist nowhere in universal-game-agent: GUI-thread threading (Tk `root.after` until 2026-10-04, now Qt signal marshalling), the Windows no-console spawn helpers, and `as_posix()` path normalization. They are now tagged `(agentops)`.

The Windows no-console spawn policy has a single owner: `agentops/runtime.py` (`spawn_options()`, `CREATE_NO_WINDOW`), consumed by `runner.py` and `verification_kernel.py`. `runner.py` contains no such helper of its own; it delegates to `ProcessRuntime`. `agent_run.py` and `registry.py` also carry spawn policy.

## Memory hierarchy rule (2026-10-02)

- The **live bug ledger** is `.agents/memory/opencode/bugfinding/master-bug-synthesis.md`
  §0. The **live task queue** is `.agents/pending_tasks.md`.
- Historical audit reports are evidence, not current instructions, and are never revised
  in place. master-bug-synthesis.md §9 is explicitly SUPERSEDED.
- Agent-scoped memory (`cline/`, `hermes/`, `oh-my-pi/`, `opencode/`) never overrides
  canonical memory in `.agents/memory/*.md`.

## Memory folder layout (split planned, not implemented)

`.agents/memory/` currently mixes universal project knowledge with agent-specific
runbooks. `.agents/memory/oh-my-pi/`, `.agents/memory/opencode/`,
`.agents/memory/cline/`, and `.agents/memory/hermes/` are the agent-specific
subfolders. The intended end state
is a universal set plus one folder per agent, but that split is **not
implemented** — the two dedicated OpenCode runbooks
(`omp-opencode-free-tier-403.md`, `pi-opencode-free-tier-fix.md`) are still at the
top level and are the obvious candidates to move into `opencode/`. Until the
split happens, `opencode/README.md` and `cline/README.md` record the map rather
than duplicating that content.

`cline/` (added 2026-10-01) holds agent-scoped runbooks for Cline sessions:
`environment.md` (verified tooling facts plus a correction to a stale claim in
`opencode/environment.md`), `agentops-verification-workflow.md` (how to run and
trust the suites on this Windows shell), `lessons.md`, and `sessions/`. As with
the other subfolders, the universal files here remain authoritative.

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
## One readiness predicate 2026-10-01

- `execution_model.assess_workflow_readiness(tasks, workflow_id, *, verification_task_id=None, review_task_id=None) -> WorkflowReadiness` is the single READY rule. `WorkflowReadiness` carries `verification_ok`, `review_ok`, `evidence_present`, and `reasons` (the missing prerequisites, in operator wording); `.ready` is `not reasons` and `.summary()` renders `READY` or the joined reasons.
- `assert_workflow_ready(...)` raises `StateTransitionError` over the same reason strings; `assert_tasks_ready(...)` assesses real tasks and delegates to it. Both remain exported; no path computes readiness independently.
- `WorkflowEngine.workflow_readiness(workflow_id, ...)` is the engine-level entry point. Callers: `cli.py` (custom-DAG path) and both `run_high_level` READY returns. The task-id arguments scope the assessment to the verification/review pair that decides the outcome, which matters because `run_high_level`'s repair cycles create several of each.
- Removed from `cli.py`: `refresh_workflow_status` + `verification_evidence` as a readiness formula. `refresh_workflow_status` answers "did every task pass", which a workflow with no review task also satisfies.

## AgentOps state self-exclusion 2026-10-01

- `git.GitWorktreeManager._exclude_agentops_state(repository)` resolves the exclude file with `git rev-parse --git-path info/exclude` and appends `/.agentops/` if absent; called from `create()`. Non-fatal on failure, never duplicates the entry, preserves existing content.
- Why: worktrees live at `<repo>/.agentops/worktrees/`, so without this a fresh clone shows `?? .agentops/` and `merge()` refuses on the product's own state. `.git/info/exclude` is local and untracked, so no tracked file is modified and no target-repo configuration is needed. The dirty-tree guard in `merge()` is unchanged and still blocks genuine user changes.

## Redaction coverage across persistence sinks 2026-10-01

- `workflow.py` now redacts at every assignment that reaches durable storage: agent `task.result` (stdout/stderr, log path kept), legacy verification command output, and the `Task execution error: {error}` string.
- `verification_kernel._transcript` redacts check stdout/stderr before embedding them in the persisted `VerificationReport` transcript.
- Reuses the existing `logging.redact_text`; no new redaction mechanism.

## Verification setup failure handling 2026-10-01

- The check-creation loop in `run_verification` is wrapped; a mid-loop failure calls `_abort_setup(run_id, checks, error)`, which finishes each created check and the run as terminal FAILED, then re-raises the original error unchanged. `_abort_setup` never raises, so the closeout cannot mask the real cause.
- `AgentOpsController.recover_interrupted` forwards `workflow_id` to `recover_agent_runs` and `recover_verification_runs` as well as `recover_tasks`; the underlying store methods already supported it.

## Three-product repo + per-product CI 2026-10-01

- The repository is a container for three independent products (`agentops/`, `universal-game-agent/`, `small-projects/mini-llm/`); no product imports another. Each has its own `AGENTS.md` and its own `python -m unittest discover -s tests` from its own directory.
- CI (`.github/workflows/{agentops,universal-game-agent,mini-llm}.yml`, scope in `.github/CI.md`): path-filtered jobs, py3.11 CPU. UGA/mini-llm jobs install `requirements.txt` first. No lint/format/coverage, no GPU, no live-game, no exe packaging in CI.

## UGA checkpoint/lifecycle contracts 2026-10-01 (commit 2cb2413)

- `PPOConfig.checkpoint_every_updates`: non-negative int, 0 disables periodic checkpoints; `train()` always writes `ppo_final.pt`. `save_checkpoint` is atomic (same-dir temp + `os.replace`).
- `external_experiment.launch_phase2_process` transfers proc ownership to the caller only on success (stops it on attach/liveness failure); `load_eval_model` closes the throwaway load env; `stop()` is idempotent. `evaluate()` closes each per-episode env even on exception.
- Measurement law (documented, not enforced): external `reset(seed=)` ignores the seed (game seeded once at launch); `hits`/`misses` count reward sign and are only paddle-hits/misses for sign-based providers.

## GUI projection layer (control center) 2026-10-04

- `gui/control_center.py` sits **below** the Qt layer and **above** nothing else: it takes plain dicts that `AgentOpsController` already serialized and returns plain dicts. No Qt import, no SQLite, no subprocess, no filesystem, no clock except an injected `now`. This is the same leaf rule the non-GUI models already follow (`tasks.py`, `execution_model.py`), applied to presentation logic.
- Dependency direction: `views/workflows.py` -> `gui/control_center.py` -> `gui/format.py` (pure formatters). Widgets consume already-computed stage dicts and never recompute a status, a duration, or a readiness verdict.
- **Readiness is a read, never a re-derivation.** `AgentOpsController.workflow_readiness()` delegates to `assess_workflow_readiness()` in `execution_model.py`, the same function `WorkflowEngine.workflow_readiness()` uses to gate merges. `merge_readiness()` in the projection layer may only *add* the operational precondition the assessment does not cover (a worktree must exist to merge from); it never adds or relaxes a success condition. A surface that cannot read readiness must render "blocked", never a guessed ready.
- **Truthfulness rule for projections:** a value is shown only when a record carries it. An absent duration is `None`/blank, not `0.0`; an absent elapsed time stays unknown rather than falling back to a field that measures something else; verification counts prefer individual check rows over a run's stored counter, because the checks are the evidence. No progress percentages are derived anywhere, because nothing measures per-stage progress.
- Stage keys are role names for the standard pipeline (`architecture` -> PLAN ... `review` -> REVIEW, plus `finalize`) and task ids for a custom DAG. `is_custom_dag()` switches the representation so dependency edges are real rather than implied.
- `Cancellation` is projected (`cancellation_state()`) before it is applied: `cancellable` gates the Cancel action, `cancel_requested` locks it, and `cancelled` is carried verbatim so a cancelled run is never rendered as a success. Worktree mutations gate on that recorded state, never on a sibling widget's enabled property.
- Verified by `tests/test_control_center.py`: `ControlCenterProjectionTests` runs display-free; `ControlCenterViewTests` covers widget behavior under `@requires_qt`. Suite: 501 tests, 4 environment skips, OK.

## mini-llm checkpoint config is path-blind 2026-10-01

- **SUPERSEDED 2026-10-10** by "mini-llm checkpoints are content-addressed" below. Kept as the record of the defect.
- `Config.tokenizer_path` has no CLI flag and `prepare_data.py` does not record it in `meta.json`, so every checkpoint stores the default `data/tokenizer.json` regardless of which tokenizer was used. Only `src/generate.py --tokenizer` can override it at generation time.
- `Config.train_bin` / `val_bin` / `checkpoint_dir` *are* settable via `src.train` flags, but a resumed run takes its config from the checkpoint, so those stored strings are authoritative unless re-passed. A checkpoint trained against non-default paths therefore resumes against the default paths.
- That combination is load-bearing for validation: `Config.validate_against_data()` reads `meta.json` next to `train_bin` and raises when `vocab_size` disagrees, so a resume against a differently-prepped default path fails before any training happens. Reproduced on the TinyStories artifact (vocab 8192 vs the vocab-308 `data/processed/` prep).
- Resolved in `docs/EXPERIMENT-tinystories.md`; the CLI gap itself was a known source-level wart, unfixed at the time of this entry.

## mini-llm checkpoints are content-addressed 2026-10-10

- **Artifacts are identified by sha256, not by path.** `prepare_data.py` writes `train_bin` / `val_bin` / `tokenizer_path` plus `train_sha256` / `val_sha256` / `tokenizer_sha256` into `meta.json`. Paths are recorded for humans and error messages; the digests are the contract. A relocated or copied artifact tree still verifies; a *different* file at the same path does not.
- `Config.data_provenance()` is the single writer of that identity: it validates, then returns the digest set. `validate_against_data()` extended to check vocab, provenance presence, and all three digests. A `meta.json` lacking provenance keys is rejected, never assumed.
- `save_checkpoint()` stamps `data_provenance` (a plain dict of str/int, so `weights_only=True` still holds). `check_resume_provenance()` compares it against freshly hashed current artifacts and raises `SystemExit` naming both sides. It runs **before** `validate_against_data()` in `main()`, so a resume against foreign artifacts is reported as a mismatch rather than as whichever internal check tripped first.
- `config_for_data()` adopts `tokenizer_path` and `val_bin` from `meta.json`, so `--train-bin` alone describes a prepared data set. `src/generate.py` applies the same digest check to `--tokenizer`.
- Consequence worth remembering: the 168 MB `checkpoints/tinystories/final.pt` (2026-09-30) predates provenance and is now **refused** on `--resume` rather than guessed at. It is still usable for generation with an explicit `--tokenizer`. Re-running that experiment is what makes it resumable.
- Suite: `small-projects/mini-llm/` **115 run, 1 skip, OK**.
## Desktop interactions: recovery probe and notification flow (2026-10-04)

- **Detection lives beside recovery.** `StateStore.count_interrupted_work()` sits immediately before `recover_all()` in `state.py` and counts exactly the rows the three `recover_*` passes would reap (read-only SELECTs, same status predicates). `tests/test_state.py::test_count_interrupted_work_mirrors_recover_all` asserts `count_interrupted_work() == recover_all()` so the two can never drift; if a predicate changes, the parity test fails first. No second state owner, no schema change, no migration.
- **Controller facade:** `AgentOpsController.interrupted_work(directory)` returns `{agent_runs, verification_runs, tasks, total}`; `recover_interrupted(directory)` (unchanged) applies the existing passes. The shell never touches SQLite itself.
- **Shell recovery flow (gui/shell.py):** probe key `interrupted-work` on first show / repository switch / idle refresh (never while an operation is active — live rows are not interrupted); `_apply_interrupted_counts` drops stale results (repository changed, operation started, non-dict), hides on zero, keeps a dismissed detail sticky, and notifies once per distinct detail. Recover action = re-probe -> QMessageBox confirm -> `recover-apply` probe -> toast + re-probe. Esc/banner shortcut is enabled only while the banner is visible.
- **Readiness notifications:** on `workflow-result` with `ready=False` the shell submits `workflow_readiness()` (the existing read of `assess_workflow_readiness`) and picks the toast from `verification_ok`: `Verification failed` (error) vs `Workflow blocked` (warning), reasons truncated to three. The GUI never re-derives readiness — same rule as the control center.
- **Shortcut registry:** `MainWindow._shortcuts` maps sequence -> QShortcut (built from `_SHORTCUTS` plus Ctrl+1..0); tests assert the desktop contract from that map instead of hunting QShortcut children.
- **Attribute namespace caution:** the shell mixes widget attributes and helper methods in one namespace — the banner label `self._recovery_detail` initially shadowed the `_recovery_detail()` helper (TypeError at first probe). Helpers that compute strings for widget attributes need distinct names (`_recovery_summary` here); the offscreen tests caught it on first run.
