# AGENTS.md

AgentOps — local-first orchestrator for coding-agent CLIs (worktrees + SQLite + Tkinter GUI + Windows exe). No LLM called for classification/parsing; deterministic kernels only.

## Repo layout

- This git repo root is a container folder, not a Python project. The product is the single package in `agentops/`. `tasks/`, `.agents/`, `agent-intercom-fix/` are process materials, not code.
- `tasks/task.md`, `tasks/after-task.md`, `tasks/task-ignorethis.md` are rewritten by an external process. They will frequently appear modified in `git status` — that is expected. Never "fix" `task-ignorethis.md` or stage those files.
- Runtime dependencies: Python >= 3.11, stdlib only (Tkinter, sqlite3, asyncio, unittest). Optional: PyYAML (`.[yaml]`), PyInstaller (`.[windows]`). `pyproject.toml` has zero required deps.

## Commands (run from agentops/)

- Tests: `python -m unittest discover -s tests` — **must** run from the `agentops/` dir. Root-level invocation shadows the package and produces false import errors.
- Current baseline: 335 passing, 4 environment skips. A new failure/skip is yours until proven otherwise.
- CLI: `python -m agentops <cmd>` (agent, run, task, workflow, status, logs, runs, verify, failures, recover, events, artifacts, gui).

## Non-negotiable conventions

- **Single writer**: only the `pi` session writes the dirty tree. OpenCode's role here is architecture reviewer via read-only `/tmp/agentops-review-*` snapshots through `agentops run <agent>` — reviewers never edit the repo directly. Never task codex/claude as review collaborators (allowed: opencode, fcc-claude, copilot).
- **DB changes additive only**: `CREATE TABLE IF NOT EXISTS`, `INSERT OR IGNORE` for migration versions, explicit column names on migrated tables (positional `INSERT INTO ... VALUES` breaks legacy DBs — `ALTER TABLE ADD COLUMN` reorders nothing). Never rewrite a table.
- After touching migrations: bump the migration-version assertions in `tests/test_events.py`.
- Every bug fix gets a regression test first — convention lives in `tests/test_review_regressions.py`.
- Read `tasks/after-task.md` and `.agents/team.md` before substantial work; update `.agents/memory/*` after.

## Architecture map (quickest way in)

- Entry points `cli.py` and `gui.py`/`gui_controller.py` compose an injected `WorkflowEngine`. The GUI never imports service modules directly; all Tk updates go through `root.after()`, work runs on controller threads.
- Leaf no-I/O domain modules (pure, unit-tested, no SQLite): `execution_model.py`, `agent_result.py`, `agent_adapter.py`, `failure.py`, `verification_model.py`, `events.py`, `artifacts.py`.
- `state.py` owns all SQLite (WAL, busy-timeout, RLock): workflows/tasks/events plus additive `agent_runs`, `verification_runs`/`_checks`/`_reports`, `failures` (FK-free TEXT refs), `typed_events`, `artifacts`, `worktree_refs`. Schema versions are bumped additively (currently v6).
- Data flow: workflow → isolated `agentops/<slug>-<rand>` worktree → plan→implement→verify→review (repair loop) → `finalize.py` commit/merge (conflict → preserved worktree + debugging task).
- Verification: kernel (`verification_kernel.py`) over configured profiles; an agent reporting success never marks a task verified — only a passed report does.
- Process execution: `runtime.py` owns spawn/cancel/timeout/termination for `runner.py`, `verification_kernel.py`, and legacy `verification.py`. Runner/kernel keep their public APIs; custom process factories keep their spawn behavior and get direct-kill cleanup.

## Windows / packaging gotchas

- Never `print()` raw agent output — arbitrary Unicode crashes cp1252 consoles; use the `errors="replace"` helper pattern.
- All subprocess spawns under the windowed exe need `CREATE_NO_WINDOW` (`_no_window_kwargs()` in `git.py`) or consoles flash.
- Normalize paths to `as_posix()` when crossing a UI/persistence boundary (log names).
- Packaged exe lives at `agentops/dist/AgentOps.exe` (gitignored — ship as GitHub Release asset, never commit). Build from `agentops/` with `python scripts/build_windows_exe.py` after `[windows]` install.
- Every build must be archive-inspected: a successful PyInstaller build != a working bundle (entry point must be `agentops_gui.py`, never `agentops/gui.py` — package-relative imports break when frozen). Use `pyi-archive_viewer` and grep for `agentops.*` modules.
- Before rebuilding: kill lingering `AgentOps.exe` with `cmd //c "taskkill /F /IM AgentOps.exe"` or the rebuild fails with `PermissionError` (dist exe locked).