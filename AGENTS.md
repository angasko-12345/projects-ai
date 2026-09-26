# Repository instructions (compatibility entrypoint)

The canonical repository instructions are [.agents/AGENTS.md](.agents/AGENTS.md).

This root file exists for tools that only discover `AGENTS.md` at the repository root. It is a compatibility shim, not an independent source of truth. Read the canonical file before substantial work.

Minimum rules while loading the canonical file:

- Work only within the assigned task and repository scope.
- Either Pi or Oh-My-Pi is the sole writer in a dirty tree; reviewers are read-only.
- This repository has **two** products. Read `agentops/AGENTS.md` or
  `universal-game-agent/AGENTS.md` for the one you are changing; each has its own test
  command and they are not interchangeable.
- Keep SQLite migrations additive and use explicit column names.
- Do not persist prompts, secrets, credentials, tokens, or private keys.
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
