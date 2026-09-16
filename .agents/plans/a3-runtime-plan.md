# Plan — A3 Shared ProcessRuntime (P1)

> Owner: pi (writer); copilot (test review); opencode (architecture review, async).
> Status: implementation and copilot review complete; suite 335 OK; opencode reply pending.

## Objective

Give `AgentRunner` and `VerificationKernel` one shared process layer (spawn,
cancellation, timeout, termination, env policy, process-group cleanup, output
capture) without changing public APIs or execution outcomes.

## Improvements over the roadmap paragraph

- Legacy `Verifier` also delegates to the runtime, so the runner stops being
  the implicit process layer for all three execution paths.
- One cross-platform spawn policy for every default spawn: Windows
  `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, POSIX `start_new_session=True`
  (fixes killpg targeting the parent group for kernel children).
- Windows `taskkill` cleanup goes through the same no-window spawn path.
- Custom process factories keep their exact spawn behavior and get direct-kill
  cleanup instead of process-group signals.
- `asyncio.CancelledError` terminates the child before propagating (the runner
  previously leaked the process on task cancellation).
- Cooperative cancellation polls the event instead of parking a thread per
  process in `to_thread`.
- `on_running` spawn callback preserves the runner's RUNNING lifecycle timing;
  a failing callback terminates the child instead of orphaning it.
- Kernel setup fix: duplicate check names raise before a run is persisted.
- Kernel semantics fix: required FAILED/TIMED_OUT evidence dominates fail-fast
  sibling cancellation in the overall report.

## Verification

- `tests/test_runtime.py`: 13 tests (env parity, spawn policy, custom
  factories, timeout/cancel, asyncio cancellation, POSIX/Windows cleanup,
  running-lifecycle wiring, legacy parity, real agent+verification smoke).
- `tests/test_runner.py`: +1 running-before-finish regression.
- `tests/test_verification_kernel.py`: +2 regressions (no stranded RUNNING run,
  fail-fast overall FAILED).
- Full suite: 335 passing, 4 environment skips.
- Copilot review: 6 findings, 5 fixed, 1 deferred to A8 with rationale.
- Exe rebuilt (14,851,920 bytes), archive-inspected, smoke-tested.
- Backup: `/tmp/agentops-backup-a3-runtime-20260916-203040`.
