# Plan — Roadmap Phase 1 (Storage DTOs) + Phase 3 (Persisted Worktree Refs)

> Executed 2026-09-15 per user directive (read memory + GitHub `.agents/plans`, complete Phase 1, implement Phase 3). All subtasks done, suite 224 OK, committed `1047121`, pushed.

## Subtask 1 — Read memory + GitHub plans
Agent: pi
Depends on: none
Status: done

Read `.agents/memory/*` + `team.md`; fetched `origin/main` and read `.agents/plans/` (phase-3 kernel plan already DONE; `chatgpt_recommendations.md` audit + `chatgpt_addition_recommendations.md` UX roadmap). Backed up dirty tree to `/tmp/agentops-backup-phase1-phase3/`.

---

## Subtask 2 — Phase 1: Storage DTOs (no Row leakage)
Agent: pi
Depends on: Subtask 1
Status: done

- `tasks.py`: new `Workflow` DTO (id, description, status, created/updated_at).
- `state.py`: `_workflow_from_row`; `latest/list/get_workflow` return `Workflow` DTOs; `list_events` returns `list[dict]` (never `sqlite3.Row`).
- `gui_controller.py`: `serialize_workflow`/`serialize_task` emit plain dicts; `get/latest_workflow` include serialized failures + `worktree_ref`.
- `cli.py`: status uses attribute access; prints provenance line.
- `__init__.py`: export `Workflow`, `Worktree`, `WorktreeRef`.
- Updated `test_state.py`, `test_review_regressions.py` (DTO assertions).

---

## Subtask 3 — Phase 3: persisted worktree refs (schema v6)
Agent: pi
Depends on: Subtask 2
Status: done

- `git.py`: new `WorktreeRef` DTO (id, workflow_id, path, branch, base_branch, base_commit, created_at).
- `state.py`: `_migrate_worktree_refs` (FK-free table + indexes, version 6, idempotent); `record/get/find_by_path/list` CRUD; `_worktree_ref_from_row`.
- CLI + controller record provenance after workflow creation (never memory-only).
- Controller `retry_merge` prefers stored `base_branch`/`base_commit` (`used_stored_provenance` flag); new `get/list_worktree_refs` pass-throughs.
- Updated `test_events.py` migration assertion `[1..6]`.

---

## Subtask 4 — Tests + full suite
Agent: pi
Depends on: Subtask 3
Status: done (224 OK, 3 skips)

New `tests/test_storage_dtos_worktree_refs.py` (7 tests: DTO mapping, controller payloads, record/fetch, path lookup, missing-ref, migration idempotency, close/reopen persistence). Two self-found bugs fixed (CLI splice indent, migration-version assertion).

---

## Subtask 5 — Sync plans, memory, commit, push
Agent: pi
Depends on: Subtask 4
Status: done

Checked out `chatgpt_*.md` from `origin/main` into local `.agents/plans/`; updated `memory/{project,architecture,decisions,lessons,roadmap}.md`; committed `1047121`; merged `origin/main`; pushed.
