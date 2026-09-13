# AgentOps

AgentOps is a local-first orchestrator for installed coding-agent CLIs. It detects tools at runtime, persists workflows in SQLite, uses isolated Git worktrees, and verifies changes with allowlisted checks.

```powershell
python -m agentops agents
python -m agentops task "Add a small feature to the current project"
```

`agents/agents.yaml` includes OpenCode, Codex, Pi, Claude, `fcc-claude`, Copilot, and Antigravity. Unavailable commands are skipped automatically.
