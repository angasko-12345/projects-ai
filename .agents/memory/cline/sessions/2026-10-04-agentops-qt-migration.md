# Session record — 2026-10-04 AgentOps Qt desktop migration

Continuation of an in-progress Tk→PySide6 migration. Scope was explicitly
"bring to a known-good state": no redesign, no new features, no backend
semantics change. Commit `5b80d9b`, pushed to `origin/main` as merge `7d5e7e1`.

## The starting state was worse than "unfinished"

The existing Qt package was already substantial (ten views, shell, palette,
tray) and **could not start at all**. Four independent defects stood between
import and first paint:

| Defect | Symptom | Location |
|---|---|---|
| `QBoxLayout.addWidget()` given a `QHBoxLayout` | `TypeError` at construction | `shell.py::_build_chrome` |
| Tray action wired to a nonexistent `_show_window` | `AttributeError` at construction | `shell.py::_build_tray` |
| `_set_status_idle()` before `self._poll` existed | `AttributeError` at construction | `shell.py::__init__` |
| `detail.py` ↔ `views/__init__.py` circular import | package unimportable | `gui/detail.py`, `gui/views/listdetail.py` |

They were **serial**: each fix exposed the next. Stopping at the first green
result would have shipped a still-broken app.

## How it was verified

The temporary smoke scripts (`tmp_full_smoke.py`, `tmp_qt_smoke.py`) were
finished and used as the harness. The known-good assertions were then promoted
into a permanent `tests/test_gui_qt.py` (11 tests) rather than being deleted, and
the temp files were removed.

- Smoke run: all checks green, **including a new audit that no view raised a
  hidden `Request failed`/`Load failed` toast** (0 found).
- Full suite: `cd agentops && python -m unittest discover -s tests` →
  **438 tests, 4 skipped, OK**. Baseline was 444; the drop is the replaced Tk
  widget tests. No new failures.
- Manual launch, twice: `python -m agentops gui` ran offscreen with no traceback
  and stayed alive; and a driven run through the real `agentops.gui.main()` with
  the real `AgentOpsController` rendered a 1024x680 window, navigated all ten
  views, and closed cleanly.

The harness was **strengthened**, not relaxed: the workflow-list selection now
drives the real user action (clicking row 0), and the toast audit above was
added because silent per-view failures were the thing most likely to be missed.

## Backend audit result

Kept only what the GUI demonstrably needed, each with a stated reason:

- `state.py` — `count_events` / `count_agent_runs` / `count_verification_runs` /
  `count_failures`, required by `gui_controller.list_recent_*` to window the
  newest N rows. Additive; no schema change.
- `workflow.py` — `STANDARD_TASK_ROLES`, required by `gui_controller.run_task`
  to pin a fixed agent across the four standard roles.

Reverted: `MagicMock(asyncio.sleep(...))` → `AsyncMock` churn in five test files.
Unrelated to the GUI. **Caveat worth keeping:** `test_agent_run.py` already
imported and used `AsyncMock` at HEAD in five places; reverting the churn
initially removed that import and broke the whole module. Restore it before
touching those mocks again.

## Mistakes made here, so the next session does not repeat them

1. **Asserting on Tk-era attribute names.** The harness assumed a single
   `_table` per view; Workflows uses `_list` for its master list, so its detail
   panes were never selected and three assertions failed. The fix was to drive
   the real selection, not to drop the assertions.
2. **`APPDATA` override hid PySide6.** Isolating settings by redirecting
   `APPDATA` also relocates the per-user `site-packages`, producing a bogus
   `No module named 'PySide6'`. See `../environment.md`.
3. **Chunked `insert_line` corrupted a file built from scratch** (three
   consecutive parse failures). Regenerated deterministically instead of
   patching further.
4. **Push was rejected** because `origin/main` had advanced to `1dc479d`
   mid-task. Resolved with a read-only fetch + `merge-tree` proof + merge; the
   reported SHA `5b80d9b` survived because a rebase was not used.
5. **A replace-style edit destroyed a file's body.** Using an existing heading
   as `old_text` for new content overwrote everything after it. Caught by
   diffing immediately; recovered with `git checkout -- <file>` and redone as a
   separate new file. Check `git diff --stat` after any large edit.

## Known open item

**`AgentOps.spec` has not been rebuilt.** It still lacks PySide6 in
`hiddenimports`, so the packaged `AgentOps.exe` is unverified since the
migration. Rebuild and archive-inspect (`agentops.*`, PySide6, SQLite,
`agents/agents.yaml`) before shipping an exe — a successful build is not proof
of a working bundle.

## Stale baseline left deliberately

`.agents/AGENTS.md` and `agentops/AGENTS.md` both still state **444 tests**; the
true figure is **438 tests, 4 skipped, OK**. They were not corrected because
another session was working in `agentops/` at the time. Whoever owns that
directory next should update both, or the next session will trust a number that
no longer reproduces.

## Also noted, not fixed

`.agents/memory/lessons.md` contains one invalid UTF-8 byte (`0x97`, around byte
72127, inside the uncommitted 2026-10-03 tiktok-slop-factory entry, standing
where an em dash should be). `HEAD` decodes cleanly, so the corruption arrived
with that uncommitted edit. Left untouched — it belongs to another session's
work — but the file no longer decodes as UTF-8, which can break tooling.

## Uncommitted state left in the tree

Deliberately **not** staged or committed: `.agents/memory/lessons.md`,
`.agents/memory/opencode/environment.md`, `.agents/memory/project.md` (prior
tiktok-slop-factory work predating this session), the deleted
`small-projects/mini-llm/data/tokenizer.json`, and four untracked
`small-projects/` entries. Confirm ownership before touching them.