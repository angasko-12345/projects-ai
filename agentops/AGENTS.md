# AgentOps product instructions

Local instructions for the `agentops/` package. The repository-wide contract lives in
`.agents/AGENTS.md` and outranks this file on process, roles, and collaboration. Where
this file and `.agents/AGENTS.md` disagree on AgentOps product facts, this file is correct.

## Identity

- A local-first orchestrator for installed coding-agent CLIs. It plans, implements,
  verifies, reviews, persists, and merges work in isolated Git worktrees.
- Runtime target: Python 3.11 or newer. Runtime dependencies are standard-library only;
  PyYAML and PyInstaller are optional extras (`.[yaml]`, `.[windows]`).
- The repository root is a container. This package is one of two products here; see
  `universal-game-agent/AGENTS.md` for the other. Neither is a subproject of the other,
  and a change in one is not a change in the other.

## Tests

Run from `agentops/`, never from the repository root:

```bat
cd agentops
python -m unittest discover -s tests
```

- 21 test modules, all `unittest.TestCase`. `pytest` may be used for convenience but is
  not the source of truth and is not configured in the repository.
- The latest recorded baseline is 357 tests total, with 4 environment skips (so 353 passed).
  That observation was recorded at commit `a03e907` on 2026-09-26. Treat any new failure or
  skip as attributable to the current work until proven otherwise. Do not rely on remembered
  counts; run the suite.
- There is no configured lint, formatter, type-check, or coverage command. Do not invent
  one as part of normal validation.
- Tests use in-memory SQLite and `tempfile`; they do not write into `.agentops/`.

## CLI

```bat
python -m agentops <command>
```

Console scripts: `agentops` -> `agentops.cli:main`, `agentops-gui` -> `agentops.gui:main`.

## Persistence

- `agentops/state.py` is the sole SQLite owner. Current additive schema is v7, including
  the `structured_evidence` column on failures (`state.py:1615-1631`). Do not create a
  second state owner.
- `StateStore` owns SQLite with WAL, a busy timeout, and its existing locking model.
- The migration set covers workflows, tasks, agent runs, verification runs/checks/reports,
  failures, events, artifacts, and worktree provenance.
- Migrations are additive only: `CREATE TABLE IF NOT EXISTS`, explicit column names on
  every migrated-table insert, and `INSERT OR IGNORE` for migration versions. Never
  rewrite an existing table and never rely on positional inserts.
- After touching migrations, update the hardcoded `[1..7]` migration-version assertions in
  both `tests/test_events.py:276` and `tests/test_failure_kernel.py:588`. Adding a
  migration means updating both.
- `.agentops/` under this product's repository root is where runtime state and logs live.
  It is generated state, not a repository deliverable.
- Artifact and event payloads stay redacted, schema-tolerant, and free of secrets.
  Reuse the existing redaction and prompt-hash mechanisms; never persist raw prompts,
  credentials, tokens, or private keys.

## Architecture map

- Entry points: `agentops/cli.py`, `agentops/gui.py`, and threaded
  `agentops/gui_controller.py`. `agentops_gui.py` is the package-aware PyInstaller
  launcher; freezing `agentops/gui.py` directly breaks relative imports.
- Orchestration: `agentops/workflow.py` runs the standard plan -> implementation ->
  verification -> review flow and custom dependency-checked DAGs.
- Agent selection: `agentops/registry.py` (PATH discovery, role preference),
  `agentops/agent_adapter.py` (per-agent command and capability behavior), and
  `agentops/routing.py` (deterministic, explainable routing with fallback).
- Execution: `agentops/runner.py` delegates process lifecycle to the shared
  `agentops/runtime.py` layer.
- Verification: `agentops/verification_kernel.py` executes configured profiles with
  sequential or safe-parallel checks, fail-fast or continue-on-failure, timeouts, and
  cancellation. `agentops/verification.py` preserves the legacy allowlisted command path
  and is still imported by the CLI and GUI.
- Failure handling: `agentops/failure.py` provides deterministic classification, bounded
  repair, and recovery decisions.
- Success model: `agentops/execution_model.py` is authoritative. Process success, agent
  success, verification, review, merge, and workflow readiness are separate gates.
- Git integration: `agentops/git.py` and `agentops/finalize.py` manage isolated
  worktrees, commit/merge behavior, provenance, and conflict preservation. Dirty bases,
  changed base commits, and conflicts are refused or preserved, never silently merged.
