# Project Memory (canonical)

> Read before substantial work. Update when project state changes. Never store secrets. Populate only verifiable facts; otherwise `Not yet established.`

## Project purpose

- AgentOps: local-first orchestrator for installed coding-agent CLIs (opencode, codex, pi, claude/fcc-claude, copilot, antigravity). Plan → implement → verify → review workflows in isolated Git worktrees, with SQLite persistence, a Tkinter GUI, and a Windows .exe. Established 2026-09-13 from `D:/admin/code/projects/agentops` + `tasks/task.md`.

## Current project state

- Current verified state (2026-10-02): three products in one container. `agentops/` v0.1.3, additive schema v7; `universal-game-agent/`; `small-projects/mini-llm/`. The canonical instruction layout is `.agents/AGENTS.md` plus `.agents/pi_AGENTS.md`, with root compatibility shims.
- **Test baselines, re-run 2026-10-02** (each product's own command, from its own directory): `agentops/` **443 tests, 4 environment skips, OK**; `universal-game-agent/` **317 tests, 1 skip, OK**; `small-projects/mini-llm/` **96 run, 1 skip, 3 pre-existing `TestGenerationSeed` errors**. The 3 mini-llm errors are caused by the working-tree deletion of `data/tokenizer.json` (pre-existing user change, not committed) and are not a code defect.
- **UGA baseline re-verified 2026-10-03** after commit `59f5a1b`: `universal-game-agent/` **318 tests, 1 skip, OK** (the 2026-10-02 figure above remains current for `small-projects/mini-llm/`; the `agentops/` figure was re-verified below on 2026-10-03). The +1 is the new `tests/test_cwd_isolation.py`; direct execution of every `tests/test_*.py` now works from any working directory and matches discovery's per-module counts.
- **AgentOps baseline re-verified 2026-10-03**: `agentops/` **444 tests, 4 environment skips, OK** — the +1 is `tests/test_runtime.py::test_real_parent_terminate_process_kills_descendant_tree` (ROOT-034 DBG-08 closed; real Windows parent→child→grandchild termination, mutation-proven). The 2026-10-02 figure above remains current for `small-projects/mini-llm/`.
- **Bug status (2026-10-02):** the live ledger is `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` §0 — 36 canonical roots: 20 FIXED, 0 ACTIVE, 1 PARTIALLY FIXED, 2 CONTRACT GAP / ACCEPTED DEBT, 1 HELD, 12 DISPROVEN. ROOT-015..025 and ROOT-028 are disproven and must not be revived.
- **A10 (CI/regression gating) is DONE**, delivered at `2cb2413` as per-product path-filtered workflows with scope in `.github/CI.md`. Whether CI gates PRs remains undecided (roadmap D5 follow-up).
- **A5/A6/A7 are FROZEN by deliberate decision.** Their stated freeze condition ("close the correctness queue") has been satisfied, but the freeze remains. Re-entry requires an explicit user decision; they are not ordinary backlog.
- Current open UGA bugs: only the UGA share of ROOT-034 (test-quality cluster), of which **DBG-06 (direct test execution) and DBG-07 (CWD checkpoint pollution) were closed 2026-10-03 in `59f5a1b`**; the remaining ROOT-034 items were untouched this pass and the ledger is reconciled separately. ROOT-014, ROOT-027, and ROOT-036 were fixed 2026-10-02 (commits `b46a3e0`, `2a7b25a`). ROOT-011 and ROOT-026 are CONTRACT GAP / ACCEPTED DEBT.
- Current open AgentOps bugs: only the AgentOps share of ROOT-034 (test-quality cluster), of which **DBG-08 was closed 2026-10-03** by a real Windows parent→child→grandchild termination test (`tests/test_runtime.py::test_real_parent_terminate_process_kills_descendant_tree`, mutation-proven; 444 tests, 4 skips, OK); DBG-12/DBG-15 remain as residual test debt. ROOT-004, ROOT-005, ROOT-006, ROOT-029, ROOT-030, ROOT-032, and ROOT-035 were fixed 2026-10-02 (commit `b46a3e0`). ROOT-033 is HELD pending a Windows termination contract.
- **mini-llm checkpoint-path wart (current):** `Config.tokenizer_path` has no CLI flag and `prepare_data.py` does not record it in `meta.json`, so every checkpoint stores the default `data/tokenizer.json`; `train_bin`/`val_bin`/`checkpoint_dir` are taken from the checkpoint on resume. A bare `--resume` of the TinyStories artifact fails in `Config.validate_against_data()` with `vocab_size=8192 but data/processed/train.bin was encoded with a vocabulary of 308`. Source-level wart, documented, not fixed.
- Historical: (2026-09-20) AgentOps v0.1.3, additive schema v7, A3/A4/B7 complete, full suite 357 passing with 4 environment skips. That number is superseded by the current baseline figures above.
- AgentOps v0.1.2 at `D:/admin/code/projects/agentops` (bumped 2026-09-14). AgentRun support implemented 2026-09-14 (95 tests passing). Verification Kernel implemented 2026-09-14: 122-test suite passing (1 Windows-platform skip), including 27 kernel tests; relayed opencode review findings fixed and rebuilt. `dist/AgentOps.exe` rebuilt (~14.7 MB, PyInstaller one-file windowed), archive-inspected (15 `agentops.*` modules), and startup/shutdown smoke-tested.
- Correctness queue closed for AgentOps on 2026-10-01 (Cline): full suite 425 passing, 4 environment skips. Four verified changes: READY now requires ONE verification task to supply PASSED + `verified=True` + evidence (signal-mixing across tasks fixed and reproduction-locked); `_exclude_agentops_state()` raises an actionable `GitError` instead of silently deadlocking merges when `.git/info/exclude` cannot be written; `retry_merge()` audited and deliberately left ungated (roadmap D9 resolved); headless GUI handled by a test-only `@requires_display` policy in `tests/tk_display.py`, with production GUI unchanged. Verified on Windows only - Linux CI conditions were simulated, not run.
- Working tree carries uncommitted feature work (do not assume HEAD == working state): GUI (`gui.py`, `gui_controller.py`), packaging (`AgentOps.spec`, `agentops_gui.py`, `scripts/build_windows_exe.py`), History/Logs/Worktrees tabs, cancellation tokens, operation-ID staleness guard, shared `finalize.py`, AgentRun lifecycle, Verification Kernel, Failure/Repair/Recovery Kernel (backed up to `/tmp/agentops-backup-failure-kernel/` before work began), and now the Structured Results upgrade (backed up to `/tmp/agentops-backup-structured-results/`; 184-test suite passing, 1 platform skip).
- Milestones 1–2 complete: workflow history + task inspector, safe log browser, worktree inspect/cleanup/retry-merge. Full architecture audit completed 2026-09-13 with a 10-phase roadmap (in session, not yet user-approved).
- Historical 2026-09-12 note: repo root was greenfield (memory scaffold only); product work lives in `agentops/`.
- Shared memory scaffolding created 2026-09-12; full memory system initialized same day per user directive (Parts 1–7).

## Memory hierarchy rule (2026-10-02)

- The **live bug ledger** is `.agents/memory/opencode/bugfinding/master-bug-synthesis.md`
  **§0** (Current Status). Read a root's status there before acting on any bug.
- The **live task queue** is `.agents/pending_tasks.md`. It contains only unfinished work.
- **Historical audit reports remain evidence, not current instructions.** Sections 1-11 of
  master-bug-synthesis.md and every other dated report in
  `.agents/memory/opencode/bugfinding/` are never revised in place and are never a work
  queue. master-bug-synthesis.md §9 is explicitly SUPERSEDED.
- **Agent-scoped memory never overrides canonical memory.** `.agents/memory/cline/`,
  `.agents/memory/oh-my-pi/`, and `.agents/memory/opencode/` hold runbooks and session
  records only. When they disagree with `.agents/memory/*.md`, the canonical file wins and
  the agent-scoped file is wrong and needs correcting.
- `decisions.md` and `lessons.md` are append-only. Add dated entries; never rewrite old ones.

## Canonical instruction layout — 2026-09-20

- `.agents/AGENTS.md` and `.agents/pi_AGENTS.md` are the canonical repository and Pi-specific instruction files; `.agents/ohmypiagents.md` is the Oh My Pi-specific supplement.
- Root `AGENTS.md` and `pi_AGENTS.md` remain compatibility entrypoints for tools that only discover instructions at the repository root. They redirect readers to `.agents/` and are not independent sources of truth.
- The canonical repository instructions record the current schema-v7 state. The dated 357/4-skip baseline once recorded there is superseded; see `.agents/AGENTS.md` for the 2026-10-02 baselines.

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
- Test runner: `python -m unittest discover -s tests` (latest recorded baseline: 425 passing, 4 environment skips, recorded 2026-10-01 on Windows).
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
- Reviews: copilot and opencode reviews complete and adjudicated (suite 356 OK). Opencode: fixed legacy-evidence redaction, timeout precedence, broader token patterns; command-field finding rebutted with scrub evidence.
- Backup: `/tmp/agentops-backup-a4-evidence-20260916-211809`.

## A8 persistence failure policy 2026-09-26

- Delivered: `agentops/persistence.py` (policy table + degradation recorder + `event_emitter`), `EventType.PERSISTENCE_DEGRADED`, verification-kernel fail-closed on lost check state, visible degradation in the runner and workflow. Real defect fixed (a `passed` report backed by a `running` check row). 18 new tests; suite 375 OK (4 skips).
- Backup: `/tmp/agentops-backup-a8-persistence-20260926-174814`. No exe rebuild: packaging untouched.
- Pending follow-ups: `SpawnFactory` Protocol typing; repository characteristics for routing (`workflow.py` still passes `{}`).

## Temp artifact archive 2026-09-17

- Safely moved 27 top-level Temp entries whose names contain `agent-intercom` or `agentops` into `.local-temp-archive/agent-intercom/` and `.local-temp-archive/agentops/`.
- Archive verification: 30,476 files, 314,882,683 bytes; staged copies were SHA-256 verified before Temp source deletion, and final files were reverified against `move-manifest.json`.
- At archive time, no name-matched Temp entries remained. The two Agent Intercom trees were later restored to Temp; the 25 AgentOps entries remain archived. The machine-local archive is excluded via `.git/info/exclude`; no product code or packaging files changed.
- Full suite from `agentops/` at archive time: 357 passing, 4 environment skips.

## Agent Intercom Temp restore 2026-09-17

- Restored `agent-intercom-opencode-test` (4,208 files, 97,542,276 bytes) and `agent-intercom-pi-test` (26,056 files, 212,548,349 bytes) from `.local-temp-archive/agent-intercom/` to their original Temp directories.
- Verification: staged copies were SHA-256 verified before the Temp rename; final Temp trees reverified. The AgentOps archive stayed untouched (212 files, 4,792,058 bytes; tree digest unchanged). Restore manifest: `.local-temp-archive/agent-intercom-restore-manifest.json`.
- Restored working-tree Git deltas are preserved (`dist/broker.mjs`, `dist/plugin.mjs`, `dist/tui.mjs` in the opencode tree; `provider/provider.mjs` in the pi tree).
- Full suite from `agentops/` after restore: 357 passing, 4 environment skips.

## Repo reorganization + agent instruction hierarchy 2026-09-26

- **Two products confirmed 2026-09-26; three products confirmed 2026-10-01.** `agentops/`, `universal-game-agent/`, and `small-projects/mini-llm/` are independent; none depends on another. The root is a container plus agent process material. A single repository-wide test command does not exist. Any "two-product" statement below this entry is a dated historical record.
- **Backup:** `C:\Users\admin\AppData\Local\Temp\opencode\projects-backup-20260926` — 50 files (0.399 MB) plus `worktree.patch` (56,902 bytes) covering the modified and untracked files.
- **Cleanup, 19.71 MB reclaimed:** deleted `agentops/build/`, `agentops/.pytest_cache/`, `agentops/agentops.egg-info/`, all `__pycache__` directories, and the `state.sqlite-shm` / `state.sqlite-wal` sidecars. `dist/AgentOps.exe` (14,851,920 bytes) and `.agentops/state.sqlite` were preserved deliberately — the exe is a release deliverable and the sqlite file is live state.
- **`universal-game-agent/.gitignore` fixed:** rules are now `checkpoints/**/*.pt`, `checkpoints/**/*.zip`, `checkpoints/**/*.pkl`, preceded by an ignore-all-then-allow pattern for `README.md` and `.gitkeep`. The previous flat `checkpoints/*.pt` rule did not match nested checkpoints, so `git add -A` would have staged ~16.3 MB of binaries, 11.8 MB of it unique. No root `.gitignore` was created — see the 2026-09-15 commit-strategy decision, item 4.
- **`.git/info/exclude`** gained `.pi/` and `small-projects/`; existing entries preserved.
- **`checkpoints/README.md`** created, documenting checkpoint provenance and regeneration. Two trained checkpoints exist: `checkpoints/ppo_final.pt` and `checkpoints/extern_pong_01/ppo_final.pt`; the third `.pt` (`ppo_untrained.pt`) is untrained.
- **`.agents/skills/` committed** as `65ef097` — 22 files across 13 skill directories, verified free of reparse points before staging. The four concurrently modified memory/task files were deliberately excluded from that commit.
- **Instruction hierarchy created** (documentation only; no runtime code changed). `.agents/AGENTS.md` is the universal contract, `agentops/AGENTS.md` and `universal-game-agent/AGENTS.md` are product-local, and root `AGENTS.md` / `pi_AGENTS.md` are compatibility shims that must not be treated as sources of truth. `.agents/ohmypiagents.md` and `.agents/pi_AGENTS.md` were reduced to tool-specific deltas; `.github/copilot-instructions.md` and `.agents/team.md` were corrected to match.
- **A correctness review found real errors in the first draft of those files** — seven factually wrong claims plus one defect that did not exist. All were corrected. See lessons 2026-09-26.
- **Test baselines at `a03e907` (2026-09-26):** `agentops/` ran 357 tests with 4 environment skips (353 passed); `universal-game-agent/` ran 261 tests with no skips. The UGA count moved from 258 to 261 within roughly an hour because a parallel session added tests, which is why both baselines are now recorded as dated observations rather than contracts.
## AgentOps failure-path audit 2026-10-01 (commit fb5f3f3)

- Four defects fixed in `agentops/`, each reproduced against current source before fixing and covered by regression tests. Full suite: **407 tests, OK with 4 environment skips** (previous recorded baseline 357 total / 4 skips; the suite had grown to 382 before this work, all green).
- **Readiness gate.** The CLI custom-DAG path derived READY from `status == "passed" and evidence` with no review check, so a custom workflow with verification but no review was auto-merged. One predicate now owns the rule (`assess_workflow_readiness`), used by the CLI, the GUI, and both `run_high_level` READY returns.
- **`.agentops/` self-deadlock.** Fresh repos could never merge: worktrees under `<repo>/.agentops/` made the base look dirty and `merge()` refuses a dirty base. Now self-excluded via `.git/info/exclude`; no tracked file is modified and genuine user changes still block the merge.
- **Unredacted persistence.** Raw agent stdout/stderr reached `tasks.result`; legacy verification output, task execution errors, and the kernel report transcript were the same category. All now redact through the existing `redact_text`. Verified with the fake secret `sk-FAKE-TEST-SECRET-NOT-REAL` only.
- **Failure-path holes.** A check-creation failure left the verification run RUNNING permanently (now terminal FAILED via `_abort_setup`); GUI workflow-scoped recovery passed `workflow_id` to only one of three passes.
- **Open item for a product decision:** `retry_merge` still merges without a readiness check. It is a manual retry of an already-reviewed worktree, so it was deliberately left unchanged rather than gated without evidence.
- Historical audit reports under `.agents/memory/opencode/bugfinding/` were treated as untrusted leads. All four AgentOps findings above were confirmed against current source; the UGA items (entropy sign, GAE truncation, GRU hidden-state leakage, curiosity/shared-gradient) were out of scope and untouched.
- Backup of the pre-existing dirty tree before work: `C:\Users\admin\AppData\Local\Temp\agentops-backup-reviewgates-20261001-185102`.

## Three-product repo + CI + suite baselines 2026-10-01 (commit 2cb2413, omp session)

- **Three products, not two.** `agentops/`, `universal-game-agent/`, `small-projects/mini-llm/`. All instruction files corrected (root + `.agents/` + `.github/copilot-instructions.md` + both product files); new `small-projects/mini-llm/AGENTS.md`. Each product has its own `python -m unittest discover -s tests` from its own directory; no root command exists.
- **CI (new, `.github/workflows/` + `.github/CI.md`).** Per-product jobs on `ubuntu-latest` py3.11 CPU, path-filtered. No lint/format/coverage gates (none configured), no GPU, no live external-game runs, no Windows exe packaging. Torch-gated UGA tests skip without torch, so the install step is load-bearing.
- **Suite baselines at 2cb2413:** UGA 288 OK; agentops 407 OK (4 env skips); mini-llm 96 run with 3 pre-existing errors — all `TestGenerationSeed`, caused by the working-tree deletion of `data/tokenizer.json` (pre-existing user change, staged ` D` state at session start; left untouched, not committed).
- **UGA P5/P6/P7 in 2cb2413:** `checkpoint_every_updates` validated (0 = disabled periodic, `final.pt` always written), atomic temp-file checkpoint replace; `launch_phase2_process` owns proc on attach/liveness failure, `load_eval_model` closes temp env, idempotent `stop()`, per-episode eval env closed on exception; seed/hits-misses measurement contracts documented (external `reset(seed=)` is a no-op — game seeded once at launch; hits/misses only meaningful for sign-based providers). No PPO-math changes; old entropy/GAE/GRU/curiosity audit claims left as-is (suites green).
- **mini-llm baseline in 2cb2413:** `docs/EXPERIMENT-tinystories.md` records the real run — 3.1M-token prep from `data/raw/tinystories-small.txt` (NOT the 1.9GB file), vocab 8192, step-10000 checkpoint (train 1.785/val 1.833), coherent story samples; full prepare→train→resume→generate loop smoke-verified in `$TEMP/mlsmoke`. Known wart (corrected 2026-10-01, see the entry below): the checkpoint stores default *paths*, not just a default `tokenizer_path`. `data/` corpora/bins + `checkpoints/` stay git-ignored (now via `mini-llm/.gitignore`, not the root blanket rule).
- **A5/A6/A7 frozen.** See `.agents/plans/architecture-freeze-a5-a6-a7.md` + decisions.md 2026-10-01 entry. A6 has no `ReviewRun`/`MergeRun` classes — design task first, not a split.

## mini-llm experiment-doc corrections 2026-10-01

Documentation-only; no source, test, or artifact changed. Suite re-run: 96 tests, 1 skip, 3 pre-existing `TestGenerationSeed` errors (unchanged from the 2cb2413 baseline, see lessons.md).

- **The tokenizer claim in `docs/EXPERIMENT-tinystories.md` was wrong.** It said generation *and* `--resume` must pass `--tokenizer`. Verified against both parsers: `src/generate.py:70` has `--tokenizer` and falls back to the checkpoint's `cfg.tokenizer_path`; `src/train.py` has **no** `--tokenizer` flag and the training loop never loads a tokenizer (it reads `.bin` files via `build_dataloader`). Resume needs no tokenizer.
- **The real resume defect was undiscovered.** `checkpoints/tinystories/final.pt` stores `train_bin: data/processed/train.bin`, `val_bin: data/processed/val.bin`, `checkpoint_dir: checkpoints` — all defaults, not the `data/tinystories/...` paths the doc's training command produces on current code. Locally `data/processed/` is the vocab-308 shipped-sample prep, so the documented bare `--resume` died in `Config.validate_against_data()` with `ValueError: vocab_size=8192 but data/processed/train.bin was encoded with a vocabulary of 308`. Reproduced read-only, without invoking `train()`.
- **"3.1M unique" corrected to a token count.** `data/tinystories/meta.json` records `train_tokens: 3103492`; nothing in the codebase measures corpus-level token uniqueness or vocabulary coverage.
- **Verified, not changed:** corpus byte sizes (12,988,293 / 1,924,281,556), git-ignore rules, all example CLI flags in `prepare_data.py` / `src.train` / `src/generate.py`, checkpoint bytes (167,987,879), `train_loss` 1.7849 / `val_loss` 1.8331, exact unique parameter count 13,982,976 at vocab 8192, `data/processed/meta.json` (736/184, vocab 308, ctx 32), and the seed-7 sample reproduced verbatim from the recorded command.
- **Unresolved and stated as such in the doc:** the artifact and the documented training command disagree about where the corpus lived during the recorded run. The checkpoint does not record enough to say which is right. Root cause is that no CLI flag sets `Config.tokenizer_path` and `prepare_data.py` does not record it in `meta.json`, so every checkpoint stores the default. Fixing that is a source change and was out of scope.

## Assigned-root bug-fix batch 2026-10-02 (test-first)

- Fixed eight ledger roots, each with a regression test written and verified red first: **ROOT-004** (`finalize.py` classifies `GitError` via `FailureClassifier`: dirty base → clean-worktree task, refused/changed base → re-validate task, conflict → unchanged conflict task, other → generic merge task), **ROOT-005** (the six `record_failure` wrappers and the three Path-B observer arms in `workflow.py` now record `failure.create` / `agent_run.create` / `agent_run.transition` degradations instead of `except: pass`; no control-flow or breadth change), **ROOT-006 + ROOT-035** (`gui_controller.py`: fresh cancel `Event` per operation with identity-checked `_end_operation`, plus `_root_cache_lock` single-flight around `_operation_root`), **ROOT-029 + ROOT-030** (`verification_kernel.py`: ≥1 passed check required for PASSED — A1; artifact-pointer write failure records a `verification_check.artifacts` degradation, `SAFE_TO_DEGRADE` row added in `persistence.py` — A8; stdout/stderr set to None, no fail-closed flip), **ROOT-032** (`git.py` `_run` forces UTF-8 decoding with replacement), **ROOT-027** (UGA: `run_external_experiment` split into a wrapper that writes `<stem>_results.json` with `status: failed` + `error` + `error_type` before re-raising; cleanup behavior unchanged).
- New regression tests: `tests/test_gui_operation_state.py` (5), `tests/test_failure_recording_degradation.py` (5); additions to `tests/test_git.py` (`Utf8DecodingTests`), `tests/test_finalize.py`, `tests/test_verification_kernel.py` (`VacuousPassAndArtifactDegradationTests`), UGA `tests/test_external_experiment.py` (`TestFailureResults`).
- Suites after the batch: `agentops/` 443 tests, 4 environment skips, OK; `universal-game-agent/` 296 tests, 1 skip, OK. Ledger §0.1/§0.2 updated (FIXED 18, ACTIVE 2 = ROOT-014/036, PARTIALLY FIXED 1 = ROOT-034) and `.agents/pending_tasks.md` rows for the eight roots removed.
- Deliberately untouched: ROOT-033/011/026, A5/A6/A7, retry_merge readiness gating, DISPROVEN roots, packaging, and the user's dirty files (the five modified agentops test files, `.agents/memory/opencode/environment.md`, deleted `small-projects/mini-llm/data/tokenizer.json`, untracked reel/small-projects files).

## External-game three-phase orchestration verified 2026-10-03 (test-first)

- Closed the `training/external_experiment.py` open row. `tests/test_external_experiment_orchestration.py` (30 tests, fakes only, no window, no OS) drives the whole driver: exact phase order, state and config propagation into both envs, the isolated `run-<pid>` checkpoint handoff from phase 2 to phase 3, per-phase teardown, failure propagation with a `status: "failed"` artifact and no leaked eval, Ctrl-C cleanup, and window-loss relaunch/resume.
- Three defects it exposed, all fixed, each **proven load-bearing** by reverting the fix and watching exactly the matching test go red:
  - `train_with_window_relaunch` cleaned up only on a session loss or on success. A non-session failure and a `KeyboardInterrupt` skipped both arms, so the run left the env open and the launched Pong process running — a real window left up, still taking `SendInput`. Fixed with an `except BaseException` arm plus `_close_quietly`.
  - `ExternalGameEnv._ensure_session()` raised a bare `RuntimeError` when the session was gone and `attach()` failed, and `_SESSION_ERRORS` did not list it. Capture-time window loss was relaunchable; a window lost between decisions ended the experiment. Added `SessionUnavailableError(RuntimeError)` and put it in `_SESSION_ERRORS`.
  - The success report indexed the training history directly. A resumed checkpoint that already met the budget (ROOT-014) trains nothing, so its history is legitimately empty and the report raised `KeyError: mean_reward` on a run that should have reported cleanly. Now aggregates via `summarize_history` and records `training_updates`.
- Also tightened `tests/test_external_game.py::test_dead_lifecycle_fails_fast` from `assertRaises(RuntimeError)` to the specific subclass, plus a guard test that the type has not collapsed back. Without this the env fix was unproven: the orchestration suite stubs the env, so reverting the raise site changed nothing.
- Suites after: `universal-game-agent/` **349 tests, 1 skip, OK** (was 318, +31). Baselines updated in `.agents/AGENTS.md` and `universal-game-agent/AGENTS.md`. `.agents/pending_tasks.md` lost the orchestration row; completed history lives here.
- Still unexercised and unchanged: the STEP-3 exp02 re-run, which needs a real window and sends real keystrokes. **Ask before starting it.**
- Deliberately untouched: AgentOps tests, `.agents/memory/opencode/environment.md`, deleted `small-projects/mini-llm/data/tokenizer.json`, untracked reel/small-projects files.