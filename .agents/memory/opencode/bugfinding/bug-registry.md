# Bug Audit Report — projects-ai repository

**Repository:** https://github.com/angasko-12345/projects-ai (local: `D:\admin\code\projects`)
**Audit date:** 2026-09-27
**Products audited:** `agentops/` (orchestrator) and `universal-game-agent/` (RL agent)
**Test baselines:** agentops 382 tests / 4 skips (pass); universal-game-agent 278 tests (pass)

---

## 1. Executive summary

**Triage review (2026-09-29):** Findings were checked against the current checkout at `a64ac96`. The original audit's counts and recommendations below are superseded by this review where noted. No source files were modified and no tests were rerun during triage.

The reviewed ledger contains **6 confirmed bugs, 1 likely bug, 1 suspected issue, and 4 false positives** (12 reported findings total). No finding was identified as already fixed.

The most impactful findings are:

- **A secret-persistence defect** in AgentOps: raw agent stdout/stderr (which may contain credentials, tokens, or private keys) is written unredacted into the `tasks` SQLite table via `task.result`, violating the repository's own non-negotiable convention.
- **A Windows-only process-classification concern** in AgentOps: the POSIX negative-return-code check does not establish a reliable Windows termination signal. The proposed high-bit exit-code heuristic is not safe because abnormal application exits are not necessarily process termination.
- **A crash-on-config bug** in the RL trainer: `checkpoint_every_updates: 0` is accepted by config validation but causes a `ZeroDivisionError` on the first training update.
- **A game-process leak** in phase-2 external-experiment startup, plus an external checkpoint-loading environment that is not closed.

No CRITICAL bugs (data loss, corruption, unrecoverable state) were found. No HIGH-severity bugs were confirmed; the findings are MEDIUM and LOW.

---

## 2. Bug counts

| Classification | Count |
|---|---|
| CONFIRMED | 6 |
| LIKELY | 1 |
| SUSPECTED | 1 |
| FALSE POSITIVE | 4 |
| **Total** | **12** |

By original reported severity: CRITICAL 0 · HIGH 0 · MEDIUM 7 · LOW 5. Severity counts include false-positive reports and are not counts of actionable bugs.

---

## 3. Critical and high-severity bugs

**None.** No CRITICAL or HIGH-severity bugs were confirmed. Audited statuses and the actionable queue follow.

---

## 4. Complete bug ledger

### Triage disposition

| Finding | Reviewed status | Disposition |
|---|---|---|
| BUG-AO-02 | CONFIRMED | Keep in fix queue: raw task-result persistence bypasses redaction. |
| BUG-AO-01 | SUSPECTED | Hold: a positive/high-bit Windows exit code does not prove termination. |
| BUG-UGA-01 | CONFIRMED | Keep in fix queue: zero interval reaches modulo operation. |
| BUG-UGA-02 | CONFIRMED | Keep in fix queue: startup exceptions occur before cleanup ownership is returned. |
| BUG-UGA-03 | CONFIRMED | Keep narrowly scoped to the external experiment checkpoint-loading env. |
| BUG-UGA-04 | LIKELY | Clarify per-mode output-size contract before changing behavior. |
| BUG-AO-03 | CONFIRMED (latent) | Keep narrowly scoped; no callers found in the checked source. |
| BUG-UGA-05 | FALSE POSITIVE | Backend and process are cleaned up in `finally`; no independent window-handle leak established. |
| BUG-UGA-06 | FALSE POSITIVE | This experiment runner uses the toy env path, not the external window path. |
| BUG-AO-04 | CONFIRMED | Keep in fix queue: check-creation failure can leave a persisted run RUNNING. |
| BUG-AO-05 | FALSE POSITIVE | `BLOCKED` tasks do not match the later `PENDING` guard; repeated writes/unblocking failure not established. |
| BUG-UGA-07 | FALSE POSITIVE | CLI builds the toy env; external-resource impact is unreachable through this path. |

### BUG-AO-02 — AgentOps persists raw unredacted agent output to SQLite

