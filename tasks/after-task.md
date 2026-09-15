# After-Task Procedure (AgentOps)

> Read this file after completing any task in `tasks/`. It is the canonical
> post-task checklist. Follow it in order; do not skip steps. Rules here
> distill `../.agents/team.md`, the memory operating rules, and hard-won
> lessons in `../.agents/memory/lessons.md`.

## 0. Know what counts as "done"

A task is done only when **all** of these hold:

1. Every requirement in the task header is implemented (no partial credit).
2. The full test suite passes (`python -m unittest discover -s tests`, run
   from `agentops/` — never from the repo root; root-level invocation
   shadows the package and produces false import errors).
3. Valid reviewer findings are fixed (or rebutted with file/line evidence).
4. Memory and `.agents/` records are updated (Section 4).
5. Work is committed and pushed (Section 5), unless the user said otherwise.

## 1. Run the full test suite

```bat
cd agentops
python -m unittest discover -s tests
```

- Expect the current baseline (224 passing, 3 environment skips as of
  2026-09-15). Any new failure or skip is yours until proven otherwise.
- Every bug gets a **regression test first** (`tests/test_review_regressions.py`
  convention) — reproduce, then fix, then watch it go green.
- DB changes must be **additive only** (`CREATE TABLE IF NOT EXISTS`,
  `INSERT OR IGNORE` for migration versions). Never rewrite a table.
  New behavior goes behind config defaults preserving current semantics.
- Never use bare `INSERT INTO <migrated-table> VALUES` — always name the
  columns (`ALTER TABLE ADD COLUMN` appends; positional inserts break on
  legacy DBs).
- If you touched migrations, bump the migration-version assertions in
  `tests/test_events.py` (`[1..N]`).

## 2. Handle reviews (single writer, always)

- **Pi is the sole writer** in the dirty tree. Reviewers (opencode,
  fcc-claude, copilot only — never Codex/Claude) work via read-only
  `/tmp/agentops-review-*` snapshots through `agentops run`, never by
  touching the repo. Logs live under the snapshot.
- Before Intercom-based review: resolve the **exact live name** from
  `intercom_list` (never message remembered names; `intercom_team` shows
  configured targets, `intercom_list` shows live sessions — they disagree).
- A failed delivery is a **new disconnect signal** — investigate with
  status/logs, don't assume normal delay, don't retry blindly.
- Fix valid findings with evidence-backed changes + regression tests;
  record false positives with file/line evidence. Total functions must
  distrust `repr`/`str` of caller-controlled values (warning paths crash too).

## 3. Rebuild + smoke-test the exe (only if packaging-affecting code changed)

- Check for lingering processes first — `Popen.terminate()` does **not**
  guarantee a windowed-exe exit, and a lingering process locks
  `dist/AgentOps.exe` (`PermissionError` on rebuild):
  `cmd //c "taskkill /F /IM AgentOps.exe"`, then verify with `tasklist`.
  (From git-bash, Windows slash-flags need `cmd //c` with double slash.)
- Rebuild: `python scripts/build_windows_exe.py` (from `agentops/`).
- **Every build gets archive-inspected** (a successful build ≠ a working
  bundle): `printf 'o PYZ.pyz\nl\nq\n' | pyi-archive_viewer dist/AgentOps.exe`
  — check the top-level listing for the exact `z`-type entry name first —
  then grep for `agentops` (expect all modules incl. new kernels).
- Smoke-test startup/shutdown **and verify process exit** with `tasklist`.
- The exe itself **stays out of git** (ignored `dist/`). Ship binaries as
  GitHub Release assets, never as commits.

## 4. Update memory and `.agents/` (every substantial task)

Read-before/after rule: memory is shared institutional knowledge, not
session scratch. Keep entries concise, no transcripts, no duplicates, no
secrets (never grep session logs or dump env — they can surface secret
values; never print env).

| File | What to record |
|---|---|
| `.agents/memory/project.md` | Current state, priorities, what shipped |
| `.agents/memory/architecture.md` | New modules, data flow, layering changes |
| `.agents/memory/decisions.md` | Append-only: date, decision, reason, alternatives, agents |
| `.agents/memory/lessons.md` | Append-only: symptom → root cause → solution → "Remember" |
| `.agents/memory/roadmap.md` | Phase status (`PROPOSED`/`IN PROGRESS`/`DONE` + date + verification) |
| `.agents/plans/<name>-plan.md` | Archive the task plan (all subtasks, agent, status) |
| `.agents/outputs/<name>.md` | What was built, verification, files, follow-ups |
| `.agents/pending_tasks.md` | Flip resolved items to ✅, add new follow-ups |
| `tasks/task.md` | Append `## Plan` + `## Output` sections (history preserved) |

Conflict priority (old memory never overrides current code or explicit
instructions): (1) current source, (2) current tests, (3) explicit user
instructions, (4) `decisions.md`, (5) `architecture.md`, (6) `project.md`,
(7) `lessons.md`, (8) Supermemory history, (9) agent assumptions.

## 5. Commit and push

- Back up the dirty tree **before** substantial work, not after:
  `git diff` + untracked tarball to `/tmp/agentops-backup-*/`.
- `agentops/` is a normal tracked directory (single-repo fold) — run tests
  from `agentops/`, commit from the repo root.
- Leave externally-rewritten files alone (e.g. `tasks/task-ignorethis.md`
  has been repeatedly replaced by an external process — do not "fix" it
  into your commits; stage only your own files).
- Push to `origin/main`. If the push is rejected, `git fetch` + merge
  (never force-push shared history).

## 6. Report to the user

- What shipped (files, tests, verification numbers).
- Commit hash + push status.
- Reviewer findings and their disposition.
- Residual risks and follow-ups (with owners).
- Keep it short; details live in `.agents/`, not in chat.
