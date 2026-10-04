# AgentOps repository instructions

This is the canonical repository instruction file. The root `AGENTS.md` is only a compatibility entrypoint for tools that discover instructions at the repository root.

## Project identity and scope

- This repository is a container for three independent Python products plus the process material agents use to work on them. The products do not depend on each other, and a change in one is not a change in the other.
  - `agentops/` — a local-first orchestrator for installed coding-agent CLIs. It plans, implements, verifies, reviews, persists, and merges work in isolated Git worktrees. See `agentops/AGENTS.md`.
  - `universal-game-agent/` — a reinforcement-learning agent that plays Pong, including a Windows-only path that plays a real external game window through screen capture and real keyboard input. See `universal-game-agent/AGENTS.md`.
  - `small-projects/mini-llm/` — a small GPT-style causal language model built from scratch with PyTorch. See `small-projects/mini-llm/AGENTS.md`.

## Canonical locations

- Repository instructions: `.agents/AGENTS.md` (this file — universal contract)
- AgentOps product instructions: `agentops/AGENTS.md`
- universal-game-agent product instructions: `universal-game-agent/AGENTS.md`
- Pi-specific instructions: `.agents/pi_AGENTS.md`
- Oh My Pi instructions: `.agents/ohmypiagents.md`
- Team and collaboration policy: `.agents/team.md`
- Canonical memory: `.agents/memory/`
- Live bug ledger: `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` §0
- Live task queue: `.agents/pending_tasks.md`
- Agent-scoped memory: `.agents/memory/opencode/`, `.agents/memory/oh-my-pi/`, `.agents/memory/cline/` — runbooks and session records only; each has a `README.md` describing its scope
- Plans and outputs: `.agents/plans/` and `.agents/outputs/`
- Agent skills: `.agents/skills/`
- Root `AGENTS.md` and `pi_AGENTS.md` are compatibility shims and must not be treated as independent sources of truth.

Read the local `AGENTS.md` for whichever product the task touches, in addition to this file.

## Startup checklist

Before substantial work:

1. Read `.agents/AGENTS.md`, `.agents/pi_AGENTS.md` when applicable, `.agents/team.md`, `tasks/task.md`, and `tasks/after-task.md`.
2. Read the relevant files under `.agents/memory/`, especially `project.md`, `architecture.md`, `roadmap.md`, `decisions.md`, and `lessons.md`.
3. Inspect current source, tests, `git status`, and the current diff. Current source, tests, and explicit user instructions outrank memory.
4. Back up the dirty tree before substantial work, including relevant untracked files.
5. Identify which product the task touches, read that product's `AGENTS.md`, and use its test command. There is no single repository-wide test command; the products differ.
6. If using Intercom, resolve the exact live session name before messaging. A failed delivery is a disconnect signal.

## Commands

There is no single repository-wide test command. Each product has its own, and each runs
from its own directory rather than from the repository root. Read the local
`AGENTS.md` before running anything. The baselines below are the current verified values
as of 2026-10-03 (`agentops/` after ROOT-034 DBG-08, `universal-game-agent/` after `59f5a1b`);
they are dated observations, not contracts, and tests
get added, so run the suite for current truth.

| Product | Test command | Baseline 2026-10-03 |
|---|---|---|
| `agentops/` | `cd agentops` then `python -m unittest discover -s tests` | 444 tests, 4 skipped by environment, OK |
| `universal-game-agent/` | `cd universal-game-agent` then `python -m unittest discover -s tests` | 349 tests, 1 skip, OK |
| `small-projects/mini-llm/` | `cd small-projects/mini-llm` then `python -m unittest discover -s tests` | 98 run, 1 skip, OK (2026-10-04, after restoring the tracked `data/tokenizer.json`) |

Treat any new failure or skip as attributable to the current work until proven otherwise,
and do not rely on remembered counts — run the suite. An empty, interrupted, all-skipped,
or unknown run is never a pass. Do not invent lint, formatter, type-check, or coverage
commands; no product configures one.

CLI entry points are also per-product and are documented in the local files.

## Non-negotiable conventions