| Field | Value |
|---|---|
| **Severity** | MEDIUM |
| **Confidence** | CONFIRMED (reproduced) |
| **Component** | Workflow orchestration / persistence |
| **Affected files** | `agentops/agentops/workflow.py` (`_execute_task`) |
| **Affected functions** | `WorkflowEngine._execute_task` |
| **Observed behavior** | `task.result` is built as `f"log={result.log_path}\n{result.stdout}\n{result.stderr}".strip()` and persisted via `state.update_task(task)` with no redaction. Agent stdout/stderr is written verbatim into the `tasks` table. |
| **Expected behavior** | Per `.agents/AGENTS.md`: "Do not persist raw prompts, secrets, credentials, tokens, or private keys" and "Artifact and event payloads stay redacted, schema-tolerant, and free of secrets." The `tasks` table is a persistence boundary and must be redacted. |
| **Reproduction** | Ran a workflow with a fake agent whose stdout contained `api_key=sk-abcdefghijklmnopqrstuvwxyz123456`. After execution, `state.get_task(id).result` contained the raw secret. |
| **Root cause** | `task.result` is assigned raw `result.stdout`/`result.stderr` without calling `redact_text()`. The `record_failure` path redacts `evidence`/`primary_error`, but `task.result` itself is written unredacted by `update_task`. |
| **Why it fails** | `LogManager.write_run_artifacts` redacts the on-disk log files, but the in-database `task.result` copy is not redacted, creating a second unredacted persistence path. |
| **Potential impact** | Secrets (API keys, tokens, passwords) present in agent output are persisted in plaintext in `state.sqlite`, which may be shared, backed up, or inspected. |
| **Recommended minimal fix** | Redact `result.stdout`/`result.stderr` through `redact_text()` before assigning to `task.result`, or store only a redacted summary plus the log path. |
| **Regression test** | Assert that a secret pattern in agent stdout does not appear in `task.result` after a failed task. |

---

### BUG-AO-01 — AgentOps `terminated` status never set on Windows

| Field | Value |
|---|---|
| **Severity** | MEDIUM |
| **Confidence** | SUSPECTED (the POSIX check is present, but the reported reproduction does not prove termination) |
| **Component** | Process runner / execution classification |
| **Affected files** | `agentops/agentops/runner.py` (`run_agent`) |
| **Affected functions** | `AgentRunner.run_agent` |
| **Observed behavior** | `terminated = not timed_out and not cancelled and exit_code is not None and exit_code < 0`. On Windows, `subprocess` return codes are unsigned 32-bit values (e.g. `3221225501` for a terminated process), never negative. |
| **Expected behavior** | A process killed by a signal/termination should be classified as `TERMINATED` regardless of platform. |
| **Reproduction** | The reported process returned positive `3221225501` for `ExitProcess(0xC000001D)`, so the existing negative-return-code check evaluated to false. This demonstrates an abnormal exit code, not that the process was terminated rather than crashed. |
| **Root cause** | The `terminated` check uses the POSIX convention (`exit_code < 0` for signal termination), which does not apply on Windows where `GetExitCodeProcess` returns a `DWORD`. |
| **Why it fails** | Windows does not use the POSIX convention of negative subprocess return codes for signal termination. A reliable Windows condition for setting `TERMINATED` has not been established by this audit. |
| **Potential impact** | Misclassification of process outcomes on Windows; `FailureClassifier` and repair planning receive wrong signals; `recover_agent_runs` and audit trails are inaccurate. |
| **Recommended minimal fix** | First define and verify the Windows termination signal/contract. Do not classify all high-bit exit codes as termination; those can represent ordinary application failures. |
| **Regression test** | Use a Windows-specific test that produces a known termination signal distinct from an ordinary nonzero application exit, and assert only the former maps to `TERMINATED`. |

---

### BUG-UGA-01 — `checkpoint_every_updates: 0` crashes training with ZeroDivisionError

| Field | Value |
|---|---|
| **Severity** | MEDIUM |
| **Confidence** | CONFIRMED (reproduced) |
| **Component** | PPO trainer / config validation |
| **Affected files** | `universal-game-agent/training/ppo.py` (`PPOConfig.__post_init__`, `PPOTrainer.train`) |
| **Affected functions** | `PPOConfig.__post_init__`, `PPOTrainer.train` |
| **Observed behavior** | `PPOConfig` validates `rollout_length`, `minibatch_size`, `update_epochs`, `total_timesteps` as positive ints, but does NOT validate `checkpoint_every_updates`. A value of `0` is accepted, then `self.num_updates % cfg.checkpoint_every_updates` raises `ZeroDivisionError` on the first update. |
| **Expected behavior** | `checkpoint_every_updates` should be validated as a positive int (or `None`/0 handled as "disabled"). |
| **Reproduction** | `PPOConfig(checkpoint_every_updates=0)` accepted; `trainer.train()` raised `ZeroDivisionError: division by zero` after the first update. |
| **Root cause** | `PPOConfig.__post_init__` omits `checkpoint_every_updates` from the positive-int validation loop. |
| **Why it fails** | The modulo guard `if self.num_updates % cfg.checkpoint_every_updates == 0` is reached on every update; a zero divisor crashes training. |
| **Potential impact** | A config with `checkpoint_every_updates: 0` (a plausible "disable checkpoints" value) crashes training after the first update, losing all progress. |
| **Recommended minimal fix** | Add `checkpoint_every_updates` to the positive-int validation in `__post_init__`, or guard the modulo with `if cfg.checkpoint_every_updates and self.num_updates % cfg.checkpoint_every_updates == 0`. |
| **Regression test** | Assert `PPOConfig(checkpoint_every_updates=0)` raises `ValueError`, or that `train()` treats 0 as "disabled". |