- Observability: `agentops/events.py` and `agentops/artifacts.py` provide versioned
  timeline and artifact records.
- `tasks.py`, `execution_model.py`, `verification_model.py`, `failure.py`,
  `agent_result.py`, and `events.py` are leaves. They hold pure validation and
  deterministic rules and must not perform SQLite, subprocess, or GUI I/O.

## Data flow

Task or workflow -> isolated `agentops/<slug>-<rand>` worktree -> plan -> implement ->
verify -> review -> `finalize.py` commit/merge. A conflict creates a persistent debugging
task and preserves the worktree. Verification evidence, not agent-reported success, marks
a task verified. Empty, interrupted, unknown, or all-skipped work is never success.

## Conventions

- Every bug fix gets a regression test first, following
  `tests/test_review_regressions.py`.
- Only commands allowlisted in `agents/agents.yaml` may run as agent or verification
  commands. The bundled file is JSON-valid YAML so it works without PyYAML.
- Use explicit argument arrays when spawning configured commands; never pass untrusted
  text through a shell. Prompts are substituted into the `{prompt}` argument of an
  allowlisted command, and the runner reduces the process environment it hands to the
  child.
- Do not use an LLM for basic classification, parsing, verification, or success decisions.
- Preserve constructor injection and the existing seams for state, registry, runner,
  verifier, and workflow dependencies. Tests rely on fake runners, mocked registries, and
  in-memory SQLite; do not introduce global services.
- Keep GUI updates on the Tk thread through `root.after()`; long-running work belongs on
  controller or background threads.
- Keep presentation boundaries explicit: `StateStore` returns DTOs and plain data to the
  controller and CLI. GUI-facing serialization belongs in `gui_controller.py`, not in Tk
  widgets or raw SQLite row handling.
- Normalize paths to `as_posix()` when crossing a UI or persistence boundary.
- Preserve legacy behavior and compatibility unless the request changes it. Several
  dataclasses intentionally maintain positional constructor behavior.
- Do not reinstall or reconfigure Agent Intercom and do not change its scope.

## Windows and packaging

- On Windows, subprocesses launched by the GUI or the frozen executable must use the shared
  no-console-window helper: `agentops/runtime.py:102-112` (`spawn_options()`, with
  `CREATE_NO_WINDOW` at `:106-110`). It is consumed by `runner.py` and
  `verification_kernel.py:19,99,416`; `agent_run.py` and `registry.py` carry their own spawn
  policy. `agentops/runner.py` contains no such helper — it delegates to `ProcessRuntime`
  (`runner.py:24,88,199`). Do not add ad-hoc process flags.
- Avoid raw Unicode writes that can fail in legacy console encodings.
- Packaged executable: `agentops/dist/AgentOps.exe`. It is gitignored; ship it as a
  release asset and never commit it.
- Build from `agentops/` after installing the Windows extras:
  `python scripts/build_windows_exe.py`, driven by `AgentOps.spec`.
- Rebuild and archive-inspect only when packaging-affecting code changes. A successful
  PyInstaller build is not proof of a working bundle: verify the archive contains
  `agentops.*`, Tk, SQLite, and `agents/agents.yaml`, then smoke-test startup and
  shutdown and confirm process exit.
- Stop lingering `AgentOps.exe` processes before rebuilding. A locked executable causes
  rebuild failures.

## Known defects

Recorded so they are not rediscovered as if new. Fix them deliberately, not incidentally.

- `agentops/config.py:62` resolves the default `agents/agents.yaml` by escaping the
  package (`Path(__file__).parent.parent`). `pyproject.toml` packages only `agentops*`, so
  a non-editable wheel install ships with no default config. The frozen executable is
  unaffected because `AgentOps.spec` bundles the file as data.
- `agentops.egg-info/` is regenerable build residue that goes stale; it is not in the
  working tree. It is correctly gitignored via `agentops/.gitignore:7` (`*.egg-info/`) and
  reappears on build. Do not read version or entry-point data from it.
- `tests/test_storage_dtos_worktree_refs.py` is named for a `storage_dtos.py` module that
  no longer exists; the DTO serializers it imports (`serialize_task`, `serialize_workflow`,
  `serialize_worktree_ref`) live in `gui_controller.py`, and `WorktreeRef` lives in
  `git.py`. The name is a fossil.