- Either Pi or Oh-My-Pi is the sole writer in a dirty tree. Never run two writers concurrently in the same tree, and do not make uncoordinated edits to files another session owns. Reviewers work read-only in `/tmp/agentops-review-*` snapshots and never edit the repository directly.
- Allowed review collaborators are OpenCode, free-claude-code (`fcc-claude`), Copilot, Antigravity, and Oh-My-Pi. Do not task Codex or Claude as review collaborators.
- Antigravity may be tasked as a reviewer only while its quota has remaining capacity. If its quota is fully used up, do not task it: pick another allowed reviewer, or proceed without a review and say so in the report.
- Do not persist raw prompts, secrets, credentials, tokens, private keys, or sensitive environment values in any product, log, artifact, or memory file. Reuse existing redaction and prompt-hash mechanisms.
- Write a regression test before fixing a bug, and follow the pattern the product's local `AGENTS.md` names.
- Keep deterministic classification, parsing, and verification logic in leaf modules without SQLite, subprocess, or GUI I/O.
- On Windows, use the shared no-console-window helpers for subprocesses and avoid raw Unicode writes that can fail in legacy console encodings. (agentops)
- Normalize paths to `as_posix()` when crossing a UI or persistence boundary. (agentops)
- Agent-reported success is never verification evidence. A task is verified by a passed verification command or a passed verification report.
- Do not reinstall or reconfigure Agent Intercom and do not change its scope.
- Leave externally rewritten files such as `tasks/task-ignorethis.md` alone. Do not stage unrelated changes.
- Product-specific rules — migration and test-file conventions, GUI threading, packaging, and anything tagged `(agentops)` — are stated in each product's local `AGENTS.md` and apply only to that product.

## Product architecture and packaging

Architecture maps, data flow, and packaging rules are product-specific and are documented
in each product's local `AGENTS.md`:

- `agentops/AGENTS.md` — entry points, orchestration, agent selection, execution,
  verification, failure handling, persistence, Git integration, observability, the leaf-module
  rule, the task-to-merge data flow, and the Windows PyInstaller packaging and
  archive-inspection procedure.
- `universal-game-agent/AGENTS.md` — the deliberate layer dependency direction, the
  Gymnasium toy path versus the Windows-only external path, configuration and checkpoint
  handling, and the CLI entry points.

Read the relevant local file before changing code in a product. Do not add product
architecture back into this file.

<!-- antislop:start -->
## antislop

For UI, copy, people, mobile layout, or code comments work, load the antislop skill for the
task. These skills live in `.agents/skills/` and are tracked in git:

- Core filter, always on: `antislop`
- UI / visual: `antislop-ui`
- Copy & text: `antislop-copywriting`
- People: `antislop-human`
- Mobile / responsive: `antislop-layoutmobile`
- Code comments: `antislop-code`

Before starting, ask the user when antislop applies: during the work, or after it is done.
<!-- antislop:end -->

## Memory and completion

After substantial work, update the relevant `.agents/memory/` files with concise, verifiable facts. Preserve append-only history in `decisions.md` and `lessons.md`. Update plans, outputs, and task records only when the task procedure calls for it. Do not duplicate transcripts or store secrets.

## Memory hierarchy

- The live bug ledger is `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` §0.
  Read a root's status there before acting on any bug; §9 of that file is SUPERSEDED.
  Current state (2026-10-03): **zero ACTIVE roots** across all 38 canonical roots — 22
  FIXED, 1 PARTIALLY FIXED (ROOT-034), 2 CONTRACT GAP / ACCEPTED DEBT (ROOT-011,
  ROOT-026), 1 HELD (ROOT-033), 12 DISPROVEN. This file deliberately does not restate
  per-root statuses; the ledger is the only place that list lives. Do not treat ROOT-015
  through ROOT-025 or ROOT-028 as bugs — they are DISPROVEN and must not be revived.
- The live task queue is `.agents/pending_tasks.md`. It holds only unfinished work.
- Historical audit reports remain evidence, not current instructions. Never revise them in
  place and never treat one as a work queue.
- Agent-scoped memory never overrides canonical memory. When `.agents/memory/cline/`,
  `.agents/memory/oh-my-pi/`, or `.agents/memory/opencode/` disagrees with a canonical
  `.agents/memory/*.md` file, the canonical file wins and the agent-scoped file needs
  correcting.
