# projects-ai Independent Review: Handoff

**Repo:** https://github.com/angasko-12345/projects-ai
**Reviewed:** 2026-09-27, full clone (`git fetch --unshallow`) at commit `018de82`, tag `v0.1.3`, 95 commits total.
**Method:** cloned locally, read source directly, ran the test suite, ran `ruff`, traced call chains by hand, then did a second consolidation pass. Not a formal audit: no fuzzing, no `universal-game-agent` test run (needs torch/gymnasium, not installed).

## 0. What's in this repo

Two independent products plus a large agent-process layer, bundled in one repo.

- **`agentops/`**: local-first orchestrator that dispatches tasks to CLI coding agents (Codex, OpenCode, Copilot CLI, etc.), runs them in isolated git worktrees, and gates merges on a verification kernel. Zero external dependencies, pure stdlib, `requires-python = ">=3.11"`. This is the "spec, execution, verification, output, GitHub" pipeline.
- **`universal-game-agent/`**: PyTorch/Gymnasium PPO agent learning a toy Pong clone from pixels. The README is honest that "learn arbitrary games" is not yet implemented; it's currently a single-game agent.
- **`.agents/`**: inter-agent process scaffolding (memory, decisions, lessons, plans, outputs, skills). 72 markdown files, 11,225 lines, more prose than the entire game-agent codebase.

**Repo-wide stats:** 203 tracked files. 102 Python files, 26,897 lines. 72 Markdown files, 11,225 lines. 42 test files.

**Context:** the repo already contains its own internal AI-agent-written review at `.agents/memory/opencode/repo-review-2026-09-26.md`, dated one day before this one. I independently verified several of its claims by reading the code myself rather than trusting the document, found additional issues it didn't catch, and traced two of its findings all the way to their root cause in the underlying code. Cross-checked against `.agents/memory/decisions.md`, `roadmap.md`, and `pending_tasks.md`: as of this review, none of the critical or high findings below (items 1, 2, 3, 4) have been promoted into a tracked decision or task. Only the "no CI" gap is on the roadmap, and it's stalled on an unanswered question.

---

## 1. CRITICAL: The flagship merge workflow is broken for any real-world target repo

**Files:** `agentops/agentops/git.py:100`, `git.py:239-243`

`GitWorktreeManager` creates task worktrees at:
```python
path = repository / ".agentops" / "worktrees" / branch.replace("/", "-")
```
inside the target repository being operated on.

`merge()` refuses to proceed if the base repo isn't clean:
```python
result = git status --porcelain   # on the base repo
if result.stdout.strip():
    raise GitError("...")  # merge refused
```

Nothing in the `agentops` package writes or checks a `.gitignore` entry for `.agentops/` in the target repo. Its own `.gitignore` only covers its own dev checkout, not repos it's pointed at.

**Consequence:** the first time `agentops task` runs against any repo that doesn't already have `.agentops/` hand-added to its `.gitignore`, the untracked `.agentops/worktrees/...` directory makes `git status --porcelain` non-empty, and every subsequent merge is refused. This breaks the entire pipeline, the core value proposition of the tool, on first real use.

README.md:67's claim that `.agentops/` is "ignored by Git" is false for any repo that doesn't already know to ignore it.

**Fix options (pick one):**
- On first run against a repo, programmatically append `.agentops/` to that repo's `.gitignore` if not already present, and commit or warn the user to commit it.
- Move worktrees outside the target repo entirely, e.g. under a per-project cache directory (`~/.cache/agentops/worktrees/<repo-hash>/<branch>/`), so the target repo's tree is never touched.
- At minimum, exclude the `.agentops/` path from the dirty check itself (`git status --porcelain -- . ':!.agentops'`); a smaller patch, but stray untracked files elsewhere still block merges, which is arguably correct behavior once the showstopper is gone.

**Status:** not tracked anywhere in `decisions.md`, `roadmap.md`, or `pending_tasks.md`. Exists only in the 2026-09-26 findings dump, which is explicitly not a decision per that file's own framing.

---

## 2. CRITICAL: Review gate is bypassable via the documented custom-workflow CLI path

Traced end to end across four files.

**`state.py:863-874`**, `refresh_workflow_status`:
```python
status = TaskStatus.PASSED if tasks and statuses == {TaskStatus.PASSED} else (...)
```
Returns `PASSED` the instant every task's status is `PASSED`, with no inspection of task `role` at all.

