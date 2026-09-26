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
- **Agents involved:** pi-manager (opencode architecture review received 2026-09-15 via `opencode-projects-12884`: 4 findings + 1 note; all adjudicated — see 2026-09-15 A1R entry).

## 2026-09-15 — A1R opencode review adjudication (4/4 fixed + note applied)
- **Date:** 2026-09-15
- **Decision:** (1) Kernel empty-profile fails fast at resolve (`ValueError`: no checks configured) → task FAILED + repair via the generic handler, consistent with the legacy empty-suite rule (verified outcome parity by test, not just intent). (2) `assert_no_fabricated_success` wired as post-conditions into all three recover passes (was export-only). (3) PASSED now also requires `passed_checks >= 1` (all-skipped suites rejected); simultaneously narrowed `failed_checks != 0` to `required_failures != 0` after proving optional failures legally coexist with PASSED (self-found false positive the review missed). (4) Same-state `_transition` early-returns (no write, no event) matching the documented no-op. NOTE applied: READY call sites pass actual task statuses.
- **Reason:** Review correctness bar per collaboration workflow; every fix evidence-backed with regression tests (kernel-empty ×2, all-skipped, optional-failure, recovery-honesty, same-state-no-event); suite 256 OK.
- **Alternatives considered:** Hard-aborting on kernel-empty (rejected — inconsistent with legacy FAILED+repair for the same policy); leaving the validator export-only (rejected — unenforced contracts rot); keeping `failed_checks != 0` (rejected — proven false positive against optional failures).
- **Agents involved:** pi-manager, opencode-projects-12884 (reviewer).
- **Sign-off 2026-09-15:** opencode re-reviewed `908dce6` (working tree + local 256-OK run): all 4 fixes + note accepted, no new findings; agreed the optional-failure catch was a real landmine-sweep; legacy-empty/kernel fail-fast consistency endorsed with user-override retained. A1 + A1R closed.

## 2026-09-15 — A2 adapter boundary (D1/D2 + A3-first variance)
- **Date:** 2026-09-15
- **Decision:** D1 = fixed `Capability` enum + free-form extras (config accepts any non-empty strings; unknown names warn, pass through). D2 = adapter WRAPS `AgentConfig` (overlay; `CliAdapter` + `adapter_for` factory). A2 proceeded BEFORE A3 (variance from the DAG): adapters exclude process-spawning/env/output-parse/cancel — those stay in the runner as generic code until A3 gives them a runtime to target. M-A2.4 (legacy-path removal) deferred indefinitely: the legacy path IS the path. Shipped `agents.yaml` unchanged (no fabricated capabilities; B7 populates them). `select()` gains optional `required_capabilities` (empty = byte-identical legacy scan); `build_command` keeps signature/output.
- **Reason:** PI directive (incremental behavior-preserving refactor around existing code) + roadmap mitigations (thin wrappers, no CLI protocol changes). Import direction forced one wart: function-local `agent_adapter` import inside `load_config` (module-level would cycle, since adapters wrap config).
- **Alternatives considered:** Per-agent adapter subclasses (`PiAdapter`, …) now (rejected — no per-agent behavior differences exist yet; subclasses with identical bodies are decoration, add when a CLI diverges); capabilities field replacing `roles` (rejected — roles drive selection today, capabilities filter); hardcoding capability maps per agent name (rejected — duplicates config, rots).
- **Agents involved:** pi-manager (opencode contract review received 2026-09-15: APPROVE, 2 LOW + 2 notes; all applied — dead None-branch removed, capability names stripped/deduped at load, adapter-cache pin warning documented; suite 278 OK; A2 closed).
- **Sign-off 2026-09-15 (GUI rounds):** opencode light review APPROVED (289 OK confirmed locally): no-flash consistency verified incl. POSIX passthrough + kill semantics; signature-gate proven safe (sole mutator, superset fields); root-cache design endorsed; layout internally consistent. Notes: N1 import order (applied), N2 POSIX session asymmetry → recorded in A3, N3/N4 accepted as-is. GUI rounds closed.

## 2026-09-15 — Roadmap v2.0 expansion (unified three-track plan)

- **Date:** 2026-09-15
- **Decision:** Expanded `roadmap.md` from the 10-phase list into a unified, execution-ready v2.0: Track A (architecture stabilization, A1–A10: state machine, AgentAdapter, ProcessRuntime, structured evidence, WorkflowEngine decomposition, ReviewRun/MergeRun, StateStore split, persistence-failure policy, artifact lifecycle, CI gating), Track B (governance/API: B6 policies → B7 router → B8 approvals → B9 project memory → B10 REST/evals), Track C (UX product layer C1/C2/C3 from `chatgpt_addition_recommendations.md`). Added a Decision Backlog (D1–D8), sequencing DAG, milestones, and assumptions. Preserved the entire prior roadmap verbatim under "Preserved historical record" — nothing removed.
- **Reason:** User directive: read the GitHub `.agents/plans/` documents and expand the roadmap into a practical, prioritized, execution-ready plan with objectives, owners, dependencies, risks, milestones, and opened decisions; horizon stays open-ended; UX items included.
- **Alternatives considered:** Keeping the two parallel tracks (original 10 phases + separate ChatGPT-hardening track — rejected, overlaps/duplication); keeping UX in a separate document (rejected — user requested it in the roadmap).
- **Agents involved:** pi-manager.

