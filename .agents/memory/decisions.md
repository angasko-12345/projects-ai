# Decisions Log (canonical, append-only)

> Each entry: Date, Decision, Reason, Alternatives considered, Agent(s) involved. Never delete entries. Keep concise.

## 2026-09-12 — Team structure and lead workflow

- **Date:** 2026-09-12
- **Decision:** `pi-manager` leads; `codex-builder` = primary implementer; `opencode-arch` = architecture/secondary impl; `agy-reviewer` = independent reviewer. 11-step coordinated workflow (understand → read memory → arch analysis → implement → review → fix → test → manager review → update memory → report).
- **Reason:** User directive; separation of implementation vs. independent review improves quality; manager relay avoids user copy-paste.
- **Alternatives considered:** Single-agent implementation without review; uncoordinated multi-writer edits (rejected — race/conflict risk).
- **Agents involved:** user, pi-manager.

## 2026-09-12 — Canonical shared file memory

- **Date:** 2026-09-12
- **Decision:** `.agents/team.md` + `.agents/memory/{project,architecture,decisions,lessons}.md` are the canonical shared project memory with before/during/after operating rules; preserve history, no blind overwrites, no transcripts/duplicates; unverifiable fields read `Not yet established.`
- **Reason:** User directive (Parts 1–4, 6–7); survives future sessions; consistent context across agents.
- **Alternatives considered:** Per-agent local memory only; memory outside `.agents/` (rejected — fragmentation).
- **Agents involved:** user, pi-manager.

## 2026-09-12 — Memory priority order

- **Date:** 2026-09-12
- **Decision:** Conflict priority: (1) current source code, (2) current tests, (3) explicit user instructions, (4) decisions.md, (5) architecture.md, (6) project.md, (7) lessons.md, (8) Supermemory history, (9) agent assumptions. Old memory never overrides current code or explicit instructions.
- **Reason:** User directive (Part 6); prevents stale memory from corrupting the build.
- **Alternatives considered:** None (directive).
- **Agents involved:** user, pi-manager.

## 2026-09-12 — Supermemory as secondary layer (pending verification)

- **Date:** 2026-09-12
- **Decision (proposed):** If available, use Supermemory only as secondary searchable long-term memory; never replace `.agents/memory/`; never store secrets there; never break an agent's config to force it.
- **Reason:** User directive (Part 5).
- **Alternatives considered:** Installing a new memory service if Supermemory present (rejected per directive).
- **Agents involved:** user, pi-manager (Codex/OpenCode/AGY availability to be confirmed).

## 2026-09-13 — Coworker restriction to opencode / fcc-claude / copilot

- **Date:** 2026-09-13
- **Decision:** All multi-agent collaboration uses only opencode, free-claude-code (`fcc-claude`), and copilot. No Codex/Claude coworkers, overriding the team.md 4-agent roster for this project.
- **Reason:** Explicit user instruction (outranks team.md per memory priority rules).
- **Alternatives considered:** agent_fleet coworkers (opencode harness unavailable — `opencode` not executable as a peer); pi-subagents worktrees (rejected — require clean tree, repo is dirty).
- **Agents involved:** user, pi-manager.

## 2026-09-13 — Single writer + read-only snapshot reviews

- **Date:** 2026-09-13
- **Decision:** Pi is the sole writer in the dirty AgentOps tree; opencode/fcc-claude/copilot review via read-only `/tmp/agentops-review-*` snapshots executed with `agentops run` (repo untouched, review logs under snapshot).
- **Reason:** Eliminates concurrent-writer races and accidental repo mutation by reviewer agents.
- **Alternatives considered:** Direct repo access for reviewers; worktree-isolated subagents (blocked — dirty tree).
- **Agents involved:** pi-manager, copilot (review completed).

## 2026-09-13 — Stdlib-only Tkinter GUI + PyInstaller one-file exe

- **Date:** 2026-09-13
- **Decision:** Desktop client uses stdlib Tkinter only (no PySide/Qt deps); exe is PyInstaller 6 one-file windowed with a package-aware `agentops_gui.py` launcher bundling Tk, SQLite, and default `agents.yaml`.
- **Reason:** Zero runtime dependencies; reproducible `scripts/build_windows_exe.py`; direct `agentops/gui.py` entry breaks frozen relative imports (verified via archive inspection).
- **Alternatives considered:** PySide6/PyQt6 (rejected — unavailable, heavy); one-dir bundle (rejected — single-file distribution simpler).
- **Agents involved:** pi-manager.