---

### BUG-UGA-02 — Game process leak when window attach fails in external experiment

| Field | Value |
|---|---|
| **Severity** | MEDIUM |
| **Confidence** | CONFIRMED (reproduced with mock) |
| **Component** | External game experiment / process lifecycle |
| **Affected files** | `universal-game-agent/training/external_experiment.py` (`launch_phase2`, `train_with_window_relaunch`) |
| **Affected functions** | `launch_phase2`, `train_with_window_relaunch` |
| **Observed behavior** | `launch_phase2` calls `launch_game` (which opens a log file and starts a subprocess), then `wait_attach`. If `wait_attach` raises (timeout) or `check_alive` raises (game died), the exception propagates through `launch_session` → `train_with_window_relaunch`'s `cleanup = launch_session()` line, and the game process is never terminated. The log file handle is also leaked. |
| **Expected behavior** | A game process that was launched but never attached should be terminated and its log file closed on failure. |
| **Reproduction** | Mocked `launch_game` to return a mock proc and `wait_attach` to raise. `train_with_window_relaunch` propagated the exception; `proc.terminate()` was never called. |
| **Root cause** | `launch_phase2` does not wrap `wait_attach`/`check_alive` in a try/except that stops the proc on failure. `train_with_window_relaunch` only has a `try` around `trainer.train()`, not around `launch_session()`. |
| **Why it fails** | The cleanup lambda is only returned after `launch_phase2` succeeds; if `launch_phase2` raises, the proc is orphaned. |
| **Potential impact** | A failed external-experiment run leaves a game process running and a log file handle open. Repeated failures accumulate zombie processes. |
| **Recommended minimal fix** | Wrap `launch_phase2`'s body in try/except: on exception, terminate the proc and close the log file before re-raising. |
| **Regression test** | Assert that when `wait_attach` raises, the launched proc's `terminate` is called. |

---

### BUG-UGA-03 — Environments created for checkpoint loading are never closed

| Field | Value |
|---|---|
| **Severity** | MEDIUM |
| **Confidence** | CONFIRMED (code inspection + mock) |
| **Component** | External experiment / checkpoint-load resource cleanup |
| **Affected files** | `universal-game-agent/training/external_experiment.py` (`run_external_experiment`) |
| **Affected functions** | `run_external_experiment` |
| **Observed behavior** | `PPOTrainer.load_checkpoint(ckpt, make_env()).model` creates an environment for the external phase-3 checkpoint load, extracts the model, and never closes that environment. The cited `main.py` paths build environments through the toy factory and do not establish the reported external-window resource leak. |
| **Expected behavior** | Environments should be closed after use, especially external envs that hold OS resources (screen capture backends, window handles). |
| **Reproduction** | Mock confirmed: `PPOTrainer.load_checkpoint('ckpt.pt', make_env()).model` never calls `env.close()`. |
| **Root cause** | `load_checkpoint` requires an env argument (to construct the trainer), but callers that only need the model discard the trainer without closing the env. |
| **Why it fails** | The env's `close()` method (which closes the `MSSBackend` and detaches the window lifecycle) is never invoked. |
| **Potential impact** | The external experiment's temporary MSS-backed environment remains unclosed after checkpoint loading. The broader CLI impact in the original report is unsupported because those CLI paths use the toy environment. |
| **Recommended minimal fix** | In the external experiment checkpoint-loading call, close the temporary environment in a `finally` path after model extraction. |
| **Regression test** | Assert that the external experiment closes the checkpoint-loading environment both when model extraction succeeds and when checkpoint loading raises. |

---

### BUG-UGA-04 — `capture.out_width`/`out_height` silently ignored in region and window modes

