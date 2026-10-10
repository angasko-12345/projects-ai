# AgentOps verification workflow (Cline runbook)

How to actually run, read, and trust the AgentOps test suite on this machine.
Written after losing several cycles to PowerShell output handling and to the
30-second tool timeout.

## The one rule

```bat
cd D:\admin\code\projects\agentops
python -m unittest discover -s tests
```

**Never run it from the repo root.** The root `agentops/` directory shadows the
`agentops` package and produces import errors that look like real breakage.

## Reading results without fighting the shell

`2>&1 | Select-String` reports `NativeCommandError` and a non-zero exit even
when the suite passed, because unittest writes its summary to stderr. Use:

```bat
cmd /c "cd /d D:\admin\code\projects\agentops && python -m unittest discover -s tests > %TEMP%\out.txt 2>&1"
```

then grep the file for `Ran `, `^OK`, `FAILED`, `^ERROR:`, `^FAIL:`.

Note `%TEMP%` inside `cmd /c` is expanded by **cmd**, so it must be written as
`%TEMP%` in a cmd string but `$env:TEMP` in a PowerShell string. Mixing them
produces a file literally named `\out.txt` or a path that does not exist. When
reading it back from PowerShell, use `$env:TEMP\out.txt`.

## The full suite exceeds the tool timeout

The suite runs ~35–60s (407 tests observed 2026-10-01 at ~36-43s; **438 tests
observed 2026-10-04 at ~55-59s** after the Qt migration; **701–703 tests observed
2026-10-08/2026-10-10 at ~135–178s** after the workflow-autonomy and
wheel-install work) and the tool call caps at 30s. Background it:

```powershell
Start-Process -FilePath python -ArgumentList '-m','unittest','discover','-s','tests' `
  -WorkingDirectory 'D:\admin\code\projects\agentops' `
  -RedirectStandardOutput "$env:TEMP\ao_out.txt" `
  -RedirectStandardError  "$env:TEMP\ao_err.txt" -NoNewWindow
