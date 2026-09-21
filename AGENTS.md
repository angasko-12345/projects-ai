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

<!-- antislop:start -->
## antislop
For UI, copy, people, mobile layout, or code comments work, load the antislop skill for the task:
- Core filter, always on: `antislop`
- UI / visual: `antislop-ui`
- Copy & text: `antislop-copywriting`
- People: `antislop-human`
- Mobile / responsive: `antislop-layoutmobile`
- Code comments: `antislop-code`
Before starting, ask the user when antislop applies: during the work, or after it is done.
<!-- antislop:end -->
