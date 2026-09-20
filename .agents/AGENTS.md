# AgentOps repository instructions

This is the canonical repository instruction file. The root `AGENTS.md` is only a compatibility entrypoint for tools that discover instructions at the repository root.

## Project identity and scope

- AgentOps is a local-first orchestrator for installed coding-agent CLIs. It plans, implements, verifies, reviews, persists, and merges work in isolated Git worktrees.
- The product is the Python package in `agentops/`. The repository root is a container for the package, `.agents/` process material, and task records.
- Runtime target: Python 3.11 or newer. Runtime dependencies are standard-library only; PyYAML and PyInstaller are optional.
- Keep task scope narrow. Do not add unrelated features, migrations, configuration, packaging work, or repository cleanup.

## Canonical locations

- Repository instructions: `.agents/AGENTS.md`
- Pi-specific instructions: `.agents/pi_AGENTS.md`
- Oh My Pi instructions: `.agents/ohmypiagents.md`
- Team and collaboration policy: `.agents/team.md`
- Canonical memory: `.agents/memory/`
- Plans and outputs: `.agents/plans/` and `.agents/outputs/`
- Root `AGENTS.md` and `pi_AGENTS.md` are compatibility shims and must not be treated as independent sources of truth.

## Startup checklist

Before substantial work:

1. Read `.agents/AGENTS.md`, `.agents/pi_AGENTS.md` when applicable, `.agents/team.md`, `tasks/task.md`, and `tasks/after-task.md`.
2. Read the relevant files under `.agents/memory/`, especially `project.md`, `architecture.md`, `roadmap.md`, `decisions.md`, and `lessons.md`.
3. Inspect current source, tests, `git status`, and the current diff. Current source, tests, and explicit user instructions outrank memory.
4. Back up the dirty tree before substantial work, including relevant untracked files.
5. Identify the package directory and test command. Python commands run from `agentops/`, never from the repository root.
6. If using Intercom, resolve the exact live session name before messaging. A failed delivery is a disconnect signal.

## Commands

Run tests from `agentops/`:

```bat
cd agentops
python -m unittest discover -s tests
```

The latest recorded baseline is 357 passing with 4 environment skips. Treat any new failure or skip as attributable to the current work until proven otherwise.

CLI entry point:

```bat
python -m agentops <command>
```

## Non-negotiable conventions

- Pi is the sole writer in the dirty AgentOps tree. Reviewers work read-only in `/tmp/agentops-review-*` snapshots and never edit the repository directly.
- Allowed review collaborators are OpenCode, free-claude-code (`fcc-claude`), and Copilot. Do not task Codex, Claude, or Antigravity as review collaborators.
- SQLite changes are additive only: use `CREATE TABLE IF NOT EXISTS`, explicit column names on migrated-table inserts, and `INSERT OR IGNORE` for migration versions. Never rewrite an existing table or rely on positional inserts.
- After touching migrations, update migration-version assertions in `tests/test_events.py`.
- Every bug fix gets a regression test first, following `tests/test_review_regressions.py`.
- Do not persist raw prompts, secrets, credentials, tokens, private keys, or sensitive environment values. Reuse existing redaction and prompt-hash mechanisms.
- Keep deterministic classification, parsing, and verification logic in leaf modules without SQLite, subprocess, or GUI I/O.
- Keep GUI updates on the Tk thread through `root.after()` and keep long-running work on controller/background threads.
- On Windows, use the shared no-console-window helpers for subprocesses and avoid raw Unicode writes that can fail in legacy console encodings.
- Normalize paths to `as_posix()` when crossing a UI or persistence boundary.
- Do not reinstall or reconfigure Agent Intercom and do not change its scope.
- Leave externally rewritten files such as `tasks/task-ignorethis.md` alone. Do not stage unrelated changes.

## Architecture map

- Entry points: `agentops/cli.py`, `agentops/gui.py`, and threaded `agentops/gui_controller.py`.
- Orchestration: `agentops/workflow.py` runs the standard plan -> implementation -> verification -> review flow and custom dependency DAGs.
- Agent selection: `agentops/registry.py`, `agentops/agent_adapter.py`, and `agentops/routing.py` provide detection, roles, capabilities, deterministic routing, fallback, and explainable decisions.
- Execution: `agentops/runner.py` delegates process lifecycle behavior to the shared `agentops/runtime.py` layer.
- Verification: `agentops/verification_kernel.py` executes configured profiles; `agentops/verification.py` preserves the legacy allowlisted command path.
- Failure handling: `agentops/failure.py` provides deterministic classification, bounded repair, and recovery decisions.
- Persistence: `agentops/state.py` is the sole SQLite owner. The current additive schema is v7, including the `structured_evidence` column on failures.
- Git integration: `agentops/git.py` and `agentops/finalize.py` manage isolated worktrees, commit/merge behavior, provenance, and conflict preservation.
- Observability: `agentops/events.py` and `agentops/artifacts.py` provide versioned timeline and artifact records.
- Domain/model and kernel modules should remain leaves: pure validation and deterministic rules belong in modules such as `tasks.py`, `execution_model.py`, `verification_model.py`, `failure.py`, `agent_result.py`, and `events.py`.

## Data flow

Task or workflow -> isolated `agentops/<slug>-<rand>` worktree -> plan -> implement -> verify -> review -> `finalize.py` commit/merge. A conflict creates a debugging task and preserves the worktree. Verification evidence, not agent-reported success, marks a task verified.

## Windows and packaging

- Packaged executable: `agentops/dist/AgentOps.exe` (gitignored; ship as a release asset, never commit).
- Build from `agentops/` with `python scripts/build_windows_exe.py` after installing the Windows extras.
- Every build must be archive-inspected. A successful PyInstaller build is not proof of a working bundle; verify `agentops.*`, Tk, SQLite, and `agents.yaml`, then smoke-test startup/shutdown and verify process exit.
- Before rebuilding on Windows, stop lingering `AgentOps.exe` processes; a locked executable causes rebuild failures.

## Memory and completion

After substantial work, update the relevant `.agents/memory/` files with concise, verifiable facts. Preserve append-only history in `decisions.md` and `lessons.md`. Update plans, outputs, and task records only when the task procedure calls for it. Do not duplicate transcripts or store secrets.
