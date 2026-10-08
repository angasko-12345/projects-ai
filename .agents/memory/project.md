# Project Memory (canonical)

> Read before substantial work. Update when project state changes. Never store secrets. Populate only verifiable facts; otherwise `Not yet established.`

## Project purpose

- AgentOps: local-first orchestrator for installed coding-agent CLIs (opencode, codex, pi, claude/fcc-claude, copilot, antigravity). Plan → implement → verify → review workflows in isolated Git worktrees, with SQLite persistence, a Tkinter GUI, and a Windows .exe. Established 2026-09-13 from `D:/admin/code/projects/agentops` + `tasks/task.md`.

## Current project state

- Current verified state: three products in one container. `agentops/` v0.1.3, additive schema v7; `universal-game-agent/`; `small-projects/mini-llm/`. The canonical instruction layout is `.agents/AGENTS.md` plus `.agents/pi_AGENTS.md`, with root compatibility shims.
- **Test results are not recorded in this file.** The current measured result for each product is `.agents/evidence/verification.json`, regenerated from real runs by `python tools/evidence/generate.py` and verified by `python tools/evidence/check.py`. The entries below are dated historical observations, kept as the record of what was verified when — not as current state. This file previously carried a "current baseline" block per product; those blocks disagreed with each other and with reality, which is why the counts moved here.
- **Historical, 2026-10-02** (each product's own command, from its own directory): `agentops/` 443 tests, 4 environment skips, OK; `universal-game-agent/` 317 tests, 1 skip, OK; `small-projects/mini-llm/` 96 run, 1 skip, 3 `TestGenerationSeed` errors. The mini-llm figure was superseded the same day; the errors were a working-tree deletion of `data/tokenizer.json`, not a code defect.
- **Historical, 2026-10-03, UGA:** 318 tests, 1 skip, OK after commit `59f5a1b`. The +1 is `tests/test_cwd_isolation.py`; direct execution of every `tests/test_*.py` works from any working directory and matches discovery's per-module counts.
- **Historical, 2026-10-03, AgentOps:** 444 tests, 4 environment skips, OK — the +1 is `tests/test_runtime.py::test_real_parent_terminate_process_kills_descendant_tree` (ROOT-034 DBG-08 closed; real Windows parent→child→grandchild termination, mutation-proven).
- **Bug status (2026-10-02):** the live ledger is `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` §0 — 36 canonical roots: 20 FIXED, 0 ACTIVE, 1 PARTIALLY FIXED, 2 CONTRACT GAP / ACCEPTED DEBT, 1 HELD, 12 DISPROVEN. ROOT-015..025 and ROOT-028 are disproven and must not be revived.
- **A10 (CI/regression gating) is DONE**, delivered at `2cb2413` as per-product path-filtered workflows with scope in `.github/CI.md`. Whether CI gates PRs remains undecided (roadmap D5 follow-up).
- **A5/A6/A7 are FROZEN by deliberate decision.** Their stated freeze condition ("close the correctness queue") has been satisfied, but the freeze remains. Re-entry requires an explicit user decision; they are not ordinary backlog.
- Current open UGA bugs: only the UGA share of ROOT-034 (test-quality cluster), of which **DBG-06 (direct test execution) and DBG-07 (CWD checkpoint pollution) were closed 2026-10-03 in `59f5a1b`**; the remaining ROOT-034 items were untouched this pass and the ledger is reconciled separately. ROOT-014, ROOT-027, and ROOT-036 were fixed 2026-10-02 (commits `b46a3e0`, `2a7b25a`). ROOT-011 and ROOT-026 are CONTRACT GAP / ACCEPTED DEBT.
- Current open AgentOps bugs: only the AgentOps share of ROOT-034 (test-quality cluster), of which **DBG-08 was closed 2026-10-03** by a real Windows parent→child→grandchild termination test (`tests/test_runtime.py::test_real_parent_terminate_process_kills_descendant_tree`, mutation-proven); DBG-12/DBG-15 remain as residual test debt. ROOT-004, ROOT-005, ROOT-006, ROOT-029, ROOT-030, ROOT-032, and ROOT-035 were fixed 2026-10-02 (commit `b46a3e0`). ROOT-033 is HELD pending a Windows termination contract.
- **Historical, 2026-10-04: `small-projects/mini-llm/` 98 run, 1 skip, OK.** The 3 `TestGenerationSeed` errors are **CLOSED, not deferred.** Root cause was a working-tree deletion of the tracked artifact `data/tokenizer.json` (` D`, unstaged) — **no commit ever deleted it** (`git log --diff-filter=D` on that path is empty). Restored from git (blob `6ac1190`), which is the canonical artifact: re-encoding `data/raw/train.txt` regenerates the committed `data/processed/{train.bin,val.bin,meta.json}` byte for byte (vocab 308, 920 tokens, split 736/184 at `--context-length 32 --val-frac 0.2`). Two tests added: `TestShippedData.test_shipped_tokenizer_matches_the_committed_vocab` (the committed tokenizer previously had **zero** coverage — every shipped-data check either retrained into a temp dir or read the `.bin` files) and `TestTokenizer.test_missing_file_names_the_path`. `load_tokenizer` now raises a `FileNotFoundError` naming the path instead of leaking a bare Rust `os error 2`, matching the existing `config_for_data` convention. No model or training behavior changed.
- **mini-llm checkpoint-path wart (current):** `Config.tokenizer_path` has no CLI flag and `prepare_data.py` does not record it in `meta.json`, so every checkpoint stores the default `data/tokenizer.json`; `train_bin`/`val_bin`/`checkpoint_dir` are taken from the checkpoint on resume. A bare `--resume` of the TinyStories artifact fails in `Config.validate_against_data()` with `vocab_size=8192 but data/processed/train.bin was encoded with a vocabulary of 308`. Source-level wart, documented, not fixed.
- Historical: (2026-09-20) AgentOps v0.1.3, additive schema v7, A3/A4/B7 complete, full suite 357 passing with 4 environment skips. That number is superseded by the current baseline figures above.
- AgentOps v0.1.2 at `D:/admin/code/projects/agentops` (bumped 2026-09-14). AgentRun support implemented 2026-09-14 (95 tests passing). Verification Kernel implemented 2026-09-14: 122-test suite passing (1 Windows-platform skip), including 27 kernel tests; relayed opencode review findings fixed and rebuilt. `dist/AgentOps.exe` rebuilt (~14.7 MB, PyInstaller one-file windowed), archive-inspected (15 `agentops.*` modules), and startup/shutdown smoke-tested.
- Correctness queue closed for AgentOps on 2026-10-01 (Cline): full suite 425 passing, 4 environment skips. Four verified changes: READY now requires ONE verification task to supply PASSED + `verified=True` + evidence (signal-mixing across tasks fixed and reproduction-locked); `_exclude_agentops_state()` raises an actionable `GitError` instead of silently deadlocking merges when `.git/info/exclude` cannot be written; `retry_merge()` audited and deliberately left ungated (roadmap D9 resolved); headless GUI handled by a test-only `@requires_display` policy in `tests/tk_display.py`, with production GUI unchanged. Verified on Windows only - Linux CI conditions were simulated, not run.
- **PR #5 merged 2026-10-07 (merge commit `d0c5512`):** `feat(agentops): make standard workflow autonomous`. Standard workflow now autonomous: `_dependency_handoff()` delivers bounded dependency results (`HANDOFF_RESULT_CHARS=2000`, `HANDOFF_TOTAL_CHARS=8000`) to downstream prompts; review receives `implementation.id` alongside `verification.id`; `_repair_failed_review()` implements a bounded `debugging → verification → review` cycle under `max_repair_cycles`, with failure context from review rows; exhausted repair budget remains NOT READY; BLOCKED review triggers no repair. 15 tests added in `tests/test_workflow_autonomy.py`. All 15 autonomy tests OK; full suite OK (72/72, 1 skipped). See `.agents/memory/decisions.md` for the full decision record.
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

## tiktok-slop-factory audit and fixes 2026-10-03

- `tiktok-slop-factory/` is a fourth directory in this container (procedural TikTok video generator: Gemini ideas/script/TTS + FFmpeg-rendered visuals). It was audited post-Pexels-removal and repaired in commit `11a105c` (pushed to `origin/main`).
- Fixes: removed duplicate `h=0` option in the cells-style hue filter (static visuals), wrapped particle Y position with `mod()` (drift off-screen), isolated the top-scrim `gblur` to its own overlay layer (was blurring the whole frame), resolved FFmpeg/ffprobe via `app.config` (`FFMPEG_PATH`/`FFPROBE_PATH` from `.env`) in tests instead of PATH-only `shutil.which`, removed duplicate test definitions, deleted stray `scene*.txt` artifacts from the repo root.
- FFmpeg is installed via WinGet (not on PATH); `tiktok-slop-factory/.env` holds `FFMPEG_PATH`/`FFPROBE_PATH` (gitignored). `GEMINI_API_KEY` is set in user environment variables, not `.env`.
- Test baseline 2026-10-03: `cd tiktok-slop-factory` then `python -m pytest tests` — **97 passed** (previously 83 passed + 14 FFmpeg-skipped; the skip condition was PATH-based and is now config-resolved).
- End-to-end `python make_videos.py --count 3` was blocked by an external Gemini free-tier quota (20 requests/day on `gemini-3.8-flash`, HTTP 429). This is not a code defect; rerun after the quota resets (~2026-10-04). No videos have been validated yet.
- Constraints honored: no Pexels/database/browser-automation reintroduction, no new dependencies.

## tiktok-slop-factory speech-aware caption timing 2026-10-04 (commit 58d50c2)

- Caption timing moved from even duration slicing to a weighted model. `captions.speech_aware_timestamps` weights each word `1.0 + 0.35 × (syllables − 1) + pause(trailing punctuation)`, where syllables are a vowel-group count and pause is `,` 0.50, `;`/`:` 0.70, `.`/`?`/`!`/ellipsis 0.90. The scale factor is found by bisection so `Σ clamp(k·wᵢ, MIN, MAX)` equals the probed narration duration.
- Bounds: `MIN_CUE_SEC = 0.40`, `MAX_CUE_SEC = 7.00`, applied **per on-screen caption** via a new `max_words=3` parameter that matches the pipeline's grouping. Below the floor raises `CaptionTimingError` instead of emitting unreadable cues; above `count × MAX_CUE_SEC` every cue caps and the track ends early (a deliberate behavior change — see the decisions entry).
- `simple_timestamps` is retained as an alias for the new function, so `test_renderer.py` and `test_visuals.py` are unchanged. Timing and grouping share `_group_words`, so neither merges across a sentence boundary.
- Documented as a drift-reduction heuristic, **not** speech alignment: no Whisper, no audio analysis, no hard-coded English words-per-second. The probed duration stays authoritative.
- **Test baseline 2026-10-04: `python -m pytest tests` → 109 passed, 2 failed** (was 97 passed). The two failures — `test_config.py::test_loads_dotenv_from_project_root` and `test_gemini_parsing.py::test_generate_ideas_rejects_duplicate_padding` — are environmental (`ConfigError: GEMINI_API_KEY not set`) and were reproduced on the untouched tree before any edit. 14 caption tests added, none needing network.
- **This product is pytest, not unittest.** `python -m unittest discover -s tests` collects **0 tests**, prints `NO TESTS RAN`, and exits **5**. Always check the collected count and exit code together.
- Scope held: no Gemini, TTS, renderer, visual, output-verification, batch, CLI, or manifest/resume changes. `.agents/pending_tasks.md` deliberately untouched.

## tiktok-slop-factory narration coherence gate 2026-10-04 (commit f31221f)

- `pipeline._assert_narration_coherent()` runs in `_produce_one` immediately after the duration probe and **before `visuals.plan_scenes`**, so an impossible text/audio pairing fails in milliseconds instead of reaching a full 1080x1920 render. Two independent rejections: **silence** (FFmpeg `volumedetect` mean ≤ `SILENCE_MAX_DB = -80.0` dBFS) and **duration mismatch** (probed length outside the estimated band).
- The expected range comes from `captions.estimate_speech_range(text)` → `(low, high)`: `SECONDS_PER_WEIGHT = 0.40` summed over the *same* caption `word_weight` values, scaled by `DURATION_TOLERANCE = (0.60, 2.00)`, floored at `MIN_PREDICTED_SEC = 1.0`. One speech model shared with the caption timing, so the two cannot drift apart. Documented as a heuristic band, not speech alignment; no words-per-second constant is claimed exact.
- **The -80 dBFS threshold is measured, not guessed:** digital silence reads exactly -91.0 dB, a -60 dB tone -78.3 dB, a -50 dB tone -71.1 dB, an ordinary tone -21.1 dB. -80 sits in the empty gap, so quiet narration always passes and only genuinely empty audio fails. Do not retune without repeating the measurement.
- The gate never modifies audio — nothing is stretched, truncated, or re-timed; the narration is rejected and regenerated. `MIN_CUE_SEC`/`MAX_CUE_SEC` unchanged.
- **Test baseline 2026-10-04 (current): `python -m pytest tests` → 125 passed, 2 failed** (628s). The same two environmental `GEMINI_API_KEY` failures as the caption pass, reproduced pre-edit and out of scope. 16 new tests in `tests/test_narration_coherence.py`; all 32 caption tests still green.
- **Two facts a future session needs.** (1) `test_end_to_end_produces_vertical_video` no longer hard-codes a 6s stub tone — its length is derived from `estimate_speech_range`, because a 6s tone for that 32-word script is ~5.3 words/sec and the gate correctly rejects it; do not restore the hard-coded 6s. (2) Suite runtime is now **~630s, not ~300s**, because that test renders ~22s videos instead of 6s — budget for it and run it in the background, not under a short foreground timeout.

## AgentOps Qt desktop migration 2026-10-04 (commit 5b80d9b, pushed as 7d5e7e1)

- The Tkinter desktop client (`agentops/agentops/gui.py`, 1007 lines) was replaced by a PySide6 package `agentops/agentops/gui/` (15 modules + 13 view modules). Commit `5b80d9b`, 40 files, +6286/-1398. Pushed to `origin/main` via merge commit `7d5e7e1`.
- **Scope was explicitly a bring-to-known-good pass, not a redesign.** No backend semantics, routing, or workflow behaviour changed.
- **Three real startup crashes found and fixed** in `shell.py`; the app could not start at all before this: (1) `root.addWidget(body)` was passed a `QHBoxLayout`; (2) `_build_tray` wired a tray action to a nonexistent `_show_window`; (3) `_set_status_idle()` ran before `self._poll` existed. A fourth defect, a circular import (`gui/detail.py` -> `gui/views/__init__.py` -> every view -> `gui/detail.py`), was fixed by extracting `ViewContext`/`AsyncMixin` into `gui/context.py`.
- **Backend changes kept, with reasons:** `state.py` gained `count_events`/`count_agent_runs`/`count_verification_runs`/`count_failures` because `gui_controller.list_recent_*` window the newest N rows and need a total; `workflow.py` gained `STANDARD_TASK_ROLES` because `gui_controller.run_task` pins a fixed agent across the four standard roles. Both are additive; no schema or workflow change.
- **Backend changes reverted:** `MagicMock(asyncio.sleep(...))` -> `AsyncMock` churn in `test_workflow.py`, `test_routing.py`, `test_review_regressions.py`, `test_readiness_and_failure_paths.py`, and one hunk of `test_agent_run.py`. Unrelated to the GUI, so reverted to keep the diff task-scoped. Note `test_agent_run.py` already imported `AsyncMock` at HEAD and uses it in 5 places; do not "simplify" that import away.
- **Test suite migrated off Tk.** `tests/tk_display.py` deleted; `tests/qt_display.py` added (offscreen Qt, `requires_qt`, `wait`/`wait_until`/`destroy`, `make_context`). Tk widget tests in `test_gui.py`, `test_agent_run.py`, `test_verification_kernel.py` were replaced by Qt equivalents, and a new `tests/test_gui_qt.py` (11 tests) drives the real `MainWindow` offscreen. Controller-level tests stayed display-free.
- **Suite:** `cd agentops && python -m unittest discover -s tests` -> **438 tests, 4 skipped, OK** (~55-59s). Was 444 before the Tk tests were replaced. No new failures introduced.
- **Manual launch verified** twice: `python -m agentops gui` runs offscreen with no traceback and stays alive; and a driven run through the real `agentops.gui.main()` with the real `AgentOpsController` rendered a 1024x680 window, navigated all 10 views, and closed cleanly.
- **Leftover, not done:** `AgentOps.spec` still does not list PySide6 in `hiddenimports` and has not been rebuilt or archive-inspected. The packaged `AgentOps.exe` is unverified since the migration. Rebuild + archive inspection is still owed before shipping an exe.
- **Stale-baseline warning for whoever owns `agentops/` next:** both `.agents/AGENTS.md` and `agentops/AGENTS.md` still state the AgentOps baseline as **444 tests**. The true current figure is **438 tests, 4 skipped, OK**. These were deliberately not edited during this session to avoid touching `agentops/` while another session was working there.
- Unrelated dirty files left untouched and unstaged throughout: `.agents/memory/lessons.md`, `.agents/memory/opencode/environment.md`, `.agents/memory/project.md` (prior tiktok-slop-factory work), deleted `small-projects/mini-llm/data/tokenizer.json`, and four untracked `small-projects/` entries.

## AgentOps workflow control center 2026-10-04 (commit 7a5b472, not pushed)

- Follow-on to the desktop visual system above. Task/UI files only, plus one additive controller read.
- **New Qt-free projection layer:** `agentops/agentops/gui/control_center.py` (684 lines) owns every claim the control center makes - `build_stage_flow`, `build_live_panel`, `current_stage`/`next_stage`, `recent_activity`, `verification_totals`, `failure_summary`, `worktree_summary`, `merge_readiness`, `task_detail`, `run_detail`, `cancellation_state`. No SQLite, no Qt, no I/O, and `now` is an injected parameter so elapsed time is deterministic in tests. This is the layer that made 38 of the 56 new tests runnable without a display.
- **Stage flow:** the standard pipeline renders PLAN -> IMPLEMENT -> VERIFY -> REVIEW -> FINALIZE; a workflow with any non-standard role (`is_custom_dag`) renders its real dependency edges keyed by task id instead. Each stage carries state, agent, model, duration, task status, verification state, the relevant failure, and completion timestamp. Stages are sequential and clickable; clicking one highlights it and selects its task.
- **View:** `gui/views/workflows.py` rewritten as the control center - workflow list + live card (current stage, agent, pulsing dot, model, elapsed, next stage) + `StageFlow` + `Tally` + tabs for Tasks, Runs, Verification (counts -> click into individual checks), Failures, and Worktree (branch/base branch/base commit/path/changed files/merge readiness with blocking reasons + merge/cleanup/open-folder actions via existing controller methods). Cancel is visible only while running, locks and reads "Cancelling" synchronously on click, and recovery hides while a run is live.
- **One controller read added:** `AgentOpsController.workflow_readiness()` (27 lines in `gui_controller.py` total) delegating to the existing `assess_workflow_readiness()` in `execution_model.py`. No new orchestration semantics: the GUI reads the engine's READY verdict instead of re-deriving it, and shows `not ready to merge` with the engine's own reason strings.
- **Design system:** `gui/widgets.py` gained `StageNode`, `StageFlow`, `Tally`, `LiveCard`; `gui/tokens.py` gained `QTabWidget`/`QTabBar` rules, an `active` state on `QFrame[card="true"]`, and a card hover border.
- **Tests:** new `tests/test_control_center.py` (56) - `ControlCenterProjectionTests` display-free (stage flow, DAG dependencies, stage transitions, live panel, verification totals, failure projection, worktree, readiness, cancellation, task/run detail, malformed-input tolerance) and `ControlCenterViewTests` under `@requires_qt` (active workflow display, stage transitions, task/run/verification/failure selection, cancellation state, conflicting-action disabling, final ready/blocked state, deep link, no-repository empty state). `tests/test_gui_qt.py` fixtures enriched (4 tasks across all standard roles, run roles/worktree/diff/structured_result, failure action label) and `query_events`/`workflow_readiness` added to the fake controller.
- **Four bugs found and fixed during this work** (all recorded in lessons.md): Finalize reported "running" from the workflow status while an earlier stage was still running; elapsed time fell back to `updated_at`, inventing a number; an unrecorded duration rendered as `-` in a fact line; merge/cleanup enablement read `self._cancel.isEnabled()` as a proxy for "is a run live".
- **Baseline after this work:** `cd agentops && python -m unittest discover -s tests` -> **501 tests, 4 environment skips, OK** (445 + 56 new). This supersedes the 445 figure above; `.agents/AGENTS.md` and `agentops/AGENTS.md` still state 444 and remain owed an update by whoever owns them.
- **Verification:** full suite green; focused GUI modules green; the view was rendered offscreen and inspected at 1600x980, which caught two defects no assertion could (an unstyled `QTabWidget` page stack painting a white slab, and tables leaving an unstyled white strip past their last column). Both fixed and re-verified by screenshot.
- **Commit:** `7a5b472`, local only. Not pushed - no push was requested. Unrelated dirty files (memory from other sessions, deleted mini-llm tokenizer, untracked small-projects/tiktok files) untouched and unstaged.

## AgentOps desktop visual system 2026-10-04 (commits 2159af6 + c753e3e, pushed)

- Follow-on to the Qt migration above. UI files only — no backend, controller, routing, verification, Git, or state-schema change.
- **Design system:** `gui/tokens.py` — page (17/600), section (13/600), group label roles; all buttons/single-line inputs 33px; unified radii; one accent (#4c8dff); nav checked accent bar + focus states with variant rules ordered before `:checked`. `gui/widgets.py` — `PageHeader` (title, subtitle, `set_subtitle`, `add_action`) used by all 10 views; Card/SectionHeader titles at section level.
- **Shell:** top bar restructured (sidebar toggle leftmost with Show/Hide text sync, repository + Change, status cluster, New Task); sidebar brand row + Orchestrate/Inspect/Configure groups mirroring `VIEW_SPECS` + command-palette hint; `_fade_stack` now removes its `QGraphicsOpacityEffect` in `animation.finished` (a retained effect left stale previous-view regions — see lessons.md).
- **Views:** `PageHeader` + subtitle on all 10; ListDetail first-load "Loading..." → rows / error "State unavailable" / no-repo cycle (`_loaded` flag); dashboard redesigned with 4 real stat cards, needs-attention + recent-activity lists hidden when empty (single Expanding occupant per card), first-load-only loading text so the 1 Hz poll never flickers, error subtitle keeping last-good numbers; agents detail placeholder instead of a blank card; combo arrow is `gui/assets/chevron-down.svg` via `tokens.ASSET_DIR` (the CSS border-triangle hack rendered a grey square).
- **Tests:** new `tests/test_gui_visual_states.py` (7): nav-group drift guard (every `VIEW_SPECS` id exactly once), page-header/title contract, dashboard empty↔populated visibility + severity colour/tooltip, list-detail error restore, top-bar sidebar toggle round trip, fade releases its graphics effect, combo-asset wiring.
- **Baseline after this work:** `cd agentops && python -m unittest discover -s tests` -> **445 tests, 4 environment skips, OK** (438 from the Qt migration + 7 new, per-module discovery counts). This supersedes the 444 figure in `.agents/AGENTS.md`/`agentops/AGENTS.md` and the stale-baseline warning above; those doc files still say 444 and remain owed an update by whoever owns them.
- **Verification:** live screenshots of all 10 views; window resized at 1024x680 / 1280x800 / 1600x900; four visual defects found by screenshots and fixed (empty-card title inflation, combo grey square, blank agents detail, stale navigation regions), each re-verified live after its fix; `tests.test_gui_qt` 11 OK after every shell/dashboard edit.
- **Commits:** `2159af6` (16 UI files, +456/−80), `c753e3e` (`tasks/task.md` Plan+Output append). Both pushed to `origin/main`. Unrelated dirty files (`.agents/memory/**` from other sessions, deleted mini-llm tokenizer, untracked small-projects/tiktok files) untouched and unstaged.

## AgentOps desktop application interactions 2026-10-04 (this session)

- Follow-on to the control center (7a5b472). UI/shell work plus two additive reads; no workflow-semantics, persistence-architecture, routing, verification, or Git change.
- **Command palette (Ctrl+K)** now exposes the desktop command set verbatim: `Open <view>` for the nine non-settings views (Ctrl+1..9), plus `New Task...` (Ctrl+N), `Recover Interrupted Work`, `Refresh` (F5), `Settings` (Ctrl+0), `Toggle sidebar` (Ctrl+B), `Change repository...`, cancel-active while running, and recent repositories. Command actions are wired to live shell methods (tests assert the bindings).
- **Recovery made visible from the shell:** new read-only `StateStore.count_interrupted_work()` (mirror of the `recover_all()` predicates: agent runs pending/starting/running, verification runs pending/running, tasks running) and `AgentOpsController.interrupted_work()` (counts + total). The shell probes on first show, repository switch, and idle refresh (F5/Ctrl+R/thread-finished), shows a `QFrame#RecoveryBanner` ("Interrupted work detected" / "2 runs require recovery" / Review / Dismiss), notifies once per distinct interruption (toast + tray when hidden), and keeps a dismissal sticky until counts or repository change. Review/palette action re-probes, confirms via QMessageBox, runs the existing `controller.recover_interrupted()`, toasts the summary, and re-probes.
- **Notifications:** unready workflow results now fetch `workflow_readiness()` and toast `Verification failed` (verification_ok False, error) or `Workflow blocked` (warning) with the engine's reason strings instead of a generic "not ready"; tray messages added for merge/complete/conflict/error/agent-failure/recovery-when-hidden.
- **Shortcuts:** F5 added (registry `window._shortcuts`); Esc closes the banner only while it is armed (disabled otherwise so it never swallows Esc). Ctrl+K/N/R/B and Ctrl+1..0 unchanged.
- **Tray:** Hide AgentOps action added (Show/New Task/Quit kept); tooltip mirrors live status ("AgentOps - Idle"/"AgentOps - Task running..."/elapsed while polling). Close-while-running now calls `controller.cancel()` before shutdown so the runner terminates children (no orphan subprocesses).
- **Window behavior** already covered by the shell (geometry/maximize persistence, 1024x680 minimum, bridge shutdown); a round-trip test was added for the geometry persistence.
- **New Task / recent repositories** were already first-class (repo combo from recents, routing strategy, verification profile, follow-into-workflow); unchanged.
- **Tests:** +11 (`test_state.py` parity guard `count == recover_all`, `test_gui.py` `InterruptedWorkFacadeTests` round trip, `test_gui_qt.py` palette discovery/actions, shortcut contract, banner show/notify-once/recover, sticky dismissal, verification-failed and blocked toasts, tray status/hide, geometry round trip; FakeController gained `interrupted_work`/`recover_interrupted`/outcome knobs). **Suite: 512 tests, 4 environment skips, OK (2026-10-04).**
- **Verification:** full suite green; real-entry smoke `QT_QPA_PLATFORM=offscreen python -m agentops gui` ran 8s with no traceback; a driven offscreen run with the real controller showed 16 palette commands including Recover, banner hidden at zero counts, F5 registered, tray tooltip "AgentOps - Idle", and a real `interrupted_work` probe.
- **Docs:** `agentops/README.md` desktop paragraph, `agentops/AGENTS.md` and `.agents/AGENTS.md` baselines updated to 512 (2026-10-04), ending the stale-444 note previous sessions left behind.

## mini-llm checkpoint provenance and doc reconciliation 2026-10-04 (`0aad33b`, `8e7ef83`)

> The heading date was previously recorded as 2026-10-10, which is after this
> repository's own history for the work. Corrected against `git log`; the content
> was always accurate.

- **Fix:** the known "checkpoints store default paths" wart is closed (commit `0aad33b`, pushed). Checkpoints are content-addressed — see architecture.md and decisions.md. `prepare_data.py` records artifact paths + sha256 in `meta.json`; `src/train.py` gains `--tokenizer`; every training-loop checkpoint carries `data_provenance`; `--resume` refuses a digest mismatch; `src/generate.py` checks `--tokenizer` the same way. A bare `--resume` on a current checkpoint needs no data flags — the case that previously died with `vocab_size=8192 but ... vocabulary of 308`.
- **Baseline re-verified 2026-10-04:** `small-projects/mini-llm/` **115 run, 1 skip, OK**. Supersedes the 98-run figure recorded earlier the same day; the +17 is `TestDataProvenance`. A historical observation — the current measured result is `.agents/evidence/verification.json`.
- **Data-artifact status (verified, not assumed):** `data/tokenizer.json` **is present** (18,261 bytes, tracked, restored from git 2026-10-04). `docs/EXPERIMENT-tinystories.md` claimed it was deleted; that claim was wrong and is now marked superseded. `data/raw/tinystories-small.txt` (13 MB, prepared) and `data/raw/TinyStories-train.txt` (1.9 GB, staged, unused) are both still on disk and git-ignored. `data/tinystories/meta.json` + `checkpoints/tinystories/final.pt` (168 MB, git-ignored) are present.
- **Documentation reconciled** against source, each claim verified before writing: `AGENTS.md` (115 baseline, streaming prepare, `--tokenizer`, current data paths and shipped-prep numbers), `README.md` (test file list — five, not two — streaming preparation, non-default-path training command with `--tokenizer`, 115-test coverage list), `docs/EXPERIMENT-tinystories.md` (tokenizer-present correction, resume section now says this checkpoint is refused and why, results labelled **historical** with their 2026-09-30 run date). Parameter count re-derived at runtime: 13,982,976 unique at vocab 8192, unchanged.
- **Deliberately not redone:** `checkpoints/tinystories/final.pt` predates provenance and is now refused on `--resume`. Re-running the 10,000-step experiment is what would make it resumable; it remains usable for generation with an explicit `--tokenizer`.
- **Docs committed as `8e7ef83`** (pushed): `AGENTS.md`, `README.md`, `docs/EXPERIMENT-tinystories.md`, the four memory files, and the mini-llm row of the canonical baseline table in `.agents/AGENTS.md`. Documentation-only; the suite was re-run at 115/1/OK after.
- **The four `.agents/memory/*.md` files are shared dirty state and remain that way.** Another session has uncommitted edits in all of them (`hermes/` memory-folder references in architecture.md, tiktok-slop-factory entries in decisions.md / lessons.md / project.md). They were deliberately left unstaged; only this session's sections are committed. **Whoever picks them up next must stage explicit paths, never `-a`/`-A`, or they will sweep in work that is not theirs.** See the lessons.md entry on `git commit -- <paths>` ignoring the index.

## Bug-fix pass 2026-10-04 (this session)

Six verified defects across three products; no architecture, schema, or public-behavior change.
**Two of them were red CI jobs on `main`, both now green on `kilo/bugfix-four-runtime-defects`
(PR #1):** `agentops` (7 errors, `test_gui_visual_states` without `@requires_qt`) had been
failing since `2159af6`; `mini-llm` (the f-string `SyntaxError`) since `0aad33b`.

- **agentops `gui/control_center.py`:** `next_stage()` returned the row after the first
  non-passed stage, so any failed stage upstream of the current one made the LiveCard
  report the running stage as both "Current" and "Next" (PLAN failed / IMPLEMENT pending,
  IMPLEMENT failed / VERIFY running, and every custom-DAG branch with one failed and one
  running node). It now derives the next stage from `current_stage()`'s row. +3 tests.
- **mini-llm `src/train.py`:** a backslash inside an f-string expression (`{'\n  '.join(...)}`)
  made the module a `SyntaxError` on Python <= 3.11 - the version `.github/workflows/mini-llm.yml`
  pins. The join is hoisted out. New `tests/test_source_compat.py` compiles every source
  file with no third-party dependency.
- **universal-game-agent `tests/test_ppo_objectives.py` + `test_external_experiment_orchestration.py`:**
  both referenced names from their own `try: import torch` guard at module scope
  (`class FixedPolicy(nn.Module)`, `getattr(_xp, "stop", None)`), so on a torch-less box the
  modules raised `NameError` and 43 tests were reported as errors instead of skips -
  contrary to `universal-game-agent/AGENTS.md`. `tests/test_scaffold.py` now asserts both
  modules import without torch.
- **universal-game-agent `main.py`:** an empty YAML section (`eval:` with nothing under it)
  parses as `None`, and `cfg.get("eval", {})` handed that `None` to `.get()` / `dict()` at five
  sites, ending `train` / `evaluate` / `compare` / `smoke-test` in a traceback with exit 1
  instead of the documented `error: ...` and exit 2. All five now read `cfg.get(x) or {}`,
  matching `_build_curiosity` in the same file. +2 CLI tests with stubbed lazy imports.
- **tiktok-slop-factory `tests/test_visuals.py`:** four filtergraph tests passed
  `Path(".")` as the text directory, and `_text_chain` writes `scene<N>.txt` /
  `scene<N>_n.txt` there, so running the documented test command from the project directory
  left ten untracked files in the repository root. They use `tmp_path` now.
- **Baselines measured in this environment** (Linux, Python 3.10, no PySide6 / numpy / torch /
  tokenizers / pytest / FFmpeg; agentops needed a `StrEnum` + `asyncio.TimeoutError` shim to
  import at all, so the counts are collection counts): agentops **515 collected** (512 before,
  +3) with the same 7 pre-existing `test_gui_visual_states` PySide6 errors and 42 environment
  skips before and after; universal-game-agent **165 collected** (121 before) with the 2
  `_FailedTest` errors gone - the 11 remaining are `ModuleNotFoundError: numpy`; mini-llm's new
  test passes and the 5 pre-existing errors are unchanged third-party imports. **The 349 / 115
  figures in `.agents/AGENTS.md` were not re-verified here and still owe a run on a box with
  the dependencies installed.**
- **agentops `tests/test_gui_visual_states.py`:** the widget class lacked the `@requires_qt`
  marker `agentops/AGENTS.md` mandates, and its `setUp` imports `agentops.gui.shell`
  (PySide6) before `qt_app()` can raise `SkipTest`, so on the CI runner every test in the
  class errored. The marker makes the suite `OK (skipped=49)` where PySide6 is absent.
- **Investigated and deliberately not changed:** `main.py::_need_torch` still raises
  `SystemExit(str)` (exit 1) where every other failure returns 2 - changing the documented
  status is a contract decision, not an obvious fix; `_build_eval_model` leaks its probe env on
  the checkpoint path (`main.py:228`); `configs/default.yaml`'s `logging:` block and
  `training/logger.py` are dead, which `universal-game-agent/AGENTS.md` already records;
  `src/generate.py` hashes `--tokenizer` before checking it exists, so a missing file surfaces as
  a bare `FileNotFoundError`; `compute_gae` bootstraps an interior truncation from the post-reset
  frame, which needs torch to confirm and is a larger algorithmic change.

## Universal-game-agent cadence/reward investigation 2026-10-05 (this session)

- **Scope:** premise checks → timing/action/observation analysis → synthetic cadence matrix → PPO diagnostics → justified changes → docs/report. No real SendInput run launched (none authorized); all experiments synthetic/local, no approval needed or requested.
- **Premise resolved:** `experiments/exp_external_pong_compare01_results.json` (`timestamp_utc` 2026-09-25T12:27Z) predates detector fix `b77bf4d` (2026-09-26 14:50+0800) — the ~607 px MISS banner downscaled to 61 px inside the hit band, so misses paid +1 and both policies sat at the 200-step cap (10.83/11.83 are event counts, skill-blind). Post-fix, the reward DOES discriminate: synthetic lookahead oracle +0.1 / survives all 200 steps vs random −1.0 / dies at 5.6 steps at current timing.
- **Measured:** 149.7 / 147.6 ms per decision (6.68 / 6.77 fps from exp01/02 artifacts; 60 ms hold + 80 ms delay = 140 ms, rest overhead). Paddle displacement **15-20 px per decision** (60 ms hold = 3-4 ticks × 5 px at 60 fps, synthetic-verified) — the old AGENTS.md "5 px per decision" claim conflated frame with decision, withdrawn; live-window confirmation still pending.
- **Matrix** (`training/cadence_experiment.py`, `experiments/exp_cadence_synthetic.{yaml,json}`, 4 cells × {6 probes + baseline eval + 16384-step PPO + final eval}, seed 0): decision period alone does not gate learnability (oracle clean at 147.6/33.3/16.7 ms with adequate hold); control authority does (147.6 ms + 16.667 ms hold → oracle dies at 7.9 steps, trained policy still improved hits 0.2→0.6 there); no cell reached positive rolling reward (−0.47..−0.52, pilot budget — no convergence claim).
- **PPO diagnostics:** exp01 entropy pinned at ln 3 (1.0986), exp02 1.082→1.020; constant `PRESS_LEFT` in greedy eval (381/381) = argmax over near-uniform, NOT collapse — one matrix cell genuinely collapsed (entropy 0.06, ≥98 % action 1) as reference. ROOT-015..018/028 remain DISPROVEN; nothing here revives them. No hyperparameters changed.
- **Changes:** `env.timing.reset_settle_poll_s` (default 0.05, unchanged), `upd_action_share` history key, `observation_timing` fingerprint + `metric_definitions` in external results, new cadence module/config/results artifact. Tests **366, 1 skip, OK (2026-10-05)**, +17 vs 349; baselines updated in `.agents/AGENTS.md` and `universal-game-agent/AGENTS.md`.
- **Docs:** UGA AGENTS.md gained § "Cadence / reward investigation" (hypotheses with verdicts, controlled variables, exact config); README experiment section + 2 command rows; checkpoints/README regeneration row; `.agents/pending_tasks.md` STEP-3 row corrected + two new rows (live displacement confirmation — ask first; 50k-step synthetic matrix — no approval needed). The 8 torch-less erroring test modules were reported, not "fixed" — installing torch is the resolution, done in this interpreter.
- **Open (ask-first):** STEP-3 exp02 live re-run and the live 15-20 px displacement measurement both send real keystrokes — ask before starting. Memory files updated this session at the user's explicit request ("update memory"), append-only, alongside other sessions' dirty edits. Nothing staged, committed, or pushed.

## AI Token Tracker multi-agent coverage (SHOT 3) 2026-10-06 (oh-my-pi session)

- **Scope:** `ai-token-tracker/` went from "OpenCode tracker" to a local multi-agent token tracker: collectors for the user's real local tools, agent/provider/model attribution, cross-source dedup, a Sources dashboard view, dark mode + dropdown fix. The shot's priority order (real data > correct accounting > dedup > attribution > basic GUI > polish) was honored; MVP intact, no orchestration/SaaS/architecture rewrite, stopped at the coverage goal.
- **Commits on shared `main`:** `eb153e2` core (`model.py` `tool` + `dedup_key` columns, `db.py` schema/migration/content dedup/`record_sync` COALESCE/`collector_status`/`breakdown('tool')`/legacy-import compat), `8825542` `collectors.py` + record_sync fix, `4912a1d` `gui.py` + `tests/test_collectors.py`, `70602bb` README. **7 files changed total:** `model.py`, `db.py`, `opencode.py`, `collectors.py` (new), `gui.py`, `tests/test_collectors.py` (new), `README.md`.
- **Collectors (7 implemented, all exact, full-history, idempotent):** OpenCode (preserved), Kilo, Hermes, Codex, Pi, Oh My Pi, GitHub Copilot — each reads official local data (SQLite/JSONL/rollout files) read-only. **Unavailable, shown gray with the reason:** OpenRouter (403 without key), Gemini (aggregate-only exports, double-count risk), OpenAI (no key). Estimates only ever enter via manual import; no collector estimates.
- **Live evidence:** real-DB sync 7/7 ok — 14,155 events / 2,449,365,183 tokens, all exact, 0 estimated, repeat sync unchanged; final GUI run 2.53B lifetime across 7 sources. GUI verified by screenshot: four range cards (today/7d/30d/lifetime each with exact·estimated), Sources tab (coverage `Tracked agents 7/7 · exact 7/7 · unavailable 3`, agent/provider/model rollups, colored status dots with last-sync + event counts + error text), Dark/Light toggle persisting via QSettings across a relaunch, dropdown popup readable in both themes (white-on-white fixed).
- **Deduplication:** `request_id` when present, else `ct|provider|model|tokens|epoch//5*5`; zero-token rows keyed `zero|<event_id>` stay distinct; content dedup applies only across *different* sources (first-seen attribution kept, `tool`/`agent` backfilled), same-source distinct ids never merge.
- **Tests:** `cd ai-token-tracker && python -m unittest discover -s tests` → **41 OK (2026-10-06)**, +19 vs the 22 baseline (parsing ×5, cross-source dedup ×5, aggregation ×4, sync-all ×3, legacy import ×2).
- **Shared-tree git race recurred:** a plain `git commit` swallowed another session's 5 staged agentops files (`9c309dc`); split via `git reset --soft HEAD~1` + `git restore --staged agentops/` + re-add their files, then pathspec commit → `70602bb` (README only), their staging state (`A`) restored. Rule recorded in `lessons.md`.

## Post-reboot recovery audit 2026-10-06 (cline session, read-only)

- **Reboot point:** EventLog 6008 unexpected shutdown ~1:07:23pm local; last boot 1:19:25pm. HEAD `9365920` (OpenCode evidence fix, 1:07:15pm) landed 8s before the crash. Last pre-reboot writes: oh-my-pi memory 1:05:53-1:06:05pm, `.github/CI.md` 1:06:18pm.
- **Agent 1 OpenCode (verification-evidence audit):** code fix fully COMMITTED in `9365920`; 6 staged memory/session files survive in the index (session doc `2026-10-06-verification-evidence-audit.md` is index-only, `AD` — worktree copy missing, restorable via `git show :<path>`).
- **Agent 2 Oh-My-Pi (ai-token-tracker SHOT 3):** all 4 commits `eb153e2`/`8825542`/`4912a1d`/`70602bb` on `main`, 7 exact collectors verified (14,155 events / 2.45B tokens, 41/41 tests). Only memory write-ups remain uncommitted. Session closed itself ("Todo 8/8 done", "Open: none").
- **Agent 3 Manicode/freebuff (AgentOps Windows desktop packaging):** task text recovered from `~/.config/manicode/message-history.json` ("no terminal required", "Do not merge anything"). Entire task UNCOMMITTED by design: 5 modified + 6 new source files + `build/`/`build-debug/`/`dist-debug/` evidence. Appears INTERRUPTED mid-verification; needs copy-out backup, never `git add -A` (100+ MB build output must stay untracked).
- **Agent 4 UNKNOWN:** no attributable task; shell history shows CLI poking (`hermes gateway`, `kilo stats`, `gh auth login`, `cline`); only other live sessions are the two post-reboot Cline audit sessions and their dangling checkpoint commits `fdc645c`/`b8e6139` (leave for GC).
- **Repo state:** `main` ahead 7 of `origin/main`, 4 stashes; no truncated files, no conflict markers, no corruption found. Second pass (per user corrections): OMP SHOT 4 ("find and cover the user's remaining installed AI agents", prompt recovered from `cli_files/oh-my-pi/home/agent/history.db` session `01a10ef0...`) was interrupted before any file write — `ai-token-tracker/` has zero diff vs HEAD. Hermes Discord session `20261004_215842_86a30a7b` drove Chrome/CDP toward ChatGPT ("Luna"); gateway `running`, `active_agents=1` at 05:17:50Z; conversation content is server-side, treat un-persisted turn as potentially lost. No AgentOps `state.sqlite` exists in-tree, so no workflows to recover here.
- **Security:** `D:\admin\backup\` is gone (file unrecoverable, no hash possible); quarantine holds 3 `.bdq` 1:18:01pm + 1 1:18:57pm corroborating remediation. No suspicious script executed, no security software touched. Verdict: NEEDS RECOVERY (uncommitted work intact, nothing lost that was committed).

## PR rebasing to resolve stale CI — 2026-10-06

- **Work done:** All three open AgentOps PRs (#5, #6, #7) were rebased onto current origin/main (9ce33da) and pushed back to their PR branches with `--force-with-lease`. The PRs were based on 66724b2, whose CI failures were already fixed in main by 42ed95c (2026-10-06, "test(agentops): fix 2 CLI+3 workflow failures on CI's clean checkout"); rebasing brought that fix into each PR without touching the PRs' own changes.
- **Rebased head SHAs:** #6 (Antigravity review role) → c37af9a, #7 (GUI pagination fix) → 2c87398, #5 (autonomous workflow) → 0ed030f.
- **Verification:** `python -m unittest discover -s tests` (from `agentops/`) ran against each rebased worktree: #6 = 627 tests OK (4 skipped), #7 = 630 tests OK (4 skipped), #5 = 639 tests OK (4 skipped). Each PR's GitHub Actions `agentops` tests workflow now passes. Diffs verified as still narrowly scoped to each PR's purpose.
- **Isolation:** Operations ran in detached HEAD worktrees (`.agentops/worktrees/pr-{5,6,7}-rebase`). No changes were made to main or to the user's local dirty tree; worktrees remain locally (not pushed) and can be cleaned up after review.
- **Verdicts:** #6 — merge-approve (minimal, low-risk registry fix). #7 — merge-approve (production boundary fix + 6 regression tests). #5 — needs human review of `_dependency_handoff` / `_repair_failed_review` plus a real-agent end-to-end run per Issue #4 (Step 5) before merge; then it closes the loop toward Issue #4.
- **Environment note:** the repository is shallow; `--unshallow` was required before creating the worktrees, otherwise rebases find no common ancestry (see `.agents/memory/lessons.md` §2026-10-06).

## QR Generator v1.0.0 delivered — 2026-10-08

- **Work done:** Self-contained `qr-generator/` committed as `00ad590` (50 files): 5 QR types (text/URL/email/phone/Wi-Fi), live preview, PNG save/share/copy/clear, Customize/Library/Premium sheets, dark/light/system themes, AsyncStorage storage, accessibility labels, README with exact EAS/local build commands, PRIVACY_POLICY.md, asset generator script, local signing script.
- **Verification:** `tsc --noEmit` OK, `eslint .` OK, `jest` 53/53 (5 suites); `eas.json` production profile `buildType: aab` confirmed. Local `bundleRelease` succeeded: `qr-generator/android/app/build/outputs/bundle/release/app-release.aab` (53,988,935 bytes; `base/lib` = arm64-v8a + armeabi-v7a + x86 + x86_64; 3 dex; 919 res; `jarsigner -verify` → "jar verified" against `android/app/release-qr.jks`). x86_64 `app-release.apk` (31,521,734 bytes) installed on AVD `qr36` (API 36) and exercised on-device: launch, live QR render, exports enabled, Customize locked, Premium sheet with honest billing note, system dark mode applied; user separately confirmed Clear/star work on the device.
- **Scope hygiene:** only `qr-generator/` staged; root `README.md` deletion and external `task.py`/`test_*.py`/`debug_test.py`/`test_output*.txt` left untouched.