| Field | Value |
|---|---|
| **Severity** | MEDIUM |
| **Confidence** | LIKELY (the factory omits sizes, but the intended mode-specific contract is unclear) |
| **Component** | External game env factory / capture configuration |
| **Affected files** | `universal-game-agent/environment/external_game.py` (`make_external_env_from_config`) |
| **Affected functions** | `make_external_env_from_config` |
| **Observed behavior** | For `capture.mode: region`, `ScreenCapture` is constructed as `ScreenCapture(backend, x, y, width, height)` with no `out_width`/`out_height`, so frames are returned at native resolution. For `capture.mode: window`, `WindowCapture(manager, backend)` is constructed with no out size. Only `synthetic` mode passes `out_w, out_h`. The config keys `out_width`/`out_height` are read into `out_w, out_h` but silently ignored for region/window. |
| **Expected behavior** | The factory should honor or explicitly document/validate the mode-specific meaning of these keys. Window capture currently intentionally preserves native frames for reward/termination detector detail, so resizing all live modes may be incorrect. |
| **Reproduction** | Constructed `ScreenCapture(backend, 0, 0, 320, 240)` (region mode) — output was `(240, 320, 3)` native, not the configured `96x96`. Synthetic mode with the same config produced `(96, 96, 3)`. |
| **Root cause** | The region and window branches of `make_external_env_from_config` do not pass `out_width`/`out_height` to the capture constructor, while the synthetic branch does. |
| **Why it may fail** | For region mode, the factory reads configured output dimensions but does not pass them to `ScreenCapture`. For window mode, native-resolution capture may be intentional; the mismatch alone does not establish a defect without a defined configuration contract. |
| **Potential impact** | Users configuring capture sizes for region/window mode get unexpected native-resolution frames, which may break downstream detectors or model input expectations. |
| **Recommended minimal fix** | Decide and document whether these options apply to region capture, window capture, both, or neither; preserve native-resolution frames where detectors rely on them. |
| **Regression test** | Test through `make_external_env_from_config` for each supported mode after defining the contract; explicitly assert native window behavior if that remains intentional. |

---

### BUG-AO-03 — GUI `recover_interrupted` does not scope run recovery to the workflow

| Field | Value |
|---|---|
| **Severity** | LOW |
| **Confidence** | CONFIRMED (code inspection) |
| **Component** | GUI controller / crash recovery |
| **Affected files** | `agentops/agentops/gui_controller.py` (`recover_interrupted`) |
| **Affected functions** | `GUIController.recover_interrupted` |
| **Observed behavior** | When `workflow_id` is not None, the method calls `state.recover_agent_runs()` and `state.recover_verification_runs()` WITHOUT passing `workflow_id`, but passes `workflow_id` to `state.recover_tasks(workflow_id)`. This means workflow-scoped recovery in the GUI actually recovers agent runs and verification runs across ALL workflows. |
| **Expected behavior** | Workflow-scoped recovery should pass `workflow_id` to all three recovery passes, as `workflow.py:recover_incomplete` correctly does. |
| **Reproduction** | Code inspection: `gui_controller.py:580-581` vs `workflow.py:240-241`. |
| **Root cause** | Copy-paste inconsistency between `gui_controller.recover_interrupted` and `workflow.recover_incomplete`. |
| **Why it fails** | The `workflow_id` parameter is accepted but only forwarded to `recover_tasks`, not to `recover_agent_runs`/`recover_verification_runs`. |
| **Potential impact** | If this method is ever wired up, a workflow-scoped recovery would terminate another workflow's live agent/verification runs. Currently dead code (no callers), so impact is latent. |
| **Recommended minimal fix** | Pass `workflow_id` to `recover_agent_runs(workflow_id)` and `recover_verification_runs(workflow_id)`. |
| **Regression test** | Assert that `recover_interrupted(dir, workflow_id=X)` only recovers runs for workflow X. |

---

### BUG-UGA-05 — `external_smoke.run_external_smoke` never closes the environment — FALSE POSITIVE

| Field | Value |
|---|---|
| **Severity** | MEDIUM |
| **Confidence** | FALSE POSITIVE (the alleged resource leak is not present) |
| **Component** | External smoke test / resource cleanup |
| **Affected files** | `universal-game-agent/training/external_smoke.py` (`run_external_smoke`) |
| **Affected functions** | `run_external_smoke` |
| **Observed behavior** | `run_external_smoke` does not call `env.close()`, but its `finally` closes the `MSSBackend` directly and terminates the launched game process. `WindowManager.detach()` only clears its stored window identifier. |
| **Expected behavior** | Cleanup must release owned external resources. The backend and launched process are explicitly cleaned up in this path. |
| **Reproduction** | Code inspection: `env.close()` does not appear in `run_external_smoke`. |
| **Root cause** | The env is created inside the `try` block but not tracked for cleanup in the `finally`; this omission does not leave a resource outstanding beyond the resources directly cleaned there. |
| **Why it fails** | No resource leak is demonstrated: `env.close()` would detach the manager reference, while the MSS backend is already closed directly. |
| **Potential impact** | None established. A stylistic cleanup call alone is not an actionable bug fix. |
| **Recommended minimal fix** | No fix recommended. Reconsider only if a future lifecycle implementation adds owned resources not covered by the existing `finally`. |
| **Regression test** | No bug regression test recommended; retain coverage that the backend is closed and the launched process is stopped. |

---

### BUG-UGA-06 — `experiment.run_experiment` never closes the training environment — FALSE POSITIVE

