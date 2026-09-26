# Pi instructions (compatibility entrypoint)

The canonical Pi-specific instructions are [.agents/pi_AGENTS.md](.agents/pi_AGENTS.md).

This root file exists for tools that only discover `pi_AGENTS.md` at the repository root. It is a compatibility shim, not an independent source of truth. Read the canonical file before work involving AgentOps.

Minimum rules while loading the canonical file:

- Pi is a lead implementer; either Pi or Oh-My-Pi acts as sole writer in a dirty tree.
- Reviewers work read-only in `/tmp/agentops-review-*` snapshots. Antigravity may review while its quota has capacity; do not task it once its quota is used up. Never task Codex or Claude.
- Each product has its own test command: `agentops/AGENTS.md` and
  `universal-game-agent/AGENTS.md`. Read the one for the product you are changing.
- Preserve additive SQLite migrations, redact persisted data, and keep GUI updates on the Tk thread.
- Read `.agents/AGENTS.md`, `.agents/team.md`, `tasks/task.md`, and `tasks/after-task.md` before substantial work.