**`workflow.py:776-786`**, `verification_evidence`:
```python
return [task for task in self.state.list_tasks(workflow_id)
        if task.role == "verification" and task.verified and (...)]
```
Only checks for a `verification`-role task. No `role == "review"` check anywhere in this function.

**`cli.py:391`:**
```python
ready = status.value == "passed" and bool(evidence)
```
Both conditions above; neither requires a review-role task.

Contrast with `execution_model.py`'s `assert_workflow_ready`, the gate the standard `agentops task` flow enforces: it requires `verification_ok`, `review_ok`, and `evidence_present`, three signals instead of two.

**Concrete exploit:** run `agentops workflow some.yaml`, a documented, first-class command per the README, with a task list containing only `implementation` and `verification` roles and no `review` task. It reaches `ready=True` and auto-merges into the target repo with zero code review, despite the README's claim that merged code is "reviewed and verified."

**Fix:** make `refresh_workflow_status`, `verification_evidence`, and the `cli.py` readiness check share the same gate function as `execution_model.assert_workflow_ready`, one single READY predicate used by both the standard flow and the custom-workflow CLI path, instead of two inconsistent implementations.

**Status:** first flagged by the 2026-09-26 internal review; I independently re-derived and confirmed the full chain myself. Not present in `decisions.md`, `roadmap.md`, or `pending_tasks.md` as of this review.

---

## 3. HIGH: Git merge failures are misclassified, and the fix already exists in the codebase unused

**Files:** `finalize.py:75-84`, `git.py`'s `merge()`, and `failure.py`'s `FailureClassifier`

`merge()` raises `GitError` for four distinct causes: a dirty base worktree, a base branch that changed, a base commit that moved, or an actual `git merge --no-ff` conflict. `finalize_worktree` catches all four the same way:
```python
try:
    manager.merge(worktree)
except GitError as error:
    state.add_task(Task(
        f"Resolve Git merge conflict for '{description}'.\n{error}", "debugging", ...
    ))
```
Every one of them gets the identical task title, "Resolve Git merge conflict." When bug #1 fires (the untracked `.agentops/` directory), this text is actively misleading: there is no conflict, just an untracked directory, but the task tells whoever triages it to go looking for conflicting hunks that don't exist.

**What I found on this pass:** `failure.py` already contains purpose-built classification logic for exactly this problem. `FailureCategory.GIT_CONFLICT` and `FailureCategory.DIRTY_WORKTREE` exist as distinct categories (`failure.py:335-350`), each with its own severity, retry policy, and recommended repair action (`GIT_CONFLICT`: not retryable, request human approval; `DIRTY_WORKTREE`: retryable, retry the same agent). `FailureSource.GIT` and `FailureSource.MERGE` exist as dedicated enum values (`failure.py:50-51`). The classifier's text patterns would correctly categorize `git.py`'s actual error strings: the message for bug #1 ("Base worktree has uncommitted changes; refusing to merge agent worktree.") matches the `DIRTY_WORKTREE` pattern (`"uncommitted"`) and would classify correctly if it ever reached the classifier.

It never does. `grep` across the whole package (excluding tests) confirms `FailureClassifier.classify()` is only ever called from `workflow.py`, at six call sites, none of them in `finalize.py`. The live merge-failure path bypasses this classifier entirely and creates its hardcoded generic task instead.

There is a second, narrower path: `classify_interruption()` maps `InterruptionContext.MERGE` (a process that crashed mid-merge, recovered on restart) to `GIT_CONFLICT`, but collapses every merge interruption into that one category with no dirty/branch-moved/commit-moved distinction either, and it only fires on crash recovery, not on the synchronous refusal `finalize.py` handles. Either way, the live path in `finalize.py` uses neither mechanism.

**Fix:** in `finalize.py`, replace the hardcoded task text with a call to `FailureClassifier.classify(source=FailureSource.GIT, error=str(error))`, and use the returned category and recommended action to write a task message that names the actual cause. Since the classification logic and the enum values already exist and already match the real error strings, this is closer to wiring up unused code than writing new logic.

**Status:** the conflation itself was flagged on `roadmap.md:115` as an unaddressed classification gap. The fact that a working classifier already exists for it, and is simply never called, is new to this review.

---

