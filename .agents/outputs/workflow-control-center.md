# Output — Workflow Control Center (2026-10-04)

> Commit `7a5b472`, local only (no push requested). Task/UI files only plus one additive
> controller read. Suite: **501 tests, 4 environment skips, OK** (445 baseline + 56 new).

## What was built

- **`agentops/gui/control_center.py` (new, 684 lines).** Qt-free projection layer. Takes the plain
  dicts `AgentOpsController` already serializes, returns plain dicts. No SQLite, no Qt, no I/O, no
  clock beyond an injected `now`. Functions: `build_stage_flow`, `is_custom_dag`, `current_stage`,
  `next_stage`, `build_live_panel`, `recent_activity`, `elapsed_since`, `verification_totals`,
  `failure_summary`, `worktree_summary`, `merge_readiness`, `task_detail`, `run_detail`,
  `cancellation_state`.
- **`gui/views/workflows.py` rewritten.** Workflow list + `LiveCard` (current stage, agent, pulsing
  dot, model, elapsed, next stage) + `StageFlow` (vertical stages joined by labelled dependency
  edges, each carrying state/agent/model/duration/task status/verification/failure/completed-at) +
  `Tally` + tabs for Tasks, Runs, Verification (totals → individual checks), Failures, and Worktree
  (branch, base branch, base commit, path, changed files, diff, merge readiness + reasons, and
  merge/cleanup/open-folder actions). Cancel is offered only while running, locks and reads
  "Cancelling" on click; recovery hides during a live run.
- **`AgentOpsController.workflow_readiness()`** — 27 lines in `gui_controller.py` total. Delegates
  to the existing `assess_workflow_readiness()`; the GUI reads the engine's verdict rather than
  re-deriving it. No orchestration semantics changed.
- **`gui/widgets.py`**: `StageNode`, `StageFlow`, `Tally`, `LiveCard`. **`gui/tokens.py`**: tab
  styling, `QFrame[card="true"][active="true"]`, card hover border.

## Verification

- `cd agentops && python -m unittest discover -s tests` → **501 tests, 4 environment skips, OK**.
- `tests/test_control_center.py` (new, 56): `ControlCenterProjectionTests` display-free;
  `ControlCenterViewTests` under `@requires_qt`. Covers active workflow display, stage transitions,
  task/run/verification/failure selection, verification counts, cancellation state, conflicting-action
  disabling, and the final ready/blocked state.
- Rendered offscreen at 1600x980 and inspected; two defects found by sight and fixed (unstyled
  `QTabWidget` page stack painting white, tables leaving a white strip past the last column).

## Bugs found and fixed while building this

1. Finalize reported "running" from the *workflow* status while an earlier stage was still running.
2. Elapsed time fell back to the workflow header's `updated_at`, inventing a number instead of
   reporting unknown.
3. An unrecorded duration rendered as `-` inside a fact line, which reads like a real value.
4. Merge/cleanup enablement read `self._cancel.isEnabled()` as a proxy for "a run is live".

## Follow-ups (not done here)

- `.agents/AGENTS.md` and `agentops/AGENTS.md` still state a 444-test AgentOps baseline; the
  verified figure is now 501.
- `AgentOps.spec` rebuild is still owed (unchanged since the Qt migration; `hiddenimports` lacks
  PySide6). Packaging-affecting code was not touched by this work.
- Commit is local only; `git push` was not requested.

