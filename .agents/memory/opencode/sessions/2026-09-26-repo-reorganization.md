# Session: repository reorganization and agent instruction hierarchy

**Date:** 2026-09-26
**Branch:** `main`
**Scope:** `D:\admin\code\projects` — audit, safe cleanup, instruction hierarchy
**Outcome:** complete, committed except where another agent's in-flight work was in the way

## Objective and constraints

Reorganize and optimize the repository safely. Standing constraints from the user:

- Do not read `.env` files or secrets. No secret file was opened at any point in this
  session.
- Do not delete small projects, `tasks/task.md`, `tasks/task-ignorethis.md`, or
  `agent-intercom-fix/`.
- Preserve `agentops/dist/AgentOps.exe` — the user has plans for it.
- Keep the user's existing uncommitted edits to `tasks/after-task.md` and the memory files.
- The user initially required **read-only** analysis, then explicitly authorized the
  cleanup, ignore fixes, `.agents/skills/` commit, and instruction-file changes.

## Initial audit

- 406 files, 56.13 MB. 154 tracked at the start.
- Git history is clean and small — largest historical blob 88 KB. **No history rewrite,
  filter-repo, or aggressive `gc` is justified.** Recorded as a finding, not performed.
- Largest untracked/on-disk items were build artifacts and training checkpoints.
- No secret material was found in tracked files by content search — but see the security
  finding below, which turned out to be the opposite of reassuring.

## Safety first

Backup at `C:\Users\admin\AppData\Local\Temp\opencode\projects-backup-20260926`:

- 50 files, 0.399 MB
- `worktree.patch`, 56,902 bytes
- Covers modified and untracked files, including `.agents/skills/`, `.pi/`,
  `small-projects/`, `orchestrator.py`, and `skills-lock.json`

## Changes made

### Cleanup — 19.71 MB reclaimed

Deleted as regenerable and gitignored: `agentops/build/`, `agentops/.pytest_cache/`,
`agentops/agentops.egg-info/`, all `__pycache__` directories, and the
`state.sqlite-shm` / `state.sqlite-wal` sidecars.

Preserved deliberately: `agentops/dist/AgentOps.exe` (14,851,920 bytes — release
deliverable) and `agentops/.agentops/state.sqlite` (167,936 bytes — live state).

### Checkpoint ignore gap — the highest-value fix

`universal-game-agent/.gitignore` used a flat `checkpoints/*.pt` rule, which does **not**
match `checkpoints/extern_pong_01/ppo_final.pt` (11.8 MB, unique). Any `git add -A` would
have staged ~16.3 MB of binaries. Rules are now `checkpoints/**/*.pt`, `**/*.zip`,
`**/*.pkl`, preceded by an ignore-all-then-allow pattern so `README.md` and `.gitkeep`
stay visible. Verified: all three `.pt` files ignored, `README.md` and `.gitkeep` not.

No root `.gitignore` was created — the 2026-09-15 decision removed one at the user's
request, and a product-scoped problem belongs in the product's own ignore file.

### Other changes

- `.git/info/exclude` += `.pi/`, `small-projects/`. Existing entries preserved.
- `universal-game-agent/checkpoints/README.md` created — checkpoint provenance and how to
  regenerate. Two trained checkpoints (`checkpoints/ppo_final.pt`,
  `checkpoints/extern_pong_01/ppo_final.pt`); the third `.pt` is `ppo_untrained.pt`.
- `.agents/skills/` committed as `65ef097` — 22 files, 13 skill directories, verified free
  of reparse points before staging. Four concurrently modified files deliberately excluded.

### Instruction hierarchy

Layered so universal rules live once and product facts live only with their product:

| Layer | File |
|---|---|
| Universal contract | `.agents/AGENTS.md` |
| Product-local | `agentops/AGENTS.md`, `universal-game-agent/AGENTS.md` |
| Tool deltas | `.agents/pi_AGENTS.md`, `.agents/ohmypiagents.md` |
| Compatibility shims | root `AGENTS.md`, root `pi_AGENTS.md` |
| Corrected | `.github/copilot-instructions.md`, `.agents/team.md` |

