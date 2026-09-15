# AgentOps Roadmap — 10 Phases (from 2026-09-13 audit)

> Status tracking lives here. Mark phases `PROPOSED` / `IN PROGRESS` / `DONE` with date + verification. Per-phase rule: regression test → implement → full suite → exe rebuild + smoke if packaging affected. DB changes are additive only (`CREATE TABLE IF NOT EXISTS`); new behavior behind `runtime.*` config flags defaulting to current semantics. Controller stays the only service boundary for GUI/API.

## Status

- Roadmap APPROVED by user 2026-09-14.
- Phase 1: DONE 2026-09-15 (Workflow DTO added; latest/list/get_workflow return Workflow dataclasses; list_events returns plain dicts; controller emits serialized workflow/task dicts; suite 224 OK).
- Phase 3: DONE 2026-09-15 (worktree_refs table schema v6, WorktreeRef DTO, record on CLI+controller creation, retry_merge validates stored provenance, controller/CLI exposure; suite 224 OK).
- Phases 2, 4–10: see below (4+5 already delivered ahead of sequence via Phase 4 task-header).
- Already delivered outside the phase sequence: `agent_runs` table (covers the Phase 1 `runs`-table design), structured agent results + verification contracts (Phase 4, via AgentRun structured results and the Verification Kernel), artifact references in run/check records (first step of Phase 5).
- 2026-09-15: Phase 4 task-header arrived (Event + Artifact Infrastructure). Delivered ahead of phase sequence: versioned timeline events + subscriptions (covers roadmap Phase 2 event-bus intent at the protocol/store layer; GUI polling retained, subscription available) and artifact registry with hash + CLI/GUI visibility (covers Phase 5 Artifacts registry).

## The 10 phases

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

## Incremental migration strategy

1. Additive DB changes only, with version pragmas.
2. Config-flagged behavior, current semantics by default.
3. No direct `StateStore`/`GitWorktreeManager` use in presentation layers — controller only.
4. Event-dict protocol gets an explicit version before REST work begins.

## Prioritized technical debt (condensed)

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

## Recommended domain model (target)

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