| Field | Value |
|---|---|
| **Severity** | MEDIUM |
| **Confidence** | FALSE POSITIVE (claimed external-resource impact does not apply) |
| **Component** | Experiment runner / resource cleanup |
| **Affected files** | `universal-game-agent/training/experiment.py` (`run_experiment`) |
| **Affected functions** | `run_experiment` |
| **Observed behavior** | `run_experiment` does not call `trainer.env.close()` after training. However, the function creates the env through the toy-only experiment factory, not the external-game factory. |
| **Expected behavior** | The reported external OS-resource cleanup requirement does not apply to this toy env; its `close()` is a no-op. |
| **Reproduction** | Code inspection: `env.close()` does not appear in `run_experiment` (only `probe.close()`). |
| **Root cause** | No external-resource cleanup defect is established for the toy-only env used here. |
| **Why it fails** | No external env is built by this function, so the reported MSS/window leak is not reachable through the current factory path. |
| **Potential impact** | No external-resource impact established. |
| **Recommended minimal fix** | No fix recommended for the bug as reported. Any general environment lifecycle change requires a separate contract and scope. |
| **Regression test** | No bug regression test recommended for the claimed external leak. |

---

### BUG-AO-04 — Verification check creation failure leaves stranded RUNNING run

| Field | Value |
|---|---|
| **Severity** | LOW |
| **Confidence** | CONFIRMED (the persisted run is started before check creation; exceptions are not finalized) |
| **Component** | Verification kernel / persistence |
| **Affected files** | `agentops/agentops/verification_kernel.py` (`run_verification`) |
| **Affected functions** | `VerificationKernel.run_verification` |
| **Observed behavior** | `run_verification` creates the verification run (PENDING → RUNNING) and then creates checks in a loop. If `create_verification_check` raises mid-loop (e.g., UNIQUE constraint violation on a corrupted DB), the exception propagates and the run is left RUNNING with some checks PENDING. |
| **Expected behavior** | A failure during check creation should either roll back the run or mark it FAILED, not leave it RUNNING. |
| **Reproduction** | Code inspection: the check-creation loop is not wrapped in try/except. |
| **Root cause** | No error handling around the check-creation loop after the run has been persisted as RUNNING. |
| **Why it fails** | The run is persisted as RUNNING before checks are created; a failure leaves it stranded. |
| **Potential impact** | A stranded RUNNING verification run that is only cleaned up on the next process restart (via `recover_verification_runs`). Within the same process, it blocks workflow progress. |
| **Recommended minimal fix** | Wrap the check-creation loop in try/except: on exception, call `finish_verification_run(run_id, FAILED, ...)` and re-raise. |
| **Regression test** | Assert that a failure during check creation marks the run FAILED. |

---

### BUG-AO-05 — `state.ready_tasks` mutates the database during a read operation — FALSE POSITIVE

| Field | Value |
|---|---|
| **Severity** | LOW |
| **Confidence** | FALSE POSITIVE (the stated repeated-write and unblock failures are not demonstrated) |
| **Component** | State store / task scheduling |
| **Affected files** | `agentops/agentops/state.py` (`ready_tasks`) |
| **Affected functions** | `StateStore.ready_tasks` |
| **Observed behavior** | `ready_tasks` persists `BLOCKED` for pending tasks with failed dependencies. After that transition the task no longer matches the `PENDING` guard and is not rewritten on later scheduling iterations. |
| **Expected behavior** | Persisting the dependency failure is the current scheduler's state transition; the side effect alone does not establish a correctness bug. |
| **Reproduction** | Code inspection: `ready_tasks` calls `update_task` which commits. |
| **Root cause** | Lazy blocking is implemented as a side effect of the read method. |
| **Why it fails** | The repeated-write claim is contradicted by the status guard. The proposed unblock scenario assumes dependency retry semantics not present in the checked workflow behavior. |
| **Potential impact** | None established for current behavior. |
| **Recommended minimal fix** | No fix recommended. If dependency retry/unblock behavior is introduced, specify it separately and test that state transition. |
| **Regression test** | No bug regression test recommended for the alleged repeated writes or unsupported retry scenario. |

---

### BUG-UGA-07 — `main.py cmd_train` does not close the training env after training — FALSE POSITIVE

| Field | Value |
|---|---|
| **Severity** | LOW |
| **Confidence** | FALSE POSITIVE (claimed external-resource impact does not apply) |
| **Component** | CLI / resource cleanup |
| **Affected files** | `universal-game-agent/main.py` (`cmd_train`) |
| **Affected functions** | `cmd_train` |
| **Observed behavior** | `cmd_train` does not explicitly close the env, but builds it through `training.experiment.make_env_from_config`, which is the toy-environment path. |
| **Expected behavior** | The reported MSS/window cleanup requirement does not apply here; the toy env's `close()` is a no-op. |
| **Reproduction** | Code inspection: no `trainer.env.close()` in `cmd_train`. |
| **Root cause** | No external-resource cleanup defect is established for the toy-only env used here. |
| **Why it fails** | The current CLI does not route this operation through the external-game factory, so the alleged external-resource leak is unreachable here. |
| **Potential impact** | No external-resource impact established. |
| **Recommended minimal fix** | No fix recommended for the bug as reported. |
| **Regression test** | No bug regression test recommended for the claimed external leak. |

