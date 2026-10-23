# Output — repository characteristics for routing (AgentOps)

Date: 2026-10-23 · Agent: OpenCode/GLM · Status: complete, **uncommitted** (no authorization)

## What was built

- New leaf `agentops/repo_inspection.py`: `RepoCharacteristics` (ordered `languages`,
  `language_counts`, root `manifests`, `test_runner`, `source_file_count`,
  `size` StrEnum `EMPTY/SMALL/MEDIUM/LARGE/UNKNOWN`, `truncated`), total
  `inspect_repository()`, and `RepositoryInspector` (per-resolved-path cache, default 8
  entries, thread-safe). Bounded BFS walk (`max_files=2000`, `max_depth=6`, skip dot and
  vendor dirs). Only an explicit root `MANIFEST_NAMES` allowlist is ever opened, capped at
  64 KiB; `.env` and every credential-style name stays unopened.
- `agentops/workflow.py`: `WorkflowEngine` gained an injectable
  `repo_inspector` constructor param; `_select_agent(task, excluded, working_directory=None)`
  uses `_repository_characteristics()` (total, degrades to `{}`) and passes real flags into
  `AgentRouter.route`; `_execute_task` passes the working directory. Legacy
  `_select_agent(task, excluded)` calls keep working (empty characteristics).
- Flag mapping (desired-capability hints only, never gates):
  `requires_tests` = detected test runner; `repository_exploration_required` = size large or
  truncated (capped walks classify LARGE), or a mixed-language tree;
  `security_sensitive`/`documentation_required` stay False — no safe deterministic
  name-based signal; documented in code and memory.
- Decision kept: `repository_characteristics` was consumed for real scoring in `routing.py`
  (desired capabilities, +5 each; reasons/rejections echo them), so the API was kept and
  fed rather than removed.

## Tests (all new, written with the change)

- `tests/test_repo_inspection.py` (30): empty dir, missing path, inaccessible root and
  subdir (walk continues), mixed-language ordering, tie-break, unknown suffixes, hidden and
  vendor dirs skipped, depth and file-cap bounds, size boundaries 100/101/1001 and the
  truncation→LARGE rule, runner detection table with precedence, unparseable manifests,
  builtins.open spy proving only allowlisted manifests open (`test_only_allowlisted_manifests_are_ever_opened`,
  `test_env_files_stay_unread_and_unreported`), repeat-inspection equality, cache identity
  and bounded eviction, unresolvable path.
- `tests/test_routing.py`: `RepositoryCharacteristicRoutingTests` (6) — a `requires_tests`
  flag flips a capability tie (plain vs tester at role architecture) and is reflected in
  reasons; explicit user preference still beats signals; explicit `required_capabilities`
  stay hard gates; the `enabled=False` static path ignores characteristics; the flags
  bridge into `required_capabilities`. `WorkflowRepositoryCharacteristicTests` (3): the
  engine passes derived flags for a conftest.py directory, keeps `{}` without a working
  directory, and degrades to `{}` when the inspector raises.

## Verification

- Command (per product local AGENTS.md): `cd agentops && python -m unittest discover -s tests`
- Evidence observed: `Ran 876 tests in 192.529s — OK (skipped=4)`; separate clean run
  confirmed `EXITCODE:0`. Baseline 837 + 39 new = 876; the 4 skips are the pre-existing
  environment skips. No new failures or skips.
- The `ERROR: stop after wiring` stderr line is the deliberate injected RuntimeError
  printed while `tests/test_systemd_wiring.py` exercises an error path; that module passes
  and the suite exits 0.
- `.agents/evidence/verification.json` was NOT regenerated: it requires a clean-tree real
  run and the change is uncommitted; re-measure (and rerun `tools/evidence/check.py`) at
  the next commit that lands this.

## Files

- New: `agentops/agentops/repo_inspection.py`, `agentops/tests/test_repo_inspection.py`
- Modified: `agentops/agentops/workflow.py`, `agentops/tests/test_routing.py`,
  `agentops/README.md` (one routing-behavior sentence)
- Records: `.agents/memory/decisions.md`, `.agents/memory/architecture.md`,
  `.agents/pending_tasks.md`, `.agents/outputs/repo-characteristics-routing.md`,
  `tasks/task.md` (reset to pointer; record archived)

## Skips, risks, not verified

- No lint/type-check/coverage exists for this product; none invented (per AGENTS.md).
- No exe rebuild: no packaging-affecting file changed (no `AgentOps.spec`, `agentops_gui.py`,
  or build script change); dist state unchanged (already stale per pending queue).
- Worktrees: characteristics are taken from the task's working directory, which in real runs
  is the isolated worktree checkout — representative of the repository, but a worktree is a
  checkout of one branch; untracked-only files differing between branches are a natural
  consequence, not handled specially.
- Full-suite runs were done twice (once before the exit-code confirmation) on the same
  dirty tree; counts above are from the observed runs, not copied from memory.