## 2026-09-16 — B7 capability resolver and deterministic router

- **Date:** 2026-09-16
- **Decision:** Added `routing.py` with `AgentProfile`, `AgentCapabilityResolver`, deterministic `AgentRouter`, and explainable decisions. Extended `Capability` with the requested task-level values. Added additive config/profile fields, best-effort version detection, registry profiles, workflow routing-event persistence, and `runtime.routing_enabled`.
- **Reason:** Task-header B7 requirements for capability-aware scoring, explicit preferences, historical performance, explainability, event persistence, and a routing switch, while preserving legacy role gates and fallback behavior.
- **Alternatives considered:** Scoring inferred task wording as hard eligibility (rejected — ordinary descriptions could eliminate otherwise-eligible agents); defaulting workflow routing off (rejected for this task because routing decisions are a required behavior, while direct `select()` and the false switch remain available); per-agent adapter subclasses now (rejected — no CLI-specific routing behavior exists yet).
- **Agents involved:** pi-manager (copilot snapshot review completed; opencode architecture review completed 2026-09-16).
- **OpenCode adjudication:** Fixed 1 (UTF-8/replacement version decoding), 2 (canonical role-derived task capabilities shared by adapter/resolver), 3 (removed split coding/review aliases), 5 (routing event now records executed agent and falls back on stale mappings), and 7 (single-profile router input). Rejected 4 and the route-exception half of 5 as stale against the reviewed commit; retained 6 (full-vocabulary empty-role profiles) by design; swept 8 (`AppConfig` constructions use compatible keyword/positional forms).

## 2026-09-16 — A3 shared ProcessRuntime

- **Date:** 2026-09-16
- **Decision:** Added `agentops/runtime.py` (`ProcessRuntime`, `ProcessResult`, `OperationCancelled`, `SpawnFactory`); `AgentRunner`, `VerificationKernel`, and legacy `Verifier` delegate to it. Unified default spawn policy (Windows no-window + process group, POSIX new session); custom factories keep spawn behavior with direct-kill cleanup; `CancelledError` terminates the child; cooperative cancellation polls instead of parking threads; `on_running` preserves RUNNING timing.
- **Reason:** Roadmap A3: one process layer instead of kernel→runner coupling; fixes POSIX killpg parent-group hazard and windowed-build taskkill flash as found during implementation/review.
- **Alternatives considered:** Leaving legacy `Verifier` on runner internals (rejected — runner would remain the implicit process layer); forcing platform flags into custom factories (rejected — breaks the injectable-factory contract; direct kill instead); changing state-error swallowing in the kernel (deferred to A8 — failure-semantics change outside the process layer).
- **Agents involved:** pi-manager (copilot snapshot review completed; opencode architecture review completed 2026-09-16).
- **OpenCode adjudication:** Fixed factory-alias duplication (`ProcessFactory = SpawnFactory`), dead runner `except TimeoutError`, custom-factory policy documentation, cleanup-bound/returncode/cancel-skip comments, POSIX cleanup test. Rejected threadpool-leak and POSIX-test findings as stale (fixed pre-snapshot); rebutted SIGKILL-first escalation with baseline evidence (pre-A3 code identical); accepted synthetic cleanup stderr; deferred state-error swallowing (A8), as_posix paths (pre-existing), Protocol typing (follow-up).

## 2026-09-16 — A4 structured failure evidence

- **Date:** 2026-09-16
- **Decision:** Added `FailureEvidence` leaf dataclass with structured-first `classify(evidence=)`; populate at workflow agent/verification/legacy recording sites (no runner/kernel contract changes); persist executable-only agent commands, redacted 500-char peeks, and scrubbed mappings; additive `structured_evidence` column (schema v7).
- **Reason:** Roadmap A4: machine-readable evidence beats string heuristics; legacy verification keeps text rules (no invented agent errors); secrets must never reach SQLite via evidence.
- **Alternatives considered:** Threading evidence fields through runner/kernel outputs (rejected — churns A3 contracts for no gain); persisting full agent argv (rejected — argv embeds the raw prompt); changing legacy classification to AGENT_ERROR on nonzero exit (rejected — invents retryability without check-class authority).
- **Agents involved:** pi-manager (copilot snapshot review completed; opencode architecture review completed 2026-09-16).
- **OpenCode adjudication:** Fixed unredacted legacy `failure.evidence` (redact in `record_failure` + recovery path), specified timeout-over-dependency precedence in code+test, broadened `redact_text` (AKIA/Bearer/`gho|u|s|r_`); rebutted command-field finding (persisted commands already scrubbed via `_scrub_structured`).

