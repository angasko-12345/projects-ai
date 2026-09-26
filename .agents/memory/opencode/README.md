# OpenCode-specific memory

Agent-scoped memory for work done from an OpenCode session in this repository. Part of a
**planned but not yet implemented** split of `.agents/memory/` into universal knowledge plus
one folder per agent.

## Why this folder exists

`.agents/memory/` currently mixes two different kinds of knowledge:

- **Universal** — project state, architecture, decisions, lessons. True regardless of which
  agent is driving. Stays at `.agents/memory/*.md`.
- **Agent-specific** — runbooks, tooling quirks, provider and model configuration, session
  history. Only meaningful to one agent. Belongs in a per-agent subfolder.

`.agents/memory/oh-my-pi/` was the first agent-specific subfolder; this is the second. Until
the split is actually done, the universal files remain authoritative and nothing should be
*moved* out of them on the assumption that a subfolder already holds it.

## The planned split (not implemented)

End state:

```
.agents/memory/
  project.md          universal - repo state, current priorities
  architecture.md     universal - structure and data flow
  decisions.md        universal - decisions with reasons and rejected alternatives
  lessons.md          universal - dated failure lessons
  roadmap.md          universal - phase progress
  audit.md            universal - structural audit notes
  opencode/           agent-specific - this folder
  oh-my-pi/           agent-specific
```

Files that are obvious candidates to move into this folder when the split happens:

- `omp-opencode-free-tier-403.md` — the deep OpenCode free-tier investigation (167 lines)
- `pi-opencode-free-tier-fix.md` — the Pi-side runbook for the same gate (74 lines)
- the `2026-09-22` OpenCode free-tier entry currently in `lessons.md`

**Do not move them yet.** Both are actively maintained, and moving them is a history-bearing
change that should be its own decision, not a side effect of creating this folder.

## Contents

| File | What it holds |
|---|---|
| `README.md` | This scope note and the split plan |
| `environment.md` | Verified tooling and environment facts for working in this repo |
| `free-tier-gate.md` | Distilled OpenCode Zen free-tier gate rules, with pointers to the canonical runbooks |
| `lessons.md` | OpenCode-relevant lessons; the canonical dated lessons stay in `.agents/memory/lessons.md` |
| `sessions/` | Per-session records; currently `2026-09-26-repo-reorganization.md` |

## Rules for this folder

- **Never store secrets here**, or anywhere under `.agents/memory/`. A plaintext
  `sk-…` API key was found in `pi-opencode-free-tier-fix.md` on 2026-09-26 and redacted
  from the working tree; it remains in git history, so that key must be rotated. Read
  credentials in-memory at probe time and record only the HTTP status.
- Facts here may be superseded by the universal files. When the two disagree, the universal
  file wins and this folder gets corrected.
- Prefer a pointer to the canonical file over copying its content. Duplicated text drifts.
