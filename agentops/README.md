# AgentOps

AgentOps is a local-first orchestrator for installed coding-agent CLIs. It detects tools at runtime, persists workflows in SQLite, uses isolated Git worktrees, and verifies changes with allowlisted checks. No hosted API keys are required.

```powershell
python -m agentops agents
python -m agentops gui
python -m agentops task "Add a small feature to the current project"
python -m agentops status
python -m agentops logs
```

## Commands

- `agentops agents` detects configured local CLIs.
- `agentops run <agent> "<prompt>"` runs one configured local CLI and saves stdout, stderr, metadata, duration, and exit status.
- `agentops task "<description>" --cwd <repo>` creates a plan → implementation → verification → review workflow in a Git worktree. A failed verification creates debugging, re-verification, and final-review tasks.
- `agentops workflow <file>` reads a JSON-valid YAML file with a `description` field and runs the same workflow.
- `agentops status` displays the latest persisted task state, including its agent runs; `agentops logs [--task ID]` lists log artifacts.
- `agentops runs [--workflow ID] [--task ID] [--status STATUS] [--limit N] [--offset N]` inspects persisted agent-run lifecycle records.
- `agentops verify [--workflow ID] [--run ID] [--task ID] [--limit N] [--offset N]` inspects persisted verification runs, reports, and checks.
- `agentops failures [--workflow ID] [--task ID] [--category CAT] [--limit N] [--offset N]` inspects persisted failure records with recommended repair actions.
- `agentops recover` marks agent runs, verification runs, and tasks stranded by a restart as interrupted failures (never success without evidence); worktrees are preserved.
- `agentops gui` or `agentops-gui` launches the Tkinter desktop client.

## Configuration

`agents/agents.yaml` includes OpenCode, Codex, Pi, Claude, `fcc-claude`, Copilot, and Antigravity. Unavailable commands are skipped automatically, and role preferences fall back to another compatible installed CLI. An agent may also declare an optional `model` string, which is recorded on its `AgentRun` when available. Optional `display_name`, `priority`, `metadata`, `version_command`, structured-output, cancellation, timeout, and interactive/non-interactive support fields may also be declared. The bundled file is JSON-valid YAML so it works without dependencies; install `agentops[yaml]` for conventional YAML formatting.

Only command templates configured in this file are executed. Agent output is treated as captured text, never as shell input. Verification commands are likewise explicitly configured under `verification.commands` (or named `verification.profiles`) and run without a shell. The runner passes a reduced process environment to avoid forwarding common secret variables.

Verification profiles declare named checks with a class (`tests`, `lint`, `formatting`, `type_checking`, `build`, or `custom`), command, optional working directory inside the operation directory, per-check timeout, required/optional flag, and sequential/parallel policy. Profiles also declare fail-fast or continue-on-failure behavior, check concurrency, and a default timeout. An agent reporting success never marks a task verified; only a passed verification report does.

## Desktop client

The Windows desktop client uses only the Python standard library for its interface. It provides repository and configuration selection, agent detection, direct agent runs, high-level task workflows, live workflow/task status, workflow history with task, agent-run, and verification inspection, a safe log browser, preserved-worktree inspection/cleanup/merge-retry, cancellation, and graceful error reporting. Long-running operations run outside the Tk event loop, while execution, Git isolation, persistence, and verification use the same service modules as the CLI.

```powershell
python -m agentops gui
# or, after installing the project:
agentops-gui
```

## Windows executable

Prerequisites:

- Windows 10/11, 64-bit
- Python 3.11 or later with Tkinter
- This repository checked out with Git

Build a reproducible executable:

```powershell
python -m pip install ".[windows]"
python scripts/build_windows_exe.py
```

The build creates:

- `dist/AgentOps.exe`

