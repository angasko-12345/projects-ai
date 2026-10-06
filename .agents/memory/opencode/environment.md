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

- **Single repository, three products.** `agentops/`, `universal-game-agent/`, and
  `small-projects/mini-llm/` are independent; none depends on another. A root-level test
  invocation does not work — each suite must run from its own product directory, because
  the root `agentops/` folder shadows the `agentops` package for namespace resolution.
- **No configured lint, formatter, type-check, or coverage tooling** in any product. Do
  not invent such commands.
- `.git/info/exclude` holds machine-local paths (`.pi/`, `small-projects/`, and others).
## Verification work (2026-10-06)

Verified in this environment, not inferred:

- **Measure from a throwaway clone, not the shared tree.** `git clone --quiet --no-hardlinks
  <repo> <temp>` then `git checkout --detach <sha>` inside it. This makes it cheap to commit
  synthetic history and prove how a check behaves, without touching the working tree or the
  index. `--no-hardlinks` matters: with hardlinks, editing a file in the clone can write through
  to the original object store.
- `git worktree add --detach <path> <sha>` also works, but the worktree registration lives in
  the shared `.git/worktrees/`, so it is visible to other sessions and can be pruned out from
  under you. A plain clone has no such coupling.
- **Measured suite runtimes** (Windows, Python 3.14.7, this machine): `agentops/` ~100 s,
  `universal-game-agent/` ~50 s, `small-projects/mini-llm/` ~55 s. A full
  `python tools/evidence/generate.py` is ~4 minutes because it runs each suite in a fresh
  clone. All of these exceed a 30 s tool-call timeout, so background long runs and poll.
- **Do not `git stash` in a shared tree**, even when the stash is popped immediately and
  `git status` matches before and after. Another session can commit between the two, and the
  index is shared. A plain clone has nothing to lose.
- PowerShell 5.1: `> /dev/null` does not redirect to a null device -- it resolves to a
  relative path and fails. Use `| Out-Null`, or write to a file under the scratch directory.
- `python -c "..."` works for short probes; write a scratch file under
  `C:\Users\admin\AppData\Local\Temp\opencode\` for anything longer. Same quoting hazard
  already recorded for `node -e`.
- Current test results for all three products are in `.agents/evidence/verification.json`, not
  in memory. Run `python tools/evidence/check.py` to verify the record is still true; do not
  quote a count from any memory file.
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
