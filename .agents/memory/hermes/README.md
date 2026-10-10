# Hermes-scoped memory

Agent-scoped memory for work driven from a Hermes Agent session in this repository.

This folder follows the same convention as `.agents/memory/opencode/`,
`.agents/memory/oh-my-pi/`, and `.agents/memory/cline/`: universal knowledge stays
in `.agents/memory/*.md`, and only agent-specific runbooks, tooling quirks, and
session records live here.

**The universal files remain authoritative.** Nothing in this folder overrides
`.agents/memory/decisions.md`, `architecture.md`, `lessons.md`, `project.md`, or
`roadmap.md`. When the two disagree, the universal file wins and this folder is
wrong and needs correcting.

## Contents

| File | What it holds |
|---|---|
| `README.md` | This scope note and the folder rules |
| `environment.md` | Verified tooling facts for Hermes sessions on this Windows host |
| `lessons.md` | Hermes-relevant lessons; canonical dated lessons stay in `.agents/memory/lessons.md` |
| `sessions/` | Per-session records of what was done and why |
| `sessions/2026-10-05-agentops-evidence-contract.md` | AgentOps evidence contract, the four fixes, and the five-boundary audit |

## Why this folder exists

Hermes arrives with a different tool surface from the other agents here: a
terminal that runs through `bash` (git-bash/MSYS) rather than PowerShell, a
`write_file` tool that refuses to overwrite a file whose last read used
`offset`/`limit` pagination, and a `execute_code` kernel with a five-minute
budget. Those quirks cost real time on the first session, so they are recorded
here rather than rediscovered. See `environment.md`.

## Rules for this folder

- **Never store secrets here** or anywhere under `.agents/memory/`. Record
  credential *shapes* and probe outcomes, never values. `.env` is never read or
  printed.
- Prefer a pointer to a canonical file over copying its content. Duplicated text
  drifts, and these files are agent-scoped so they get less review attention.
- Record only facts verified in this environment. Mark anything unverified as
  such rather than asserting it. A plausible-but-wrong claim in a memory file is
  more dangerous than an absent one, because the next session reads it as fact.
- Keep it to durable, cross-session-useful knowledge. Session transcripts and
  chat-length narration do not belong here.
- **The repository is a shared, dirty tree.** Other agents work in it
  concurrently. See `lessons.md` for staging a single commit out of a file that
  another session also has modified.