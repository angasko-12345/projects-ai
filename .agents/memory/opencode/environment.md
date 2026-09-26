# Environment and tooling — verified facts

Only facts actually checked in this environment are recorded here. Anything unverified is
marked as such rather than asserted.

## Machine and shell

- Windows, `win32`. Working directory `D:\admin\code\projects`. Branch `main`.
- Shell is **Windows PowerShell 5.1**, not PowerShell 7. Consequences that actually bit:
  - `&&` is not a valid command separator. Use `;` or `if ($?) { ... }`.
  - `Get-ChildItem -Recurse -Include <dir>` does not match directories reliably;
    `-Filter` does. Use `-Filter "__pycache__"` rather than `-Include "__pycache__"`.
- `rtk git -C <repo> <args>` works for git. Plain `git` is also available.

## Not on PATH

- `rg` / `rtk rg` are recorded as **not on PATH** in this PowerShell environment
  (source: `.agents/memory/pi-opencode-free-tier-fix.md`, not independently re-verified
  here). Use `Select-String`, or the dedicated Grep tool.

## Scratch space

- `C:\Users\admin\AppData\Local\Temp\opencode\` is the pre-approved scratch directory for
  work outside the repo. Probe scripts for the free-tier gate live there.
- PowerShell `node -e` quoting breaks on this setup — write probe scripts to files instead.
  (Recorded in `pi-opencode-free-tier-fix.md`.)

## Repo facts that affect tooling

- **Single repository, two products.** `agentops/` and `universal-game-agent/` are
  independent; neither depends on the other. A root-level test invocation does not work —
  each suite must run from its own product directory, because the root `agentops/` folder
  shadows the `agentops` package for namespace resolution.
- **No configured lint, formatter, type-check, or coverage tooling** in either product. Do
  not invent such commands.
- `.git/info/exclude` holds machine-local paths (`.pi/`, `small-projects/`, and others).
  There is deliberately **no root `.gitignore`** — see the 2026-09-15 commit-strategy
  decision, item 4.
- Regenerable artifacts that are gitignored and safe to delete: `build/`, `.pytest_cache/`,
  `*.egg-info/`, `__pycache__/`. Preserved on purpose: `agentops/dist/AgentOps.exe` (a
  release deliverable) and `agentops/.agentops/state.sqlite` (live state).

## Running the suites is a write

`python -m unittest discover -s tests` regenerates `__pycache__` throughout the product and
can touch local state. Treat it as a write operation: budget for re-cleanup, and never run
it "just to check" in a tree another agent is working in. Ten `__pycache__` directories were
created and removed again during the 2026-09-26 session purely by running one suite.

## Interop available in-session

- Intercom tools for local agent sessions: `intercom_list`, `intercom_send`, `intercom_status`,
  `intercom_team`, `intercom_whoami`. Session names are ephemeral — resolve the live name with
  `intercom_list` before messaging, never reuse a remembered one.
- Delegation targets: `explorer`, `librarian`, `oracle`, `fixer`, `designer`, `councillor`.
  Reviewer sessions are reusable via their task id, which saves context on follow-up work.