## 4. HIGH: Task result persists raw, unredacted stdout and stderr to SQLite

**File:** `workflow.py:563`

```python
task.result = f"...{stdout}...{stderr}..."   # no redact_text() call
```

Every other persistence sink in the same file calls `redact_text()` first (confirmed at lines 206, 207, 282, 659). This one doesn't. If agent output contains a credential, token, or secret, it lands in the SQLite state store unredacted, contradicting the project's own stated rule against persisting secrets.

This is distinct from an already-fixed issue: `decisions.md`'s 2026-09-16 entry (A4) fixed unredacted `failure.evidence`, a different call site. This one was missed by that pass and is still open.

**Fix:** wrap the `stdout`/`stderr` interpolation in `redact_text()`, matching the other sinks in the file.

**Status:** not tracked in `decisions.md`, `roadmap.md`, or `pending_tasks.md`.

---

## 5. MEDIUM: No CI (tracked, but stalled)

`.github/` contains only `copilot-instructions.md`, no workflow files, confirmed by direct listing.

This one is on the roadmap: `roadmap.md:149`, "A10: CI/regression gating (P1)," and listed in `pending_tasks.md:94` under "Still Open." It's blocked on an open decision item:

> D5: "Room for GitHub Actions CI given local-only repo policy? Ask user (auth/env secrets)"

Per the file, this question was posed and never answered. It isn't "in flight," it's parked, waiting on you.

---

## 6. MEDIUM: Silent exception swallowing in the core cancellation and failure path

**File:** `workflow.py:579-586`, `:594-601`, `:607-614`, three near-identical blocks:

```python
try:
    self.record_failure(task, FailureClassifier.classify(...), ...)
except Exception:
    pass
```

`record_failure` already has its own fallback for the expected failure mode (a SQLite write failure): it calls `self.degradation.record("failure.create", error, ...)`, classified as `SAFE_TO_DEGRADE` per the A8 persistence-policy decision (`decisions.md`, 2026-09-26; `persistence.py:69`). So these three outer `except Exception: pass` blocks only fire on something unexpected: a bug in `Failure()` construction, the classifier, or the degradation recorder itself.

If that ever happens, it disappears with no log line, no event, no degradation record: the exact class of bug the A8 decision was built to eliminate elsewhere, uncovered here because `record_failure`'s known failure path is already handled and the unknown one isn't guarded.

**Fix:** replace the bare `except Exception: pass` with at minimum a `logger.exception(...)` call or an `unexpected_error` event before continuing, so an unexpected failure here is visible instead of silent.

---

## 7. CRITICAL: GUI: the "one operation at a time" guarantee can be defeated

**Files:** `gui_controller.py:225-226`, `260-271`, combined with `gui.py`'s event flow.

```python
_cancel_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
_operation_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

def _begin_operation(self) -> threading.Event:
    with self._operation_lock:
        if self._active:
            raise RuntimeError("An AgentOps operation is already running.")
        self._active = True
        self._cancel_event.clear()
        return self._cancel_event

def _end_operation(self, event: threading.Event) -> None:
    if event is self._cancel_event:
        with self._operation_lock:
            self._active = False
```

`_cancel_event` is one instance, reused (`.clear()`-ed, never replaced) across every operation. The `event is self._cancel_event` check in `_end_operation` is a tautology: every operation this controller ever runs gets back the identical object, so the check passes regardless of which operation the caller belongs to.

**Reachable race, traced through `gui.py`:**
1. Op 1 finishes normally. Its worker thread's `try` body sends `callback({"kind": "workflow-result", ...})` (`gui_controller.py:406-408`) before it reaches its own `finally` block.
2. The main thread processes `"workflow-result"`, calls `_finish_operation()` (`gui.py:886`), then `_set_running(False)` re-enables the Start button (`gui.py:921-930`), while op 1's worker thread is still inside its `finally` block (`state.close()`, `manager.remove(worktree)`).
3. The user clicks "Start" again immediately. `_start_operation` runs on the main thread, calls `controller.run_task(...)`, which calls `_begin_operation()`, setting `_active = True` for op 2 and clearing the shared `_cancel_event`.
4. Op 1's worker thread, still finishing its `finally` block, calls `self._end_operation(cancel_event)` with the same object reference it was handed at op 1's start. The `is` check passes trivially, so it sets `_active = False` while op 2 is genuinely running.
5. `_active` no longer reflects reality. A third click at this point would pass `_begin_operation`'s guard and launch a third worker thread against the same repo and worktree state concurrently with op 2.