## 2026-09-13 — Shared finalize_worktree + ordered events (audit hardening)

- **Date:** 2026-09-13
- **Decision:** Commit→merge→conflict-task logic centralized in `agentops/finalize.py` and shared by CLI and GUI; `list_events` explicitly `ORDER BY rowid`.
- **Reason:** Architecture audit: one merge path for all current/future entry points (approvals, REST); deterministic event order as the foundation for event streaming. Behavior-preserving, covered by `tests/test_finalize.py`.
- **Alternatives considered:** Leaving the CLI/GUI duplication in place (rejected — third entry point would triple divergence risk).
- **Agents involved:** pi-manager.

## 2026-09-13 — team.md reconciled; audit.md + roadmap.md added

- **Date:** 2026-09-13
- **Decision:** Rewrote `team.md` roster to match the user restriction and verified reality: Pi = lead + sole writer; opencode = architecture reviewer; `fcc-claude` = security reviewer; copilot = test reviewer. Retired `codex-builder`/`agy-reviewer`/`opencode-arch` (history preserved in-file). Added `memory/audit.md` (diagram, dependency map, schema, sequence, coupling, test gaps) and `memory/roadmap.md` (10 phases, migration strategy, debt list, target domain model) so audit knowledge survives the session.
- **Reason:** team.md contradicted the explicit coworker restriction and listed sessions with no live counterparts; audit content existed only in chat history.
- **Alternatives considered:** Leaving team.md stale (rejected — violates memory accuracy rules); stuffing roadmap into architecture.md (rejected — different concerns, different update cadence).
- **Agents involved:** user, pi-manager.

## 2026-09-14 — First-class persistent AgentRun lifecycle

- **Date:** 2026-09-14
- **Decision:** Added `agentops/agent_run.py`, an additive `agent_runs` SQLite migration, a runner lifecycle-observer protocol, workflow retry/repair linkage, optional `AgentConfig.model`, redacted command/prompt metadata, CLI `runs` inspection, controller/GUI run inspection, and 16 dedicated tests.
- **Reason:** Task requirement: make every subprocess coding-agent execution a persistent, inspectable domain object without replacing existing workflow/task abstractions or storing secrets.
- **Alternatives considered:** Storing raw prompts/commands (rejected — secret-leak risk); recording verification commands as agent runs (rejected — they are allowlisted checks, not installed coding-agent executions); runner-owned SQLite coupling (rejected — preserves runner/state layering).
- **Agents involved:** pi-manager (copilot snapshot review completed; opencode/fcc-claude blocked by environment).

## 2026-09-14 — Deterministic Verification Kernel

- **Date:** 2026-09-14
- **Decision:** Added `verification_model.py` (leaf domain), `verification_kernel.py` (profile executor), schema v2 (`verification_runs`/`verification_checks`/`verification_reports`), task `verified` + `verification_run_id`, config `verification.profiles`, CLI `verify`, controller/GUI verification views, and 22 dedicated tests.
- **Reason:** Phase 2 requirement: explicit VerificationProfile/Check/Run/Report with deterministic parsing, execution policies, and the rule that agent success never marks a task verified.
- **Alternatives considered:** Recording verification commands as AgentRuns (rejected — allowlisted checks are not coding-agent executions; linked via `source_agent_run_id` instead); storing full passing-check output in task results (rejected — transcript keeps summaries + failed-check evidence); letting custom workflows without verification evidence stay READY (rejected — violates the critical rule; documented in README).
- **Agents involved:** pi-manager.

## 2026-09-14 — Relayed opencode review adjudication (Verification Kernel)

- **Date:** 2026-09-14
- **Decision:** Fixed 8 of 9 relayed findings with regression tests: terminal-only fail-fast sibling cancel; `CancelledError` run finalization; duplicate report-field removal; `VerificationProfileMode` rehydration; verification migration repair loop; declared-workdir persistence; duplicate-name rejection; `source_agent_run_id` existence check. Accepted 1: synchronous SQLite calls from async code, matching the established `WorkflowEngine` pattern (redesign out of scope).
- **Reason:** Independent review quality bar per collaboration workflow; all fixes evidence-backed with tests, full suite 122 passing.
- **Alternatives considered:** Deferring fixes (rejected — two were real correctness bugs: spurious parallel cancel, stranded RUNNING runs).
- **Agents involved:** pi-manager (review relayed by user; live opencode channels down).