---

## 5. Root-cause groups

### ROOT-CAUSE-001 — External checkpoint-loading env not closed (BUG-UGA-03)

**Root cause:** The external experiment creates an env for checkpoint loading, extracts the model, then drops the trainer without closing the env.

**Minimal fix:** Close only that temporary external env in a `finally` path after model extraction. Do not expand this into a general trainer/context-manager redesign.

**Regression coverage:** Close-tracking env closes on successful extraction and checkpoint-load failure.

---

### ROOT-CAUSE-002 — Unvalidated checkpoint interval (BUG-UGA-01)

**Common root cause:** `PPOConfig.__post_init__` validates most fields but omits `checkpoint_every_updates`. A value that should be rejected (0) or handled specially ("disabled") is accepted and crashes later.

**Affected files:** `training/ppo.py`

**Minimal fix:** Decide whether zero is invalid or disables checkpoints, then validate or guard while preserving positive-interval behavior.

**Regression coverage:** Zero, negative, and positive intervals.

---

### Group C — Platform-specific assumptions (BUG-AO-01; unresolved)

**Common root cause:** The `terminated` check uses the POSIX convention (`exit_code < 0`), which does not apply on Windows where exit codes are unsigned.

**Affected files:** `agentops/runner.py`

**Recommended fix:** Define and verify the Windows termination signal/contract first. Do not use a blanket high-bit exit-code heuristic; application failures can also use those values.

---

### ROOT-CAUSE-003 — Unredacted task-result persistence (BUG-AO-02)

**Common root cause:** `task.result` is persisted without redaction, while the log files and failure evidence are redacted. The redaction is applied at some persistence boundaries but not others.

**Affected files:** `agentops/workflow.py`

**Minimal fix:** Redact the result at its persistence boundary using the existing helper while preserving useful diagnostics.

**Regression coverage:** Secret-like stdout/stderr is absent from persisted task results for both passing and failing tasks.

---

### ROOT-CAUSE-004 — Process cleanup ownership lost on launch failure (BUG-UGA-02)

**Common root cause:** `launch_phase2` starts a game process but does not clean it up if a subsequent step (`wait_attach`, `check_alive`) fails. The cleanup lambda is only returned on success.

**Affected files:** `training/external_experiment.py`

**Minimal fix:** Ensure the process launched in phase 2 is terminated/reaped if attach or liveness checking fails before cleanup is returned.

**Regression coverage:** Inject failures in both `wait_attach` and `check_alive`; assert process termination and wait.

---

### ROOT-CAUSE-005 — Workflow filter not forwarded by GUI recovery (BUG-AO-03)

**Common root cause:** `gui_controller.recover_interrupted` accepts a workflow ID but fails to forward it to agent-run and verification-run recovery. The method has no callers in the checked source, so current impact is latent.

**Affected files:** `agentops/gui_controller.py`

**Minimal fix:** Forward `workflow_id` to both run-recovery calls if this API is retained and used; avoid removing it solely because it is currently unused.

**Regression coverage:** With runs in two workflows, scoped recovery mutates only the selected workflow.

---

### ROOT-CAUSE-006 — Verification setup leaves partial persisted state (BUG-AO-04)

**Common root cause:** The verification run is persisted as RUNNING before its check rows are all created, and setup exceptions bypass the run-finalization logic.

**Minimal fix:** Make setup failure-safe using the existing persistence boundary; preserve and surface the original exception, and do not fabricate a successfully stored terminal state if persistence itself failed.

**Regression coverage:** Inject a mid-loop check-creation failure and verify the run is not left RUNNING when failure finalization can be persisted.

---

### Open contract — Capture output size (BUG-UGA-04)

The factory reads `out_width`/`out_height`, but region/window capture constructors do not receive them. Decide whether the options apply per mode before editing. Window capture intentionally preserves native frames for detector detail; do not resize that path without evidence and regression coverage.

---

## 6. Dependency / order recommendations

There are no hard implementation dependencies between the confirmed root causes. The sequence prioritizes sensitive-data exposure, crash/process-leak impact, then narrower latent persistence issues. Regression tests should precede fixes.

**Phase 1 — Highest impact and directly verifiable**

- ROOT-CAUSE-003 / BUG-AO-02: prevent unredacted output from reaching persistent task results.
- ROOT-CAUSE-002 / BUG-UGA-01: define and validate the zero checkpoint interval behavior.
- ROOT-CAUSE-004 / BUG-UGA-02: clean up the launched game process on phase-2 startup failure.

