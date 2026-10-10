# 2026-10-10 — wip/stash-* merge campaign (repo-wide integration)

Merged the four kept `wip/stash-*` branches into the repo without disturbing the
parallel session's work, then consolidated everything onto `fix/play-readiness`.

## Starting state

- Four wip branches kept at reconciliation (Session 2 in
  `.git/backup-evidence/20261010-reconcile/RECONCILIATION.md`), each based on a
  different historical `main`; all bases were ancestors of current `main` (`1683750`).
- A parallel session was actively editing the shared tree on `fix/play-readiness`.
- `fix/play-readiness` was a strict superset of `main` (13 unique commits, `fpr..main = 0`).

## Phase 1 — integration branch in an isolated worktree

Created `integrate/wip-stash-20261010` from `main` in `D:/tmp/wip-stash-merge` and
merged in base order with `--no-ff`:

| branch | payload kept | conflicts resolved |
|---|---|---|
| `wip/stash-ohmypi-uga` | memory entries | keep-HEAD (pre-correction duplicates); old root `agents.yaml` path stays deleted |
| `wip/stash-pre-main-merge` | memory housekeeping | keep-HEAD; `AgentOps.spec` keeps packaged `DATA_FILES` |
| `wip/stash-audit` | 28 files (+9,790): hermes/cline/opencode memory, `privacy_audit_tool/`, `Cube Timer.html`, vids-ai HTML | 4 add/add code files → main (audit predated the `b299a1c` wheel fix) |
| `wip/stash-memory-edits` | 4 unique entries (PR#8 record, evidence-SHA lesson, Tailscale lesson, Tk-skip lesson) | union — disjoint additions |

Validation: diff vs main = 30 files, +9,814/−2, confined to `.agents/memory/**` +
`small-projects/**`; `agentops/` and `universal-game-agent/` byte-identical to main;
privacy_audit_tool `Ran 13 tests, OK`. Pushed tip `e45c2b7`.

## Phase 2 — integrate into fix/play-readiness (shared tree)

The branch is checked out in the shared tree (second checkouts of the same branch are
forbidden), so the merge happened in place with a precomputed-resolution pattern:

- `git merge-tree --write-tree` predicted exactly two conflicts (`decisions.md`,
  `lessons.md` — fpr's own `e431028` entries vs the carried 10-07 entries).
- Resolutions were precomputed outside the tree from the stage OIDs via
  `git merge-file -p`; both hunks were disjoint append-zones → keep-both union.
- Overlap checks: zero intersection with the parallel session's dirty mini-llm files;
  the untracked `privacy_audit_tool/` held only `inputs/usernames.txt` (no incoming
  filename collision; it is hidden from status by the tool's own `.gitignore`).
- Merge + install + add + commit ran as one critical section → `dceaade`, parents
  `ac8a8ec` (the parallel session's fourth mid-flight commit) + `e45c2b7`. The four
  parallel commits (`0d8360f`, `c112b4a`, `dc98601`, `ac8a8ec`) were committed by the
  other session during preparation and became the merge's first parent — nothing lost.

## Phase 3 — cleanup

- Deleted `integrate/wip-stash-20261010` locally and on origin; removed the merge worktree.
- Deleted all four `wip/stash-*` branches (all `-d` merged-checks passed).
- `fix/play-readiness` pushed: origin = local = `dceaade`; `fpr..main = 0`, so the
  user's upcoming main merge can fast-forward.

## Left open

- User decision on `fix/play-readiness-clean` (`D:/tmp/qr-clean`): 9 of 10 commits
  patch-equivalent to fpr (`git cherry` `-` lines), unique `4f029e5` "force LF for
  shell scripts" (`qr-generator/.gitattributes`, +1 line). Worktree clean, never pushed.
- GitHub Dependabot: 4 vulnerabilities (2 high, 2 moderate) on the default branch.

## Techniques worth reusing

- PowerShell `2>&1` aborts on git/unittest stderr — see `lessons.md` entry
  "PowerShell turns native-command stderr into terminating ErrorRecords".
- Shared-tree merge pattern — see the parallel `lessons.md` entry
  "Merging into a shared worktree with a live parallel session".
