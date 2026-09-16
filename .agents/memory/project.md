# Project Memory (canonical)

> Read before substantial work. Update when project state changes. Never store secrets. Populate only verifiable facts; otherwise `Not yet established.`

## Project purpose

- AgentOps: local-first orchestrator for installed coding-agent CLIs (opencode, codex, pi, claude/fcc-claude, copilot, antigravity). Plan → implement → verify → review workflows in isolated Git worktrees, with SQLite persistence, a Tkinter GUI, and a Windows .exe. Established 2026-09-13 from `D:/admin/code/projects/agentops` + `tasks/task.md`.

## Current project state

- AgentOps v0.1.2 at `D:/admin/code/projects/agentops` (bumped 2026-09-14). AgentRun support implemented 2026-09-14 (95 tests passing). Verification Kernel implemented 2026-09-14: 122-test suite passing (1 Windows-platform skip), including 27 kernel tests; relayed opencode review findings fixed and rebuilt. `dist/AgentOps.exe` rebuilt (~14.7 MB, PyInstaller one-file windowed), archive-inspected (15 `agentops.*` modules), and startup/shutdown smoke-tested.
- Working tree carries uncommitted feature work (do not assume HEAD == working state): GUI (`gui.py`, `gui_controller.py`), packaging (`AgentOps.spec`, `agentops_gui.py`, `scripts/build_windows_exe.py`), History/Logs/Worktrees tabs, cancellation tokens, operation-ID staleness guard, shared `finalize.py`, AgentRun lifecycle, Verification Kernel, Failure/Repair/Recovery Kernel (backed up to `/tmp/agentops-backup-failure-kernel/` before work began), and now the Structured Results upgrade (backed up to `/tmp/agentops-backup-structured-results/`; 184-test suite passing, 1 platform skip).
- Milestones 1–2 complete: workflow history + task inspector, safe log browser, worktree inspect/cleanup/retry-merge. Full architecture audit completed 2026-09-13 with a 10-phase roadmap (in session, not yet user-approved).
- Historical 2026-09-12 note: repo root was greenfield (memory scaffold only); product work lives in `agentops/`.
- Shared memory scaffolding created 2026-09-12; full memory system initialized same day per user directive (Parts 1–7).

## Requirements

- From `tasks/task.md`: Windows .exe + intuitive GUI integrated with existing services (not standalone); reliability/dependency upgrades without breaking architecture; bug diagnosis + regression tests; graceful errors with useful logging; reproducible build/deploy docs.
- Follow-ups requested: more exe features (planned first, then Milestones 1–2 implemented), version 0.1.1, architecture audit with 10-phase roadmap.

## Constraints

- Do not reinstall or reconfigure Agent Intercom; do not change Intercom scope.
- Never store secrets, API keys, passwords, tokens, credentials, or private keys in memory (files or Supermemory).
- Coworker policy (explicit user instruction, overrides team.md): collaborate only with opencode, free-claude-code (`fcc-claude`), and copilot. No Codex/Claude coworkers.
- Single writer in the dirty tree (Pi implements); other agents review via read-only `/tmp/agentops-review-*` snapshots through `agentops run` (keeps repo untouched, logs under snapshot).
- Back up the dirty tree (`git diff` + untracked tarball to `/tmp/agentops-backup-*`) before substantial work.
- team.md reconciled 2026-09-13 to this policy; new memory files: `audit.md` (audit record), `roadmap.md` (10-phase plan, all PROPOSED).
- pi-subagents worktree isolation requires a clean tree — it fails on this dirty repo; use `agent_fleet`/direct `agentops run` instead.
- User must not manually relay messages between agents; Pi relays via Intercom.
- No concurrent uncoordinated edits to the same files.

## Technologies

- Python >= 3.11, runtime dependencies: stdlib only (Tkinter, sqlite3, asyncio, unittest). Optional: PyYAML (`.[yaml]`), PyInstaller >= 6 (`.[windows]`).
- Test runner: `python -m unittest discover -s tests` (336 tests across 20 files as of 2026-09-16).
- Packaging: PyInstaller 6.22 one-file windowed exe; package-aware `agentops_gui.py` launcher (entry script must not be `agentops/gui.py` — relative imports break frozen).