The executable bundles the AgentOps package, Tk runtime, SQLite support, and the default `agents/agents.yaml` configuration. Double-click `AgentOps.exe` to launch the GUI. A custom configuration can be selected inside the client. Target-repository state remains under `<target-repo>/.agentops/`; successful workflows merge from an isolated worktree, while cancelled, failed, or conflicted workflows preserve their worktree for inspection. Preserved worktrees can be inspected, cleaned up, or re-merged from the Worktrees tab; dirty worktrees are never removed silently, and unmerged branches are preserved unless explicitly force-deleted.

## State and Git

State and logs are stored under `<target-repo>/.agentops/` and ignored by Git. Successful agent changes are committed in an isolated `agentops/*` worktree branch, reviewed and verified there, then merged. Failed runs and merge conflicts preserve their worktree; merge conflicts also create a persistent debugging task for manual or agent-assisted resolution.

## Agent runs

Every subprocess invocation of an installed coding-agent CLI creates a persistent `AgentRun` record. Runs move deterministically through `pending`, `starting`, `running`, and one terminal state: `completed`, `failed`, `cancelled`, `timed_out`, or `terminated`. Retry and repair executions link to their parent run, while task retries preserve their attempt numbers.

AgentRun metadata includes the resolved executable, role, optional configured model, timing, exit code, command metadata, working directory/worktree, log references, changed files/diff statistics when available, failure classification, and structured results when available. Raw prompts and sensitive environment values are not persisted; logs are redacted and execution metadata stores only a prompt hash/length. Database changes are additive, so existing state files migrate without rewriting workflow/task history.

## Agent profiles and routing

The registry exposes capability-rich `AgentProfile` records containing identifier, display name, executable path, detected version, availability, roles, capabilities, structured-output support, cancellation/timeout support, interactive/non-interactive support, configured priority, and optional metadata. Workflows use a deterministic `AgentRouter` by default: explicit role, availability, and required-capability constraints gate eligibility, while explicit user preferences, inferred task fit, historical success, configured priority, and a stable identifier tie-breaker determine ranking. Each decision is persisted as a `routing.decision` event with the selected agent, score, alternatives, reasons, rejected candidates, and constraints. Set `runtime.routing_enabled` (or `runtime.routing.enabled`) to `false` to preserve the previous static preference/fallback selection behavior.

## Verification Kernel

Verification is a deterministic kernel over explicitly configured profiles. Each check records its class, command, contained working directory, timeout, required/optional flag, execution policy, lifecycle status, exit code, timing, stdout/stderr artifact references, and failure reason. Profiles support sequential checks, safe parallel independent checks, fail-fast and continue-on-failure modes, per-check timeouts, and cancellation.

A verification report totals checks, passed, failed, skipped, and required failures, with an overall `passed`, `failed`, `cancelled`, or `timed_out` status. Agent/process success, verification success, and overall workflow success remain distinct: implementation tasks can pass while remaining unverified, and custom workflows are READY only with passed verification evidence.

## Failure, Repair, and Recovery Kernel

Failures are first-class records with source, category (16 deterministic categories covering agent, process, timeout, cancellation, test/lint/typecheck/build/verification, environment, dependency, git-conflict, dirty-worktree, policy, review-rejection, and unknown), severity, retryable/repairable flags, evidence, primary error, related agent and verification runs, recommended action, and timestamps. Classification is deterministic string matching plus explicit flags — no LLM is used for basic error classification.

Repair decisions are `retry_same_agent`, `retry_different_agent`, `repair_implementation`, `rerun_verification`, `request_approval`, or `stop`. Retries are bounded by policy (`runtime.max_attempts`, `runtime.max_repair_cycles`, exponential backoff with `backoff_base_seconds`/`backoff_max_seconds`/`backoff_factor`, cancellation-aware), inherit previous attempt context plus verification evidence, and every retry/repair creates a distinct `AgentRun` linked to its parent. Policy violations and merge conflicts request approval and preserve the worktree instead of retrying blindly.

On restart, `agentops recover` (also run via the GUI) detects incomplete agent executions, verifications, reviews, worktree creations, Git operations, and merges, and classifies them into the matching recovery states. An unknown or interrupted state is never converted into success without evidence: stranded tasks become failed with preserved evidence.
