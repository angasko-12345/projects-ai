# Pi instructions (compatibility entrypoint)

The canonical Pi-specific instructions are [.agents/pi_AGENTS.md](.agents/pi_AGENTS.md).

This root file exists for tools that only discover `pi_AGENTS.md` at the repository root. It is a compatibility shim, not an independent source of truth. Read the canonical file before work involving AgentOps.

Minimum rules while loading the canonical file:

- Pi is the lead implementer and only writer in the dirty AgentOps tree.
- Reviewers work read-only in `/tmp/agentops-review-*` snapshots.
- Run tests from `agentops/` with `python -m unittest discover -s tests`.
- Preserve additive SQLite migrations, redact persisted data, and keep GUI updates on the Tk thread.
- Read `.agents/AGENTS.md`, `.agents/team.md`, `tasks/task.md`, and `tasks/after-task.md` before substantial work.