This window opens on every operation, not just an edge case; it only needs a user, or a scripted UI driver, to click again promptly. The consequence is two `WorkflowEngine`/`GitWorktreeManager` instances operating against the same repo at once, exactly the failure mode `_active` exists to prevent.

**Fix:** mint a new `threading.Event()` inside `_begin_operation()` and store the new instance in `self._cancel_event`, instead of clearing the existing one in place. Then the identity check in `_end_operation` becomes meaningful, because a superseded operation's captured reference no longer matches the current `self._cancel_event`.

---

## 8. LOW: GUI: `_root_cache` is shared mutable state with no lock

**File:** `gui_controller.py:233-246`

```python
def _operation_root(self, directory: str | Path) -> Path:
    key = str(directory)
    cached = self._root_cache.get(key)
    ...
    self._root_cache[key] = str(root)
    return root
```

Called from both worker threads (during task or agent execution) and the main thread (`_poll_status` to `_refresh_status`, confirmed running via `.after()` in `gui.py:947-958`), with no lock. The docstring acknowledges the concurrent-access pattern ("the GUI resolves this on every poll") but doesn't guard it, inconsistent with `_active`/`_cancel_event`, which do get a lock, albeit a broken one (see #7). Under CPython's GIL this won't corrupt memory, but it's worth locking for consistency rather than relying on GIL implementation details.

**Fix:** wrap `_root_cache` reads and writes in the existing `_operation_lock`, or give it its own small lock.

---

## 9. LOW: Dead code in the module the internal review called "the strongest in the package"

**File:** `verification_kernel.py:182`

```python
snapshot = profile_to_dict(profile)   # never read again
```

Confirmed by `ruff` (F841) and by tracing every reference. Not a functional bug: the actual persisted `profile_snapshot` column is computed by a separate, redundant call to `profile_to_dict(profile)` inside `state.py:1283`, so verification runs still get their profile snapshot recorded correctly. This is leftover cruft from an incomplete refactor. The computation likely used to be passed down from here, and now happens twice, with this copy discarded. Worth a one-line cleanup; doesn't affect correctness.

---

## 10. LOW: Comment hygiene, decorative section-banner comments

Twelve instances of dash-line banner comments wrapping a section label, e.g.:
```python
    # ------------------------------------------------------------------
    # AgentRun persistence
    # ------------------------------------------------------------------
```
Locations: `state.py:876-878`, `state.py:1186-1188`, `state.py:1567-1569`, `agent_result.py:98-100`, `agent_result.py:421-423`, `agent_result.py:543-545`.

This is purely stylistic and has no effect on correctness. It's the kind of banner comment that reads as generated rather than left by an engineer organizing a large file. Not urgent; worth a pass if you're already touching these files for other fixes.

**Fix:** replace each banner with a single plain comment line naming the section (`# AgentRun persistence`), or remove it if the surrounding method names already make the grouping obvious.

---

## 11. Static analysis sweep (`ruff`, bug-relevant rule sets only: F, B, E9, PLE, C4, SIM, RUF)

91 findings across both products.

| Count | Rule | What it is | Verdict |
|---|---|---|---|
| 20 | SIM105 | bare `try/except: pass` (should be `contextlib.suppress`) | Checked the highest-stakes ones; see #6 above for the three that matter. The rest, in `runtime.py`, `state.py`, `gui.py`, `logging.py`, `persistence.py`, `artifacts.py`, are narrow `OSError`/platform-shim catches (e.g. `os.chmod` failing on Windows) and are fine as is. |
| 13 | B905 | `zip()` without `strict=` | All 13 are in `universal-game-agent/tests/`, not production code. Low priority. |
| 12 | F401 | unused imports | Cosmetic. |
| 10 | B904 | missing `raise ... from err` | Breaks exception-chain clarity in tracebacks; doesn't change behavior. |
| 9 | RUF022 | `__all__` not sorted | Cosmetic. |
| 6 | RUF059 | unused unpacked variable | Cosmetic. |
| 5 | SIM102 | nested `if` collapsible | Cosmetic. |
| 4 | RUF012 | mutable class-attribute default | Checked both (`toy_pong.py:59`, `external_game.py:140`), both are Gymnasium's standard `metadata = {...}` class-level convention, read-only in practice. Not bugs. |
| 3 | RUF100 | unnecessary `# noqa` | Cosmetic. |
| 1 each | SIM117, RUF005, SIM115, RUF102, F841, C416, C401 | misc style/dead code | `F841` is #9 above. `SIM115` (unclosed file handle in `external_experiment.py:106`) is explicitly commented as intentional ("closed with the process"), fine for a short-lived training script. |

