# AgentOps Roadmap — v2.0 (expanded, execution-ready)

> Supersedes the prior 10-phase list. The full prior roadmap is preserved verbatim in the **Preserved historical record** section at the bottom so nothing is removed. All DONE phases remain recorded there.
>
> Status tracking lives here. Mark phases `PROPOSED` / `IN PROGRESS` / `DONE` with date + verification. Per-phase rule: regression test → implement → full suite → exe rebuild + smoke if packaging affected. DB changes are additive only (`CREATE TABLE IF NOT EXISTS`); new behavior behind `runtime.*` config flags defaulting to current semantics. Controller stays the only service boundary for GUI/API.

## Current status snapshot (verified 2026-09-15)

| Item | Status | Evidence |
|---|---|---|
| Phase 1 — Storage DTOs | ✅ DONE | `commit 1047121`; Workflow DTO, latest/list/get_workflow return dataclasses, controller serializes to plain dicts; suite 224 OK |
| Phase 2 — Event bus | ✅ DONE | Delivered via Phase 4: `events.py` `EventBus` + `typed_events` (schema v4) |
| Phase 3 — Persisted worktree refs | ✅ DONE | `commit 1047121`; `worktree_refs` schema v6, `WorktreeRef` DTO, retry_merge validates stored provenance; suite 224 OK |
| Phase 4 — Structured agent results | ✅ DONE | Delivered via Phase 4 task header: `agent_result.py` (schema v1, total parser/coercion) |
| Phase 5 — Artifacts registry | ✅ DONE | Delivered via Phase 4 task header: `artifacts.py` (schema v5) |
| Phases 6–10 | ⏳ PROPOSED | Restructured into Track B below |
| Version | 0.1.3, exe rebuilt | `dist/AgentOps.exe`, suite 224 OK |

**Standing constraints that govern everything below:**
- Additive DB schema changes only (`CREATE TABLE IF NOT EXISTS`); no destructive migrations.
- `StateStore` is the only SQLite owner; presentation layers reach data via the controller only.
- Single writer (Pi) in the dirty tree; opencode (architecture), copilot (tests), fcc-claude (security, when server is up) review via read-only snapshots in `/tmp/agentops-review-*`.
- Never store secrets anywhere (memory, events, artifacts, prompts).
- Rebuild exe only if packaging is touched; always archive-inspect + smoke-test.

---

## Track A — Architecture Stabilization (foundations for everything else)

> Rationale: the ChatGPT audit scored the system 8/10 with an explicit directive: *evolution, not rewrite*. These are the contract-stabilization items that make Phases 6–10 and all UX work cheaper. Order matters: **A1 → A3 before A2** because the runtime is a prerequisite for clean adapters.

### A1 — Formal execution/result state machine (P0) ✅ DONE 2026-09-15

> Delivered: `agentops/execution_model.py` leaf (matrix docstring, `StateTransitionError`, 5 pure validators, `SUCCESS_LADDER`); `state._transition` single-sources the matrix; 3 fail-loud workflow call sites; unified `verification_evidence()`; 27 new invariant tests; suite 251 OK; exe rebuilt + re-published to Release v0.1.3. Opencode architecture review requested async. Original action list kept below for history.