## Important conventions

- Memory location: `.agents/team.md`, `.agents/memory/project.md`, `.agents/memory/architecture.md`, `.agents/memory/decisions.md`, `.agents/memory/lessons.md`.
- Operating rules: read memory before substantial work; update after; preserve history; keep concise, no transcripts; no duplicate entries.
- Manager verifies Intercom connectivity before major work.

## GUI vertical-budget fix 2026-09-15

- Follow-up: scrollbar commit alone left the prompt box UNMAPPED (zero-height cell) — measured live: notebook starved to ~99px because only its row had weight while fixed-height siblings (trees height 6, output 8) claimed the 700px window first; pre-change the box was a 4px sliver, i.e. already broken. Fix: main rows 4/5/6 share growth (2/1/1); trees 6→5 rows, output 8→6 rows; default geometry 1060x740. Verified mapped: prompt 940x98, description 894x129, notebook 210px. Row-weight regression test added. Suite 289 OK. Exe rebuilt + Release asset replaced (14,823,429 bytes).

## GUI scrollbar/layout fix 2026-09-15

- User report: text boxes cut off in Direct run / Task workflow / History / Logs / Artifacts / Worktrees tabs. Root causes: prompt/description `Text` widgets had no scrollbars (tall content unreachable); all three Treeviews + both Listboxes had no horizontal scrollbars while fixed column widths overflow even at min-size (850–940px of columns); only one column per tree was allowed to stretch. Fix: vertical scrollbars on prompt/description; vertical + horizontal scrollbars on all trees/lists with `xscroll`/`yscroll` wiring; `stretch=True` on every tree column. 3 layout regression tests; suite 288 OK. Exe rebuilt + Release asset replaced (14,822,467 bytes).

## GUI no-flash + poll efficiency fix 2026-09-15

- User report: exe "runs a ton of powershell commands", hogs desktop, slow UI. Root causes found: (1) every `git.exe`/agent spawn from the windowless exe flashed a console window (`CREATE_NO_WINDOW` missing on all spawn sites; the 1s status poll re-spawns git constantly); (2) the 1s poll rebuilt the full task tree + full `latest_workflow` payload (200 runs) on the Tk thread. Fixes: `_no_window_kwargs()` centralized in `git.py`, applied to git spawns, metadata collector, runner + legacy/kernel verification spawns (kernel via `_default_spawn` wrapper preserving injectable factory); controller caches git-root lookups (failures not cached); `_update_tasks` skips rebuilds when the task signature is unchanged. 7 new tests; suite 285 OK. Exe rebuilt + Release asset replaced (14,820,748 bytes). Cold-start AV-scan slowness (one-file extraction) noted as residual, one-dir build offered as follow-up.

## A2 adapter boundary 2026-09-15

- Track A2 DONE + review closed: opencode APPROVED (278 OK confirmed locally); 2 LOW + 2 notes all applied (dead-branch removal, strip/dedupe capabilities at load, cache-pin warning). Suite 278 OK. Exe rebuilt + Release v0.1.3 asset replaced (14,820,707 bytes).

## A1 execution state machine 2026-09-15

