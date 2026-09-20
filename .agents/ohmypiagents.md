# Oh My Pi Agent Instructions

This file applies when the `oh-my-pi` agent works in this workspace. It supplements the repository rules in `.agents/AGENTS.md` and `.agents/pi_AGENTS.md`. Current source code, current tests, and explicit user instructions take precedence over this file and historical memory.

Root `AGENTS.md` and `pi_AGENTS.md` are compatibility entrypoints only; do not maintain a separate instruction set there.

## 1. Project identity and scope

- The repository root, `D:\admin\code\projects`, is a container folder. The maintained product is the Python package in `agentops/`.
- AgentOps is a local-first orchestrator for installed coding-agent CLIs. It plans, implements, verifies, reviews, persists, and merges work in isolated Git worktrees.
- The product targets Python 3.11 or newer. Runtime dependencies are standard-library only; PyYAML and PyInstaller are optional.
- Runtime state and logs belong under the target repository's `.agentops/` directory. Generated state and the packaged executable are not repository deliverables.
- Treat the task description as the scope. Do not add features, migrations, configuration, packaging work, or unrelated cleanup that the request does not require.
- Work only in the workspace or the AgentOps worktree assigned to the task. Do not alter externally managed task files or files owned by another session.

## 2. Startup checklist

Before substantial work:

1. Read `.agents/AGENTS.md`, `.agents/pi_AGENTS.md`, `.agents/team.md`, and the relevant files under `.agents/memory/` (especially `project.md`, `architecture.md`, `roadmap.md`, `decisions.md`, and `lessons.md`).
2. Read `tasks/task.md` and `tasks/after-task.md` when those files are present.
3. Inspect the current source, tests, and working tree. Current code and tests outrank memory.
4. Back up the dirty tree before substantial work, including untracked files that belong to the task.
5. Identify the exact package directory and test command. Python commands run from `agentops/`, never from the repository root.
6. If using Agent Intercom, resolve the exact live session name with `intercom_list` before sending a message. Never assume a remembered display name is still live.

## 3. Architecture map

- Entry points: `agentops/cli.py`, `agentops/gui.py`, and the threaded `gui_controller.py` facade.
- Orchestration: `workflow.py` runs the standard plan → implementation → verification → review flow and dependency-checked custom DAGs.
- Agent selection: `registry.py`, `agent_adapter.py`, and `routing.py` provide detection, role preferences, capabilities, fallback, and deterministic explainable routing.
- Execution: `runner.py` delegates process lifecycle behavior to the shared `runtime.py` layer.
- Verification: `verification_kernel.py` executes configured profiles; `verification.py` preserves the legacy allowlisted command path.
- Failure handling: `failure.py` provides deterministic classification, bounded repair, and recovery decisions.
- Persistence: `state.py` is the sole SQLite owner and uses additive migrations.
- Git integration: `git.py` and `finalize.py` manage isolated worktrees, commit/merge behavior, provenance, and conflict preservation.
- Observability: `events.py` and `artifacts.py` provide versioned timeline and artifact records.
- Keep domain/model and kernel modules as leaves: pure validation and deterministic rules belong in modules such as `tasks.py`, `execution_model.py`, `verification_model.py`, `failure.py`, `agent_result.py`, and `events.py`; they must not perform SQLite, subprocess, or GUI I/O.

## 4. Implementation rules

- Preserve constructor injection and existing seams for state, registry, runner, verifier, and workflow dependencies.
- Use explicit argument arrays when spawning configured commands; never pass untrusted text through a shell.
- Only commands allowlisted in `agents/agents.yaml` may run as agent or verification commands. The bundled file is JSON-valid YAML.
- Do not use an LLM for basic classification, parsing, verification, or success decisions. Agent-reported success is not verification evidence.
- Do not persist raw prompts, secrets, credentials, tokens, private keys, or sensitive environment values. Reuse the existing redaction and prompt-hash mechanisms.
- For SQLite changes, migrate additively: use `CREATE TABLE IF NOT EXISTS`, named columns on every migrated-table insert, and `INSERT OR IGNORE` for migration versions. Never rewrite an existing table or rely on positional inserts.
- After touching migrations, update the migration-version assertions in `tests/test_events.py`.
- Preserve legacy behavior and compatibility unless the request explicitly changes it. Keep public constructors and persisted fields backward-compatible where practical.
- Every bug fix gets a regression test first, following `tests/test_review_regressions.py`.
- Keep GUI updates on the Tk thread through `root.after()` and keep long-running work on controller/background threads.
- On Windows, use the shared no-console-window helpers for subprocesses and avoid raw Unicode writes that can fail in legacy console encodings.
- Normalize paths to `as_posix()` when crossing a UI or persistence boundary.
- Do not reinstall or reconfigure Agent Intercom, and do not change its scope.
- Do not invent lint, formatting, type-check, coverage, or packaging commands when the repository does not configure them.

