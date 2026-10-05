# mini-llm-scoped memory

Agent-scoped memory for work driven from a mini-llm session in this
repository.

This folder follows the same convention as `.agents/memory/opencode/` and
`.agents/memory/oh-my-pi/`: universal knowledge stays in
`.agents/memory/*.md`, and only agent-specific runbooks, tooling quirks, and
session records live here.

**The universal files remain authoritative.** Nothing in this folder overrides
`.agents/memory/decisions.md`, `architecture.md`, `lessons.md`, `project.md`, or
`roadmap.md`. When the two disagree, the universal file wins and this folder is
wrong and needs correcting.

## Contents

| File | What it holds |
|---|---|
| `README.md` | This scope note and the folder rules |
| `checkpoint-schema.md` | Checkpoint metadata schema v2, load/gate behavior, compat |
| `sessions/` | Per-session records of what was done and why |

## Rules for this folder

- Never store secrets here or anywhere under `.agents/memory/`. Record
  credential *shapes* and probe outcomes, never values.
- Prefer a pointer to a canonical file over copying its content. Duplicated text
  drifts, and these files are agent-scoped so they get less review attention.
- Record only facts verified in this environment. Mark anything unverified as
  such rather than asserting it. A plausible-but-wrong claim in a memory file is
  more dangerous than an absent one, because the next session reads it as fact.
- Keep it to durable, cross-session-useful knowledge. Session transcripts and
  chat-length narration do not belong here.
