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

## 2026-09-26 - A8 persistence failure policy: classify every fallback, fail closed on evidence

- **Decision:** Added `agentops/persistence.py` classifying every store write that has a fallback as `SAFE_TO_DEGRADE` (record a `persistence.degraded` WARNING event plus an in-memory entry, continue) or `MUST_FAIL_CLOSED` (never publish the terminal state). Unknown operations default to `MUST_FAIL_CLOSED`. The verification kernel now forces a report to FAILED when a check's start or terminal state was not durably recorded; runner observer notifications, `failure.create`, and `routing.decision` degrade visibly; `task.update` and the merge conflict task were already fail-closed and are now regression-locked.
- **Reason:** The A3 review deferred kernel state-error swallowing to A8, and the audit found it was not cosmetic: a swallowed `finish_verification_check` error let a report claim `passed` while the stored check was still `running`, and the workflow then marked the task verified on evidence no durable record supported. That is the exact "persistence failure fabricates an unsafe terminal state" the roadmap forbids.
- **Alternatives considered:** Catching broad `Exception` and degrading everything (rejected — it would mask a genuinely broken store as a mere failed check, and the subsequent `finish_verification_run`/`create_verification_report` would fail anyway; the roadmap explicitly warns that overtightening causes spurious failures). Raising a dedicated error (rejected — it strands the verification run as RUNNING and gives the caller no report). Recording degradations on the kernel instance (rejected — the kernel is shared across concurrently-running tasks, so instance state would cross-contaminate runs; per-run state is threaded explicitly instead). Emitting the Event inside the recorder (rejected — `Degradation` is the domain object; the store is injected through `event_emitter` so the leaf stays SQLite-free).
- **Deliberate non-change:** only the previously-swallowed `(KeyError, ValueError)` is caught. A real `sqlite3` error still propagates and aborts the run, which was already fail-closed. Widening the catch was out of scope and would have changed which errors abort a verification.
- **Agents involved:** pi-manager.

## 2026-09-26 - A8 review follow-up: centralize the duplicated provenance write

- **Decision:** After the A8 architecture review, added the four missing classification/wiring sites — `worktree_ref.create` (SAFE_TO_DEGRADE), the four engine-side observer fallbacks, and `recover_incomplete`'s direct `create_failure` — and centralized the CLI/controller `record_worktree_ref` write into `finalize.record_worktree_provenance`. `DegradationRecorder` gained a `threading.Lock` because the controller records from background operation threads. The previous commit's completeness claim was corrected in the module docstring rather than deleted.
- **Reason:** The first pass audited only the orchestration modules and missed a write that both presentation entry points had hand-rolled. The duplication was itself the reason the hole existed twice. Extracting the write also made it testable — the controller's inline version would have needed a dozen mocks to exercise.
- **Alternatives considered:** Leaving the two inline writes and only adding a `degradation.record(...)` line to each (rejected — keeps the duplication that caused the bug and leaves the behavior untestable). Making `worktree_ref.create` MUST_FAIL_CLOSED (rejected — the row is written after the workflow finishes, so failing closed would abort completed work; losing it degrades a later merge retry rather than falsifying a recorded outcome, and the warning makes that visible). Testing the controller's `_run_task_operation` end-to-end (rejected — a dozen mocks for one `except` clause; the shared helper gives the same coverage in three lines).
- **Correction to the record:** the A8 commit message stated "Every store write that has a fallback is now classified." That was false. The reviewer's section-3 claim that the recorder is protected by an `_operation_lock` was also false — no lock existed, and `_operation_lock` belongs to `AgentOpsController`. Both corrections stand in the code and memory.
- **Agents involved:** pi-manager (opencode architecture review).

## 2026-09-26 - Root .gitignore restored, superseding the 2026-09-15 removal

- **Decision:** Commit a root .gitignore containing the patterns from .git/info/exclude plus standard Python/OS/editor ignores. Supersedes the 2026-09-15 decision to remove the root .gitignore.

- **Reason:** All ignore rules lived only in .git/info/exclude (machine-local, never committed). Fresh clones had zero ignore rules, exposing .agentops/state.sqlite (196 KB), .playwright-mcp/ (~5 MB of PNGs), .misc/, small-projects/, agent-intercom-fix/, and other scratch to git add -A. The subproject .gitignore files already cover product-specific patterns (agentops/.agentops/, checkpoints/**/*.pt, logs/*.log, experiments/*/, __pycache__, .venv, etc.); the root file covers container-level patterns only. The prior decision rationale (product-scoped problems should stay in subproject .gitignore) still holds; the root file adds protection the subproject files cannot provide.

- **Alternatives considered:** Keeping machine-local .git/info/exclude only (rejected — fresh clones unprotected). Adding a root .gitignore without superseding the recorded decision (rejected — creates a contradiction in decisions.md).

- **Agents involved:** opencode (this session).

## 2026-09-29 - mini-llm cloud GPU portability: --device flag + CUDA RNG checkpoints

- **Decision:** In `small-projects/mini-llm`, add `--device {auto,cpu,cuda}` to `src/train.py` (`resolve_device`: `auto` preserves the old auto-select, `cuda` exits with an error when CUDA is unavailable instead of falling back, `cpu` forces CPU) and save/restore per-GPU CUDA RNG state under `rng_state["cuda"]` only when the model is on CUDA (absent key = no-op, so old checkpoints and CPU checkpoints keep their exact shape). No other changes: no mixed precision, no attention/throughput work, no new dependencies.

- **Reason:** Cloud-GPU audit showed the loop was already device-agnostic and cross-device checkpoint loads were safe (`torch.load(map_location="cpu")` + optimizer `load_state_dict` casting state "to device of param", verified in installed torch 2.14 source). The two real gaps were explicit device control (a CPU-only Kaggle/Colab runtime would silently train on CPU) and resume fidelity for CUDA-side RNG draws (dropout). Both were on the requested hardening list.

- **Alternatives considered:** Storing the device in `Config`/checkpoints (rejected — device belongs to the machine; a GPU-trained checkpoint must resume on CPU). Capturing CUDA RNG on every save regardless of device (rejected — initializes CUDA on CPU runs and changes CPU checkpoint bytes).

- **Agents involved:** oh-my-pi (this session).
## 2026-10-01 - One authoritative workflow readiness rule

- **Decision:** `execution_model.assess_workflow_readiness()` is the single READY predicate for the whole product, returning a `WorkflowReadiness` that carries the three signals *and* the list of missing prerequisites. `assert_workflow_ready()` raises over the same reason strings; `assert_tasks_ready()` assesses real tasks and delegates to it; `WorkflowEngine.workflow_readiness()` is the engine-level entry point. The CLI custom-DAG path and both `run_high_level` READY returns now call these instead of computing readiness themselves.
- **Reason:** The CLI computed `status == "passed" and evidence` while the standard flow required verification + review + evidence, so a custom DAG with no review task was declared READY and auto-merged. Two divergent copies of one gate is the defect; a shared predicate with actionable messages removes the class, not the instance. Message text was kept ("no passed review task", etc.) because operators use it to tell which prerequisite is missing.
- **Scope of the assessment:** `verification_task_id` / `review_task_id` narrow the assessment to the pair a caller is gating on, because `run_high_level`'s repair cycles create several verification and review tasks and only the final pair decides the outcome. Omitting them assesses the whole workflow, which is what the custom-DAG path needs.
- **Alternatives considered:** Adding a review check to the CLI formula (rejected - fixes one instance and leaves two definitions to drift again). Gating merges in `finalize.py` instead (rejected - `finalize` receives no engine/state-of-tasks context and the CLI already constructs `WorkflowResult` from the shared rule). A `READY` enum/status on the workflow row (rejected - schema change plus a new state to migrate, and it would not stop the underlying duplicate predicate).
- **Deliberate non-change:** `retry_merge` still merges without a readiness check. It is a manual operator retry of an already-reviewed worktree; gating it would change existing behaviour with no evidence that behaviour is wrong. Flagged for a product decision, not silently changed.
- **Agents involved:** Cline (this session).