## 5. Verification contract

Run commands from `agentops/`:

```powershell
python -m unittest discover -s tests
```

- Run focused tests while developing, then run the full suite after implementation changes and after addressing review findings.
- A new failure or skip is yours until proven otherwise. Do not rely on remembered test counts.
- Verification must come from the configured verification command or a passed verification report. An agent reporting success does not verify a task.
- Empty, interrupted, unknown, or all-skipped work must not be treated as success.
- If a test exposes a race, migration issue, or platform-specific behavior, add a regression test and preserve the existing test contract.
- Rebuild and archive-inspect `dist/AgentOps.exe` only when packaging-affecting code changes. Use `python scripts/build_windows_exe.py` from `agentops/`, check the bundle for `agentops.*`, Tk, SQLite, and `agents.yaml`, then smoke-test startup/shutdown and verify process exit.
- Before rebuilding on Windows, check for and stop lingering `AgentOps.exe` processes; a locked executable causes rebuild failures.

## 6. Review and collaboration

- Follow the current collaboration policy in `.agents/team.md`.
- Allowed review collaborators are OpenCode, free-claude-code (`fcc-claude`), and Copilot. Do not task Codex, Claude, or Antigravity as review collaborators.
- Reviewers work read-only in `/tmp/agentops-review-*` snapshots through `agentops run`; they never edit the repository directly.
- For Intercom-based review, confirm the reviewer is live with `intercom_list` first. A failed delivery is a disconnect signal: inspect status/logs before retrying.
- Ask reviewers for findings with file and line evidence. Fix valid findings, add regression tests where appropriate, and record false positives with evidence rather than silently ignoring them.
- When AgentOps is managing a high-level workflow, leave implementation changes for `finalize.py` to commit and merge. Do not commit, merge, or delete the worktree yourself unless the task explicitly requires direct repository work.

## 7. Persistence, worktrees, and artifacts

- `StateStore` owns SQLite with WAL, busy timeout, and its existing locking model. Do not bypass persistence or create a second state owner.
- Preserve failed, cancelled, interrupted, dirty, and conflicted worktrees. Never silently merge a dirty or unverified worktree.
- Respect stored worktree provenance and base commit information. Do not assume the current branch or HEAD is the original task base.
- Artifact and event payloads must remain redacted, schema-tolerant, and free of secrets. Preserve existing query, pagination, and legacy-reader behavior.
- Keep presentation boundaries explicit: `StateStore` returns DTOs/plain data to the controller and CLI; GUI-facing serialization belongs in `gui_controller.py`, not in Tk widgets or raw SQLite row handling.

## 8. Memory and completion

After substantial work:

- Update the relevant `.agents/memory/` files with concise, verifiable facts. Preserve append-only history in `decisions.md` and `lessons.md`.
- Update roadmap, plans, outputs, and task records when the task procedure calls for them.
- Do not duplicate transcripts or store secrets in memory.
- Follow `tasks/after-task.md` for the final test, review, packaging, commit, push, and reporting steps.
- Leave externally rewritten files such as `tasks/task-ignorethis.md` alone and never stage unrelated changes.
- Report what changed, commands run, test results, review disposition, and any residual risk. Keep the chat summary short; detailed history belongs in `.agents/`.

## Changelog

- 2026-09-19 — Created `ohmypiagents.md` from the current workspace structure, repository conventions, AgentOps architecture, verification contract, and shared project memory.
