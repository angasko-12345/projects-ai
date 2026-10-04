# Output — Desktop application interactions (2026-10-04)

> Commit `feat(agentops): add desktop application interactions`. UI/shell files plus two
> additive reads. Suite: **512 tests, 4 environment skips, OK** (501 baseline + 11 new).

## What was built

- **Command palette (Ctrl+K)** — `gui/shell.py::open_palette` now exposes the desktop command
  set verbatim: `Open <View>` for the nine non-settings views (Ctrl+1..9), `New Task...`
  (Ctrl+N), `Recover Interrupted Work`, `Refresh` (F5), `Settings` (Ctrl+0), `Toggle sidebar`
  (Ctrl+B), `Change repository...`, cancel-active while a run is live, and recent
  repositories. Actions are bound to live shell methods and filtered incrementally as the
  user types (keyboard-first; same palette widget as before).
- **Recovery visible from the shell** — new read-only `StateStore.count_interrupted_work()`
  (mirror of `recover_all()`'s predicates: agent runs pending/starting/running, verification
  runs pending/running, tasks running) and `AgentOpsController.interrupted_work()` returning
  `{agent_runs, verification_runs, tasks, total}`. The shell probes on first show, repository
  switch, and idle refresh (F5 / Ctrl+R / thread-finished — never mid-operation, since live
  rows are not interrupted). A `QFrame#RecoveryBanner` under the top bar shows
  "Interrupted work detected" / "2 runs require recovery" with Review and Dismiss; Review
  re-probes, confirms via `QMessageBox`, and reuses the existing
  `controller.recover_interrupted()`. One notification per distinct interruption
  (toast + tray when the window is hidden); dismissal sticks until counts or repository
  change; stale results dropped (repository changed / operation started).
- **Global notifications** — `workflow-result` events with `ready=False` now read the existing
  `workflow_readiness()` and toast `Verification failed` (error) or `Workflow blocked`
  (warning) with the engine's reason strings; tray messages added for merge/complete/
  conflict/error/agent-failure/recovery-when-hidden. The shell never re-derives readiness.
- **Keyboard shortcuts** — F5 (refresh) added; registry exposed as `MainWindow._shortcuts`
  with a contract test. Esc closes the banner only while it is armed (QShortcut disabled when
  hidden, so Esc is never swallowed). Existing Ctrl+K/N/R/B and Ctrl+1..0 unchanged.
- **Tray** — Hide AgentOps action added (Show / New Task / Quit kept); tooltip mirrors live
  status ("AgentOps - Idle" / "AgentOps - Task running..." / elapsed while polling).
- **Window behavior** — close-while-running now calls `controller.cancel()` before
  `bridge.shutdown()`, so running subprocesses are terminated on quit (no orphans); geometry
  persistence, 1024x680 minimum, and worker shutdown were already in place and are now
  covered by a round-trip test.
- **New Task / recent repositories** — already first-class (repo combo from recents, routing
  strategy, verification profile, follow-into-workflow); unchanged, no backend duplication.

## Verification

- `cd agentops && python -m unittest discover -s tests` → **512 tests, 4 environment skips, OK**.
- `tests/test_state.py` +1: `count_interrupted_work()` must equal `recover_all()` (parity
  guard against predicate drift).
- `tests/test_gui.py` +1: `InterruptedWorkFacadeTests` — probe round trip through the real
  facade (counts then recovery consuming them).
- `tests/test_gui_qt.py` +9 (10 → 20, OK): palette discovery/actions, shortcut contract,
  banner show + notify-once, sticky dismissal, verification-failed toast, blocked toast,
  tray status/hide, geometry round trip — driven through the real `MainWindow` with the fake
  controller.
- Real-entry smoke: `QT_QPA_PLATFORM=offscreen python -m agentops gui` ran 8 s with no
  traceback; a driven offscreen run with the real controller showed 16 palette commands
  including Recover, banner hidden at zero counts, F5 registered, tray tooltip
  "AgentOps - Idle", a real `interrupted_work` probe, and a clean close.
- Two defects caught and fixed while writing these: the banner label `self._recovery_detail`
  shadowing the `_recovery_detail()` helper (renamed to `_recovery_summary`), and a geometry
  test exceeding the 1024×768 offscreen screen (restoreGeometry clamps to the screen).

## Files

- `agentops/agentops/state.py` — `count_interrupted_work()` (+read-only SELECTs).
- `agentops/agentops/gui_controller.py` — `interrupted_work()`.
- `agentops/agentops/gui/shell.py` — palette commands, recovery banner/probe/notify/recover,
  outcome toasts, F5/Esc shortcuts, tray status/hide, quit-cancel.
- `agentops/agentops/gui/tokens.py` — `QFrame#RecoveryBanner` styling.
- `agentops/tests/test_state.py`, `test_gui.py`, `test_gui_qt.py` — +11 tests.
- Docs: `agentops/README.md` desktop paragraph; `agentops/AGENTS.md` and `.agents/AGENTS.md`
  baselines updated 444 → 512 (2026-10-04).

## Follow-ups not done

- `AgentOps.spec` rebuild + archive inspection still owed (carried from the Qt migration).
