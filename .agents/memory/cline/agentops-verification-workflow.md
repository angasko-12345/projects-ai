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

The suite runs ~35–45s (407 tests observed 2026-10-01 at ~36s and ~43s across
runs) and the tool call caps at 30s. Background it:

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
- `git --no-pager` for anything whose output would otherwise open a pager.
- Expect LF→CRLF warnings on `.agents/` and `agentops/` files. They are benign
  on Windows and should not be "fixed".

## Editing files that other tooling rewrites

`tasks/task-ignorethis.md` is rewritten by an external process and the repo
instructions say to leave it alone. When a file unexpectedly reverts or gains
stray content mid-edit, suspect concurrent tooling before assuming your own edit
failed, and re-read the file before re-applying anything.