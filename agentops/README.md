# AgentOps

AgentOps is a local-first orchestrator for installed coding-agent CLIs. It detects tools at runtime, persists workflows in SQLite, uses isolated Git worktrees, and verifies changes with allowlisted checks. No hosted API keys are required.

```powershell
cd C:\code\my-repo
python -m agentops init      # detect the stack, write .agentops\config.yaml
python -m agentops doctor    # check agents, prompt transport, config, verification, git
python -m agentops agents
python -m agentops gui
python -m agentops task "Add a small feature to the current project"
python -m agentops status
python -m agentops logs
```

## Commands

- `agentops init [--cwd <repo>] [--force] [--print]` detects the repository's stack from evidence (pytest or unittest layout, `package.json` scripts with npm/pnpm/yarn/bun, `go.mod`, `Cargo.toml`) and writes `<repo>/.agentops/config.yaml` with verification checks that run in that repository. Installed agent CLIs are moved to the front of each role preference, and review prefers a different CLI than implementation when two are installed. It never overwrites an existing config without `--force`. When nothing is detectable it writes a `TODO-configure-verification` check that fails if run and that `doctor` reports as blocking, never a vacuous pass. On Windows, npm-style tools are written as `npm.cmd` because checks run without a shell.
- `agentops doctor [--cwd <repo>] [--json] [--probe]` is read-only. It reports Python and Git, repository state (has a commit, on a branch, clean base), which config won and whether it validates, each agent's resolved path, executable kind (`exe`/`cmd`/`script`) and the prompt transport it will get (or why a `.cmd` shim would be refused), whether implementation and review each have a usable agent, and whether every default verification check is runnable here (spawnable without a shell, `python -m` module importable, unittest start directory and npm script present, no placeholder). `--probe` also runs each agent's version command. Exit code 1 on any blocker; `--json` emits schema `agentops.doctor/v1`.
- `agentops agents` detects configured local CLIs.
- `agentops run <agent> "<prompt>"` runs one configured local CLI and saves stdout, stderr, metadata, duration, and exit status.
- `agentops task "<description>" --cwd <repo>` creates a plan → implementation → verification → review workflow in a Git worktree. A failed verification creates debugging, re-verification, and final-review tasks.
- `agentops workflow <file>` reads a JSON-valid YAML file with a `description` field and runs the same workflow.
- `agentops status` displays the latest persisted task state, including its agent runs; `agentops logs [--task ID]` lists log artifacts.
- `agentops runs [--workflow ID] [--task ID] [--status STATUS] [--limit N] [--offset N]` inspects persisted agent-run lifecycle records.
- `agentops verify [--workflow ID] [--run ID] [--task ID] [--limit N] [--offset N]` inspects persisted verification runs, reports, and checks.
- `agentops failures [--workflow ID] [--task ID] [--category CAT] [--limit N] [--offset N]` inspects persisted failure records with recommended repair actions.
- `agentops recover` marks agent runs, verification runs, and tasks stranded by a restart as interrupted failures (never success without evidence); worktrees are preserved.
- `agentops gui` or `agentops-gui` launches the PySide6 desktop client (requires the `desktop` extra).

## Configuration

Config precedence: `--config <file>` > `<repo>/.agentops/config.yaml` (written by `agentops init`; `.agentops/` is kept out of Git via `.git/info/exclude`) > user config (`%APPDATA%\AgentOps\agents.yaml` on Windows, `$XDG_CONFIG_HOME/agentops/agents.yaml` or `~/.config/agentops/agents.yaml` elsewhere) > the bundled defaults. `agentops doctor` prints which one won. The desktop client still loads only an explicitly selected file or the bundled defaults.

