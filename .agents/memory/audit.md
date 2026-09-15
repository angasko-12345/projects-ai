# AgentOps Architecture Audit (canonical record, 2026-09-13)

> Full audit performed on the v0.1.1 working tree (14 modules, 79 tests passing, 1 platform skip).
> Current-state companion: `architecture.md`. Forward plan: `roadmap.md`. Do not rewrite the app per this file; use it to guide phased work.

## Module dependency map (imports)

```
cli          → config, git, registry, logging, runner, state, verification, workflow, finalize, gui(lazy)
gui          → gui_controller, __version__          (no service imports — clean)
gui_controller→ config, git, logging, registry, runner, state, verification, workflow, finalize
workflow     → config, registry, runner, state, tasks, verification
verification → runner
runner       → config, logging, registry
registry     → config
state        → tasks
git          → stdlib only
logging      → tasks (utc_now)
finalize     → git, state, tasks
config/tasks → stdlib only
```

Layering verdict: sound. `gui` touches no service modules; `config`/`tasks` are leaves. Entry points compose injected services; engine dependencies are constructor-injected and fully fakeable.

## Architecture diagram (ASCII)

```
                ┌─────────────┐      ┌──────────────┐
                │  CLI (cli)  │      │ GUI (gui)    │  Tk thread only
                └──────┬──────┘      └──────┬───────┘
                       │              events│after()
                       │             ┌──────▼───────┐
                       │             │ gui_controller│ bg threads, cancel events
                       │             └──────┬───────┘
                       ▼                    ▼
 ┌────────┐  ┌──────────┐  ┌────────┐  ┌──────────────┐  ┌───────┐  ┌─────────┐
 │ config │─▶│ registry │─▶│ runner │─▶│ workflow eng │─▶│ state │  │ logging │
 └───┬────┘  └──────────┘  └───┬────┘  └──────┬───────┘  └───┬───┘  └────┬────┘
     │ select()          subprocess     claim/execute   SQLite WAL      │redacted
     │                   (taskkill/      repair loop    .agentops/      │files
     │                    killpg)                     state.sqlite      │
     │                         ┌──────────┐  ┌──────────────┐           │
     └────────────────────────▶│ verifier │  │ finalize+git │◀──────────┘
                               │ allowlist│  │ worktrees    │
                               └──────────┘  └──────────────┘
```

## Current workflow sequence (task → merge)

```
CLI:  main() → load_config → resolve state_root (repo root) → StateStore
      → manager.create(cwd, desc)            # worktree agentops/<slug>-<rand>
      → engine.run_high_level(desc, wt.path)
GUI:  controller thread → same, plus workflow-started/result/conflict events
Engine:
  create_standard_workflow → 4 tasks (architecture→implementation→verification→review)
  execute loop:
    ready_tasks()         # dependency check; FAILED dep → BLOCKED
    claim_task()          # atomic PENDING→RUNNING (+1 attempt)
    _execute_task:
      verification role → Verifier.run() → PASSED/FAILED
      else → registry.select(role, excluded) → runner.run_agent(prompt, cwd=worktree)
      FAILED + attempts left → PENDING (retry)
  run_high_level: verification failed × max_repair_cycles →
      debugging → re-verification → final-review tasks
Finalize (shared): commit_changes → merge → on GitError: debugging task + preserved worktree
  CLI/GUI: remove worktree on ready
```

## Database schema summary

```sql
workflows(id TEXT PK, description, status, created_at, updated_at)
tasks(id TEXT PK, workflow_id FK→workflows, description, role, assigned_agent,
      dependencies TEXT /* JSON array */, status, attempts, max_attempts,
      result TEXT, created_at, started_at, finished_at, updated_at)
events(id TEXT PK, workflow_id, task_id NULL, kind, detail, created_at)
```

Notes: PRAGMAs `foreign_keys=ON`, `busy_timeout=30000`, `journal_mode=WAL` (file DBs); `claim_task` conditional UPDATE is the cross-process atomicity guarantee; `list_events` explicitly `ORDER BY rowid`; ordering elsewhere is `ORDER BY created_at` / `rowid DESC`.

## Coupling notes (for future readers)

- Engine↔registry/runner/verifier/state: constructor-injected — exemplary.
- `_prompt()` hardcodes agent instructions — the seam for policies/memory/structured outputs.
- GUI↔controller: dict-event protocol with operation-ID staleness guard; Tk touched only on Tk thread; 1s SQLite polling is the event bus.
- `sqlite3.Row` leaks into the GUI via controller — map to dataclasses at the boundary (roadmap Phase 1).
- Config validated once per operation; `role_preferences` unknown agents = hard error; `args` without `{prompt}` = warning only.
- Redaction is heuristic (misses `Bearer`, multiline secrets); log filenames embed timestamps.

## Test architecture gaps (as of audit)

- Redaction variants, concurrent `write_run` collisions, `limit=0`/boolean pagination args, unknown-status filters, `update_task` on missing id (silent no-op), prune-failure surfacing, frozen-exe import coverage (archive-inspect + smoke only), large-DB performance tests.
- Convention (keep): every bug gets a regression test first — see `tests/test_review_regressions.py`.
