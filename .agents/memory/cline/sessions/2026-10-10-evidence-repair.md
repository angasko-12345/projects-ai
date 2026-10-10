# 2026-10-10 — Verification-evidence repair (AgentOps)

Repaired `.agents/evidence/verification.json`, which named a commit GitHub
Actions could not resolve, and fixed the real defect the record exposed.

## Starting state

`python tools/evidence/check.py` exited 1:

```
agentops: evidence was measured at 2922ec532bff, which is not an ancestor of
HEAD e3dc0c5d4263; the history it described is gone
```

The AgentOps record claimed 701 tests, `errors=2`, `result: "fail"`. The
independent `agentops.yml` workflow was green on `c8f09f19`. Neither was
assumed authoritative; the committed code was measured.

## Two independent causes

**1. The measurement commit was unreachable.** `2922ec532bffb2428ac62965c37504eab656e35c`
resolved via `git cat-file -t` — which is why it looked valid — but was
reachable from no ref: `for-each-ref --contains` empty, absent from
`rev-list --all` and `git ls-remote origin`, not an ancestor of `HEAD`. Its
parent `242f9748` was orphaned as well, and the commit shares its subject with
the on-`main` twin `4842e46`, so a rewrite stranded it in this clone's object
store. It had never been pushed.

**2. The recorded `errors=2` was a genuine defect.** `generate.py agentops` in
default clean-clone mode reproduced exactly 2 errors on committed code. Both
were `NotADirectoryError: [WinError 267]` from `subprocess.run` in
`agentops/tests/test_wheel_install.py`. Cause: `cwd="/tmp"`. On Windows an
absolute POSIX path resolves against the **current drive**, so it was `D:\tmp`
in the working tree — which exists on this machine by accident — and `C:\tmp`
under `%TEMP%\evidence-clone-*`, which does not.

That is why the in-place suite reported OK while the clean clone failed, and why
a Linux CI run is green: `/tmp` exists on both. The 2026-10-08 lesson recorded
these errors as "Root cause: None in the fix"; that triage ran the module in the
working tree, on the drive where `/tmp` happened to exist, so it could not see
the defect. `lessons.md` now carries a superseding entry.

Proof of mechanism, run deliberately:

- from a `C:` cwd: `subprocess.run([python, "-c", "print(1)"], cwd="/tmp")` → `[WinError 267]`
- from a `D:` cwd: same call → returncode 0
- `Test-Path D:\tmp` → True; `Test-Path C:\tmp` → False

## Fix

`off_tree_directory(tmpdir)` derives the launch directory from the caller's own
`tempfile.TemporaryDirectory()`, so it always exists and is never the source
checkout — the tests' actual intent (prove the *packaged* `agents.yaml` loads) is
preserved. Both call sites updated. Two regression tests added:
`test_a_child_process_starts_in_the_off_tree_directory` and
`test_the_off_tree_directory_is_not_the_source_checkout`. Only those two call
sites existed; `Path("/tmp")` in `test_failure_kernel.py` is never resolved by
the OS and was left alone.

## Verification

| Check | Result |
|---|---|
| `tests.test_wheel_install` in isolation | 6 tests OK (2 previously erroring now pass, plus 2 new) |
| `generate.py agentops` (clean clone of `70bad5936`) | **pass: 703 tests, 4 skipped, 0 failures, 0 errors** |
| `check.py` before commit | OK, exit 0 |
| `check.py` after commit | OK, exit 0 |
| `unittest discover -s tools/evidence -t tools/evidence -p "test_*.py"` | 38 tests OK |
| other three product records | verified **byte-identical** to before the run |

The fix was committed **before** regenerating, because clean-clone mode measures
`HEAD` and would otherwise have reproduced the failure being recorded as fixed.

## Commits

- `70bad59` — fix(agentops): portable off-tree working directory
- `1683750` — chore: refresh agentops verification evidence

Pushed to `origin/main`; `git ls-remote origin refs/heads/main` equals
`git rev-parse HEAD` at `1683750f3c6943c62d0ccfedb8739ed2e32d01e0`. Diff scope
`e3dc0c5..HEAD` was exactly two files. No history was rewritten, no count was
hand-edited, no failure suppressed.

## Constraints observed

- Never hand-edited a result to manufacture a pass.
- Did not rewrite history to make the old commit resolvable.
- Preserved the per-product measurement design (each record keeps its own commit).
- One product writer at a time; the other three products were not re-run.
- Pre-existing unrelated dirty entries left unstaged: `.agents/memory/*` from
  another session, plus untracked `.kilo/`, `QR-Generator-beta-1.0.0.apk`,
  `task.py`, `test_*.py`, `debug_test.py`, `test_output*.txt`.

## Residual risks

- **The in-place suite can still lie.** It passed for the same reason the defect
  survived: `D:\tmp` exists here. Any machine without `<drive>:\tmp` reproduces
  the original failure when running in place. The runbook now says not to trust
  an in-place pass over a failing clean-clone run.
- **Another process changed the branch mid-session.** `git status` reported
  `On branch main` at the start and `fix/play-readiness` at the end, created by
  `checkout -b` from the reflog — not by this session. `HEAD`, `main`, and
  `origin/main` all still point at the same commit, so nothing is stranded, but
  the tree has more than one actor in it.
- Documented checker limit stands: a self-consistent forgery editing counts and
  summary lines together is indistinguishable from a measurement.