## 2026-09-20 — Canonical instruction location

- **Decision:** Make `.agents/AGENTS.md` and `.agents/pi_AGENTS.md` the canonical repository and Pi-specific instruction files while retaining root `AGENTS.md` and `pi_AGENTS.md` as explicit compatibility entrypoints.
- **Reason:** Centralizes the source of truth for project memory and agent-specific guidance while preserving tools that only discover root-level instruction files.
- **Alternatives considered:** Keep root files as full duplicates (rejected because duplicated guidance drifts); remove root files entirely (rejected because root-only loaders would lose the compatibility path).
- **Agents involved:** pi-manager.

## 2026-09-26 - Two-product instruction hierarchy

- **Decision:** `.agents/AGENTS.md` holds only universal rules; product facts live in `agentops/AGENTS.md` and `universal-game-agent/AGENTS.md`; root `AGENTS.md` and `pi_AGENTS.md` stay as compatibility shims and are explicitly not sources of truth. `.agents/team.md` is reconciled to match rather than left to drift.
- **Reason:** A single file could not describe both products honestly. It had already drifted: root instructions asserted one repository-wide test command and named AgentOps as the only maintained product, while `universal-game-agent/` had no local guidance at all.
- **Alternatives considered:** One combined file per product pair (rejected — reintroduces exactly the drift being fixed); local files only with no universal layer (rejected — the sole-writer and secret rules must be stated once).
- **Agents involved:** pi-manager (OpenCode session).

## 2026-09-26 - Sole writer is Pi *or* Oh-My-Pi; Antigravity is quota-gated

- **Decision:** Either Pi or Oh-My-Pi may act as sole writer in a dirty tree, never both at once. Antigravity is an allowed review collaborator only while its quota has remaining capacity. Codex and Claude remain forbidden as review collaborators.
- **Reason:** Explicit user instruction. The previous rule ("Pi is the sole writer", "do not task Antigravity") was both too restrictive on the writer and needlessly restrictive on reviewers.
- **Alternatives considered:** Unconditional Antigravity inclusion (rejected — it fails hard once quota is gone); keeping Pi as the only writer (rejected — contradicts the user's decision).
- **Agents involved:** pi-manager (OpenCode session).

## 2026-09-26 - Checkpoint ignore fixed in the subproject, not a new root ignore file

- **Decision:** Fix the nested-checkpoint gap in `universal-game-agent/.gitignore` with `checkpoints/**` patterns rather than recreating a root `.gitignore`. Machine-local paths stay in `.git/info/exclude`.
- **Reason:** The flat `checkpoints/*.pt` rule missed `checkpoints/extern_pong_01/ppo_final.pt` (11.8 MB, unique), so any `git add -A` risked staging ~16.3 MB of binaries. The existing subproject file is the right owner for product-specific artifacts. The 2026-09-15 decision removed the root `.gitignore` at the user's request and that stands.
- **Alternatives considered:** New root `.gitignore` (rejected — reverses an explicit user decision for a product-scoped problem); `git update-index --skip-worktree` (rejected — local-only, invisible to every other clone).
- **Agents involved:** pi-manager (OpenCode session).

## 2026-09-26 - Test baselines are dated observations, not contracts

- **Decision:** Record test counts with the commit they were observed at, state the count is volatile, and make "run the suite" the actual contract. Keep the run-the-suite rule and the "treat any new failure as attributable to current work" rule.
- **Reason:** The `universal-game-agent/` count went 258 → 261 within about an hour because a parallel session committed three new tests. A bare number in a governance file is wrong almost immediately and teaches agents to trust a stale figure.
- **Alternatives considered:** Omit counts entirely (rejected — a rough magnitude is still useful); keep counts and accept the rot (rejected — that is the failure mode being fixed).
- **Agents involved:** pi-manager (OpenCode session).

## 2026-09-26 - Governance files are reviewed before commit, and claims are verified before writing

- **Decision:** Any instruction or memory file asserting facts about code gets an independent read-only review before it is committed. Facts are verified against the source, or explicitly marked unverified, before being written down.
- **Reason:** The first draft of the instruction hierarchy carried seven factually wrong claims (a test count, a checkpoint count, a config key count, a file that had just been deleted, an attributed-to-the-wrong-file helper, an inverted fragility claim, a wrong module path) and one entirely invented defect. All were caught by review and corrected. Writing a plausible-sounding claim is the failure mode, not a typo.
- **Alternatives considered:** Self-review only (rejected — the author is the worst reviewer of their own unchecked assumptions); commit then fix later (rejected — a bad governance file misleads every agent that reads it, and the mistake compounds silently).
- **Agents involved:** pi-manager (OpenCode session).
