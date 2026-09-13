# AgentOps

AgentOps is a local-first orchestrator for installed coding-agent CLIs. It detects tools at runtime, persists workflows in SQLite, uses isolated Git worktrees, and verifies changes with allowlisted checks. No hosted API keys are required.

```powershell
python -m agentops agents
python -m agentops task "Add a small feature to the current project"
python -m agentops status
python -m agentops logs
```

## Commands

- `agentops agents` detects configured local CLIs.
- `agentops run <agent> "<prompt>"` runs one configured local CLI and saves stdout, stderr, metadata, duration, and exit status.
- `agentops task "<description>" --cwd <repo>` creates a plan → implementation → verification → review workflow in a Git worktree. A failed verification creates debugging, re-verification, and final-review tasks.
- `agentops workflow <file>` reads a JSON-valid YAML file with a `description` field and runs the same workflow.
- `agentops status` displays the latest persisted task state; `agentops logs [--task ID]` lists log artifacts.

## Configuration

`agents/agents.yaml` includes OpenCode, Codex, Pi, Claude, `fcc-claude`, Copilot, and Antigravity. Unavailable commands are skipped automatically, and role preferences fall back to another compatible installed CLI. The bundled file is JSON-valid YAML so it works without dependencies; install `agentops[yaml]` for conventional YAML formatting.

Only command templates configured in this file are executed. Agent output is treated as captured text, never as shell input. Verification commands are likewise explicitly configured under `verification.commands` and run without a shell. The runner passes a reduced process environment to avoid forwarding common secret variables.

## State and Git

State and logs are stored under `<target-repo>/.agentops/` and ignored by Git. Successful agent changes are committed in an isolated `agentops/*` worktree branch, reviewed and verified there, then merged. Failed runs and merge conflicts preserve their worktree; merge conflicts also create a persistent debugging task for manual or agent-assisted resolution.
