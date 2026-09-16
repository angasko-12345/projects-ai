# Output — A3 Shared ProcessRuntime

> Delivered 2026-09-16. Full suite 335 OK (4 skips). Copilot review closed.
> OpenCode architecture review pending.

## What was built

- **New `agentops/runtime.py`:** `ProcessRuntime` (configurable env allowlist,
  spawn factory, cleanup timeout), `ProcessResult`, `OperationCancelled`,
  `SpawnFactory`. No logging, SQLite, or service imports.
- **`AgentRunner`:** delegates spawn/wait/timeout/terminate to its runtime;
  keeps `run_agent` signature, observer lifecycle (RUNNING now fired via spawn
  callback at the same point as before), logging, metadata, and structured
  results. `_environment`/`terminate`/`_communicate_with_cancel` remain as
  compatibility delegates.
- **`VerificationKernel`:** uses `ProcessRuntime` directly; no longer imports
  runner internals. `process_factory=` still injects custom spawns.
  `_default_spawn` is now a thin runtime-backed factory.
- **Legacy `Verifier`:** same delegation; merged-stderr contract unchanged.
- **Package exports:** runtime types added to `agentops/__init__.py`.

## Verification

- 13 runtime tests + running-lifecycle + 2 kernel regressions; full suite green.
- Real agent + real verification smoke test through the shared runtime.
- Copilot adjudication: fixed thread leak (polling cancel), custom-factory
  killpg hazard (`process_group` flag + direct kill), `on_running` orphan,
  stranded RUNNING run on duplicate names, fail-fast overall status.
  Deferred state-error swallowing to A8 (persistence failure policy) — changing
  it here would alter failure semantics outside the process layer.
- Exe rebuilt + archive-inspected (`agentops.runtime` present) + smoke-tested.

## Follow-ups

- Adjudicate the pending OpenCode architecture reply when it arrives.
- A8 should define fail-closed behavior for state persistence failures.
- Consider supplying repository characteristics to routing from workflow context.