**Takeaway:** aggregate hygiene issue (20 unaudited bare excepts) worth a cleanup pass given the project's stated discipline around error visibility, but only 3 of the 20 (item #6) are load-bearing.

---

## 12. Test suite results

Ran the `agentops` suite myself (pure stdlib, no dependencies needed beyond installing `python3-tk` for GUI tests).

- 382 tests total: 315 non-GUI plus 67 GUI, after installing `tk`.
- One reproducible, deterministic failure: `test_posix_terminate_uses_process_group` in `test_runtime.py`, failed identically on three isolated runs, each time after a roughly 10-second cleanup timeout, expecting `b"partial"` output but getting empty output plus a timeout message.
- Caveat: this may be a sandbox or container artifact (process-group and session semantics can differ under containerized environments) rather than a genuine bug on a bare-metal Linux or Windows host. I couldn't rule this out without a bare-metal comparison; treat it as needing verification on your actual dev machine, not as confirmed broken.
- No leaked secrets found in the tracked tree; a grep for key-shaped strings only matched the redaction test's own AWS example fixture, correctly redacted.
- `universal-game-agent`'s test suite was not run (requires `torch`/`gymnasium`, judged not worth the install time given `agentops` is the flagship).

---

## 13. What was checked and found clean

- **`routing.py`** (711 lines, the deterministic agent router): scoring function, historical-performance calculator (guards divide-by-zero correctly), priority-ordering convention (consistent between the static and deterministic selection paths; lower number means higher priority in both), and the preference/fallback-tagging logic (`route():685`, all three cases traced). No bugs found. Legitimately solid.
- **`claim_task`** (`state.py:595`): genuinely atomic, a conditional `UPDATE ... WHERE id=? AND status=?` that correctly relies on SQLite's writer serialization for cross-process safety, checking `rowcount` before treating a claim as successful. Correct as designed.
- **`config.py`**: command templates are validated as non-empty string lists (`_command()`), YAML is loaded via `yaml.safe_load` rather than the unsafe loader, and prompt substitution (`agent_adapter.py:178`, `argument.replace("{prompt}", prompt)`) happens per-argv-element before `create_subprocess_exec`. No shell is involved anywhere, so arbitrary prompt content can't cause command injection. This boundary is sound.
- **GUI thread-marshaling pattern**: `_emit()` (`gui.py:856-863`) never touches Tk widgets directly from a worker thread; it correctly schedules via `self.root.after(0, self._handle_event, event)`, and `_handle_event` re-validates `operation_id` and `_is_alive()` before acting. The mechanism for crossing the thread boundary is right; the bug (#7) is in the mutual-exclusion primitive layered on top of it, not in the marshaling itself.

---

## 14. Priority order if you're fixing these

1. **#1**, worktree/.gitignore merge bug: blocks the entire product's primary use case on first real run. Fix first.
2. **#7**, GUI mutual-exclusion bypass: data-corruption risk (concurrent operations against the same repo) if the GUI is used at all.
3. **#2**, review-gate bypass: the security and quality guarantee is false for a documented CLI path.
4. **#4**, unredacted task.result: credential-leak risk into persisted state.
5. **#3**, Git failure misclassification: compounds #1, and the fix is mostly wiring up code that already exists. Fix alongside #1.
6. **#6**, silent exception swallowing: visibility gap, lower urgency but cheap to fix.
7. **#8, #9, #10**: cleanup, no functional impact.
8. **#5**, CI: answer D5 yourself; agents can't unblock this one.

None of items 1, 2, 3, 4, 6, or 7 currently exist in `decisions.md` or `pending_tasks.md`. If you want a future agent session to act on this without rediscovering it, the fastest path is having that session read this file and promote items 1 through 10 into `decisions.md`/`pending_tasks.md` before it does anything else.