```

The summary lands in the **stderr** file. Poll with
`Get-Content $env:TEMP\ao_err.txt -Tail 6` until a `Ran N tests` line appears,
then confirm `Get-Process python` returns nothing.

Targeted runs finish well inside the limit and can be foregrounded:

```bat
cmd /c "cd /d D:\admin\code\projects\agentops && python -m unittest tests.test_readiness_and_failure_paths > %TEMP%\t.txt 2>&1"
```

## Qt GUI tests (added 2026-10-04)

The Tk widget tests were replaced by PySide6 equivalents. `tests/qt_display.py`
sets `QT_QPA_PLATFORM=offscreen` at import, so there is nothing to configure and
no display is needed. The suite skips only when PySide6 itself is absent
(`@requires_qt`), not when a display is missing.

Two failure modes cost real time, and both look like "stuck" rather than
"failed":

- **No `QApplication` yet.** A widget constructed before the app object exists
  hangs. Call `qt_app()` first in `setUp`; `make_context()` does it for panels.
- **A modal dialog.** `MainWindow.closeEvent` asks for confirmation while a
  background operation is active, and an event loop with nobody to answer it
  hangs forever. Drive the fake controller through `thread-finished`, and patch
  `agentops.gui.shell.QMessageBox.question` in teardown.

Because controller reads are asynchronous, assert with `wait_until(...)`, not a
fixed sleep:

```python
self.assertTrue(wait_until(lambda: view._table.proxy.rowCount() == 3))
```

Targeted GUI runs finish inside the tool limit and can be foregrounded:

```bat
cmd /c "cd /d D:\admin\code\projects\agentops && python -m unittest tests.test_gui_qt -v > %TEMP%\q.txt 2>&1"
```

## Proving a regression test actually bites

A new test that passes against broken code is worse than no test, because it
reports safety that does not exist. For each fix, verify the failure:

1. **Revert only that fix** (edit the one expression back) and run the tests.
   They must go red.
2. **Restore the fix** and confirm green.

Two examples that worked:

- Reverting `redact_text(result.stdout)` to `result.stdout` in `workflow.py`
  made 5 of 6 redaction tests fail with the fake secret visible in the stored
  column — the exact leak, surfaced by the test.
- Removing the `_exclude_agentops_state` call from `git.py` made the
  fresh-repository tests fail on a real temp repo.

Whole-file reversion via `git stash push -- <paths>` also works and is useful
when new tests import symbols that do not exist in the old code — the failure is
then an `ImportError`, which confirms the new API is genuinely new rather than
pre-existing.

## Writing files from this shell

Prefer the editor tool over shell redirection. When a here-string plus
`WriteAllText` is unavoidable, always pass
`(New-Object System.Text.UTF8Encoding $false)` — the default emits a BOM and
Python refuses to parse the result. Verify afterwards:

```bat
python -c "import ast,pathlib; ast.parse(pathlib.Path('<file>').read_text(encoding='utf-8'))"
```

`ast.parse` is a fast syntax gate that does not import the module, so it is the
right check before spending a test run on a mangled file.

## Git hygiene in this repo

- Stage by **explicit path list**, never `git add -A` — see the concurrent-writer
  hazard in `environment.md`.
- Commit messages must go in a file; heredocs fail. Write the file, then
  `git commit -F <path>`, then confirm with `git show --stat --oneline HEAD`.
- After pushing, verify against the remote rather than trusting the console:
  `git ls-remote origin main` must equal `git rev-parse HEAD`. A push can
  succeed while its output is lost to the stderr problem above.
- **The remote can advance while you work.** A push approved at the start of a
  long task may be rejected as non-fast-forward. Do not reach for `--force`: on
  a shared branch that destroys other people's commits irrecoverably. Instead
  `git fetch` (read-only; does not move `HEAD` or touch the working tree), check
  whether the changes are disjoint with `git diff --stat HEAD...origin/main`,
  and prove the merge with `git merge-tree --write-tree HEAD origin/main`. Then
  ask before merging, and prefer **merge over rebase** when a commit SHA has
  already been reported — a rebase rewrites it, and it also demands a clean
  tree, which would force stashing unrelated dirty work.
- `git --no-pager` for anything whose output would otherwise open a pager.
- Expect LF→CRLF warnings on `.agents/` and `agentops/` files. They are benign
  on Windows and should not be "fixed".

## The evidence tool: never repair a number, always re-measure

Two commands, both run from the **repository root** (unlike the suite itself):

```bat
python tools\evidence\generate.py agentops   (default = clean clone of HEAD)
python tools\evidence\check.py
```

`generate.py` without a product argument measures all four. Default clean-clone
mode is the one to use: it clones `HEAD` into `%TEMP%\evidence-clone-*` and runs
there, so the record describes **committed** code. Two consequences that matter:

- **A fix must be committed before regenerating.** Clean-clone mode measures
  `HEAD`, so an uncommitted fix is invisible to it and the run reproduces the
  failure you were trying to record as fixed.
- **A partial run carries the other products forward unchanged.** Each record
  keeps its own `commit`, which is how `check.py` detects that a product went
  stale. Re-measuring one product re-dates the document, not the others.

`generate.py` exits 1 when any selected product is not a pass, and that is
correct — it writes the file either way. Exit 1 is a finding, not a failure of
the tool.

Do not hand-edit `verification.json`. The counts and the recorded runner
summary lines are re-parsed by `check_schema` and must agree, so editing one
without the other is caught; editing both is a forgery the staleness rule is
there to expire. A record naming an unreachable commit cannot be repaired by
editing it — it can only be re-measured.

## In-place and clean-clone are different environments (cost 2 false greens)

`cd agentops && python -m unittest discover -s tests` passed while the same
committed code failed in a clean clone, and the difference was **the drive the
run started from**. `agentops/tests/test_wheel_install.py` launched a child with
`cwd="/tmp"`; on Windows that resolves against the current drive, so it was
`D:\tmp` (exists here by accident) in the working tree and `C:\tmp` (absent) in
`%TEMP%\evidence-clone-*`. Both tests errored with `[WinError 267]`.

So when a clean-clone evidence run disagrees with an in-place run, **do not
trust the in-place pass**. Reproduce in a clone and read the traceback:

```bat
git clone --quiet --no-hardlinks D:\admin\code\projects %TEMP%\repro
cd /d %TEMP%\repro && git checkout --quiet --detach <commit>
```

The lesson's general form: a hardcoded POSIX absolute path is not portable, and
isolation-in-the-working-tree cannot prove a portability defect absent.

## Editing files that other tooling rewrites

`tasks/task-ignorethis.md` is rewritten by an external process and the repo
instructions say to leave it alone. When a file unexpectedly reverts or gains
stray content mid-edit, suspect concurrent tooling before assuming your own edit
failed, and re-read the file before re-applying anything.