# Output — A2 AgentAdapter + Capability Model

> Delivered 2026-09-15. Full suite 278 OK (3 skips). Exe rebuilt + Release v0.1.3 asset replaced. Opencode contract review requested async (reply pending).

## What was built

- **New `agentops/agent_adapter.py` leaf** (wraps `AgentConfig`, imports nothing else): `Capability` (9 values), `KNOWN_CAPABILITIES`, `capabilities_for_roles` (honest role-derived baseline; empty roles → all three), runtime-checkable `AgentAdapter` Protocol, `CliAdapter` (supports/build_command byte-identical to pre-adapter paths, TOCTOU comment preserved), `adapter_for` factory, `matches` (enum or raw string). Deferred seams documented: env/output-parse/cancel stay in runner until A3.
- **`AgentConfig.capabilities`** additive tuple (positional construction preserved) + `load_config` parsing (non-empty strings; unknown names warn, pass through; function-local vocabulary import to avoid a cycle).
- **`AgentRegistry`**: cached `adapters()`/`adapter(name)` (KeyError on unknown) + `select(role, excluded, required_capabilities=())` — empty requirements take the byte-identical legacy path; otherwise same preference order filtered by adapter match. `DetectedAgent` untouched.
- **`AgentRunner.build_command`** delegates to the adapter; signature and output unchanged (workflow persistence callers unaffected).
- **Engine verified flag-free**: only `registry.select` + `runner.build_command` calls; `_prompt_with_history` untouched.
- **Shipped `agents.yaml` unchanged** — no fabricated capabilities; B7 populates them.

## Verification

- New `tests/test_agent_adapter.py`: 14 contract tests OK.
- Extended `test_config_registry.py` (+7), `test_runner.py` (+1 delegation test).
- Targeted runs green after each step (config → adapter → registry → runner).
- Full suite: 278 passing (was 256), 3 environment skips.
- Variance from DAG: A2 before A3 (adapters exclude process-spawning); M-A2.4 deferred (nothing to remove).

## Files

- Created: `agentops/agent_adapter.py`, `tests/test_agent_adapter.py`
- Modified: `agentops/config.py`, `agentops/registry.py`, `agentops/runner.py`, `agentops/__init__.py`, `tests/test_config_registry.py`, `tests/test_runner.py`
- Backup: `/tmp/agentops-backup-a2/diff.patch`

## Follow-ups

- Opencode contract review reply pending (non-blocking).
- A3 (ProcessRuntime) next: runner env/spawn/cancel + kernel process use move under one runtime; adapters gain env/parse/cancel hooks only then.
