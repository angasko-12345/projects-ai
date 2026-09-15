# Output — Roadmap Phase 1 (Storage DTOs) + Phase 3 (Persisted Worktree Refs)

> Delivered 2026-09-15, committed `1047121`, merged + pushed. Full suite 224 OK (3 skips).

## What was built

- **Phase 1 DONE:** `tasks.Workflow` DTO; `StateStore.latest/list/get_workflow` return DTOs; `list_events` returns plain dicts; controller serializes workflow/task payloads to dicts (tasks no longer leak dataclasses); `get/latest_workflow` include serialized failures + `worktree_ref`; CLI status uses attribute access + prints provenance.
- **Phase 3 DONE:** `git.WorktreeRef` DTO; additive schema v6 `worktree_refs` table (FK-free, indexed by workflow/path, idempotent migration); `record/get/find_by_path/list` CRUD; CLI + controller record provenance on creation; `retry_merge` validates against stored base (`used_stored_provenance` flag); controller `get/list_worktree_refs`; restart-safe (close/reopen tested).
- **GitHub plans synced:** `chatgpt_recommendations.md` + `chatgpt_addition_recommendations.md` restored locally from `origin/main` (phase-3 kernel plan was already DONE). ChatGPT audit items (AgentAdapter, ProcessRuntime, engine split) deferred per stabilization-first principle.

## Verification

- New `tests/test_storage_dtos_worktree_refs.py`: 7 tests OK.
- Full suite: 224 passing (was 217), 3 environment skips.
- Self-found fixes: CLI indent splice, migration-version assertion `[1..6]`.

## Follow-ups

- Release: fresh `dist/AgentOps.exe` rebuilt (14,807,079 bytes, 21 modules, smoke-tested) and published as GitHub Release `v0.1.3` — see release record in `memory/project.md`.
- `tasks/task-ignorethis.md` local modification left untouched (externally rewritten file).