- Track A1 DONE + A1R review adjudicated: opencode 4 findings + 1 note all fixed (kernel-empty fail-fast with legacy parity, recover post-conditions wired, all-skipped rejected + optional-failure correction, same-state no-op, self-contained READY asserts). Suite 256 OK. Exe rebuilt (22 modules) + Release v0.1.3 asset replaced twice (final 14,814,649 bytes). Behavior change stands: empty verification suites no longer verify (supersedes Review #2 — user may overrule).

## Release v0.1.3 + exe 2026-09-15

- Fresh `dist/AgentOps.exe` rebuilt from Phase 1/3 tree (14,807,079 bytes, PyInstaller 6.22.3; 2 stale processes taskkilled first per lesson); archive-inspected (21 `agentops.*` modules incl. all kernels); startup/shutdown smoke-tested with process-exit verification. Published as GitHub Release asset `v0.1.3` (tag pushed, release id 389098654). Exe stays out of git per standing decision.

## Phase 1 + Phase 3 delivery 2026-09-15

- Roadmap Phase 1 (Storage DTOs) + Phase 3 (persisted worktree refs) implemented in `agentops/`: Workflow/WorktreeRef DTOs, schema v6, controller serialization, stored-provenance retry, CLI/GUI exposure. Suite 224 OK (3 skips). GitHub `.agents/plans/chatgpt_*.md` synced locally; phase-3 kernel plan already DONE. See `memory/roadmap.md`, `memory/architecture.md`, `memory/decisions.md`, `memory/lessons.md`.

## Current priorities

1. Structured Results upgrade implemented 2026-09-15: `agentops/agent_result.py` leaf (versioned `AgentResult` schema with all 12 required fields, `ParseMode`, total `parse_agent_result`/`agent_result_from_dict`/`coerce_agent_result`, `evaluate_execution` five-way outcome distinction), runner + workflow store normalized envelopes (TEXT column unchanged, no migration), prompt seam gains optional JSON contract with plain-text fallback, 34 dedicated tests. Copilot snapshot review (94s, read-only `/tmp/agentops-review-structured-results/`) reported 4 findings, all fixed with regressions (JSON-text coerce, strict process-success bool, unsupported-version downgrade for all statuses, warnings+parse_mode persisted); hostile-payload test caught a 5th bug (repr crash in warnings). Full suite 184 passing (1 platform skip). No live Intercom peers; opencode/fcc-claude reviews not requested (no live sessions, backend/server unverified). No exe rebuild (packaging untouched).
2. Phase 3 Failure/Repair/Recovery Kernel implemented 2026-09-14: `agentops/failure.py` leaf domain (16 categories, deterministic classifier, RetryPolicy, RepairPlan, interruption classification), schema v3 `failures` table, workflow bounded-retry + inherited-context + parent-linked AgentRun integration, `failures`/`recover` CLI, controller/GUI failure views, 24 new tests (146-test suite passing, 1 platform skip), exe rebuilt (~14.8 MB, 16 modules incl. `failure`, smoke-tested). Reviews requested from live `copilot` (tests) + `opencode-projects-17196` (architecture) via read-only `/tmp/agentops-review-failure-kernel/` snapshots; replies pending at close. fcc-claude review not requested (`fcc-server` down).
3. Roadmap approved 2026-09-14: work Phase 1 (finish `Row`→dataclass mapping); Phases 2–10 remain an ordered backlog. AgentRun/Verification Kernel already delivered parts of Phases 1, 4, and 5.
4. Commit strategy resolved 2026-09-15: AgentOps work committed as `935f4dc` (37 files, +10283/-173). Projects-root repo tracks everything except machine-local dirs (`.misc/`, `.playwright-mcp/`, `agent-intercom-fix/` via `.git/info/exclude`; committed `.gitignore` removed per user request). `agentops/` is recorded as an embedded-repo pointer (mode 160000), not file contents — its history lives in its own repo. Root HEAD: `ef3c4c8`. Root `master` pushed to `https://github.com/angasko-12345/projects-ai` (origin) 2026-09-15. Branches united into `main` 2026-09-15 (merged remote Initial commit for LICENSE, dropped its Qt `.gitignore`, deleted remote `master`; nested `agentops` repo branch also renamed `master`→`main`). Root HEAD: `fd9abfb`, tracking `origin/main`.
5. Single-repo fold 2026-09-15: nested `agentops/` repo dissolved into the root via subtree merge (`a1c95d3`, both histories linked; safety bundle at `/tmp/agentops-pre-fold.bundle`; 2 stale worktrees removed after clean check). `agentops/` files now browsable on GitHub under `projects-ai`. Run tests from `agentops/`: `python -m unittest discover -s tests` (184 OK) — root-level invocation shadows the package, do not use.
6. Consolidation 2026-09-15: `.agent/` folded into `.agents/` (`outputs/`, `plans/`); stray empty root `.agentops/` removed (runtime state lives only at `agentops/.agentops/`, git-ignored; root copy excluded machine-locally).
7. Failure Analysis audit 2026-09-15: `tasks/task.md` header was externally replaced with the Failure Analysis spec (already built as Phase 3). Audited spec-vs-code: no gaps, no code changes, 184 tests green, fresh Plan + Output written to task.md, pushed (`dd6e6b3`). Note: `tasks/task-ignorethis.md` was also externally rewritten; left untouched. Untracked strays `working_models.txt` / `omniroute-model-results.csv` are not ours — left alone (excluded machine-locally).
8. Phase 4 Event + Artifact Infrastructure 2026-09-15: `events.py` leaf (schema v1, 27 types, bus) + `artifacts.py` (hash-verified store, retention), schema v4/v5, CLI/controller/GUI exposure, 30 new tests, suite 214 OK. Opencode review: 15 findings, blockers + hardening fixed. Committed `5742b2a`, pushed. Copilot test review: 2 findings + gaps fixed post-push (atomic-rename + orphan scan, legacy mirror, traversal/symlink tests, whole-open retry fixing a real first-open flake); suite 217 OK.
9. Version 0.1.3 + exe rebuild 2026-09-15: bumped `__init__.py` + `pyproject.toml` (`023abc7`, suite 217 green); rebuilt `dist/AgentOps.exe` (14,801,169 bytes, PyInstaller 6.22.3) — archive-inspected (21 `agentops.*` modules incl. all new kernels) and startup/shutdown smoke-tested with process-exit verification. Exe itself stays out of git (ignored `dist/`).
10. Standing 2026-09-12 items (superseded where product work took precedence): memory peer reviews PENDING (Intercom EPIPE outage); Supermemory availability for non-Pi agents PENDING (Pi: none — 0 MCP servers).

## B7 capability router 2026-09-16

- Implementation complete: `routing.py`, registry profiles/version detection, workflow routing with exclusions/history, `routing.decision` events with executed-agent tracking, routing switch, README/package exports, 25 routing tests plus one adapter regression test. Suite 336 OK (4 skips). Backup: `/tmp/agentops-backup-b7-router-20260916-182309`.
- Reviews: copilot and opencode reviews complete. Fixed scalar-selector compatibility, non-ASCII version decoding, role-derived selector capabilities, legacy-capability consolidation, executed-agent/event agreement, and single-profile input; rejected stale/harness observations with evidence.
- No executable rebuild: packaging untouched.

## A3 shared ProcessRuntime 2026-09-16

- Implementation complete: `runtime.py`, runner/kernel/legacy-verifier delegation, unified spawn policy, CancelledError termination, thread-free cancel polling, duplicate-name validation before persistence, fail-fast evidence-dominated overall status. 16 new tests; suite 336 OK (4 skips). Exe rebuilt (14,851,920 bytes), archive-inspected, smoke-tested.
- Reviews: copilot and opencode reviews complete and adjudicated (suite 336 OK). Opencode: fixed factory alias, dead TimeoutError branch, policy docs/comments, POSIX cleanup test; stale/threadpool and POSIX-test findings rejected with evidence; SIGKILL-first rebutted from baseline; state-error swallowing stays deferred to A8.
- Backup: `/tmp/agentops-backup-a3-runtime-20260916-203040`.

## A4 structured failure evidence 2026-09-16

- Implementation complete: `FailureEvidence`, structured-first classifier, evidence at agent/verification/legacy recording sites, executable-only agent commands, redacted/scrubbed evidence, schema v7 column. 17 new tests; suite 353 OK (4 skips).
- Reviews: copilot snapshot review completed (5 fixed with regressions); opencode architecture review requested asynchronously and pending.
- Backup: `/tmp/agentops-backup-a4-evidence-20260916-211809`.