## 2026-09-14 — Deterministic Failure, Repair, and Recovery Kernel
- **Date:** 2026-09-14
- **Decision:** Added `agentops/failure.py` leaf domain (16 `FailureCategory` values, severity/source/repair-action/recovery-state enums, deterministic `FailureClassifier` with no LLM, `RetryPolicy` with exponential backoff, `RepairPlan`, inherited-context prompt builder, 6-context interruption classifier), additive schema v3 `failures` table, workflow failure recording on every task outcome path plus cancellation-aware bounded retries with inherited context, `failures`/`recover` CLI, controller/GUI failure views, and 24 dedicated tests.
- **Reason:** Phase 3 requirement: first-class failure and recovery subsystem with deterministic classification, bounded policy-driven repairs, distinct parent-linked AgentRuns per retry/repair, and crash recovery that never converts unknown/interrupted states into success without evidence while preserving worktrees.
- **Alternatives considered:** FK-constrained run references in `failures` (rejected — no-agent failures have no run to reference; plain TEXT columns); mid-struct AppConfig backoff fields (rejected — breaks positional construction; appended with defaults instead); LLM-assisted classification (rejected — determinism required).
- **Agents involved:** pi-manager (reviews requested from live copilot + opencode snapshots; replies pending; fcc-claude not requested — server down).

## 2026-09-14 — Agent Intercom Windows EPERM fsync fix validated

- **Date:** 2026-09-14
- **Decision:** Validated reproducible patch artifacts for Windows EPERM in `writeDurableJson()`. Split combined patch into `durable-json.windows-eperm.pi.patch` (1 line) and `durable-json.windows-eperm.opencode.patch` (3 lines). Both apply cleanly via `git apply --check`. Only `durable-json.ts` modified. Fix confirmed working with real-FS roundtrip.
- **Root cause:** `openSync(temporaryPath, "r")` on Windows creates read-only handle; `FlushFileBuffers` requires `GENERIC_WRITE`. Fix: `"r"` → `"r+"`.
- **Upstream status:** Fix NOT yet applied in `ctliz/agent-intercom-pi` (v0.12.2) or `ctliz/agent-intercom-opencode` (v0.12.1). Patch artifacts ready for upstream PR submission.
- **Alternatives considered:** `"w"` flag (rejected — truncates file), blanket EPERM try/catch (rejected — swallows legitimate errors), no fix (rejected — breaks all durable writes on Windows).
- **Agents involved:** pi-manager (via opencode relay confirmation).

## 2026-09-15 — Versioned structured agent results (schema v1)

- **Date:** 2026-09-15
- **Decision:** Added `agentops/agent_result.py` leaf (versioned `AgentResult` with all 12 required fields, total parser with plain-text/empty/malformed fallbacks, JSON-TEXT-tolerant coercion preserving legacy payloads, five-way process/agent/verification/review/merge distinction with merge requiring all four). Runner + workflow persist normalized envelopes into the existing `structured_result` TEXT column (no DB migration); `extract_structured_result` retained; prompt seam advertises the optional JSON contract with plain-text fallback.
- **Reason:** Task requirement: never trust natural-language output as the success signal; parser must never crash a valid execution; preserve backward compatibility.
- **Alternatives considered:** New DB column/table for results (rejected — existing TEXT column already stores JSON, additive-only rule); storing `None` for plain-text output (rejected — envelope with UNKNOWN status + summary preserves evidence without changing process-success semantics).
- **Agents involved:** pi-manager (copilot read-only snapshot review, 4 findings fixed).