- **Objective:** One authoritative definition of success per layer (process / agent / verification / review / merge / workflow) with invalid transitions rejected, not just documented.
- **State today:** `evaluate_execution` in `agent_result.py` already distinguishes the five-way outcome; `TaskStatus`/`AgentRunStatus`/`VerificationRunStatus` have transition guards. The gap is *cross-object* rules (e.g., a task can't be COMPLETED if its required verification is not PASSED) and central documentation.
- **Concrete actions:**
  1. Write a transition matrix in `docs/` (or `agentops/execution_model.py` docstring) enumerating every state × actor, marking allowed/forbidden.
  2. Add a `StateValidator`/assertion helper invoked at task/run/report finalization points that raises `StateTransitionError` on violation (fail-loud; no silent coercion).
  3. Add invariant tests: RUNNING→PENDING blocked, PASSED verification cannot contain failed required checks, task COMPLETED requires verification evidence (mirrors existing "recovery never fabricates success").
- **Owner:** pi (writer); opencode (architecture review).
- **Dependencies:** none.
- **Risks:** least-astonishment risk — existing test fixtures or legacy paths may violate new invariants. Mitigate by running the suite immediately and treating violations as bugs.
- **Success criteria:** Transition matrix documented; ≥10 new invariant tests; `evaluate_execution` semantics become the single reference; suite stays green.

### A2 — AgentAdapter + capability model (P1) ✅ DONE 2026-09-15

> Delivered: `agentops/agent_adapter.py` leaf (`Capability` enum + extras passthrough per D1, `AgentAdapter` Protocol, `CliAdapter` overlay per D2, honesty rule — baseline derived from roles only, shipped `agents.yaml` unchanged); `AgentConfig.capabilities` additive field + validation (unknown names warn, pass through); `AgentRegistry.adapter(s)` + capability-aware `select(required_capabilities=())` (empty = byte-identical legacy path); `AgentRunner.build_command` delegates (signature + output unchanged); engine verified flag-free (only `select` + `build_command` calls). 22 new tests; suite 278 OK; exe rebuilt + re-published to Release v0.1.3. Opencode contract review requested async. Variance: A2 proceeded before A3 — adapters exclude process-spawning (stays in runner); M-A2.4 deferred (legacy path IS the path, nothing to remove). Original action list kept below for history.

- **Objective:** Remove agent-CLI-specific knowledge from `WorkflowEngine` and `registry.select`. Adapters own detection, command/env construction, capability reporting, output parsing, cancellation.
- **Key decisions to make (see Decision Backlog):**
  - D1: Capability vocabulary — fixed enum vs. extensible set. Recommend enum subset of `{coding, planning, review, structured_output, streaming, mcp, model_selection, read_only, non_interactive}` + allow unknown extra strings.
  - D2: Overlay semantics — does an adapter **wrap** the existing `AgentConfig` (recommended: yes, minimal migration) or replace it?
- **Concrete actions:**
  1. New leaf `agentops/agent_adapter.py`: `AgentAdapter` Protocol + `Capability` enum + `build_command(config, prompt)`, `build_environment()`, `parse_output()`, `supports(role)`.
  2. Add `PiAdapter`, `CodexAdapter`, `OpenCodeAdapter`, `AntigravityAdapter`, `CliAdapter` (default passthrough) in `agentops/agents/`.
  3. `AgentRegistry.select()` becomes capability-aware: `required_capabilities` from role mapping, fallback to the existing preference list.
  4. Keep `_prompt_with_history` seam untouched; adapters only shift command/env/output handling.
- **Owner:** pi (writer); opencode (adapter contract review).
- **Dependencies:** A1 (success semantics inform parse_output), A3 (runtime for any adapter that spawns).
- **Risks:** scope creep. Mitigate: adapters start as thin wrappers around current `AgentConfig` behavior — NO CLI protocol changes in this phase.
- **Milestones:** M-A2.1 contract + Protocol + tests (stub adapters); M-A2.2 real adapters (pi/codex/opencode); M-A2.3 registry capability selection; M-A2.4 legacy command path removed behind a config flag.
- **Success criteria:** `WorkflowEngine` has zero direct knowledge of CLI flags; `supports(role)` drives selection; suite green.

### A3 — Shared ProcessRuntime (P1)

- **Objective:** `AgentRunner` and `VerificationKernel` share one process layer (spawn, cancellation, timeout, termination, env policy, process-group cleanup, output capture) instead of the current one-depends-on-other structure.
- **Concrete actions:**
  1. New `agentops/runtime.py`: `ProcessRuntime` with the behaviors currently living in `AgentRunner.run_agent` (taskkill/killpg, TOCTOU-resolved executables, env allowlist, `OperationCancelled`, timeout).
  2. Refactor `AgentRunner` to delegate to it; refactor `VerificationKernel` to use it directly (removes the current kernel→runner internal coupling).
  3. Add tests: consistent timeout/cancellation; Windows process-group cleanup regression.
- **Owner:** pi (writer); copilot (test review — subtle regressions hide here).
- **Dependencies:** A1 (uniform terminal-state rules for cancelled/timed-out).
- **Risks:** highest-risk refactor on the 416-line `runner.py`. Mitigate: ship as behavior-preserving extraction, keep `AgentRunner` public API identical, rely on existing `test_runner.py` + add dedicated runtime tests.
- **Review input (opencode, GUI round):** POSIX session asymmetry — runner spawns with `start_new_session=True` but kernel spawns passthrough without it, so `os.killpg` on a kernel child is a no-op (grandchildren orphan on POSIX; win32 unaffected). A3 must give both paths one session policy. Also unify `CREATE_NO_WINDOW` handling in the runtime.
- **Success criteria:** `VerificationKernel` imports runtime, not runner internals; full suite green; smoke-test a real agent run + a real verification run back-to-back.

### A4 — Structured failure evidence (P2)

- **Objective:** Classification driven by structured `FailureEvidence` (source, check_class, exit_code, timed_out, cancelled, terminated, command, stderr peek) with substring matching demoted to fallback.
- **Concrete actions:**
  1. Add `FailureEvidence` dataclass in `failure.py`; classifier gets a structured-first branch.
  2. `VerificationKernel` and `Runner` populate evidence at the point of failure (they already own exit codes / check classes).
  3. Keep the existing `classify()` signature for compatibility; persist evidence JSON in the `failures` row.
- **Owner:** pi (writer).
- **Dependencies:** A1 (terminal-state definitions), A3 (runtime exposes uniform exit/timeout/cancel signals).
- **Risks:** classification behavior changes (in the intended direction) — add before/after tests so improvements are visible, not silent.
- **Success criteria:** ≥5 tests prove structured evidence beats string heuristics; every failure row carries machine-readable evidence.

### A5 — WorkflowEngine decomposition (P1)

- **Objective:** Split the 918-line god object into `WorkflowPlanner` / `WorkflowScheduler` / `TaskExecutor` / `RepairCoordinator` / `RecoveryCoordinator` behind a thin `WorkflowEngine` facade.
- **Concrete actions:**
  1. Extract the dependency-graph/cycle-check → planner; the claim/ready loop → scheduler.
  2. Extract the verify/retry/repair loop body → executor + repair coordinator (logic is already mostly delineated inside `run_high_level` — this is a mechanical move).
  3. Extract `recover_incomplete` + interruption classification → recovery coordinator.
  4. Keep the public `WorkflowEngine` method set identical so CLI/controller/GUI don't change.
- **Owner:** pi (writer); opencode (architecture review).
- **Dependencies:** A2, A3 (executor will call adapters + runtime).
- **Risks:** pure-cut risk is low (facade preserves API); the real risk is splitting *before* A2/A3 land and doing the work twice. Sequence deliberately.
- **Success criteria:** No public API change; `workflow.py` ≤ ~350 lines; existing workflow tests untouched and green.

### A6 — First-class ReviewRun / MergeRun (P1/P2)

- **Objective:** Review and merge become persisted, observable lifecycle objects like agent runs and verification runs.
- **Concrete actions:**
  1. New `agentops/review_run.py` + `agentops/merge_run.py` leaf domains (status enums, decision, evidence refs).
  2. Additive schema: `review_runs` + `merge_runs`; plain-TEXT refs, no FK (per the standing `failures` lesson: crash-recovery evidence must persist without parent rows).
  3. `MergeRun` records: base commit, source branch/commit, verification_run_ids, review decision, result, conflict reason, resulting commit.
  4. **Fix Git failure classification** in `finalize.py`/recovery: dirty base worktree ≠ changed base commit ≠ merge conflict — the ChatGPT audit flags these as currently conflated.
- **Owner:** pi (writer); copilot (Git safety tests).
- **Dependencies:** A5 (executor/recovery coordination), A1 (merge-eligibility rules).
- **Risks:** touches the `git.py` + `finalize.py` merge path the GUI relies on. Mitigate with dedicated `test_merge_run.py` + conflict simulation.
- **Success criteria:** Every merge produces a `merge_runs` row; conflicted merges never become successful; "which verified commit merged into which base" is answerable via SQL.

### A7 — StateStore repository split (P2)

- **Objective:** Break the 1948-line `state.py` into domain repositories behind a `StateStore` facade. Not urgent — do **after** A1–A6 so repositories stabilize around final object shapes.
- **Concrete actions:**
  1. Extract `state/repositories.py` with `WorkflowRepository`, `TaskRepository`, `RunRepository`, `VerificationRepository`, `FailureRepository`, `EventRepository`, `ArtifactRepository`, `MergeRepository`.
  2. `StateStore` becomes thin dispatch re-exporting the same methods; mirrors the audit's `persistence/` target within one package.
- **Owner:** pi (writer).
- **Dependencies:** A1, A4, A6 (all define the final object types).
- **Risks:** low functional risk, high churn. Mitigate: pure move (no SQL changes) verified by the suite.
- **Success criteria:** `state.py` ≤ ~500 lines; suite green; no SQLite behavior change.

### A8 — Persistence failure policy (P1/P2)

- **Objective:** Surface degraded persistence explicitly instead of silent fallbacks; never let a persistence failure fabricate an unsafe terminal state.
- **Concrete actions:**
  1. Audit `_safe_*` persistence fallbacks in runner/workflow; tag each as "safe-to-degrade" vs "must-fail-closed".
  2. Emit a WARNING event + degraded-state marker when an operation succeeds but persistence fails.
  3. Make critical transitions (task COMPLETED, merge MERGED) fail-closed on persistence error.
- **Owner:** pi (writer); copilot (test review).
- **Dependencies:** A1.
- **Risk:** overtightening causes spurious failures — keep the fatal set minimal (the audit explicitly calls this out).
- **Success criteria:** test matrix for each degradation path; no silent persistence loss.

### A9 — Artifact lifecycle + orphan recovery (P2)

- **Objective:** complete lifecycle (created → attached → verified → preserved → cleaned/orphaned) with crash-window protection and orphan scanning.
- **Concrete actions:** extend `ArtifactStore` with state transitions, `scan_files`/`find_orphans` (partially added from the copilot review), and a cleanup policy tied to worktree teardown.
- **Owner:** pi (writer).
- **Dependencies:** A6 (merge teardown), A7 (repository).
- **Success criteria:** orphan + cleanup tests; every artifact answers *which workflow/task/run/worktree, what created it, can it be deleted safely?*.

### A10 — CI/regression gating (P1)

- **Objective:** continuous protection for lifecycle code.
- **Concrete actions:** GitHub Actions workflow running `python -m unittest discover -s tests` from `agentops/` + `ruff` + a compile check; gate on the standing suite; add targeted regression tests for duplicate task claims, cancellation during agent execution & verification, repeated-recovery idempotency, base-commit races, missing-agent fallback, malformed agent output, persistence failures, orphan recovery.
- **Owner:** pi (setup); copilot (test additions).
- **Dependencies:** any (can run in parallel with A1–A9).
- **Risk:** CI runner ≠ Windows local (subprocess/path differences). Document skips.
- **Success criteria:** red→green workflow; PRs blocked on failure.

---

## Track B — Governance, Routing, Approvals, API (original Phase 6–10, restructured)

> All depend on Track A (they need the stabilized contracts). B6–B8 are **prerequisites for any REST exposure**.

### B6 — Policy gates

- **Objective:** per-role path/network/approval flags enforced pre-claim (task won't start until policy is satisfiable).
- **Concrete actions:** new `Policy` dataclass (or extension of `AgentConfig`); enforcement hook in `TaskExecutor`; pre-claim check in scheduler. `DIRTY_WORKTREE`/`POLICY_VIOLATION` failure categories already exist — wire them to actual violations.
- **Dependencies:** A2, A3, A5.
- **Success criteria:** a task blocked by policy records `POLICY_VIOLATION`, never executes.

### B7 — Router (capability/cost scoring)

- **Objective:** automatic agent selection beyond preference lists, scoring capability match + recent success rate + user prefs.
- **Dependencies:** A2 (capabilities), A6/A1 (success-rate metrics need recorded runs/reviews).
- **Success criteria:** `Agent: Auto` resolves to a scored candidate with an explainable reason.

### B8 — Approvals (human gate, first-class)

- **Objective:** approval becomes a persisted operation (approve / reject / send-back-for-repair), gating merge/finalize.
- **Concrete actions:** `approvals` table, `request_approval`/`resolve_approval` in controller, `APPROVAL_REQUESTED` event, gate between READY and `finalize_worktree`.
- **Dependencies:** A6 (MergeRun must consume approval decisions), B6.
- **Success criteria:** no merge without an approval decision when gated; full audit trail persisted.

### B9 — Project memory (per-repo conventions)

- **Objective:** inject per-repository conventions (`AGENTS.md`-style) into agent prompts via the `_prompt()` seam, with configurable injection.
- **Dependencies:** A5 (prompt seam stabilized).
- **Success criteria:** conventions reach prompts; redaction verified (no secrets injected).

### B10 — REST API + evals

- **Objective:** read endpoints first (history/logs/status), then runs with approval enforcement; eval harness on recorded runs.
- **Dependencies:** B8 (approvals MUST exist before any write API), A7.
- **Success criteria:** read API serves the same data the GUI sees via the controller protocol; write API requires approvals.

---

## Track C — UX / Product layer (from `chatgpt_addition_recommendations.md`, 3 phases)

> Backs onto the existing event bus + artifacts + failures + controller — no new engine. GUI stays Tkinter (stdlib) until B10 changes the surface; these phases do not touch orchestration semantics.

### C1 — "Make it feel like an app"

- New Task wizard (natural-language → auto workflow), Mission Control dashboard, live activity feed, run detail/timeline, approval screen, diff viewer, failure explanation view, one-click retry/repair.
- **Priority order within C1:** Dashboard → New Task → Run timeline → Live feed → Failure explanation → Diff viewer → Approval screen → One-click recovery.
- **Key decision (D3):** diff viewer implementation — shell out to `git diff` (recommended, minimal) vs. bundled parser.
- **Dependencies:** Track A (esp. A6 for merge/review visibility, A8 for degraded-state surfacing); events/artifacts already exist.
- **Success criteria:** a first-time user can open → pick project → enter task → watch it → understand a failure → approve → see the diff → see the result, without ever reading `workflow.py`.

### C2 — "Make it smart"

- Auto agent selection (B7), agent profiles, verification presets (config-driven — profiles already exist, surface them), project auto-detection (`agentops init`), AgentOps Doctor (`agentops doctor` — diagnostic read-only checks), Recovery Center (list out of `failures` + recommendations), task templates (compile to existing workflows — explicitly NOT a new engine), command palette (Ctrl+K).
- **Dependencies:** B6/B7/B8, A1.
- **Success criteria:** defaults are correct in ≥90% of projects with zero configuration; Doctor explains failure in plain language.

### C3 — "Make it powerful"

- Visual workflow builder (renders the *same* DAG model — read-only first), run replay (replays persisted events), global search (indexed metadata), cost/resource tracking ($0 for local), budgets/limits (enforce via config – stop workflows past limits), project analytics, agent health page, notifications.
- **Dependencies:** A5 (workflow DAG model stable), A10 (CI), B10 (API for power features).
- **Success criteria:** power users can inspect/search/replay/optimize without losing the C1 simplicity.

---

## Decision Backlog (unresolved questions requiring explicit choices)

| ID | Question | Recommendation | Needed before |
|---|---|---|---|
| D1 | Capability vocabulary: fixed enum vs extensible | Enum + extra-string passthrough | A2 |
| D2 | AgentAdapter wraps `AgentConfig` vs replaces it | Wrap (minimal migration) | A2 |
| D3 | Diff viewer: `git diff` shell-out vs bundled parser | Shell-out first, parser later | C1 |
| D4 | REST framework for B10 (stdlib only vs FastAPI) | stdlib first, gate behind approvals | B10 |
| D5 | Room for GitHub Actions CI given local-only repo policy? | Ask user (auth/env secrets) | A10 |
| D6 | Exe rebuild cadence after each Track A milestone vs batched | Batch, unless packaging touched | A1–A9 |
| D7 | Sequencing of C1 vs B-tracks: start C1 early or after A? | Start C1's non-engine items (dashboard/feed) concurrently with A5+ | C1 |
| D8 | `fcc-claude` security review availability (server was down 2026-09-15) | Retry per phase; else document as pending | All review gates |

---

## Sequencing & dependencies (DAG summary)

```
A1 (state machine) ─┬─► A4 (evidence)
                    ├─► A3 (ProcessRuntime) ─┬─► A2 (adapters) ─► A5 (decompose) ─► A6 (Review/MergeRun)
                    └─► A8 (persist policy) ─┴──► A7 (state split) ◄───────────────────┘
A10 (CI) ──────────────────────────────► runs across all

A2/A3/A5 ─► B6 (policies) ─► B8 (approvals) ─► B7 (router) ─► B10 (API)
A6 ───────► B8 (approval→merge)          B9 (prompt seam) ─► after A5

C1 (dashboard/feed/wizard) ─► can start after A3/A5/events stable
C2 ─► needs B6/B7 + A1      C3 ─► needs B10 + A5/A6
```

## Milestones (no fixed dates — open-ended horizon)

| Milestone | Entry criteria | Exit criteria |
|---|---|---|
| **M1 — Contracts stable** | A1..A4 done | All five contract docs/tests; suite green |
| **M2 — Orchestration simple** | A5..A6 done | `workflow.py` ≤350 lines; ReviewRun/MergeRun persisted; Git misclassification fixed |
| **M3 — Persistence layered + CI** | A7..A10 done | Repos split; no silent degradation; CI green on PRs |
| **M4 — Governance live** | B6..B8 done | No unapproved merge; policies enforced pre-claim; approval audit trail |
| **M5 — API + memory** | B9..B10 done | REST read endpoints + eval harness |
| **C-milestones** | per C1→C2→C3 acceptance criteria above | per those criteria |

## Assumptions / flags

1. **Phase 1–5 verified DONE** — confirmed via commit history, test files, and the prior roadmap record (suite 224 OK). If the working tree at `D:/admin/code/projects/agentops` differs from `git log` (dirty-tree policy), re-check before M1.
2. **Reviewer availability is unpredictable** (opencode auth, fcc-claude server). Per standing policy, review gates degrade to "record as pending + rely on regression tests" rather than block.
3. **The 3 ChatGPT documents are advisory**, not user-approved directives. The actionable items were mapped into Track A–C; the user still owns the decision to adopt each roadmap item.
4. **No exe rebuild per Milestone** unless packaging is touched (D6) — assumes the exe cadence stays as documented.
5. **The opencode backend auth failure** (lessons.md) means live opencode reviews may be unavailable; the architecture-review role may need a different live peer or relay.

---

## Preserved historical record (prior roadmap — kept in full, nothing removed)

### Original status (as of 2026-09-15, before v2.0 expansion)

- Roadmap APPROVED by user 2026-09-14.
- Phase 1: DONE 2026-09-15 (Workflow DTO added; latest/list/get_workflow return Workflow dataclasses; list_events returns plain dicts; controller emits serialized workflow/task dicts; suite 224 OK).
- Phase 3: DONE 2026-09-15 (worktree_refs table schema v6, WorktreeRef DTO, record on CLI+controller creation, retry_merge validates stored provenance, controller/CLI exposure; suite 224 OK).
- Phases 2, 4–10: see below (4+5 already delivered ahead of sequence via Phase 4 task-header).
- Already delivered outside the phase sequence: `agent_runs` table (covers the Phase 1 `runs`-table design), structured agent results + verification contracts (Phase 4, via AgentRun structured results and the Verification Kernel), artifact references in run/check records (first step of Phase 5).
- 2026-09-15: Phase 4 task-header arrived (Event + Artifact Infrastructure). Delivered ahead of phase sequence: versioned timeline events + subscriptions (covers roadmap Phase 2 event-bus intent at the protocol/store layer; GUI polling retained, subscription available) and artifact registry with hash + CLI/GUI visibility (covers Phase 5 Artifacts registry).

### The 10 phases (original numbering preserved for cross-reference)

1. **Storage DTOs** — map `Row`→dataclasses at the controller boundary; design (not build) the `runs` table. Unblocks every consumer of state.
2. **Event bus** — controller subscriptions over ordered events; GUI polling becomes one subscriber.
3. **Persisted worktree refs** — record base branch/commit per workflow; retry uses stored provenance (fixes memory-only provenance risk).
4. **Structured agent results** — JSON-mode contracts for review/verification roles with text fallback.
5. **Artifacts registry** — hash + index files agents produce; surface in the GUI Logs tab.
6. **Policy gates** — path/network/approval flags per role, enforced pre-claim.
7. **Router** — capability/cost scoring behind `role_preferences` fallback.
8. **Approvals** — human gate between READY and `finalize_worktree` (GUI approve button first).
9. **Project memory** — per-repo conventions store injected into prompts (via the `_prompt()` seam).
10. **REST API + evals** — read endpoints first (history/logs/status), then runs with approval enforcement; eval harness on recorded runs.

### Incremental migration strategy

1. Additive DB changes only, with version pragmas.
2. Config-flagged behavior, current semantics by default.
3. No direct `StateStore`/`GitWorktreeManager` use in presentation layers — controller only.
4. Event-dict protocol gets an explicit version before REST work begins.

### Prioritized technical debt (condensed)

1. Worktree provenance is memory-only (fixed by Phase 3).
2. Polling-as-event-bus (fixed by Phase 2).
3. Free-text-only agent I/O (fixed by Phase 4).
4. Single in-flight operation per controller (needs a sequential operation queue; schedule alongside Phase 7).
5. `Row` leakage into GUI (fixed by Phase 1).
6. Incomplete redaction patterns (harden in Phase 1).
7. Preference-list-only routing (fixed by Phase 7).
8. No approvals/policy gate (fixed by Phases 6+8 — required before any API exposure).
9. `args` without `{prompt}` only warns (strict flag later).
10. Frozen-exe layout brittleness (archive-inspect every build — standing practice).

### Recommended domain model (target)

```
Agent        {name, command, args, roles, timeout, enabled, capabilities[]}
Run          {id, agent, task_id?, prompt_hash, started/finished, exit, timed_out, log_refs, cost?}
Task         {…existing…, priority, policy_ref, approvals[]}
Workflow     {id, description, status, worktree_ref, created/updated}
WorktreeRef  {path, branch, base_branch, base_commit}   # persisted, not memory-only
Event        {id, workflow_id, task_id?, kind, detail, at}  # ordered stream
Artifact     {id, run_id, path, sha256, kind}
Policy       {role, write_paths, network, approval_required}
```