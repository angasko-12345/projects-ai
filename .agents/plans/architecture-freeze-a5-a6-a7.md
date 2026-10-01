# Architecture freeze: A5 / A6 / A7 deferred until the correctness queue closes

Status: accepted. Date: 2026-10-01.

## Deferred work

- **A5 — WorkflowEngine decomposition** (`agentops/agentops/workflow.py`, 1238 lines).
  The engine owns orchestration, agent selection, execution, verification,
  failure handling, and merge gating in one class.
- **A6 — ReviewRun / MergeRun persistence.** No such classes exist today:
  review and merge runs are persisted as agent runs / verification runs plus
  the `finalize.py` worktree path, not as first-class rows. A6 is therefore
  a design task (define the rows), not a split task.
- **A7 — StateStore decomposition** (`agentops/agentops/state.py`, 1973 lines).
  One store owns workflows, tasks, agent runs, verification, failures, events,
  artifacts, and worktree provenance with additive migrations v4/v5.

## Why deferred

Large refactors now would churn exactly the code the current correctness queue
is verifying:

1. The 2026-10-01 fixes all touch engine/store/finalize seams: the single READY
   predicate (`execution_model` + `WorkflowEngine.workflow_readiness`), redaction
   at persistence boundaries (`tasks.result`, verification transcripts),
   verification setup-failure finalization, and workflow-scoped recovery in the
   controller. Splitting `WorkflowEngine` or `StateStore` mid-queue would move
   these seams while their regression tests are still being established, making
   each fix harder to verify (which test pins which seam?).
2. A6 has no stable interface to preserve yet. Decomposing persistence around a
   ReviewRun/MergeRun shape before that shape is agreed risks migrating the
   schema twice. The `finalize.py` centralization (commit/merge/conflict-task in
   one 91-line module) is the current stable seam; build on it.
3. `StateStore` migrations are additive by contract. A decomposition that
   re-tables existing data violates that contract unless done as additive
   tables + backfill, which is only safe once no correctness fix is still
   adding columns.

## Entry criteria for A5 / A6 / A7

- The correctness queue is closed: readiness, redaction, verification
  finalization, and recovery-scope fixes merged with green suite, no open
  P0/P1 against `workflow.py` / `state.py` / `finalize.py` / controller paths.
- Baseline suite green at the freeze commit (record the count; see below).
- A6 design agreed first: the ReviewRun/MergeRun row shapes and which existing
  rows they replace or join, reviewed before any A5/A7 split references them.
- Each decomposition ships as behavior-preserving moves with the existing suite
  green at every step; no schema rewrite, only additive tables.

## Explicit non-goals during the freeze

- No new abstractions to "clean up" engine/store/controller.
- No speculative cleanup of `finalize.py`, `persistence.py`, or the GUI thread
  facade.
- Correctness work (wrong behavior), reliability work (lost rows, hung runs,
  leaked worktrees), and architecture work (splits) stay in separate backlog
  lanes; only the first two are active.

## Baseline

`cd agentops` then `python -m unittest discover -s tests` at the freeze point.
Record the pass/skip count in the commit message that closes the queue; any
later A5/A6/A7 branch must match it before review.