## 2026-09-15 — Storage DTOs (Phase 1) + persisted worktree refs (Phase 3)
- **Date:** 2026-09-15
- **Decision:** Completed roadmap Phase 1 (Workflow DTO, no Row leakage past StateStore, controller serializes to plain dicts) and Phase 3 (worktree_refs schema v6, record-on-create in CLI+controller, retry_merge prefers stored provenance). Synced GitHub `.agents/plans/chatgpt_*.md` into the local tree (phase-3 kernel plan was already DONE).
- **Reason:** User directive (read memory + GitHub .agents/plans, run it; complete Phase 1, implement Phase 3). Stored provenance fixes the memory-only base branch/commit risk (debt #1); DTOs unblock every state consumer.
- **Alternatives considered:** Returning dataclasses directly from the controller (rejected — GUI/CLI/API need stable plain-dict payloads; dataclasses stay inside StateStore); FK-constrained worktree refs (rejected — recovery evidence must persist without a parent row, per failures/typed_events precedent).
- **Agents involved:** pi-manager.

## 2026-09-15 — Event and Artifact Infrastructure (Phase 4)

- **Date:** 2026-09-15
- **Decision:** Added `agentops/events.py` leaf (versioned timeline schema v1, 27 event types, total codec, thread-safe bus) and `agentops/artifacts.py` (10-kind registry, contained hash-verified store, retention), additive schema v4/v5 (`typed_events`, `artifacts`; plain-TEXT refs, no FK — crash-recovery events must record for never-persisted runs), CLI `events`/`artifacts`, controller pass-throughs, GUI Artifacts tab; legacy `events` table and `list_events` preserved untouched.
- **Reason:** Task requirement (Phase 4 header): durable execution timeline + first-class artifacts with SQLite as source of truth, GUI now / API later via the shared subscriber protocol.
- **Alternatives considered:** Extending the legacy `events` table in place (rejected — kind/detail strings can't carry severity/payload/version; new table keeps old readers intact); FK-constrained run refs (rejected — same lesson as `failures` table: recovery evidence has no run to reference).
- **Agents involved:** pi-manager (copilot + opencode live reviews requested).

## 2026-09-15 — A1 execution state machine + vacuous-success reversal
- **Date:** 2026-09-15
- **Decision:** Delivered Track A1 (`execution_model.py`, single-sourced matrix, 3 fail-loud workflow gates). Two refinements forced by validator-vs-suite confrontation: (1) legacy-verified tasks count as evidence via result transcript (kernel run OR transcript — legacy output IS the evidence per the preserved contract); (2) empty legacy command suites (`Verifier(())`) no longer verify — supersedes Review #2 (`test_empty_verification_commands_pass` renamed to `test_empty_verification_commands_fail_without_evidence`), because vacuous success is exactly what A1 forbids. `verification_evidence()` unified to the same definition (was kernel-only). User may overrule (2) — flagged in report.
- **Reason:** A1 objective (invalid transitions rejected, never coerced); validators exposed real gaps, treated as bugs per the A1 risk plan.
- **Alternatives considered:** Weakening validators to match legacy behavior (rejected — legalizes fabricated success); giving legacy runs synthetic run ids (rejected — fake linkage is worse than honest transcript evidence); leaving Review #2 intact with a carve-out (rejected — carve-outs defeat a state machine).
- **Agents involved:** pi-manager (opencode architecture review requested async via live `opencode-projects-12884`; reply pending).

## 2026-09-15 — Roadmap v2.0 expansion (unified three-track plan)

- **Date:** 2026-09-15
- **Decision:** Expanded `roadmap.md` from the 10-phase list into a unified, execution-ready v2.0: Track A (architecture stabilization, A1–A10: state machine, AgentAdapter, ProcessRuntime, structured evidence, WorkflowEngine decomposition, ReviewRun/MergeRun, StateStore split, persistence-failure policy, artifact lifecycle, CI gating), Track B (governance/API: B6 policies → B7 router → B8 approvals → B9 project memory → B10 REST/evals), Track C (UX product layer C1/C2/C3 from `chatgpt_addition_recommendations.md`). Added a Decision Backlog (D1–D8), sequencing DAG, milestones, and assumptions. Preserved the entire prior roadmap verbatim under "Preserved historical record" — nothing removed.
- **Reason:** User directive: read the GitHub `.agents/plans/` documents and expand the roadmap into a practical, prioritized, execution-ready plan with objectives, owners, dependencies, risks, milestones, and opened decisions; horizon stays open-ended; UX items included.
- **Alternatives considered:** Keeping the two parallel tracks (original 10 phases + separate ChatGPT-hardening track — rejected, overlaps/duplication); keeping UX in a separate document (rejected — user requested it in the roadmap).
- **Agents involved:** pi-manager.
