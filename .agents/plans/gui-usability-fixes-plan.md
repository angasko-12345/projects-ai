# Plan — GUI Usability Fixes (3 rounds, 2026-09-15)

> User-reported: exe flashed consoles, hogged desktop, slow UI; then clipped text boxes; then a literally unmapped text box. All fixed, suite 289 OK.

## Round 1 — No-flash + poll efficiency
Status: done (`57d591b`)

- Central `_no_window_kwargs()` in `git.py` (CREATE_NO_WINDOW on win32); applied to git spawns, `GitRunMetadataCollector`, runner exec (OR-ed with NEW_PROCESS_GROUP), legacy verifier, kernel `_default_spawn` wrapper (injectable factory preserved).
- Controller caches git-root lookups (failures never cached); `_update_tasks` signature-gated against the 1s poll.
- 7 regression tests (flag mocks per site, cache hit/miss, skip-on-identical).

## Round 2 — Scrollbars + stretch
Status: done (`3827c7a`)

- Vscroll on prompt/description; v+h scroll on all trees/lists with xscroll/yscroll wiring; `stretch=True` on every tree column.
- 3 layout regression tests.

## Round 3 — Vertical budget
Status: done (`3499c90`)

- Root cause (measured live, A/B vs parent via temp worktree): only the notebook row had weight; fixed siblings starved it to ~99px (prompt was a 4px sliver pre-change, 0px after Round 2). Fix: rows 4/5/6 weights 2/1/1; trees 6→5, output 8→6; geometry 1060x740. Verified mapped (prompt 940x98, description 894x129). Row-weight regression test.
- Lesson recorded: map a real window and measure cells before/after; headless grid_info cannot catch starvation.

## Review
Status: requested async from opencode (non-blocking)