## 2026-10-01 - AgentOps self-exclusion via .git/info/exclude, not the user's .gitignore

- **Decision:** `GitWorktreeManager._exclude_agentops_state()` appends `/.agentops/` to the repository's `.git/info/exclude` (resolved via `git rev-parse --git-path info/exclude`) when a worktree is created. Existing exclude content is preserved, the entry is never duplicated, and a failure to write is non-fatal.
- **Reason:** Worktrees live under `<repo>/.agentops/worktrees/`, so a fresh clone showed `?? .agentops/` and `merge()` refused on AgentOps' own state - the product deadlocked itself on any unconfigured repository. Verified by reproduction: fresh repo -> `git status --porcelain` non-empty -> merge refused.
- **Alternatives considered:** Writing `.agentops/` into the target repository's tracked `.gitignore` (rejected - silently modifies a file the user owns and creates a tracked change in an unrelated repository; the task explicitly warned against assuming that contract). Moving worktrees outside the target repository entirely (rejected - much larger change to `.agentops/` state layout, log paths, provenance rows, and the GUI worktree browser; it also breaks existing user-local state). Relaxing `merge()` to ignore `.agentops/` (rejected - weakens the genuine dirty-tree protection, which must keep blocking real user changes).
- **Preserved behaviour:** A genuine uncommitted user change still refuses the merge; `cleanup_worktree` still refuses to remove a worktree with uncommitted changes.
- **Known limitation:** this leaves a local ignore entry in each target repository. A repository that intentionally wants `.agentops/` tracked would need it removed. No such contract exists in the repo, so the default was taken.
- **Agents involved:** Cline (this session).

## 2026-10-01 - Redaction is a persistence-boundary requirement, applied to every sink

- **Decision:** `tasks.result` is now redacted at assignment. Beyond the reported agent stdout/stderr sink, three adjacent persistence paths found by inspection were fixed in the same change: legacy verification command output, the `Task execution error: {error}` string, and the verification-kernel report transcript (raw check stdout/stderr embedded in a persisted `VerificationReport`). The `log=<path>` line is preserved so the full transcript stays reachable.
- **Reason:** The repository rule is that persistent content must not carry credentials, and `tasks`/`verification_reports` are durable boundaries. Nearly every other sink already redacted (`workflow.py:206,207,282,659`); this one did not, and `redact_text` was already imported in the same module. The kernel transcript was the more serious of the two adjacent finds: it persisted raw command output into a report object.
- **Alternatives considered:** Storing only the log path and dropping output from `task.result` (rejected - loses diagnostic value and breaks UI flows that read the result). Redacting in `StateStore.update_task` instead of at assignment (rejected - redaction policy applied at the store would be invisible at the call site and would not cover non-store sinks such as the report transcript).
- **Agents involved:** Cline (this session).

## 2026-10-01 - Verification setup failure closes the run; workflow-scoped recovery scopes every pass