Documentation only — no runtime code changed.

## The important part: the first draft was wrong

An independent read-only review found **seven factually incorrect claims and one invented
defect** in the drafted files:

| Claim | Reality |
|---|---|
| "262 test methods" | 258 at review time, 261 after a parallel commit |
| "the four `ppo_final.pt` files" | two exist |
| "12-line `ppo:` tail byte-identical across all five YAMLs" | 9 keys match, 5 differ; the `model:` block is the identical part |
| "two of the 1000s are unreachable" | three |
| `agentops.egg-info/` "in the working tree" | deleted earlier in the same session |
| no-console helpers "in `git.py`, `runner.py`, verification" | owner is `runtime.py`; `runner.py` has none |
| CWD-fragile relative-path checks | inverted — those tests are CWD-*independent* |
| `storage_dtos` DTOs "in `tasks.py` and `state.py`" | `gui_controller.py` and `git.py` |
| `.agents/AGENTS.md` "points at `agentops/` and is wrong" | it states no single command exists and gives a two-row table |

Also found and fixed: `.agents/team.md` still said Pi was the *only* writer and omitted
Antigravity entirely, while three other files named it mandatory reading. Four items of
technical content were lost in the split and had to be restored (StateStore WAL/locking
model, the `state.py` migration inventory, `.agentops/` as the runtime-state location, and
the `{prompt}` substitution contract).

The reviewer's own spec then contained an error — it claimed `.agents/AGENTS.md:64` held a
Tk `root.after` rule, when line 64 is the leaf-modules rule. The fixer declined to invent a
rule to match and scoped the two that genuinely exist. Worth remembering: give specialists
authority to reject a spec item.

## Security finding

`pi-opencode-free-tier-fix.md:24` contained a **plaintext `sk-…` API key** in a tracked
file, so it is in git history. This violates the "never store secrets in any memory file"
rule. The working-tree copy was redacted on 2026-09-26, but **redaction does not remove it
from history — the key must be rotated.** It was deliberately left uncommitted so the
redaction would not capture another session's in-flight edit to the same file.

## Verification evidence

- `git diff --check` clean.
- All six removed-claim strings confirmed gone by grep across all nine instruction files.
- `runtime.py:102` confirmed cited as the spawn-helper owner in all three files that
  mention it.
- `team.md` carries the new 2026-09-26 policy entry; the 2026-09-12 and 2026-09-13 history
  entries are preserved verbatim.
- Test baselines run first-hand at `a03e907`: `agentops/` 357 tests, 4 environment skips
  (353 passed); `universal-game-agent/` 261 tests, no skips.
- 10 `__pycache__` directories created by that run were removed again; `AgentOps.exe` and
  `state.sqlite` confirmed intact afterwards.

## Parallel-writer conditions

Another agent committed throughout: `135ed8a`, `228930f`, `a03e907` all landed mid-session,
a 4.1 MB `checkpoints/ppo_untrained.pt` appeared with no config reference, deleted
`__pycache__` directories were regenerated, and 7 `universal-game-agent/` source files were
modified by that agent and left uncommitted by design.

## Open items

- Commit the instruction layer and these memory files, scoped to explicit paths.
- Rotate the exposed API key.
- Consider consolidating `.github/copilot-instructions.md` into a pointer (currently kept as
  a real file with its false product-focus claim corrected).
- `.agents/memory/project.md:44` still reads "357 passing, 4 environment skips" — the same
  353-passed ambiguity, in a memory file rather than a governance file.
- `orchestrator.py` and `skills-lock.json` remain untracked and unprotected at the repo root.
- The `.agents/memory/` split into universal plus per-agent folders is **planned, not
  implemented**. See `../README.md`.
