# Pi Agent Instructions

This file applies when AgentOps selects the `pi` agent. It supplements the repository-level rules in `AGENTS.md`; explicit user instructions, current source code, and current tests take precedence over this file and historical memory.
This file also appiles when `pi` is used in creating, modifying, coding, or editing AgentOps.

## 1. Role and scope

- Pi is the lead implementer and the only writer in the dirty tree. Other agents review; they do not edit this repository.
- Work only in the workspace or AgentOps worktree assigned to the task. Do not alter unrelated files, AgentOps configuration, or externally managed task files.
- Treat the task description as the scope. Do not add features, migrations, configuration, or packaging work that the request does not require.
- Do not make concurrent uncoordinated edits to files owned by another session.
- When AgentOps is managing a high-level workflow, leave implementation changes for `finalize.py` to commit and merge. Do not commit, merge, or delete the worktree yourself unless the task explicitly requires direct repository work.

## 2. Startup checklist

Before substantial work:

1. Read `(pi_)AGENTS.md`, `tasks/task.md`, and `tasks/after-task.md` when a task file is present.
2. Read `.agents/team.md` and the relevant files under `.agents/memory/` (especially `project.md`, `architecture.md`, `roadmap.md`, `decisions.md`, and `lessons.md`).
3. Inspect the current source, tests, and `git status`; current code and tests outrank memory.
4. Back up the dirty tree before substantial work, including untracked files that belong to the task.
5. Identify the exact package directory and test command. For this repository, Python commands run from `agentops/`, not the repository root.
6. If using Intercom, resolve the exact live session name with `intercom_list` before sending a message. Never assume a remembered display name is still live.

## 3. Implementation rules

- Keep deterministic domain and kernel logic in leaf modules. Do not put SQLite, subprocess, GUI, or other I/O in modules such as `execution_model.py`, `verification_model.py`, `failure.py`, `agent_result.py`, or `events.py`.
- Preserve constructor injection and existing seams for state, registry, runner, verifier, and workflow dependencies.
- Use explicit argument arrays for subprocesses; never pass untrusted text through a shell.
- Do not use an LLM for basic classification, parsing, verification, or success decisions. Agent-reported success is not verification evidence.
- Do not persist raw prompts, secrets, credentials, tokens, or private keys. Use the existing redaction and prompt-hash mechanisms.
- For SQLite changes, migrate additively: `CREATE TABLE IF NOT EXISTS`, named columns on every migrated-table insert, and `INSERT OR IGNORE` for migration versions. Never rewrite an existing table or rely on positional inserts.
- Add a regression test before fixing a bug. Follow the convention in `tests/test_review_regressions.py`.
- Preserve legacy behavior and compatibility unless the request explicitly changes it. Keep public constructors and persisted fields backward-compatible where practical.
- Keep GUI updates on the Tk thread through `root.after()` and keep long-running work on controller/background threads.
- On Windows, use the shared no-console-window helpers for subprocesses and avoid raw Unicode writes that can fail in legacy console encodings.
- Do not invent lint, formatting, type-check, coverage, or packaging commands when the repository does not configure them.

## 4. Verification

Run commands from `agentops/`:

```powershell
python -m unittest discover -s tests
```

- Run the full suite after implementation changes and after addressing review findings.
- A new failure or skip is yours until proven otherwise. Do not mark a task verified by narrative success, an exit code alone, or an agent's self-report.
- Verification must come from the configured verification command or a passed verification report. Empty, interrupted, unknown, or all-skipped verification does not prove success.
- Run focused tests while developing, but do not substitute them for the full suite at completion.
- If a test exposes a race, migration issue, or platform-specific behavior, add a regression test and preserve the existing test contract.
- Rebuild and archive-inspect `dist/AgentOps.exe` only when packaging-affecting code changes. Use the existing build script and smoke-test startup/shutdown; never commit the generated executable.

## 5. Review and collaboration

Allowed review collaborators are OpenCode, free-claude-code (`fcc-claude`), and Copilot. Do not task Codex, Claude, or Antigravity as review collaborators.

- Reviewers work read-only in `/tmp/agentops-review-*` snapshots through `agentops run`; they never edit the repository directly.
- For Intercom-based review, confirm the reviewer is live with `intercom_list` first. A failed delivery is a disconnect signal: inspect status/logs before retrying.
- Ask reviewers for findings with file and line evidence. Fix valid findings, add regression tests where appropriate, and record false positives with evidence rather than silently ignoring them.
- Pi owns all resulting edits, test runs, and final disposition.

## 6. Persistence, worktrees, and artifacts

- `state.py` owns SQLite with WAL, busy timeout, and its existing locking model. Do not bypass persistence or create a second state owner.
- Preserve failed, cancelled, interrupted, and conflicted worktrees. Never silently merge a dirty or unverified worktree.
- Respect stored worktree provenance and base commit information. Do not assume the current branch or HEAD is the original task base.
- Artifact and event payloads must remain redacted, schema-tolerant, and free of secrets. Preserve existing query, pagination, and legacy-reader behavior.
- Generated runtime state and the packaged executable are not repository deliverables.

## 7. Memory and completion

After substantial work:

- Update the relevant `.agents/memory/` files with concise, verifiable facts; preserve append-only history in `decisions.md` and `lessons.md`.
- Update `.agents/roadmap.md`, plans, outputs, and `tasks/task.md` when the task procedure calls for them.
- Do not duplicate transcripts or store secrets in memory.
- Follow `tasks/after-task.md` for the final test, review, packaging, commit, push, and reporting steps.
- Leave externally rewritten files such as `tasks/task-ignorethis.md` alone and never stage unrelated changes.
- Report what changed, commands run, test results, review disposition, and any residual risk. Keep the chat summary short; detailed history belongs in `.agents/`.

## 8. AgentResult response contract

When useful, end the response with a JSON `AgentResult` object. Plain-text summaries remain valid, but any reported test counts must reflect commands actually run.

```json
{
  "schema_version": 1,
  "status": "success|failure|partial|unknown",
  "summary": "Concise outcome and verification result.",
  "files_changed": [],
  "tests_run": 0,
  "tests_passed": 0,
  "tests_failed": 0
}
```

Use `unknown` or `failure` when evidence is incomplete. Do not claim `success` solely because the agent process exited successfully.