- **Decision:** Two failure-path fixes. (1) The kernel's check-creation loop is wrapped so a mid-loop failure calls `_abort_setup()`, which finishes every created check and the run as terminal FAILED, then re-raises the original error unchanged. (2) `AgentOpsController.recover_interrupted()` passes `workflow_id` to `recover_agent_runs` and `recover_verification_runs`, not only to `recover_tasks`.
- **Reason:** The loop sat outside the surrounding `try`, so a failure left the run RUNNING permanently - invisible to recovery and indistinguishable from live work. The recovery scope bug meant "recover this one workflow" also terminated other workflows' live rows; the underlying `StateStore` methods already accepted `workflow_id`, so the controller simply was not passing it.
- **Alternatives considered:** Catching and continuing (rejected - publishes a run whose checks were never created). Swallowing the setup error (rejected - hides a load-bearing failure). Letting `_abort_setup` raise a secondary error (rejected - would replace a useful message with a confusing one, so it swallows its own failures only). Fixing recovery inside `StateStore` (rejected - it already scoped correctly; the bug was at the call site, and the engine's `recover_incomplete` already did it right, which is what made the controller copy a divergence).
- **Deliberate non-change:** the A8 persistence policy was left as-is. `record_failure` already degrades visibly via `DegradationRecorder`; no new error architecture was introduced and no finding was invented to justify one.
- **Agents involved:** Cline (this session).

## 2026-10-01 - Architecture freeze: A5/A6/A7 deferred until the correctness queue closes

- **Decision:** No WorkflowEngine decomposition (A5), no ReviewRun/MergeRun persistence work (A6), no StateStore decomposition (A7) until the correctness queue is closed. Full rationale and entry criteria: `.agents/plans/architecture-freeze-a5-a6-a7.md`.
- **Reason:** The active correctness fixes (READY predicate, persistence-boundary redaction, verification finalization, workflow-scoped recovery) all touch the exact engine/store/finalize seams a split would move; refactoring now would make each fix harder to verify. A6 additionally has no agreed row shapes yet (review/merge runs persist as agent/verification runs + `finalize.py`, not first-class rows), so it is a design task first.
- **Baseline:** agentops suite 407 tests OK (4 environment skips) on 2026-10-01; A5/A6/A7 branches must match it before review.
- **Agents involved:** omp (this session).

## 2026-10-01 - small-projects/ unignored at container level, artifacts ignored locally

- **Decision:** Removed the blanket `small-projects/` rule from the tracked root `.gitignore` (plus its stale machine-local `.git/info/exclude` twin). Large/regenerable mini-llm artifacts are now ignored in `small-projects/mini-llm/.gitignore` instead: `data/tinystories/`, the two large raw corpus files, `data/processed/*.bin`, `checkpoints*/`, `.pytest_cache/`. Source, the tiny shipped sample, `meta.json`, and the default tokenizer stay tracked. The two new mini-llm docs were force-added (`-f`) under the same precedent as the already-tracked `tokenizer.json`.
- **Reason:** The blanket rule forced every legitimate mini-llm doc into a `-f` exception and hid sibling projects (`Cube Timer.html`, `privacy_audit_tool/`, `quickscripts/` now visible as untracked, left alone). Artifact ignores preserve the intent (no gigabyte blobs in git) without hiding source.
- **Agents involved:** omp (this session, per explicit user instruction).
## 2026-10-01 - READY is a conjunction over ONE verification task; exclusion failure is explicit; headless GUI is a test-only skip

- **Decision:** Four AgentOps correctness/CI decisions, all verified against current source before changing anything. (1) `assess_workflow_readiness()` now requires a SINGLE verification task to supply terminal PASSED + `verified=True` + evidence; evidence found only on a different verification task no longer satisfies the contract. (2) `GitWorktreeManager._exclude_agentops_state()` raises an actionable `GitError` when the exclude write fails AND `git status` still shows `.agentops` untracked; it stays silent only when the repo already ignores the directory. (3) `retry_merge()` is confirmed an intentional manual recovery path and keeps its existing gates - no readiness gate added. (4) Headless GUI policy is test-only: GUI test classes that build a real Tk root are marked `@requires_display` (`tests/tk_display.py`); production GUI code is untouched.
- **Reason:** (1) Reproduced a false READY: a PASSED+verified task with no evidence plus a *different FAILED* task carrying evidence satisfied all three independently-scanned signals. The task-level rule `assert_task_completion()` already required a PASSED verification task to carry its own evidence, so the two levels now agree. (2) The old docstring claimed "a failure here is not fatal", which is false: a failed write leaves `?? .agentops/` in `git status` and `merge()` then refuses forever as a dirty base worktree, so AgentOps can never merge its own work. (3) `retry_merge()` operates on an already-reviewed worktree, may run long after the producing workflow left state, and the normal flow already gates on `result.ready`; a readiness gate would be a behaviour change with no demonstrated defect. (4) `tk.Tk()` raises `TclError` with no `$DISPLAY`, so CI errors were environmental, not product defects.
- **Alternatives considered:** Requiring "some task with evidence" instead of the same task (rejected - that is precisely the mixing defect). Relaxing `merge()` to ignore `.agentops/` (rejected - weakens genuine dirty-tree protection, which must keep blocking real user changes). Writing `.agentops/` into the target repo's tracked `.gitignore` (rejected - modifies a file the user owns; explicitly out of contract). Adding a readiness gate to `retry_merge()` (rejected - changes behaviour to satisfy a suspicion; documented instead). Faking a Tk display in production code or adding `xvfb` to CI (rejected - either fakes GUI success or changes CI infrastructure; platform-scoping the tests is the smallest correct change).
- **Deliberate non-change:** `ProcessRuntime.terminate_process()` was NOT weakened. The failing `test_posix_terminate_uses_process_group` was a faulty test double (`HangingProcess.communicate()` returns only after `kill()`, but real `killpg()` SIGKILLs the group without calling `Popen.kill()`); the double was fixed and a new test locks the bounded-cleanup guarantee for an unkillable process.
- **Baseline:** agentops suite 425 tests OK (4 environment skips) on 2026-10-01, up from 407; +18 tests. Verified on Windows only - Linux CI conditions were reproduced by forcing `sys.platform` and stubbing `tk.Tk`, not by an actual Ubuntu run.
- **Agents involved:** Cline (this session).

## 2026-10-02 - Canonical memory is the live state; dated audit reports are evidence

- **Decision:** Make one hierarchy explicit and record it in `.agents/AGENTS.md`, `.agents/memory/project.md`, and `.agents/memory/architecture.md`. The live bug ledger is `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` section 0. The live task queue is `.agents/pending_tasks.md`. Sections 1-11 of master-bug-synthesis.md, including the section 9 "Authoritative Fix Queue", are historical evidence and were marked SUPERSEDED without having their contents rewritten. Agent-scoped memory (`.agents/memory/cline/`, `oh-my-pi/`, `opencode/`) never overrides canonical memory in `.agents/memory/*.md`.
- **Reason:** The 2026-09-29 audit carried a section titled "Authoritative Fix Queue" with per-root READY TO FIX / BLOCKED statuses. Fixes landed afterwards, and twelve findings were disproven against current source, so a reader following that table would have worked twelve disproven roots and re-fixed five closed ones. A file that calls itself authoritative must state which part of it is authoritative.
- **Alternatives considered:** Deleting the stale queue (rejected - it is audit evidence and deleting evidence destroys traceability). Rewriting the section 9 statuses in place (rejected - it would silently rewrite a dated audit and destroy the before/after record). Keeping it live with a note at the bottom only (rejected - the title is what a reader trusts; the note has to be on the heading).
- **Disposition of the 36 roots (2026-10-02):** 10 FIXED, 9 ACTIVE, 2 PARTIALLY FIXED (ROOT-027, ROOT-034), 2 CONTRACT GAP / ACCEPTED DEBT (ROOT-011, ROOT-026), 1 HELD (ROOT-033), 12 DISPROVEN (ROOT-015..025, ROOT-028). ROOT-010 was added to FIXED on source evidence; the audit's FIXED enumeration had omitted it.
- **A5/A6/A7 remain frozen by deliberate decision.** The 2026-10-01 freeze stated its entry condition as "close the correctness queue". That condition has been satisfied, and the freeze nonetheless stays. Re-entry requires an explicit user decision. Satisfying a freeze condition is not the same as lifting the freeze.
- **Baseline re-verified 2026-10-02** at commit `849e015`, each product's own command from its own directory: `agentops/` 425 tests with 4 environment skips OK; `universal-game-agent/` 295 tests with 1 skip OK; `small-projects/mini-llm/` 96 run with 1 skip and 3 pre-existing `TestGenerationSeed` errors caused by the working-tree deletion of `data/tokenizer.json`.
- **Documentation only.** No production source and no test was modified.
- **Agents involved:** Cline (this session).

## 2026-10-02 - Assigned root batch fixed test-first; disposition now 20 FIXED / 0 ACTIVE

- **Decision:** Fix the eight roots assigned to this session test-first (red regression test, smallest fix, focused green): ROOT-004, ROOT-005, ROOT-006, ROOT-027, ROOT-029, ROOT-030, ROOT-032, ROOT-035 — committed and pushed as `b46a3e0`. A parallel session then closed ROOT-014 and ROOT-036 at `2a7b25a`. Ledger §0.1/§0.2, `.agents/pending_tasks.md`, canonical `project.md`, and `.agents/memory/oh-my-pi/project.md` were synced to the combined state.
- **Reason:** These were the remaining CONFIRMED actionable roots after the 2026-10-02 verification. ROOT-005 routes swallowed failure-record losses through the existing `DegradationRecorder` (A8 persistence policy) instead of adding a second logging channel; ROOT-027's failure-results write is guarded so the recorder can never mask the original run error; ROOT-029 makes the producer satisfy the validator's existing invariant rather than relaxing the validator.
- **Alternatives considered:** `logger.exception` in the bare arms (rejected — the persistence policy already defines the visible sink for record loss; a log line would be an unstructured duplicate). Editing superseded §9 statuses in place (rejected — same traceability reason as the 2026-10-02 hierarchy decision above).
- **Disposition of the 36 roots (2026-10-02, post-batch):** 20 FIXED, 0 ACTIVE, 1 PARTIALLY FIXED (ROOT-034), 2 CONTRACT GAP / ACCEPTED DEBT (ROOT-011, ROOT-026), 1 HELD (ROOT-033), 12 DISPROVEN (ROOT-015..025, ROOT-028).
- **Baseline after the batches (2026-10-02):** `agentops/` 443 tests, 4 environment skips, OK; `universal-game-agent/` 317 tests, 1 skip, OK; `small-projects/mini-llm/` unchanged (96 run, 1 skip, 3 pre-existing `TestGenerationSeed` errors from the working-tree tokenizer deletion).
- **Agents involved:** omp (this session); ROOT-014/ROOT-036 by a parallel session.

## 2026-10-02 - Hit/miss counts require a DECLARED reward semantics; absence, not zero, means "not measured"

- **Decision:** (ROOT-036.) Reward semantics are declared, never inferred from the reward sign. `environment/reward.py` defines `SIGN_SEMANTICS`/`GENERIC_SEMANTICS` plus a `reward_semantics_of()` reader that fails closed: undeclared or unrecognised becomes `generic`, with a warning and never an exception. `RewardProvider` carries `reward_semantics = GENERIC_SEMANTICS` as its default; only `ToyPongEnv` and `ExternPongReward` declare `sign`. `ExternalGameEnv` exposes it as a **property** over the live provider. `evaluate()` reports `episode_hits`/`episode_misses`/`mean_hits`/`mean_misses` only when semantics are `sign` and otherwise **omits the keys**, keeping the counts as `episode_positive_reward_steps`/`mean_negative_reward_steps`. `PreprocessingWrapper` downgrades to `generic` when `skip > 1`. Consumers (`main._print_eval_report`, `summarize_eval`, `summarize_difference`) became conditional, and comparison metrics are now derived from the summaries instead of a second hand-maintained tuple. No reward semantics, PPO mathematics, or tracked results JSON changed. Separately (ROOT-014), a resumed checkpoint that already met `total_timesteps` is a no-op that keeps the existing `ppo_final.pt`, and readers go through a new `summarize_history()` that tolerates the empty history.
- **Reason:** Counting `reward > 0` as a hit is only correct when the reward sign *means* hit/miss. Under `SurvivalReward` every surviving step pays `+1`, so the "hits" column would have reported the step count as a game statistic. `experiments/*_results.json` are the evidence artifact, so the report has to be self-certifying: a reader in six months must be able to see whether a hit count was earned. Declaration also makes the claim testable, which inference is not.
- **Alternatives considered:** Always emit the keys with `None`/`0.0` (rejected - `0.0` reads as "hit nothing", which is the false claim being closed, and `float(None)` breaks the delta computation). Ask the provider for an `event_classification()` API (rejected - five no-op overrides to reproduce one `getattr`, and `CompositeReward` would then have to aggregate child classifications correctly, a new bug surface). Hard-fail on an unrecognised semantics value (rejected - `evaluate()` runs after a multi-hour phase-2 training run against checkpoints that are not cheaply reproducible; a tag typo must not destroy it at reporting time, and the local idiom for unrecognised input is `warnings.warn`). Snapshot `reward_semantics` in `ExternalGameEnv.__init__` (rejected - callers swap `reward_provider` after construction, so a snapshot keeps publishing hit counts for rewards that no longer mean that). Keep `"sign"` through frame skip (rejected - summing rewards across frames breaks the one-event-per-decision-step invariant, so the wrapper must withdraw the claim it cannot honour). Event labels in `info` (rejected as a separate piece of work - it is the better long-term design but changes the `step()` return contract).
- **Deliberate non-change:** `ExternalGameEnv.reset()` still does not reseed the external game; the process is seeded once at launch and episodes are session continuations. That is documented on `reset()` rather than changed, because the game is a separate process.
- **Verification:** `universal-game-agent/` 317 tests, 1 skip, OK (dated baseline before: 295, 1 skip, OK). The new tests were confirmed to fail against the old behaviour. A read-only design review ran before any code was written and returned six failure modes in the first proposal, two of them unlisted consumers that would have broken the suite.
- **Open:** the exp02 re-run did not run (cancelled, machine in use); the exp02 verdict stays suspended. Session record: `.agents/memory/opencode/sessions/2026-10-02-uga-root-014-036.md`.
- **Agents involved:** OpenCode (this session).

## 2026-10-03 - UGA direct execution fixed with a test-only bootstrap; test checkpoints isolated

- **Decision:** Close ROOT-034 DBG-06 (direct test execution ineffective) and DBG-07 (four tests writing checkpoints into the caller's CWD) in one commit `59f5a1b`: shared `tests/_bootstrap.py` plus a 4-line relative-first prelude in all 20 UGA test modules, 7 mid-file `unittest.main()` calls moved to EOF, the four checkpoint-writing tests redirected to per-test temp dirs with their `ppo_final.pt` assertions kept and strengthened, and a new `tests/test_cwd_isolation.py` regression guard. `python -m unittest discover -s tests` remains the source-of-truth command; AGENTS.md now describes direct execution as a convenience, and its dated baseline moved 317 → 318 (2026-10-03).
- **Reason:** The pass required the smallest coherent repository-level fix that makes direct execution meaningful without becoming a second test framework. A test-only bootstrap is the one mechanism that serves script, `-m`, and top-level discovery entry points from any working directory without touching production code; the audit's other causes (silent ImportError → skip, mid-file main) are one-line-shape fixes in the test files themselves.
- **Alternatives considered:** pytest-style `conftest.py` (rejected — the suite is unittest; it would add a second mechanism and a dependency for one path fix). A `sitecustomize`/path hack in production code (rejected — no path machinery in shipped code). Rewriting all 20 files' import structure (rejected — the prelude is four lines per file and the relative-first form keeps package imports unchanged). Deleting checkpoint generation from the four tests (rejected — training is exactly what they exercise; pointing the same writes at temp dirs keeps the claim and loses only the pollution).
- **Verification:** discovery 318 tests, 1 skip, OK with `checkpoints/ppo_final.pt` mtime unchanged across the run; all 21 test files executed from a fresh scratch cwd produced counts identical to discovery's per-module counts and left the scratch directory empty; `python -m tests.test_diagnostics` and direct `python tests/test_cwd_isolation.py` both OK. One latent defect was found and fixed in the same pass: the `side_effect` mock leak described in today's lessons.md entry.
- **Scope:** no production source, no AgentOps file, and no queue/ledger file changed; pre-existing dirty files left unstaged and untouched. Pre-existing, reported not regenerated: the gitignored working-tree `checkpoints/ppo_final.pt` was already clobbered by pre-fix runs.
- **Agents involved:** omp (this session).

## 2026-10-04 - The Qt desktop client is a package with one shared context module, and its widget tests run offscreen

- **Decision:** The Tkinter client was replaced by a PySide6 package `agentops/gui/` (commit `5b80d9b`). `ViewContext` and `AsyncMixin` live in `gui/context.py`, **outside** `gui/views/`, with `views/base.py` re-exporting them. Tk widget tests were replaced by Qt equivalents driven through a new `tests/qt_display.py` that runs on `QT_QPA_PLATFORM=offscreen` and skips only when PySide6 is absent; `tests/tk_display.py` was deleted. PySide6 is an optional `desktop` extra, not a core dependency. Backend diffs were kept only where the GUI demonstrably required them (`StateStore.count_*`, `workflow.STANDARD_TASK_ROLES`); unrelated `AsyncMock` test churn was reverted.
- **Reason:** The shared context had to leave `views/` because `detail.py` imports it while `views/__init__.py` imports every view and `views/listdetail.py` imports `detail.py`; leaving it in place made any standalone panel import a circular import that broke the whole package at startup. Offscreen Qt is strictly better than the old display-skip for CI: Tk tests silently skipped on headless Linux, so they were decoration there, whereas offscreen tests run identically on a developer machine and in CI. The backend was deliberately left alone because the task was a bring-to-known-good pass, and an unreviewed backend diff is where scope creep hides.
- **Alternatives considered:** Keeping `ViewContext` in `views/base.py` and making `detail.py` import it lazily inside functions (rejected - it hides a real layering problem behind deferred-import noise, and every call site still reaches into `views`). Keeping Tk tests with `@requires_display` skips (rejected - Tk is no longer a dependency, so the tests would test deleted code). Forcing the async rows to load synchronously to simplify tests (rejected - that would have changed the bridge's threading contract, which is the correct design). Accepting the `AsyncMock` modernization because it is arguably an improvement (rejected - it is unrelated to the GUI; "better" is not "in scope", and mixing unrelated churn into a migration commit makes the diff unreviewable).
- **Deliberate non-change:** No visual redesign, no routing change, no workflow semantics change, no new features. `AgentOps.spec` was left un-rebuilt even though packaging-affecting code changed; the rebuild plus the documented archive-inspection procedure is owed before shipping an exe. No visual verification on a real display was performed - only offscreen.
- **Verification:** `cd agentops && python -m unittest discover -s tests` -> **438 tests, 4 skipped, OK**. The 11 new Qt GUI tests exercise the real `MainWindow`. `python -m agentops gui` was launched offscreen twice: once staying alive with no traceback, and once driven end-to-end through the real entry point with the real controller (window rendered 1024x680, all 10 views navigated, clean close).
- **Open:** `AgentOps.spec` still lacks PySide6 in `hiddenimports`. Both `.agents/AGENTS.md` and `agentops/AGENTS.md` still state the AgentOps baseline as 444 tests when it is now 438; left stale on purpose to avoid touching `agentops/` while another session owned it.
- **Agents involved:** Cline (this session). Commit `5b80d9b`, pushed as merge `7d5e7e1`.

## 2026-10-04 - The control center projects truth in a Qt-free layer and reads READY, never re-derives it

- **Decision:** Rebuild the Workflows surface as an orchestration control center in UI files plus one additive controller read (commit `7a5b472`). New `gui/control_center.py` (684 lines, Qt-free and pure) owns every projection: stage flow, live panel, verification totals, failure summaries, worktree provenance, merge readiness, cancellation. `gui/views/workflows.py` only lays out what the projection layer computed. `gui_controller.py` grew exactly one method, `workflow_readiness()`, which delegates to the existing `assess_workflow_readiness()` in `execution_model.py`. New widgets in `gui/widgets.py`: `StageNode`, `StageFlow`, `Tally`, `LiveCard`; `QTabWidget`/`QTabBar` styling added to `gui/tokens.py`.
- **Reason:** Two separate problems. First, a live workflow state was being spread across Qt widgets, so the interesting logic could only be tested with a display and each claim needed a widget assertion. Second, the READY contract in `execution_model.py` is the single source of truth for merge gating, but no surface could read it, so a GUI merge state would have to re-implement it - exactly the "no path may hold a weaker contract" failure the contract exists to prevent. Splitting the projections out made every claim testable without Qt *and* let readiness stay a read.
- **Alternatives considered:** Put the projections in `gui_controller.py` next to the serializers (rejected - it would mix DTO serialization with policy and re-import Qt-free logic into a module the GUI mutates through). Re-derive readiness in the GUI from task rows (rejected - this is precisely the duplicate-contract bug; a GUI copy would drift silently). Fold control-center fields into the existing `get_workflow` payload (rejected - it would make one unbounded read and lose the ability to degrade per-section when one query fails). Compute a progress percentage per stage (rejected - the mission forbids fabricated progress and nothing measures per-stage progress). Keep the horizontal `PipelineBar` (rejected - a stage carries agent, model, duration, verification, failure, and timestamp; that does not fit a 200px column, and the workflow is read top to bottom anyway).
- **Deliberate non-change:** No orchestration semantics, no state-schema change, no migration, no routing/verification/Git change. `workflow_readiness` degrades to "blocked" rather than claiming ready if absent. `last_view`, the sidebar nav groups, and the other nine views are untouched.
- **Verification:** `cd agentops && python -m unittest discover -s tests` -> **501 tests, 4 environment skips, OK** (445 baseline + 56 new). `tests/test_control_center.py` splits display-free (projections) from `@requires_qt` (widget) coverage. The view was rendered offscreen and inspected; that caught two defects no assertion could (unstyled `QTabWidget` page stack painting a white slab, and tables leaving an unstyled white strip past their last column). Three truthfulness bugs were caught by tests and fixed: Finalize falsely reported "running", elapsed fell back to the workflow header's `updated_at`, and an unrecorded duration rendered as "-".
- **Agents involved:** Cline (this session). Commit `7a5b472`, not pushed (no push was requested).

## 2026-10-04 - The Qt desktop got one visual system: page/section hierarchy, one accent, screenshots as evidence

- **Decision:** Establish the client's visual layer in UI files only (commits `2159af6`, `c753e3e`). `gui/tokens.py` owns the type/spacing/colour contract (page 17/600, section 13/600, group labels; 33px single-line controls; unified radii; exactly one accent `#4c8dff`; nav checked accent bar with variant rules ordered before `:checked`). `gui/widgets.PageHeader` is the mandatory view header (title, subtitle, action row) used by all ten views. Sidebar `_NAV_GROUPS` mirrors `VIEW_SPECS` (Orchestrate/Inspect/Configure) so navigation cannot drift from the registered views. The combo arrow is a bundled asset (`gui/assets/chevron-down.svg`) referenced through `tokens.ASSET_DIR.as_posix()`. `_fade_stack` must remove its `QGraphicsOpacityEffect` in `animation.finished`. New guard `tests/test_gui_visual_states.py` (7 tests) pins nav-group mapping, page-header contract, dashboard empty/populated visibility, list-detail error restore, sidebar toggle, effect release, and asset wiring.
- **Reason:** The Qt migration was explicitly bring-to-known-good and shipped no visual language; ten views had inconsistent headers, hierarchy, and empty states. Screenshot-driven verification then found four defects that no amount of static reading had — stale navigation regions, an inflated empty dashboard card, a grey-square combo arrow, a blank agents detail panel. Visual claims need visual evidence, and `VIEW_SPECS`/`_NAV_GROUPS` are two hand-maintained lists over the same views, so a drift guard is cheaper than the next silent mismatch.
- **Alternatives considered:** Keeping the CSS border-triangle combo arrow (rejected - Qt Style Sheets do not implement CSS border drawing; it rendered a grey square). Leaving the fade's opacity effect installed (rejected - a live effect routes the whole stack through effect compositing and left stale previous-view regions). A second accent or gradient headers (rejected - one-accent rule; hierarchy comes from type roles, not colour). More empty-state copy on the dashboard (rejected - Qt splits spare height between non-stretching labels; the fix is one Expanding occupant per card, not wording).
- **Verification:** `cd agentops && python -m unittest discover -s tests` -> **445 tests, 4 environment skips, OK** (438 baseline + 7 new); `tests.test_gui_qt` 11 OK after every shell/dashboard edit; live app screenshots of all 10 views at 1024x680 / 1280x800 / 1600x900; each defect above re-verified live after its fix (navigation round trip twice, toggle/chevron/row selection/focus rings observed).
- **Deliberate non-change:** no backend/controller/state/verification/Git change; no routing change; `AgentOps.spec` rebuild still owed (unchanged from the migration decision above); `.agents/AGENTS.md` and `agentops/AGENTS.md` still say 444 and were not touched (other sessions own them).
- **Agents involved:** omp (this session). Commits `2159af6`, `c753e3e`, pushed to `origin/main`.

## 2026-10-04 — mini-llm: restore tracked data artifacts from git, never regenerate them in place

- **Decision:** `small-projects/mini-llm/data/tokenizer.json` is a **tracked artifact and a
  canonical input**, not a build output to be regenerated on demand. When it is missing,
  restore it from git; do not retrain a substitute into `data/`. No test was weakened,
  deleted, or skipped to clear the failure.
- **Why:** `prepare_data.py` writes the tokenizer and the processed `.bin`/`meta.json`
  together, and the tokenizer is the *input* to encoding — so the tokenizer defines what
  the processed data means. A regenerated BPE is not guaranteed to reproduce the committed
  ids (BPE output depends on trainer and library version), so regenerating would silently
  change the meaning of the committed `data/processed/*` instead of restoring it. Verified
  here in the restore direction: the committed tokenizer regenerates the committed
  `data/processed/*` byte for byte (vocab 308, 920 tokens, split 736/184).
- **Evidence the artifact was tracked, not deprecated:** `git ls-files` includes it,
  `git log --diff-filter=D` on the path is empty, and its only commit (`12dc37f`) is the
  one that added it. A working-tree ` D` is therefore an accident, never an intent signal.
- **Consequence:** shipped data artifacts get a test that reads the *committed* file.
  `TestShippedData` previously guarded corpus-vs-`meta.json` agreement while every check
  either retrained into a temp dir or read the `.bin` files, leaving the committed
  tokenizer with zero coverage — which is how a deleted tracked artifact read as a stable
  three-session baseline.
- **Diagnostic policy:** a load path must name the artifact it wanted. `load_tokenizer`
  now raises `FileNotFoundError` with the absolute path, matching the existing
  `config_for_data` convention, instead of leaking a bare Rust `os error 2`.
- **Process rule adopted:** a repeated "known baseline failure" is re-derived with
  `git status` and `git log --diff-filter=D` before being recorded as accepted. Three
  sessions inherited this one as a fact; it was an accident each time.
- **Agents involved:** opencode (this session). Artifacts and tests restored and verified
  locally; commit left to the user.


## 2026-10-04 - Interrupted work is counted read-only beside recover_all, surfaced by the shell

- **Decision:** Add the desktop-interaction layer (command palette commands, recovery banner, outcome-specific notifications, F5, tray status/hide, quit-cancel) with exactly two additive reads: `StateStore.count_interrupted_work()` placed directly beside `recover_all()` as a read-only mirror of its predicates, and `AgentOpsController.interrupted_work()` exposing counts plus total to the shell. Banner probing is idle-only (first show, repository switch, refresh), notifies once per distinct interruption, and Review reuses the existing `recover_interrupted()` behind a confirmation. Unready workflow results read `workflow_readiness()` to choose `Verification failed` vs `Workflow blocked`.
- **Reason:** The shell must show interrupted work *before* an operator confirms recovery, but recovery mutates state, so detection cannot call it. Counting in `state.py` next to the recovery queries keeps both predicates in one file with a parity test (`count == recover_all`), which is the only cheap way to stop them drifting. Idle-only probing matters because during a live operation the pending/running rows are legitimately live — flagging them would cry wolf every poll. Readiness stays a read (same rule as the control center): the GUI names the engine's own reasons instead of inventing a verdict.
- **Alternatives considered:** Listing runs/verifications through the existing facade methods and filtering in the GUI (rejected - no count API exists, windowed limits would make the banner lie at scale, and predicate logic would live in two modules). Calling `recover_all()` to "detect" (rejected - detection would mutate state silently). A periodic always-on probe (rejected - it would notify during live operations and add steady-state reads for no benefit). Toasting a generic "not ready" (rejected - the mission asks for distinct verification-failed vs blocked notifications, and `workflow_readiness` already provides the distinction).
- **Deliberate non-change:** No schema/migration change, no workflow semantics change, no new daemon for the tray (existing tray extended in place), no change to New Task or recent-repositories flows (already complete), no packaging work (`AgentOps.spec` rebuild still owed from earlier decisions).
- **Verification:** `cd agentops && python -m unittest discover -s tests` -> **512 tests, 4 environment skips, OK** (501 + 11 new). `QT_QPA_PLATFORM=offscreen python -m agentops gui` ran 8s with no traceback; a driven offscreen run with the real controller exercised palette discovery (16 commands incl. Recover), banner hidden at zero counts, F5 registry, tray tooltip, a real `interrupted_work` probe, and clean close.
- **Agents involved:** omp (this session). Commit `feat(agentops): add desktop application interactions`.

## 2026-10-04 - tiktok-slop-factory caption timing distributes weight, not equal slices

- **Decision:** `captions.speech_aware_timestamps` replaces even duration slicing. Weight = 1.0 base + 0.35 per syllable beyond the first (vowel-group estimate, silent trailing `e` dropped) + a pause from trailing punctuation (`,` 0.50, `;`/`:` 0.70, `.`/`?`/`!`/ellipsis 0.90). Weights are normalized against the probed narration duration. `MIN_CUE_SEC = 0.40`, `MAX_CUE_SEC = 7.00`; the floor raises `CaptionTimingError` rather than strobing. Grouping and timing share `_group_words`, so both agree on caption edges and neither merges across a sentence boundary.
- **Reason:** Even slicing drifts because speech does not spend equal time per word. The scale factor is found by bisection rather than an iterative clamp loop: re-scaling after a clamp pushes the remaining cues below the floor and the budget walks off a cliff (this actually failed the first test run). The readability floor is applied per *caption*, not per word, so the pipeline passes `max_words=3` and a 32-word 6s narration still renders instead of failing.
- **Deliberate non-change:** Documented as a drift-reduction heuristic, not speech alignment — no Whisper, no hard-coded English speaking rate; the probed duration is authoritative. No Gemini, TTS, renderer, visual, verification, batch, CLI, or manifest changes.
- **Verification:** `python -m pytest tests -q` -> 109 passed, 2 failed; the two failures (`test_loads_dotenv_from_project_root`, `test_generate_ideas_rejects_duplicate_padding`) reproduce identically on the untouched tree before any edit and need `GEMINI_API_KEY`/`.env`. Baseline was 97, now 111 collected, 14 added, none needing network. Suite is pytest, not unittest: `python -m unittest discover -s tests` collects 0.
- **Agents involved:** Pi (this session).

## 2026-10-04 - mini-llm checkpoints are content-addressed, not path-bound

> The heading date was previously recorded as 2026-10-10, which is after
> this repository's own history for this decision (`0aad33b`, reconciled
> in `8e7ef83`, 2026-10-04). Corrected against `git log`; the content was
> always accurate.

- **Decision:** Identify every training artifact by the sha256 of its bytes rather than by the path string it was reached through. `prepare_data.py` records `train_bin` / `val_bin` / `tokenizer_path` and their three digests in `meta.json`; `Config.data_provenance()` is the single writer of that identity; every checkpoint the training loop writes carries a `data_provenance` block; `--resume` refuses a digest mismatch. `src/train.py` gains `--tokenizer`, and `src/generate.py` applies the same check to its existing one.
- **Reason:** A checkpoint stored only the paths, and a resumed run took its config *from the checkpoint*, so `train_bin` / `val_bin` / `tokenizer_path` were whatever the run happened to have been given - or the default, since `tokenizer_path` had no flag at all. Nothing then verified that the files at those paths were the ones that produced the weights. The only thing that caught it was an incidental `vocab_size` disagreement, which fires on the vocabulary and says nothing about the corpus or the tokenizer. Digests make "is this the same data" a question with an answer, and comparing content rather than location means a moved or copied artifact tree still resumes while a swapped file at the same path does not.
- **Alternatives considered:** Comparing paths as strings (rejected - it refuses the legitimate case of a relocated artifact tree and still misses a file replaced in place). Hashing only the tokenizer (rejected - it leaves train/val drift undetected, and mixing a val split from another data set is the quieter version of this bug). Storing digests in the checkpoint but not in `meta.json` (rejected - then a fresh run cannot be checked either, and the first resume after a re-prepare would be unverifiable). Warning instead of refusing (rejected - the entire failure mode is that it was previously silent; a warning nobody reads restores it).
- **Deliberate non-change:** No architecture, model, dataset, or optimizer change. `weights_only=True` loading is preserved (`data_provenance` is a plain dict of str/int). Stride remains a non-persisted pipeline choice, as before. The 168 MB `checkpoints/tinystories/final.pt` was not rewritten: it is git-ignored, cannot be resumed under the new rule, and re-running a 10,000-step experiment to change a file's provenance was not in scope.
- **Verification:** `cd small-projects/mini-llm && python -m unittest discover -s tests` -> **115 run, 1 skip, OK** (98 + 17 in `TestDataProvenance`). End-to-end smoke in `$TEMP/mlsmoke` on non-default paths: train -> bare `--resume` succeeds with no data flags (the case that previously died), a resume against the shipped `data/processed/` artifacts is refused naming both digests, and generation with a foreign tokenizer is refused.
- **Agents involved:** Cline (this session). Commit `0aad33b`, pushed to `origin/main`.

## 2026-10-05 - UGA cadence/reward investigation: synthetic matrix is the evidence base, displacement claim corrected

- **Decision:** Add `training/cadence_experiment.py` + `experiments/exp_cadence_synthetic.{yaml,json}` — a window-free, SendInput-free 4-cell timing matrix that drives the REAL `ExternalGameEnv`/`ExternPongReward`/`ExternPongTermination` over a 60 fps virtual clock, varying only `decision_period_ms` and `hold_ms` with the `model:` block and nine shared `ppo:` keys byte-identical to exp01/exp02. Withdraw the AGENTS.md "paddle moves 5 px per decision" claim in favour of **15-20 px** (60 ms hold = 3-4 ticks × 5 px source math, synthetic-verified; live-window confirmation queued as a pending task). Expose `env.timing.reset_settle_poll_s` (default 0.05, behaviour unchanged), add `upd_action_share` to PPO history, and add an `observation_timing` fingerprint + `metric_definitions` to external results artifacts. Full findings documented in `universal-game-agent/AGENTS.md` § "Cadence / reward investigation".
- **Reason:** The investigation had to measure timing/action/observation behaviour without hours of real GUI time, and controlled experiments — not intuition — were required before calling cadence the root cause. Probes per cell (oracle upper bound, random/no-op floor) make discrimination measurable in seconds. The 5 px claim conflated one frame with one decision and was never live-verified; a known-wrong number in a governance file misleads every future reader (claims are measurements).
- **Alternatives considered:** Measuring displacement in a live window first (rejected for this task — real SendInput runs require asking; queued in `.agents/pending_tasks.md` instead); re-running exp01/02 for fresh metrics (rejected — hours of GUI time, no approval); changing PPO hyperparameters after seeing entropy (rejected — no diagnostic justified it, and ROOT-015..018/028 are DISPROVEN).
- **Deliberate non-change:** no PPO math or hyperparameter change; no test edited to accommodate the torch-less baseline (the 8 modules that error instead of skipping without torch are reported as-is; the fix is installing torch, done in this interpreter); `.agents/memory/*.md` initially untouched because they were shared dirty state, updated later at the user's explicit request ("update memory"), append-only.
- **Verification:** `cd universal-game-agent && python -m unittest discover -s tests` -> **366 tests, 1 skip, OK (2026-10-05)** (+17 vs the 349 orchestration baseline). Matrix run exit 0; every doc claim re-derived before writing (compare01 `timestamp_utc` from the JSON, `b77bf4d`/`6890c42` dates from `git log`, model-block equality from a YAML parse). Key results: reward discriminates (oracle +0.1/len 200 vs random −1.0/len 5.6 at current timing); decision period alone does not gate learnability; 147.6 ms period + 16.667 ms hold (~5 px/press) kills even the oracle (len 7.9) — control authority binds; no cell reached positive rolling reward at the 16384-step pilot budget; exp01/exp02 constant PRESS_LEFT = argmax over near-uniform (entropy ≈ ln 3), not collapse.
- **Agents involved:** omp (this session). Nothing staged, committed, or pushed.

## 2026-10-04 - tiktok-slop-factory rejects incoherent narration before visual rendering

- **Decision:** Add a coherence gate in `_produce_one`, immediately after the duration probe and before `visuals.plan_scenes`. Two independent failures: (1) **silence** - FFmpeg `volumedetect` mean volume at or below `SILENCE_MAX_DB = -80.0` dBFS; (2) **duration mismatch** - the probed length outside `captions.estimate_speech_range(narration)`, a band of `SECONDS_PER_WEIGHT = 0.40` scaled by `DURATION_TOLERANCE = (0.60, 2.00)` and floored at `MIN_PREDICTED_SEC = 1.0`. The estimator sums the *existing* caption `word_weight` values, so there is one speech model rather than two. The gate never modifies audio: it rejects with an actionable message and asks for regeneration.
- **Reason:** Visual rendering is the expensive stage, so an impossible text/audio pairing should cost a few hundred milliseconds of FFmpeg analysis rather than a full 1080x1920 encode. Reusing the caption weights also means the predictor cannot drift away from the caption timing it is supposed to describe.
- **Silence threshold was measured, not guessed.** FFmpeg `volumedetect` on 16-bit PCM reports digital silence as exactly **-91.0 dB**, a -60 dB tone as -78.3 dB, a -50 dB tone as -71.1 dB, and an ordinary tone as -21.1 dB. -80 dBFS sits inside the empty gap, so quiet-but-audible narration always passes and only genuinely empty audio fails. Verified empirically before the constant was chosen; do not retune it without repeating that measurement.
- **Existing test changed deliberately:** `test_end_to_end_produces_vertical_video` stubbed TTS with a **6s tone for a 32-word script** (~5.3 words/sec) - exactly the incoherence this gate targets, so the gate correctly rejected it. The fixture now derives tone length from `captions.estimate_speech_range(...)` instead of hard-coding 6. Do not "restore" the hard-coded 6s. Consequence: suite runtime rose from ~300s to ~630s, because the test now renders ~22s videos instead of 6s.
- **Deliberate non-change:** `MIN_CUE_SEC = 0.40` and `MAX_CUE_SEC = 7.00` untouched. No Gemini prompt, TTS, visuals, renderer, manifest/resume, retry, CLI, or batch change. A narration that passes this gate but still exceeds the caption bounds is explicitly deferred to a separate caption-policy task.
- **Verification:** `cd tiktok-slop-factory && python -m pytest tests -q` -> **125 passed, 2 failed** (628s). The two failures (`test_config.py::test_loads_dotenv_from_project_root`, `test_gemini_parsing.py::test_generate_ideas_rejects_duplicate_padding`) are the same environmental `GEMINI_API_KEY` failures carried from the caption pass, reproduced on a clean tree before any edit and out of scope. 16 new tests in `tests/test_narration_coherence.py`; all 32 caption tests still green.
- **Agents involved:** Pi (this session). Commit `f31221f`, pushed to `origin/main`.
## 2026-10-06 - Verification results live in one generated file, and instruction files carry no counts

- **Decision:** the current measured result for each product is
  `.agents/evidence/verification.json`. `tools/evidence/generate.py` produces it
  by running each product's own suite - by default inside a throwaway clone of
  the current commit, so the record describes committed code rather than the
  working tree. `tools/evidence/check.py` verifies the record and fails when it
  is malformed, when a file under a measured product changed after the recorded
  commit, when the run was dirty, or when an instruction file restates a count.
  `.github/workflows/evidence.yml` runs the checker and its tests, not a fourth
  copy of the three suites.
- **Reason:** five files each held their own hand-copied test count and all five
  disagreed with each other and with reality. Two date claims were impossible
  against the repository's own history. A green suite here had already been
  proved untrustworthy in both directions: tests that passed only because the
  working tree was dirty, and a merge-gate test that could not fail against the
  bug it was written for. The generalisable fix is not better prose, it is
  removing the manual copies and leaving one generated artifact with a checker
  attached.
- **Alternatives considered:** (a) a wiki page or a single markdown table - no,
  the table was the problem; (b) a coverage or lint gate - no product configures
  one, so CI would be inventing a standard; (c) checking the recorded count
  against a live run on every CI push - rejected as a fourth place for a count
  to live, and it duplicates what the three product workflows already do;
  (d) per-product evidence files - rejected, one file keeps the cross-product
  comparison in one place and the checker small.
- **Deliberate non-change:** dated historical baselines stay in the memory files,
  relabelled as history rather than deleted, because they record what was
  measured when and are still evidence of that. Memory files were not turned
  into a second database: they point at the evidence file, they do not copy it.
  `tiktok-slop-factory` and `ai-token-tracker` are recorded as deliberate
  exclusions with reasons rather than silently omitted.
- **Known limit, stated rather than implied:** editing a count *and* the runner
  summary it came from is indistinguishable from a measurement without re-running
  the suite. Staleness closes the useful part of that window, since a forged
  number cannot outlive the code it describes. The record keeps the verbatim
  summary lines precisely so the ordinary case - editing one number - fails.
- **Agents involved:** OpenCode.

## 2026-10-06 - AI Token Tracker shot 3: exact-only collectors, cross-source-only dedup

- **Decision:** a collector either reports exact tokens from official local data or is listed unavailable with a visible reason (gray status dot) — nothing may estimate and be labeled exact. Agent and provider are independent dimensions (persisted `tool` column plus `infer_tool` backfill for legacy rows), so OpenCode→OpenRouter→X and OMP→OpenRouter→X each keep their agent attribution while sharing one provider row. Canonical usage-event identity is `request_id` when the source provides one, otherwise the content key `ct|provider|model|tokens|epoch//5*5`; content dedup runs only when the stored row's `source` differs from the incoming row's `source` (first-seen attribution wins), so one underlying request seen through two sources counts once, while same-source events with different ids and zero-token rows are never merged.
- **Alternatives considered:** (a) provider usage APIs (OpenRouter/OpenAI/Gemini) reported as estimates — rejected: violates the never-label-estimates-exact rule, and OpenRouter 403s without a key anyway; (b) scraping screenshots or terminal output — rejected as untrustworthy per shot constraints; (c) content dedup including same-source rows — rejected: two legitimate distinct requests with identical token counts would collapse; (d) a per-session "provider-only" counting mode — rejected: adds a second accounting path for a double-count that source-scoped dedup already prevents.
- **Evidence:** 7/7 collectors exact against real local data (14,155 events, 2.45B tokens, 0 estimated, idempotent re-sync); Sources view shows `7/7 · exact 7/7 · unavailable 3` with per-source reasons; 41/41 tests (2026-10-06).
- **Agents involved:** Oh-My-Pi.

## 2026-10-06 - Post-reboot recovery: committed work is safe, uncommitted work is intact

- **Decision:** treat the 7 ahead-commits (`eb153e2` through `9365920`) as safe without re-verification; recovery effort goes only to uncommitted state — Manicode's AgentOps packaging body (uncommitted by task design, "Do not merge anything"), OpenCode's 6 staged memory/session files (session doc index-only, restore via `git show :<path>`), and OMP/ canonical memory write-ups. OMP SHOT 4 ("find and cover the user's remaining installed AI agents") had written zero bytes when the reboot hit (`ai-token-tracker/` zero diff vs HEAD), so its resume point is HEAD + the SHOT 4 prompt recovered from `cli_files/oh-my-pi/home/agent/history.db`, step 1 (discovery listing) first. Hermes' Chrome/ChatGPT turn is server-side; no local replay. AgentOps packaging stays fenced off from OMP recovery (separate pathspec commits, never `git add -A`).
- **Alternatives considered:** re-running suites to "confirm" committed work — rejected: commits are hash-pinned and the index-race history proves the risk is in new commits, not old ones; pushing before review — rejected: operator must review 7 ahead-commits first.
- **Evidence:** read-only audit 2026-10-06 (cline session): EventLog 6008 ~1:07:23pm, boot 1:19:25pm, HEAD 1:07:15pm; `git status`/`git log --all`/mtime scan; `D:\admin\backup\` absent with 4 quarantine `.bdq` 1:18:01-1:18:57pm.
- **Agents involved:** Cline (auditor).

## 2026-10-06 — PR rebasing to resolve stale CI failures

- **Date:** 2026-10-06
- **Decision:** Rebase PRs #5, #6, and #7 onto origin/main (9ce33da) to resolve identical CI failures caused by pre-existing test issues fixed in main but absent from the PR base commit (66724b2).
- **Reason:** All three PRs failed with the same 5 test errors (PermissionError on '/agentops-cli-test' from LogManager mkdir, plus 3 workflow-agent-run assertion errors) that were fixed in main by commit 42ed95c ("test(agentops): fix 2 CLI+3 workflow failures on CI's clean checkout", 2026-10-06). Rebasing each PR's branch onto current main brings in that fix without modifying the PRs' intended changes.
- **Method:** Each PR branch was checked out into an isolated detached HEAD worktree (`.agentops/worktrees/pr-{5,6,7}-rebase`), fully fetched with `--unshallow` (the clone is shallow and lacks common ancestry for a direct rebase), then rebased onto `origin/main`. All three rebases applied cleanly with no conflicts. Resulting heads were pushed back to GitHub with `--force-with-lease`: PR #6 → c37af9a, PR #7 → 2c87398, PR #5 → 0ed030f.
- **Verification:** Each rebased branch ran the AgentOps suite from `agentops/` (`python -m unittest discover -s tests`): #6 = 627 tests OK (skipped=4), #7 = 630 tests OK (skipped=4), #5 = 639 tests OK (skipped=4). GitHub Actions for each PR now show the `agentops` tests workflow passing. Diffs verified as still narrowly scoped to each PR's original purpose.
- **PR-specific verdicts:** #6 merge-approve (minimal registry fix); #7 merge-approve (GUI pagination boundary fix, all 3 methods + 6 regression tests preserved); #5 needs human review of `_dependency_handoff` / `_repair_failed_review` plus a real-agent end-to-end run before merge (per Issue #4, Step 5), then it can close the loop toward Issue #4.
- **Alternatives considered:** Editing the failing tests inside each PR (rejected — those failures are main's fix, not PR defects, and changing them would hide the real state and risk re-introducing the bug when main advances).
- **Agents involved:** Cline (this session).

## 2026-10-07 - PR #5 merged into main: standard workflow made autonomous

- **Decision:** PR #5 (`feat(agentops): make standard workflow autonomous`, PR head `0ed030f` / merged `f742773` / merge commit `d0c5512`) merged into `main` via `git merge --no-ff`, verified, and pushed to `origin/main`.
- **Reason:** The standard workflow produced a chain of independent agents rather than one workflow: dependent agents only received their own request text (implementation never read the architecture plan; review never saw the change it was judging), and a failed review ended the run at "Review did not pass" instead of being repaired. PR #5 closes this loop: `_dependency_handoff()` prepends each PASSED dependency's role/description/result to the dependent agent's prompt, bounded by `HANDOFF_RESULT_CHARS=2000` and `HANDOFF_TOTAL_CHARS=8000`; review now depends on `implementation.id` in addition to `verification.id`; `_repair_failed_review()` turns a demonstrated review rejection into a bounded `debugging → verification → review` cycle governed by `max_repair_cycles`; repair context is recovered from the review task's failure rows; an exhausted budget returns NOT READY; a BLOCKED review creates no repair (mirroring the UNVERIFIED rule). Adds 15 tests in `tests/test_workflow_autonomy.py`.
- **Method:** Rebased PR branch onto current `origin/main` (17 commits on main since the PR merge-base), ran the AgentOps suite, no conflicts, all tests green, then `git merge --no-ff merge-pr5-work`. Merge commit `d0c5512` has exactly 2 changed files: `agentops/agentops/workflow.py` (+181/-16) and `agentops/tests/test_workflow_autonomy.py` (+332 new).
- **Evidence:** PR #5 `merged: true` on GitHub (authoritative); `main` SHA `d0c5512`; PR files present on `main` confirmed via `git show`; `test_workflow_autonomy.py` present on `main`; all 15 autonomy tests OK (15/15, ~15.5s); full suite OK (72/72, 1 skipped).
- **Agents involved:** Cline (this session).

## 2026-10-08 - Structured FAILURE/PARTIAL can never PASS an AgentOps task (PR #10, unmerged)

- **Decision:** `WorkflowEngine._execute_task` grants `TaskStatus.PASSED` only when `result.succeeded` (timeout/cancel/terminate/exit-code) AND the role evidence contract AND the structured agent status is not `FAILURE`/`PARTIAL`. New `_structured_agent_status()` helper prefers the runner-attached `structured_result` envelope (`coerce_agent_result`), falls back to parsing `stdout` (STRUCTURED/PARTIAL modes only, so runner doubles that skip the envelope are still honoured), returns `None` for legacy/plaintext. `SUCCESS`/`UNKNOWN`/absent keep historical exit-code behaviour; nonzero-exit + `SUCCESS` still fails.
- **Reason:** The task decision read only `RunResult.succeeded`, so an agent that exited 0 while explicitly reporting failure/partial carried the task to PASSED and the workflow to READY — the same evidence-in-translation-object class as the files_changed and cancelled/terminated defects. Six regression tests (`StructuredAgentResultTaskStatusTests`); verification evidence regenerated (`tools/evidence/generate.py agentops`: 701 tests, same 3 pre-existing failures + 2 flaky wheel errors as the prior record).
- **Alternatives considered:** Reusing `evaluate_execution` for the task gate (rejected — it conflates merge-eligibility with task completion and treats UNKNOWN as failure, which would break legacy/plaintext agents); trusting stdout-parse alone without the envelope (rejected — the runner already normalized it; envelope-first avoids double-parse divergence).
- **Agents involved:** omp assistant (this session).