**Phase 2 — Resource and persistence correctness**

- ROOT-CAUSE-001 / BUG-UGA-03: close the external checkpoint-loading environment.
- ROOT-CAUSE-006 / BUG-AO-04: prevent verification setup failures from stranding a RUNNING record.
- ROOT-CAUSE-005 / BUG-AO-03: fix workflow scoping before this GUI recovery method is used.

**Phase 3 — Contract-dependent**

- BUG-UGA-04: define and test capture-size behavior by mode before changing it.

**Hold**

- BUG-AO-01: establish a reliable Windows termination signal distinct from ordinary nonzero application exits before implementation. The original high-bit heuristic is unsafe.

---

## 7. False positives / previously-fixed issues checked

**False positives in this audit (removed from the actionable queue):**

- **BUG-UGA-05:** `run_external_smoke` directly closes the MSS backend and stops the process in `finally`; no separate OS window-handle leak was established.
- **BUG-UGA-06:** `training.experiment.run_experiment` uses the toy factory, not the external window environment; no MSS/window leak is reachable through this path.
- **BUG-AO-05:** `ready_tasks` transitions a task from PENDING to BLOCKED, so it does not rewrite that task on subsequent passes. Retry/unblock semantics are not established.
- **BUG-UGA-07:** `main.py cmd_train` uses the toy environment path; the reported external-resource leak is not applicable.

**No reported finding was already fixed** in the checkout reviewed for this triage.

- **`agentops/config.py:62` default config path** — documented known defect (non-editable wheel install ships no default config). Not a new bug.
- **`agentops` egg-info staleness** — documented known defect. Not a new bug.
- **`tests/test_storage_dtos_worktree_refs.py` fossil name** — documented known defect. Not a new bug.
- **`training/external_experiment.py` orchestration has no tests** — documented known defect. Not a new bug.
- **`run_experiment` defined twice** (`training/ppo.py:383` and `training/experiment.py:66`) — documented known defect. Not a new bug.
- **`environment/external_game.py` hardcodes `extern_pong`** — documented known defect. Not a new bug.
- **`games/` has no `__init__.py` and mutates `sys.path`** — documented known defect. Not a new bug.
- **Two resizers with no shared owner** (`interface/capture.py:resize_rgb` and `environment/preprocessing.py:_resize_bilinear`) — documented known defect. Not a new bug.
- **Virtual-key action mapping defined in multiple places** — documented known defect. Not a new bug.
- **`main.py` smoke-test builds a second separate `ToyPongEnv`** — documented known defect. Not a new bug.
- **`training/logger.py` and `logging:` config block are dead** — documented known defect. Not a new bug.
- **`checkpoint_every_updates` drift across configs** — documented known defect. The ZeroDivisionError for value 0 is a NEW consequence of this drift (BUG-UGA-01).
- **`recover_verification_runs` does not create a report** — verified this is handled: `verification_evidence` requires `task.verified` which is False for recovered tasks, and `cli.py verify --run` catches the KeyError. Not a bug.
- **`ready_tasks` BLOCKED mutation** — the reported repeated writes are false; see BUG-AO-05 disposition above. Do not add speculative retry/unblock behavior without a separate requirement.
- **`FrameStack.push` padding branch** — analyzed and found correct (only triggers without reset, which is an edge case). Not a bug.
- **`PPOTrainer` GAE / bootstrap / segment-splitting logic** — analyzed line by line and found correct. Not a bug.
- **`compute_gae` with `terminated` vs `truncated`** — verified correct (truncation bootstraps, termination does not). Not a bug.
- **`CuriosityModule` masking** — verified correct (boundary steps masked to zero). Not a bug.
- **`verification_kernel` fail-fast / cancellation logic** — verified correct. Not a bug.
- **`runtime.py` environment building (Windows case-insensitive)** — verified correct. Not a bug.
- **`runtime.py` terminate_process (taskkill / killpg)** — verified correct. Not a bug.
- **`state.py` migration additive-only pattern** — verified correct. Not a bug.
- **`failure.py` classification order** — verified correct (structured evidence takes precedence, specific patterns before generic). Not a bug.
- **`agent_result.py` parser robustness** — verified correct (never raises, total function). Not a bug.
- **`events.py` EventBus reentrancy** — verified correct (nested emit queued and drained). Not a bug.
- **`artifacts.py` containment and hashing** — verified correct. Not a bug.
- **`git.py` worktree isolation and merge guards** — verified correct. Not a bug.
- **`routing.py` deterministic scoring** — verified correct. Not a bug.
- **`registry.py` version probe and role gate** — verified correct. Not a bug.

---

## 8. Areas audited and found healthy

