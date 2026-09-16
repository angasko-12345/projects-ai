# Team — AgentOps Collaboration Roster (reconciled 2026-09-13)

> Reconciled per explicit user instruction (memory priority: user instructions outrank old memory).
> Canonical shared memory. All collaborating agents MUST read this file before substantial work.
> Never store secrets, API keys, passwords, tokens, credentials, or private keys in any memory file.
> Do not reinstall or reconfigure Agent Intercom. Do not change Intercom scope.

## Agents and roles

- **Pi — Session `pi-manager` — Lead + sole implementer/writer.**
  Long-term manager and only writer on project files. Maintains project state, priorities, coordination, decisions. Coordinates reviewers, runs/reviews tests, reviews final diffs, updates memory, reports to user.
- **OpenCode — architecture reviewer.**
  Reviews layering, threading/lifecycle hazards, API shapes. Reachable two ways: live Intercom session `opencode-projects-6896` (observed idle 2026-09-13), or `agentops run opencode` in a read-only snapshot. NOTE: backend auth failed 2026-09-13 (`Unauthorized`) — verify auth with a trivial run before tasking.
- **free-claude-code (`fcc-claude`) — security reviewer.**
  Reviews path traversal, injection, secret leakage, unsafe error handling. No live Intercom session observed 2026-09-13; use `agentops run fcc-claude` in a read-only snapshot. Requires `fcc-server` running (`http://127.0.0.1:8082`; binary exists at `~/.local/bin/fcc-server`, was down 2026-09-13).
- **Copilot — test/edge-case reviewer.**
  Reviews coverage gaps, races, flaky-test risks. Live Intercom session `copilot` (observed idle 2026-09-13), or `agentops run copilot` (completed a full 87s review 2026-09-13).
- **Out of scope (do not task):** `codex-builder`, `agy-reviewer`, `opencode-arch` (retired 2026-09-12 roster — see history below). A live `claude` Intercom session exists but is NOT an allowed collaborator — never send it work. `codex`/`pi` AgentOps CLIs may execute locally as tools, but Codex is not a review collaborator.

## Collaboration workflow (Pi coordinates)

1. Understand the task.
2. Read shared memory (`team.md`, `memory/project.md`, `memory/architecture.md`, relevant `decisions.md` / `lessons.md`, `memory/roadmap.md` for phased work).
3. Pi implements (sole writer; back up dirty tree first).
4. Snapshot reviews: copy relevant files to `/tmp/agentops-review-*`, run each reviewer via `agentops run <agent>` with an explicit READ-ONLY prompt (no file writes, no committing git commands); or via Intercom when a live session exists AND `intercom_list` confirms liveness.
5. Pi addresses findings (fix valid ones, record false positives with evidence).
6. Run the full test suite (`python -m unittest discover -s tests`).
7. Rebuild + smoke-test `dist/AgentOps.exe` if packaging-affecting code changed.
8. Update shared memory.
9. Report the result to the user.

Rules: single writer, always; reviewers never touch the repo (snapshots only); verify Intercom liveness via `intercom_list` before Intercom-based work (it shows live sessions; `intercom_team` shows configured targets — they can disagree); stale session names must never be messaged — resolve the current live name first; AGY-style independence applies to all reviewers (must not rubber-stamp; findings need file/line evidence).

## Memory rules (summary — full rules in Part 2 of the setup directive)

- Before: read `team.md`, `memory/project.md`, `memory/architecture.md`, `memory/roadmap.md`, relevant `decisions.md`, relevant `lessons.md`, `memory/audit.md` for structural questions.
- During: treat files as shared institutional knowledge; own session memory is not authoritative; verify others' claims where practical.
- After: update `project.md` (state), `architecture.md` (architecture), `decisions.md` (decisions), `lessons.md` (lessons), `roadmap.md` (phase progress). No duplicate entries; concise, no transcripts.
- Preserve history; never blindly overwrite. Populate only verifiable facts; otherwise write `Not yet established.`

## Memory layers

- `.agents/memory/` = canonical project knowledge (authoritative, priority over Supermemory).
- Supermemory = secondary long-term searchable memory / recalled historical context (if available; never a replacement).
- Agent Intercom = real-time agent communication.

## Memory priority (conflict order)

1. Current source code
2. Current tests
3. Explicit user instructions
4. `decisions.md`
5. `architecture.md`
6. `project.md`
7. `lessons.md`
8. Supermemory historical memories
9. Agent assumptions

Never allow an old memory to override current code or explicit instructions.

## Intercom targets (current, verified 2026-09-16)

- Observed live: `opencode-projects-20740` (idle). `copilot` was not observed live; reach it via `agentops run copilot` snapshots instead. Allowed via CLI snapshots: `fcc-claude` (needs `fcc-server`), `opencode` (needs backend auth).
- Never task: `claude` (live but out of scope), `codex-builder` / `agy-reviewer` / `opencode-arch` (retired names — no such live sessions).
- Session names are ephemeral — always resolve the exact live name from `intercom_list` before messaging; never message remembered names.

## History (superseded, preserved)

- 2026-09-12 roster: `pi-manager` (lead), `codex-builder` (primary implementer), `opencode-arch` (architecture/secondary impl), `agy-reviewer` (independent reviewer), with an 11-step workflow. Retired 2026-09-13: user restricted collaboration to opencode / free-claude-code / copilot, and established Pi as sole writer with snapshot-based reviews. Original text preserved in git history.
