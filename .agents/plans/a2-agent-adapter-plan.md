# Plan — A2 AgentAdapter + Capability Model (P1)

> Sharpened 2026-09-15 from the roadmap paragraph into file-level actions. Owner: pi (writer); opencode (contract review, async).
> Variance from roadmap DAG (which prefers A3 before A2): A2 proceeds WITHOUT moving process-spawning — adapters own only what is actually agent-specific today (command shape, role support, capabilities). Env/output-parse/cancellation stay in the runner (generic today) and move in A3. Recorded in decisions.md.

## Where agent-specific knowledge lives today (verified by grep)

1. `runner.build_command` (runner.py:156-168) — SOLE command-construction site: TOCTOU executable resolution + `{prompt}` substitution.
2. `registry.select` (registry.py) — role gate via `config.roles` + preference order.
3. `AgentConfig` fields (command/args/roles/model/timeout/enabled) + `agents/agents.yaml` per-agent data.
4. `workflow.py:398,639` — calls `registry.select` + `runner.build_command`; ZERO direct flag knowledge (criterion already ~met; A2 formalizes it).

## Decisions (D1/D2)

- **D1:** fixed `Capability` enum + free-form extras passthrough. Config accepts any non-empty strings (unknown names warn, kept as extras — forward-compat, typo-visible).
- **D2:** adapter WRAPS `AgentConfig` (overlay). No config replacement, no new execution path.
- **Honesty rule:** baseline capabilities derived ONLY from `roles` (implementation/debugging→coding, architecture→planning, review→review; empty roles→all three, mirroring the existing empty-means-all gate). Nothing else declared without evidence — shipped `agents.yaml` gains NO explicit capabilities. B7 populates them.

## Subtask 1 — `AgentConfig.capabilities` (additive config field)
Status: pending

- `AgentConfig` gains `capabilities: tuple[str, ...] = ()` (appended field, defaults preserve all current construction — positional construction in tests keeps working).
- `load_config` parses optional `capabilities` (must be a list of non-empty strings; unknown names `warnings.warn`, kept as extras).
- Tests first: valid list, invalid type rejected, unknown-name warn + passthrough, default empty. Run `test_config_registry`.

## Subtask 2 — Leaf `agentops/agent_adapter.py` + contract tests
Status: pending

- `Capability` StrEnum (coding, planning, review, structured_output, streaming, mcp, model_selection, read_only, non_interactive).
- `AgentAdapter` Protocol (runtime_checkable): `name`, `capabilities()`, `extra_capabilities`, `supports(role)`, `build_command(prompt)`.
- `CliAdapter` dataclass wrapping `(config, executable=None)`: role-derived baseline + explicit extras; `supports` preserves the exact `not roles or role in roles` gate; `build_command` byte-identical to current logic (incl. TOCTOU comment).
- Module docstring documents deferred seams (env/output/cancel → A3) and the honesty rule.
- `tests/test_agent_adapter.py`: supports matrix, derivation honesty, extras passthrough, build_command parity (executable resolution, `{prompt}` substitution, missing-placeholder passthrough, absolute-path command).

## Subtask 3 — Registry capability-aware select (compatible extension)
Status: pending

- `AgentRegistry.adapter(name)` (cached `CliAdapter`s) + `select(role, excluded=None, required_capabilities=())`. Empty requirements → byte-identical path. With requirements → same order, filtered by adapter match (Capability or raw string).
- `DetectedAgent` untouched. Tests: required-capability filtering, fallback order preserved, disabled/role-gate/excluded preserved, string-form requirements.

## Subtask 4 — Runner delegates construction (no behavior change)
Status: pending

- `AgentRunner.build_command` delegates to `CliAdapter`; signature unchanged. Targeted run: `test_runner` + `test_agent_adapter` + `test_config_registry`, then full suite.

## Subtask 5 — Records + review + release
Status: done

- Roadmap A2 → DONE (M-A2.1–M-A2.3; M-A2.4 deferred explicitly — the legacy path IS the path, there is nothing to remove). architecture/decisions/lessons/project + Output file. Opencode contract review async. Exe rebuild + Release asset replace (new module = stale bundle). Commit + push.