- **AgentOps state machine** (`execution_model.py`): transition matrix, vacuous-success rejection, recovery honesty — all correct.
- **AgentOps verification kernel** (`verification_kernel.py`): check execution, fail-fast, cancellation, degradation fail-closed — all correct.
- **AgentOps runtime** (`runtime.py`): environment building, spawn policy, cancellation, timeout, process-group termination — all correct.
- **AgentOps failure classification** (`failure.py`): deterministic, structured-evidence precedence, interruption mapping — all correct.
- **AgentOps agent result parsing** (`agent_result.py`): total function, never raises, version tolerance — all correct.
- **AgentOps event bus** (`events.py`): thread-safe, reentrancy-safe, delivery never breaks publishers — all correct.
- **AgentOps artifact store** (`artifacts.py`): containment, hashing, atomic writes, orphan detection — all correct.
- **AgentOps git integration** (`git.py`): worktree isolation, merge guards, conflict preservation — all correct.
- **AgentOps routing** (`routing.py`): deterministic scoring, capability gates, explainability — all correct.
- **UGA PPO trainer** (`ppo.py`): GAE, bootstrap semantics, segment splitting, episode accounting, curiosity integration — all correct (except BUG-UGA-01).
- **UGA model** (`agent/model.py`): forward/forward_sequence, weight init, save/load — all correct.
- **UGA preprocessing** (`environment/preprocessing.py`): grayscale, bilinear resize, frame stacking, frame skipping — all correct.
- **UGA reward/termination providers** (`environment/reward.py`, `termination.py`, `extern_pong_rewards.py`): edge detection, latch logic, threshold validation — all correct.
- **UGA interface layer** (`interface/capture.py`, `controller.py`, `window.py`, `adapter.py`): SendInput, MSS capture, window management, action mapping — no other issue confirmed; see open capture-size contract for BUG-UGA-04.
- **UGA toy environment** (`environment/toy_pong.py`): game logic, rendering, API — all correct.
- **UGA external game** (`games/extern_pong.py`, `games/pong_logic.py`): standalone game, rendering, input — all correct.
- **UGA evaluation** (`training/evaluate.py`): separate env per episode, greedy/sampled, fixed seeds — all correct.
- **UGA curiosity** (`training/curiosity.py`): forward model, normalization, masking — all correct.
- **UGA config system** (`configs/__init__.py`): YAML loading, mapping check — all correct.

---

## 9. Recommended regression tests

| Bug | Regression test |
|---|---|
| BUG-AO-02 | Secret-like stdout/stderr must not appear in persisted `task.result` for passing or failing tasks. |
| BUG-AO-01 | Establish a known Windows termination signal and distinguish it from an ordinary nonzero application exit before asserting `TERMINATED`. |
| BUG-UGA-01 | `PPOConfig(checkpoint_every_updates=0)` must raise `ValueError` (or `train()` must treat 0 as disabled). |
| BUG-UGA-02 | When `wait_attach` or `check_alive` fails in `launch_phase2`, the launched process is terminated and reaped. |
| BUG-UGA-03 | External experiment checkpoint-loading env closes after model extraction, both on success and load failure. |
| BUG-UGA-04 | After documenting the capture-size contract, test the factory behavior per supported mode, including native window capture if retained. |
| BUG-AO-03 | `recover_interrupted(dir, workflow_id=X)` must only recover runs for workflow X. |
| BUG-AO-04 | Injected check-creation failure must not leave a RUNNING run when terminal failure persistence succeeds; preserve the original exception. |

---

## 10. Risks for implementation agents

- **BUG-AO-02:** Redact at the task-result persistence boundary and preserve useful diagnostics. Test both successful and failed agent tasks; do not assume failure-record redaction protects ordinary task results.
- **BUG-UGA-01:** Decide whether zero disables checkpoints or is invalid. Keep existing positive checkpoint intervals unchanged.
- **BUG-UGA-02:** Cleanup must cover every exception after process launch and before cleanup ownership is returned, including liveness failures; do not terminate unrelated processes.
- **BUG-UGA-03:** Close only the temporary external checkpoint-loading env and do so without masking the original load/evaluation error.
- **BUG-UGA-04:** Resizing live frames may degrade reward/termination detection. Specify mode semantics and test through the factory before changing capture behavior.
- **BUG-AO-03:** Preserve workflow isolation; do not broaden recovery to other workflows.
- **BUG-AO-04:** If the database failure also prevents finalization, do not claim a stored terminal state or swallow the original setup exception.
- **BUG-AO-01:** Do not classify every high-bit Windows exit code as termination; application-defined failure codes may also occupy that range.
- **False positives:** Do not add broad env-lifecycle abstractions or speculative BLOCKED-task retry behavior to address findings removed from the actionable queue.

---

**End of report.** No source code was modified during this audit.