The bundled defaults are repository-neutral: one required check, `python -m unittest discover -s tests` at the repository root, and `systemd_run_enabled: false`. (Before this change the bundled profile ran AgentOps' own tests from `working_directory: agentops`, so verification could never pass on any other repository.) The previous personal fleet configuration, including the AgentOps self-checks and systemd isolation, is kept verbatim as `examples/hacir-fleet.yaml`; copy it to your user config or pass it with `--config`.

`agents/agents.yaml` includes OpenCode, Codex, Pi, Claude, `fcc-claude`, Copilot, and Antigravity. Unavailable commands are skipped automatically, and role preferences fall back to another compatible installed CLI. An agent may also declare an optional `model` string, which is recorded on its `AgentRun` when available. Optional `display_name`, `priority`, `metadata`, `version_command`, structured-output, cancellation, timeout, and interactive/non-interactive support fields may also be declared. The bundled file is JSON-valid YAML so it works without dependencies; install `agentops[yaml]` for conventional YAML formatting.

Unattended Codex runs use `codex exec --sandbox workspace-write`, so they cannot escalate: tasks that need network access or writes outside the workspace fail rather than prompting. To allow that for your own setup, add `--approve-for-me` to the Codex `args` in your `agents.yaml`; it routes approval requests through automatic review.

### Prompt transport (Windows `.cmd` shims)

Prompts are multi-line. On Windows, `shutil.which()` often resolves an npm-installed CLI (OpenCode, Codex, Copilot, Kilo) to a `.cmd` shim, and cmd.exe ends the forwarded command line at the first newline and re-parses `" & | < > ^ %`, so a prompt passed as an argument would silently arrive truncated. Each agent therefore has `prompt_transport`: `auto` (default), `argv`, `stdin`, or `file`.

- `auto` on POSIX, or for a native `.exe`: the prompt is one argv element, exactly as before.
- `auto` on Windows for a `.cmd`/`.bat`: AgentOps first bypasses the shim (the native `opencode.exe` npm bundles next to `opencode.cmd`, or the `node.exe` + entry script a standard npm shim forwards to). If that fails it passes the prompt through the shim only when it contains no newline and no cmd metacharacter; otherwise it uses stdin if the agent declares `stdin_args`, or a prompt file if `args` has a `{prompt_file}` placeholder. If none apply, the run is **refused** with an environment failure naming the agent, the resolved path, and the fix. Prompts are never truncated silently and never go through `shell=True`.
- `stdin`: the prompt is written to the child's stdin as UTF-8, then stdin is closed (EOF). `stdin_args` replace `args` for that run (for example `["exec", "-"]`). Setting `stdin_args` also declares that the CLI reads stdin, so set it only for a CLI you have checked. Children using argv or file transport still get `stdin=DEVNULL` (the issue #4 no-hang fix).
- `file`: the prompt is written to a private file under `.agentops/logs/prompts/`, its path replaces `{prompt_file}`, and the file is deleted after the run.

The chosen transport, executable kind (`exe`/`cmd`/`script`), and how the executable was resolved are recorded in the AgentRun's `command_metadata`; the prompt itself is not persisted. The Windows behaviour is covered by simulated tests on Linux and a `windows-latest` CI job using a fake `.cmd` agent; it has not yet been confirmed against real npm-installed CLIs.

Only command templates configured in this file are executed. Agent output is treated as captured text, never as shell input. Verification commands are likewise explicitly configured under `verification.commands` (or named `verification.profiles`) and run without a shell. The runner passes a reduced process environment to avoid forwarding common secret variables.

Verification profiles declare named checks with a class (`tests`, `lint`, `formatting`, `type_checking`, `build`, or `custom`), command, optional working directory inside the operation directory, per-check timeout, required/optional flag, and sequential/parallel policy. Profiles also declare fail-fast or continue-on-failure behavior, check concurrency, and a default timeout. An agent reporting success never marks a task verified; only a passed verification report does.

## Desktop client

The Windows desktop client is built with PySide6 (`pip install "agentops[desktop]"`); the orchestration core stays standard-library only. The client is a multi-view shell — Dashboard, Tasks, Workflows, Agents, Runs, Verification, Failures, Worktrees, Artifacts, and Settings — providing repository and configuration selection, agent detection, direct agent runs, high-level task workflows, live workflow/task status, workflow history with task, agent-run, and verification inspection, a safe log browser, preserved-worktree inspection/cleanup/merge-retry, cancellation, a Ctrl+K command palette, an interrupted-work recovery banner, keyboard shortcuts (Ctrl+K/N/R/B, F5, Ctrl+1..0), system-tray show/hide with live status, and graceful error reporting. Controller reads run on a worker pool and results are delivered back as Qt signals on the GUI thread, while execution, Git isolation, persistence, and verification use the same service modules as the CLI.

```powershell
python -m agentops gui
# or, after installing the project:
agentops-gui
```

## Windows executable

Prerequisites:

- Windows 10/11, 64-bit
- Python 3.11 or later with PySide6 (`pip install ".[desktop]"`)
- This repository checked out with Git

Build a reproducible executable:

```powershell
python -m pip install ".[windows]"
python scripts/build_windows_exe.py
```

The build creates:

- `dist/AgentOps.exe`

The executable bundles the AgentOps package, the Qt runtime, SQLite support, and the default `agents/agents.yaml` configuration. Double-click `AgentOps.exe` to launch the GUI. A custom configuration can be selected inside the client. Target-repository state remains under `<target-repo>/.agentops/`; successful workflows merge from an isolated worktree, while cancelled, failed, or conflicted workflows preserve their worktree for inspection. Preserved worktrees can be inspected, cleaned up, or re-merged from the Worktrees tab; dirty worktrees are never removed silently, and unmerged branches are preserved unless explicitly force-deleted.

## State and Git

State and logs are stored under `<target-repo>/.agentops/` and ignored by Git. Successful agent changes are committed in an isolated `agentops/*` worktree branch, reviewed and verified there, then merged. Failed runs and merge conflicts preserve their worktree; merge conflicts also create a persistent debugging task for manual or agent-assisted resolution.

## Agent runs

Every subprocess invocation of an installed coding-agent CLI creates a persistent `AgentRun` record. Runs move deterministically through `pending`, `starting`, `running`, and one terminal state: `completed`, `failed`, `cancelled`, `timed_out`, or `terminated`. Retry and repair executions link to their parent run, while task retries preserve their attempt numbers.

AgentRun metadata includes the resolved executable, role, optional configured model, timing, exit code, command metadata, working directory/worktree, log references, changed files/diff statistics when available, failure classification, and structured results when available. Raw prompts and sensitive environment values are not persisted; logs are redacted and execution metadata stores only a prompt hash/length. Database changes are additive, so existing state files migrate without rewriting workflow/task history.

## Agent profiles and routing

The registry exposes capability-rich `AgentProfile` records containing identifier, display name, executable path, detected version, availability, roles, capabilities, structured-output support, cancellation/timeout support, interactive/non-interactive support, configured priority, and optional metadata. Workflows use a deterministic `AgentRouter` by default: explicit role, availability, and required-capability constraints gate eligibility, while explicit user preferences, inferred task fit, historical success, configured priority, and a stable identifier tie-breaker determine ranking. Each decision is persisted as a `routing.decision` event with the selected agent, executed agent, score, alternatives, reasons, rejected candidates, and constraints. Set `runtime.routing_enabled` (or `runtime.routing.enabled`) to `false` to preserve the previous static preference/fallback selection behavior. The engine additionally derives small bounded repository characteristics for the task's working directory (`repo_inspection.py`: dominant languages, root manifests, detected test runner, size category; names-only scan with a capped cache, never opening `.env` or credential files) and passes them as desired-capability hints — a signal, not a gate; selection degrades to empty characteristics if inspection fails.

### Headroom gate and usage-limit failover (opt-in)

Both are off unless the config has the section; the bundled `agents.yaml` has neither, so routing and failure classification are unchanged by default. Both act through the router, so they need routing enabled (the default).

`headroom` reads the versioned JSON that `ai-token-tracker` writes (`python -m token_tracker headroom --json`, schema `ai-token-tracker/headroom` version 1). AgentOps does not import the tracker; the JSON document is the contract. Give exactly one of `file` (what `--out` wrote) or `command` (an argv list, run without a shell, output cached for `cache_seconds`), and map each agent to the tracker provider it draws on:

```json
"headroom": {
  "file": "%LOCALAPPDATA%/ai-token-tracker/headroom.json",
  "max_age_seconds": 900,
  "reject_at_percent": 95,
  "deprioritize_at_percent": 80,
  "unknown_policy": "deprioritize",
  "agents": {"codex": "codex", "claude": {"provider": "claude", "windows": ["5h", "weekly"]}}
}
```

- A mapped agent with a window that is `exhausted`, reported as reached, or at/above `reject_at_percent` is rejected. The reason lands in the `routing.decision` event's `rejected_candidates`, for example `headroom: codex weekly 100% used (exhausted), resets 2026-10-27 05:40 UTC`. A stale high reading still rejects (usage only rises until the reset); an expired one does not.
- At/above `deprioritize_at_percent` (set `null` to turn off), the agent ranks below every agent that is not deprioritized, preferences included, but still runs if nothing else is eligible.
- No trustworthy reading for a mapped agent (provider `no_data`/`not_configured`/`error`, a stale or expired window): `unknown_policy` decides, `allow`, `deprioritize` (default) or `reject`.
- If the document is missing, unreadable, not JSON, an unknown `schema`/`schema_version`, or older than `max_age_seconds` by its `generated_at`, the gate does nothing: routing is exactly as without it. Each routing decision's constraints say so, and a `headroom-unavailable` event is logged once per distinct cause.
- Agents not listed under `agents` are never affected. If every candidate is rejected, the task fails with an environment failure that lists each rejection.

`usage_limits` adds a deterministic `USAGE_LIMIT` failure category. When a failed agent run's output tail (last 4000 characters) contains a known limit message (`usage limit`, `rate limit`, `too many requests`, `quota exceeded`, `resource_exhausted`, ...; see `DEFAULT_USAGE_LIMIT_PATTERNS` in `failure.py`) or one of your patterns (case-insensitive substrings), the failure is `USAGE_LIMIT`, the agent gets a cooldown stored in the state database (survives a restart), and the bounded retry goes to the next eligible agent. The cooldown lasts as long as the message says ("try again in 20 minutes", `Retry-After: 30`), else `default_cooldown_seconds`, capped by `max_cooldown_seconds`. Absolute reset clock times are not parsed (their zone is ambiguous). Failover needs `runtime.max_attempts` of at least 2.

```json
"usage_limits": {
  "default_cooldown_seconds": 3600,
  "max_cooldown_seconds": 86400,
  "use_default_patterns": true,
  "patterns": [],
  "agents": {"claude": ["Claude AI usage limit reached"]}
}
```

## Process runtime

`agentops/runtime.py` is the single shared process layer for agent runs, kernel verification checks, and legacy verification commands. It owns the reduced process environment, one cross-platform spawn policy (Windows `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, POSIX `start_new_session=True`), cancellation-aware output collection, timeouts, and process-group termination with bounded cleanup. Custom process factories keep their existing spawn behavior and are cleaned up with direct kills rather than process-group signals. Cooperative cancellation, timeouts, and external task cancellation all terminate the child before reporting; cancellation never strands a running process.

## Verification Kernel

Verification is a deterministic kernel over explicitly configured profiles. Each check records its class, command, contained working directory, timeout, required/optional flag, execution policy, lifecycle status, exit code, timing, stdout/stderr artifact references, and failure reason. Profiles support sequential checks, safe parallel independent checks, fail-fast and continue-on-failure modes, per-check timeouts, and cancellation.

A verification report totals checks, passed, failed, skipped, and required failures, with an overall `passed`, `failed`, `cancelled`, or `timed_out` status. Agent/process success, verification success, and overall workflow success remain distinct: implementation tasks can pass while remaining unverified, and custom workflows are READY only with passed verification evidence.

## Failure, Repair, and Recovery Kernel

Failures are first-class records with source, category (16 deterministic categories covering agent, process, timeout, cancellation, test/lint/typecheck/build/verification, environment, dependency, git-conflict, dirty-worktree, policy, review-rejection, and unknown, plus an opt-in `USAGE_LIMIT` category, see above), severity, retryable/repairable flags, evidence, primary error, related agent and verification runs, recommended action, and timestamps. Classification is deterministic string matching plus explicit flags — no LLM is used for basic error classification.

Repair decisions are `retry_same_agent`, `retry_different_agent`, `repair_implementation`, `rerun_verification`, `request_approval`, or `stop`. Retries are bounded by policy (`runtime.max_attempts`, `runtime.max_repair_cycles`, exponential backoff with `backoff_base_seconds`/`backoff_max_seconds`/`backoff_factor`, cancellation-aware), inherit previous attempt context plus verification evidence, and every retry/repair creates a distinct `AgentRun` linked to its parent. Policy violations and merge conflicts request approval and preserve the worktree instead of retrying blindly.

On restart, `agentops recover` (also run via the GUI) detects incomplete agent executions, verifications, reviews, worktree creations, Git operations, and merges, and classifies them into the matching recovery states. An unknown or interrupted state is never converted into success without evidence: stranded tasks become failed with preserved evidence.
