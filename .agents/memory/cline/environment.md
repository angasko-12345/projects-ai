# Environment — verified facts (Cline sessions)

Every line below was checked in this environment. Anything inferred is marked.
Re-verify before relying on it; toolchains move.

## Verified 2026-10-01

- Windows, `win32`. Repo root `D:\admin\code\projects`, branch `main`, remote
  `https://github.com/angasko-12345/projects-ai.git`.
- `git version 2.55.0.windows.5`
- `python 3.14.7` (note: the AgentOps runtime target is documented as 3.11+; the
  machine runs newer, which is fine, but do not infer the target from here).
- `sqlite3` 3.50.4 (bundled with CPython 3.14).
- `pytest` **is** installed (`.../python314/scripts/pytest.exe`) even though the
  product instructions call it not the source of truth. There is still **no
  configured** pytest, lint, formatter, type-check, or coverage setup, and
  `pyproject.toml` has no pytest section. Install presence is not configuration;
  keep using `unittest`.
- `PyYAML` importable and `tkinter` importable in the main interpreter.

## Correction to an existing memory

`.agents/memory/opencode/environment.md` states "There is deliberately **no root
`.gitignore`** — see the 2026-09-15 commit-strategy decision, item 4."

**That is stale.** A root `.gitignore` exists and is tracked, and it was
deliberately restored by the 2026-09-26 decision in `.agents/memory/decisions.md`
("Root .gitignore restored, superseding the 2026-09-15 removal"). Verified
2026-10-01: the file exists, carries container-level patterns
(`.misc/`, `.playwright-mcp/`, …), and its own comment says every ignore rule
lives there so fresh clones are protected. `agentops/.gitignore` separately owns
product-level patterns (`.agentops/`, `dist/`, `build/`, `*.egg-info/`, …).

Left uncorrected in the opencode folder because that file is not mine to edit
mid-task; recorded here so the next session does not act on the stale claim.
Resolving it properly is a one-line edit in the opencode folder.

## Shell behaviour that repeatedly cost time

Windows PowerShell 5.1. See `agentops-verification-workflow.md` for the
command-level workarounds, which are the practical form of these facts:

- **Heredocs do not work.** `git commit -F - <<'EOF'` is a syntax error here.
  Write the message to a file first, then `git commit -F <file>`.
- **Native commands writing to stderr make the whole call look failed.**
  PowerShell wraps stderr in error records, so `python ... 2>&1 | Select-String`
  reports `NativeCommandError` and a non-zero exit even when the command
  succeeded. Redirect through `cmd /c "... > out.txt 2>&1"` and read the file.
  This bit me on both `unittest` and `git push` — a successful push reported
  "Everything up-to-date" only on the second attempt.
- **The tool call has a 30-second timeout.** The full AgentOps suite takes
  ~35–45s, so it must be backgrounded. See the runbook.
- `[System.IO.File]::WriteAllText` writes UTF-8 **without** BOM only if handed
  `(New-Object System.Text.UTF8Encoding $false)`. The default adds a BOM, which
  makes Python fail to parse the file with a confusing `SyntaxError` pointing at
  the first character. When PowerShell itself is unstable under memory pressure,
  `[System.IO.File]::WriteAllText` can also fail to create the file at all —
  verify with `Test-Path` before using the file.
- `Get-ChildItem -Recurse -Include <dir>` is unreliable for directories;
  `-Filter` works. Prefer it.
- `[System.IO.File]` methods resolve against the *process* working directory,
  not the shell's `cd`. Always pass an absolute path.

## Scratch space

- `%TEMP%` (`C:\Users\admin\AppData\Local\Temp\`) is used for repro scripts and
  pre-work backups. Backup taken before the 2026-10-01 AgentOps work:
  `agentops-backup-reviewgates-20261001-185102`.
- `python -c "..."` with nested double quotes is fragile inside `cmd /c`. Prefer
  a script file when the snippet is more than one statement.

## Concurrent-writer hazard observed 2026-10-01

Mid-task, five `universal-game-agent/` files (`training/ppo.py`,
`training/evaluate.py`, `training/external_experiment.py`, and two tests)
appeared as modified in `git status` that I had not touched. Either another
session was writing or a process rewrote them. Consequence: **always check
`git status` before staging, and stage by explicit path list.** A broad
`git add -A` here would have swept another session's in-progress work into a
commit. This is also the single-writer rule in `.agents/team.md` being
violated in practice, not just in theory.