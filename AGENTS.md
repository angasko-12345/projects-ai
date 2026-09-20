# Repository instructions (compatibility entrypoint)

The canonical repository instructions are [.agents/AGENTS.md](.agents/AGENTS.md).

This root file exists for tools that only discover `AGENTS.md` at the repository root. It is a compatibility shim, not an independent source of truth. Read the canonical file before substantial work.

Minimum rules while loading the canonical file:

- Work only within the assigned task and repository scope.
- Pi is the sole writer in the dirty AgentOps tree; reviewers are read-only.
- Run Python tests from `agentops/` with `python -m unittest discover -s tests`.
- Keep SQLite migrations additive and use explicit column names.
- Do not persist prompts, secrets, credentials, tokens, or private keys.
- Keep GUI updates on the Tk thread and use the shared Windows no-window process helpers.
- Read `.agents/team.md`, `tasks/task.md`, `tasks/after-task.md`, and the relevant `.agents/memory/` files before substantial work.
