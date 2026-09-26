# Copilot instructions for `projects-ai`

## Repository focus

This repository contains two independent products, plus the process material agents use
to work on them:

- `agentops/` — a local-first orchestrator for installed coding-agent CLIs.
- `universal-game-agent/` — a reinforcement-learning agent that plays Pong, including a
  Windows-only path that plays a real external game window through screen capture and
  real keyboard input.

Neither product depends on the other. The rest of this file covers `agentops/`, whose
architecture is the more intricate of the two. For work in `universal-game-agent/`, read
`universal-game-agent/AGENTS.md` instead.

Canonical agent instructions are `.agents/AGENTS.md` (universal contract) with
per-product files at `agentops/AGENTS.md` and `universal-game-agent/AGENTS.md`. The root
also contains task records under `tasks/` and agent memory under `.agents/`. Read
`.agents/team.md` and the relevant `.agents/memory/` files when a task touches the
orchestration workflow or shared project decisions; current source, tests, and explicit
user instructions take precedence over historical memory.

## Build, test, and verification commands

There is no single repository-wide test command; the two products differ. Run commands
from the product's own directory, never from the repository root.

| Product | Test command | Runtime |
|---|---|---|
| `agentops/` | `cd agentops` then `python -m unittest discover -s tests` | Python 3.11+, no required third-party runtime dependencies |
| `universal-game-agent/` | `cd universal-game-agent` then `python -m unittest discover -s tests` | requires `torch`, `gymnasium`, `numpy`, `pyyaml`, `mss` |

The sections below apply to `agentops/`. Commands there run in `agentops/`:

```powershell
Set-Location agentops

# Run the complete test suite
python -m unittest discover -s tests

# Run one test module
python -m unittest tests.test_workflow

# Run one test class or method
python -m unittest tests.test_workflow.WorkflowTests
python -m unittest tests.test_workflow.WorkflowTests.test_standard_workflow_runs_to_ready
```

The configured verification command is the same unittest discovery command.
There is no repository-configured lint, formatter, type checker, or coverage
command; do not invent one as part of normal validation.

For the Windows desktop executable, install the optional packaging dependency
and run the existing build script from `agentops/`:

```powershell
python -m pip install ".[windows]"
python scripts/build_windows_exe.py
```

This produces `agentops/dist/AgentOps.exe` from `AgentOps.spec`. Packaging
changes require checking that the frozen application contains the `agentops`
modules, Tk/SQLite support, and `agents/agents.yaml`, followed by a startup
smoke test.

## Architecture

- `agentops/cli.py` and `agentops/gui.py` are entry points. The CLI invokes
  services directly; the Tkinter desktop client uses
  `gui_controller.py` as its threaded facade and keeps long-running work off
  the Tk event loop.
- `workflow.py` is the orchestration layer. The standard workflow is
  plan → implementation → verification → review, while custom workflows are
  dependency-checked DAGs. Tasks are persisted in SQLite through `state.py`.
- Agent execution is separated into `registry.py` (PATH discovery and role
  selection), `agent_adapter.py` (agent-specific command/capability behavior),
  and `runner.py` (async subprocess lifecycle, cancellation, timeouts,
  environment filtering, logs, and `AgentRun` persistence).
- Verification is evidence-driven. `verification_model.py` contains the
  no-I/O domain types; `verification_kernel.py` executes configured profiles
  with sequential or safe parallel checks, fail-fast or continue-on-failure
  behavior, timeouts, and cancellation. `verification.py` preserves the
  legacy allowlisted command path.
- `failure.py` classifies failures deterministically and supplies bounded
  retry/repair/recovery decisions. `execution_model.py` is the authoritative
  success/transition model: process success, agent success, verification,
  review, merge, and workflow readiness are separate gates.
- Agent changes run in isolated `agentops/<slug>-<random>` Git worktrees.
  `finalize.py` centralizes commit/merge behavior. Dirty bases, changed base
  commits, and merge conflicts are refused or preserved rather than silently
  merged; conflicts create a persistent debugging task.
- `state.py` owns SQLite persistence and additive schema migrations for
  workflows, tasks, agent runs, verification runs/checks/reports, failures,
  events, artifacts, and worktree provenance. Logs and runtime state live
  under the target repository's `.agentops/` directory.

## Repository-specific conventions

- Keep domain/model and kernel modules as leaves: pure data validation and
  deterministic rules belong in modules such as `tasks.py`,
  `verification_model.py`, `execution_model.py`, `failure.py`, and
  `agent_result.py`; they should not perform SQLite, subprocess, or GUI I/O.
- Use constructor injection for state, registry, runner, verifier, and workflow
  dependencies. Existing tests rely heavily on fake runners, mocked registries,
  in-memory SQLite, and `unittest`; preserve those seams instead of introducing
  global services.
- Spawn configured commands with explicit argument arrays, never a shell.
  Agent configuration is allowlisted in `agents/agents.yaml`; prompts are
  substituted into `{prompt}` arguments, process environments are reduced by
  the runner, and sensitive prompt/environment data must not be persisted.
- Treat verification output as the source of truth. An agent reporting success
  does not verify a task; a verification task needs a passed report and
  evidence. Do not turn interrupted, unknown, empty, or all-skipped work into
  success.
- Preserve compatibility when changing persistence or public constructors:
  SQLite migrations are additive, legacy records remain readable, and existing
  positional constructor behavior is intentionally maintained in several
  dataclasses.
- Keep presentation boundaries explicit. `StateStore` returns DTOs/plain data
  to the controller and CLI; GUI-facing serialization belongs in
  `gui_controller.py`, not in Tk widgets or raw SQLite row handling.
- On Windows, subprocesses launched by the GUI or frozen executable must use
  the existing no-console-window behavior. Follow the shared spawn helper in
  `runtime.py:102-112` (`spawn_options()`, `CREATE_NO_WINDOW` at `:106-110`),
  which `runner.py` and `verification_kernel.py` consume, rather than adding
  ad-hoc process flags. `agentops/runner.py` has no such helper of its own; it
  delegates to `ProcessRuntime` (`runner.py:24,88,199`).
- The bundled `agents/agents.yaml` is JSON-valid YAML so it works without
  PyYAML. Conventional YAML support is optional via `.[yaml]`; keep the
  strict validation and role/capability selection behavior in `config.py`.
- When changing packaging entry points, preserve `agentops_gui.py` as the
  package-aware PyInstaller launcher. Freezing `agentops/gui.py` directly
  breaks relative imports.
