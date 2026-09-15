# Lessons Learned (canonical, append-only)

> Bugs, root causes, solutions, environment problems, recurring mistakes. Keep concise. Never store secrets.

### 2026-09-14 — Windows EPERM fsync in Agent Intercom durable-json.ts

- **Symptom:** `writeDurableJson()` throws `EPERM: operation not permitted, fsync` on Windows, breaking all durable state writes (outbox, inbox, access registry, health, named teams).
- **Root cause:** `openSync(temporaryPath, "r")` creates a read-only file handle. Windows `FlushFileBuffers` requires `GENERIC_WRITE`, so `fsyncSync` fails. POSIX allows fsync on read-only fds, making this Windows-only.
- **Solution:** Change to `openSync(temporaryPath, "r+")` — opens existing file read/write without truncation. For `@ctliz/agent-intercom-opencode`, also updates `DurableJsonFileOperations` interface type from `"r"` to `"r" | "r+"`.
- **Upstream status:** Fix NOT yet applied in `ctliz/agent-intercom-pi` (v0.12.2) or `ctliz/agent-intercom-opencode` (v0.12.1). Patch artifacts validated via `git apply --check`.
- **Remember:** On Windows, any `fsync`/`FlushFileBuffers` requires a writable handle. `"r+"` preserves the existing file content while adding write access needed by `FlushFileBuffers`. Never use `"w"` (truncates) or blanket try/catch (swallows legitimate errors).

## Entries

- *(No further product lessons — greenfield, no code as of 2026-09-12.)*

### 2026-09-13 — opencode review blocked: backend Unauthorized

- **Symptom:** `agentops run opencode` failed in 9.6s: `Unauthorized: authentication_error` (model qwen3.8-27b) despite `agents` showing ONLINE (only proves the binary exists).
- **Root cause:** Missing/invalid backend auth in the opencode environment — environmental, not a code bug.
- **Solution:** Record and proceed; do not retry blindly. ONLINE ≠ authenticated.
- **Remember:** `agents` ONLINE means executable found; auth must be verified by an actual run.

### 2026-09-13 — fcc-claude review blocked: proxy unreachable

- **Symptom:** `agentops run fcc-claude` failed in 3.3s: proxy not reachable at `http://127.0.0.1:8082`.
- **Root cause:** `fcc-server` not running — environmental.
- **Solution:** Start `fcc-server` in another terminal before using fcc-claude.
- **Remember:** fcc-claude depends on a separately started local server.

### 2026-09-13 — CLI crashed printing copilot Unicode output (cp1252)

- **Symptom:** Successful copilot run crashed in `cli.py` with `UnicodeEncodeError: 'charmap' codec can't encode '\u2717'`.
- **Root cause:** `print()` of agent stdout on Windows narrow-encoding consoles.
- **Solution:** `_print_text()` helper encodes with `errors="replace"` fallback. Added during review follow-through.
- **Remember:** Never `print()` raw agent output on Windows; agent text is arbitrary Unicode.

### 2026-09-13 — Frozen exe silently omitted the package (entry-point bug)

- **Symptom:** First `AgentOps.exe` build contained only a `gui` script, no `agentops.*` modules — the app could not start.
- **Root cause:** PyInstaller entry was `agentops/gui.py` directly, breaking package-relative imports.
- **Solution:** Package-aware `agentops_gui.py` launcher; verify every build via `pyi-archive_viewer` (expect `agentops.*`, Tk, sqlite3, `agents.yaml`) + launch smoke test.
- **Remember:** Always archive-inspect the exe; a successful build ≠ a working bundle.

### 2026-09-13 — Tk calls from worker threads are unsafe

- **Symptom:** Review flagged `gui._emit` calling `winfo_exists()` off the Tk thread (race with close/destroy).
- **Root cause:** Convenience liveness check in the wrong thread.
- **Solution:** `_emit` only queues via `root.after()` inside try/except; liveness checked in `_handle_event` on the Tk thread; operation-ID tags drop stale callbacks.
- **Remember:** Worker threads must never touch Tk widgets, not even for reads.

### 2026-09-13 — commit_changes treated any nonzero diff exit as "has changes"

- **Symptom:** `git diff --cached --quiet` exit 128 (inspection failure) would fall through to a commit attempt with the wrong error.
- **Root cause:** Only exit 0 was distinguished; `git diff --quiet` uses 1 = differences, other = error.
- **Solution:** Raise `GitError` for any nonzero code other than 1; regression test with mocked 128.
- **Remember:** `git diff --quiet` is tri-state: 0 clean, 1 differs, other = failure.

### 2026-09-13 — Windows log names need as_posix()

- **Symptom:** Controller test comparing log entry names failed: `relative_to` yields backslashes on Windows.
- **Root cause:** OS-specific separators in a cross-entry identifier.
- **Solution:** `list_logs` returns `as_posix()` names; `read_tail` accepts both forms.
- **Remember:** Normalize paths crossing a UI/persistence boundary.

### 2026-09-13 — Intercom recovered; live names differ from 2026-09-12 roster

- **Symptom:** 2026-09-12 memory names (`codex-builder`, `agy-reviewer`, `opencode-arch`) have no live counterparts; `intercom_list` shows `pi-manager` (self), `claude`, `copilot`, `opencode-projects-6896` — plus 0 queued messages, so the EPIPE outage is over.
- **Root cause:** Sessions are ephemeral; roster memory went stale within a day.
- **Solution:** Reconciled team.md to verified reality; rule: resolve the exact live name from `intercom_list` before messaging, never message remembered names blindly. Note the live `claude` session is out of scope per user restriction.
- **Remember:** `intercom_team` = configured targets, `intercom_list` = live sessions; trust list for liveness.

### 2026-09-12 — Intercom display-name truncation

- **Symptom:** `intercom_list` shows `Codex (codex-bu)` and `AGY (agy-revi)` while canonical targets are `codex-builder` / `agy-reviewer`.
- **Root cause:** Display truncation in list output; `intercom_team` shows canonical targets.
- **Solution:** Always message canonical targets (`opencode-arch`, `codex-builder`, `agy-reviewer`).
- **Remember:** Do not treat truncated IDs as the address.

### 2026-09-12 — Transient Intercom send failure to busy peer

- **Symptom:** 2x `intercom_send` to `opencode-arch` failed with "disconnected before acknowledging" while it showed `busy`/`retry`; `intercom_team` still showed `[connected]`.
- **Root cause:** Peer busy, likely transient.
- **Solution:** Retry later; files on disk remain readable; re-notify before next architecture request.
- **Remember:** A failed delivery is a new disconnect signal — investigate with status/logs, don't assume normal delay.

### 2026-09-12 — Never grep session logs or dump env

- **Symptom:** Searching session history can surface past tool outputs containing secret values.
- **Root cause:** Prior turns dumped environment into session files.
- **Solution:** Never grep `~/.pi/agent/sessions/`, never print env, never copy secret values into memory/chat/MCP.
- **Remember:** Memory files and Intercom relays must stay secret-free.

### 2026-09-12 — Intercom transport outage during memory reviews (EPIPE, peers missing from list)
- **Symptom:** `intercom_ask` x3 + `intercom_status` failed `write EPIPE`; `intercom_send` to `opencode-arch` failed "disconnected before acknowledging"; `intercom_list` showed only `opencode-arch` + `outbox:2` while `intercom_team` still listed all three coworkers `[connected]`. Codex/AGY sessions missing from list.
- **Root cause:** Intercom broker/transport degradation (not a config change; per directive, no reinstall/reconfig attempted).
- **Solution:** Peer reviews (OpenCode arch, Codex conventions, AGY structure) + Supermemory checks for Codex/OpenCode/AGY + responsibility confirmations are PENDING — retry when transport recovers. Memory files on disk remain the fallback source of truth.
- **Remember:** `intercom_team` (configured targets) can disagree with `intercom_list` (live sessions); trust list for liveness, team for canonical addresses; queued `outbox` means retries pending.

### 2026-09-14 — AgentRun lifecycle needs explicit terminal transitions

- **Symptom:** New AgentRun tests left runs in `running` because `finish_agent_run()` rejected `pending -> completed/failed` transitions.
- **Root cause:** The transition table only modeled live-process sequencing, while post-hoc recording and failure fallbacks legitimately finish from `pending`.
- **Solution:** Allow direct `pending -> terminal` transitions; keep invalid transitions such as `completed -> starting` rejected.
- **Remember:** Persistence compatibility paths need lifecycle coverage, not only the happy-path runner sequence.

### 2026-09-14 — Runner doubles may accept observer arguments without using them

- **Symptom:** Workflow tests passed observer-aware fake runners, but no AgentRuns were recorded.
- **Root cause:** Workflow assumed observer support meant runner-owned persistence; the doubles ignored those arguments and returned results without run IDs.
- **Solution:** Wrap observer-capable runners with a creation recorder and post-hoc record results when no run was created; preserve true runner-owned persistence when a run ID is returned.
- **Remember:** Capability-shaped arguments are not proof of behavior; verify with returned IDs or observable effects.

### 2026-09-14 — Windowed exe terminate does not guarantee process exit

- **Symptom:** A PyInstaller smoke test reported `exit_after_terminate=1`, yet `AgentOps.exe` remained listed by `tasklist`; a later rebuild failed with `PermissionError` on `dist/AgentOps.exe`.
- **Root cause:** `Popen.terminate()` did not stop the windowed GUI process; the lingering process locked the executable.
- **Solution:** After a smoke-test terminate/wait, verify with `tasklist` and use `taskkill /F /IM AgentOps.exe` before rebuilding.
- **Remember:** A successful smoke-test print is not proof the GUI process exited; check OS process state before the next build.

### 2026-09-14 — No reviewer peers or worker listing during AgentRun work

- **Symptom:** `intercom_list` showed only the current session; `agent_fleet list --all` failed with a Windows `flock` worker-lock error.
- **Root cause:** No opencode/fcc-claude/copilot peers were connected, and the worker-state lock path was unavailable.
- **Solution:** Left independent-review subtasks blocked without messaging unavailable peers; relied on new regression coverage plus real direct/workflow end-to-end checks.
- **Remember:** Do not route work to remembered coworker names when liveness checks fail.

### 2026-09-14 — ALTER TABLE appends columns; positional inserts break legacy DBs

- **Symptom:** New verification tests failed on a legacy `tasks` table with `NOT NULL constraint failed: tasks.updated_at` — the migration added `verified`/`verification_run_id` at the end, but the insert assumed new-table column order.
- **Root cause:** SQLite `ALTER TABLE ADD COLUMN` always appends; positional `INSERT INTO ... VALUES` is order-fragile across migrations.
- **Solution:** Use explicit column lists for all inserts into migrated tables (fixed `add_task`; `agent_runs`/verification inserts already explicit). Regression test with a legacy-schema database.
- **Remember:** Never use bare `INSERT INTO <migrated-table> VALUES` — always name the columns.

### 2026-09-14 — Copilot snapshot review caught recovery misclassification

- **Symptom:** `recover_verification_runs()` set `required_failures` equal to the failed count and rewrote stranded checks without required/optional distinction.
- **Root cause:** Recovery counted statuses before considering the `required` flag.
- **Solution:** Count `required AND status IN (...)` after marking stranded checks `interrupted`; preserve already-terminal CANCELLED/TIMED_OUT rows; added a required-vs-optional recovery regression test.
- **Remember:** Crash-recovery paths need their own accounting tests, not just happy-path lifecycle tests.

### 2026-09-14 — Opencode live session listed but unreachable

- **Symptom:** `intercom_list` showed `opencode-projects-21924` as busy, but two `intercom_send` deliveries failed with recipient-disconnect; `agentops run opencode` fails with backend Unauthorized.
- **Root cause:** Stale liveness listing and missing backend auth — environmental, not a code bug.
- **Solution:** Recorded the architecture review as blocked; did not retry blindly or message other names.
- **Remember:** A failed delivery is a disconnect signal even when the session still appears in the list.

### 2026-09-14 — Fail-fast parallel must only cancel on terminal failures

- **Symptom:** Opencode review noted `_run_group` cancelled PENDING/RUNNING siblings as soon as any check finished, even a passing one.
- **Root cause:** The guard tested `status is not PASSED` over the whole group instead of terminal failure states.
- **Solution:** Cancel siblings only on `FAILED`/`TIMED_OUT`/`CANCELLED`; added a fail-fast parallel regression test where the first finisher passes.
- **Remember:** Liveness-unaware `any(not done)` checks are a classic parallel-execution bug — assert on terminal states.

### 2026-09-14 — External cancellation must finalize verification runs

- **Symptom:** `asyncio.CancelledError` propagated out of `run_verification` leaving the run `RUNNING`; only the threading-event path was finalized.
- **Root cause:** No try/finally around the execution loop; recovery later collapsed the evidence to generic failure.
- **Solution:** Catch `CancelledError`, mark unfinished checks `CANCELLED`, persist a `CANCELLED` report, then re-raise; regression test cancels the asyncio task mid-run.
- **Remember:** Every async entry point that persists lifecycle state needs a cancellation finalizer, not just the cooperative-cancel path.

### 2026-09-14 — Rehydrate every persisted enum, not just statuses

- **Symptom:** `VerificationRun.mode` came back from SQLite as a plain string while statuses were wrapped in enums.
- **Root cause:** One missed `VerificationProfileMode(...)` wrap in `_verification_run_from_row`.
- **Solution:** Wrap on read; regression test asserts the enum type. GUI workaround retained for backward compatibility.
- **Remember:** When adding a row mapper, check every enum-typed column, not just the status ones.

### 2026-09-14 — Test helper overwrote the stub it was given

- **Symptom:** `test_failed_agent_task_records_failure` recorded ENVIRONMENT_FAILURE instead of AGENT_ERROR.
- **Root cause:** The `_engine()` helper unconditionally set `registry.select.return_value = None` even when a configured registry was passed in, forcing the no-agent path.
- **Solution:** Only install the default stub when no registry is provided.
- **Remember:** Shared test helpers must not mutate caller-provided doubles; default-stub only on None.

### 2026-09-14 — FK on failures.agent_run_id blocked no-agent recording

- **Symptom:** `create_failure` raised `FOREIGN KEY constraint failed` for failures without a persisted run.
- **Root cause:** `agent_run_id REFERENCES agent_runs(id)` with `foreign_keys=ON` rejects IDs that were never persisted (exactly the no-agent case).
- **Solution:** Run references in `failures` are plain TEXT without REFERENCES; workflow linkage stays FK-constrained.
- **Remember:** Failure tables must accept evidence from paths where the referenced run was never created.

### 2026-09-14 — Git-bash mangles taskkill/tasklist slash flags

- **Symptom:** `taskkill /F /IM AgentOps.exe` failed with `Invalid argument/option - 'F:/'`.
- **Root cause:** The bash layer rewrites `/F` as a POSIX path.
- **Solution:** Route through `cmd //c "taskkill /F /IM AgentOps.exe"`.
- **Remember:** Windows slash-flags need `cmd //c` (double slash) from git-bash.

### 2026-09-14 — pyi-archive_viewer PYZ entry is named PYZ.pyz

- **Symptom:** Opening `PYZ-00.pyz` showed nothing; module list empty.
- **Root cause:** This build names the archive `PYZ.pyz` (visible in the top-level `l` listing as type `z`).
- **Solution:** `printf 'o PYZ.pyz\nl\nq\n' | pyi-archive_viewer dist/AgentOps.exe`, then grep for `agentops`.
- **Remember:** Check the top-level listing for the exact `z`-type entry name before assuming `PYZ-00.pyz`.

### 2026-09-14 — Copilot Phase 3 review: 4 findings, all fixed
- **Symptom:** Post-merge review of the Failure Kernel reported (1) workflow-scoped `recover_incomplete` calling unscoped run/verification recovery, (2) non-idempotent recovery minting duplicate Failures, (3) cancellation repair bypassing `RetryPolicy` with a fixed `next_attempt`, (4) permission-denied errors classifying as UNKNOWN.
- **Root cause:** (1) New `workflow_id` filter params were never threaded through; (2) no (task, recovery_state) idempotency key; (3) cancellation branch returned before any budget check; (4) the environment matcher advertised permission tokens but only returned for systemroot text.
- **Solution:** Optional `workflow_id` on `recover_agent_runs`/`recover_verification_runs` (backward-compatible); skip when a matching recovery_state Failure exists; cancellation returns RETRY only within budget with `next_attempt+1`, else STOP; direct ENVIRONMENT return for permission/eacces/eperm. Four regression tests added (150 passing).
- **Remember:** Recovery paths need multi-workflow, repeat-call, and budget-exhaustion tests from the start — single-workflow happy-path tests hid all four.

### 2026-09-15 — Copilot structured-results review: 4 findings + 1 self-found via regression test
- **Symptom:** Snapshot review reported (1) `coerce_agent_result` rejecting JSON TEXT, (2) `evaluate_execution` trusting truthy `process_success` (e.g. `"false"` counted as success), (3) unsupported schema versions downgrading only SUCCESS to PARTIAL, (4) parse warnings discarded before persistence. A new hostile-payload regression test then crashed on `repr()` of a hostile object inside a warning f-string.
- **Root cause:** (1) Coercion assumed pre-decoded values though the column is TEXT; (2) bool annotation without runtime normalization; (3) downgrade guard checked `is SUCCESS` instead of all statuses; (4) runner stored `result.to_dict()` without folding `ParsedAgentResult.warnings`; (5) `{value!r}` in warning paths calls user-controlled `__repr__`.
- **Solution:** JSON-text branch in `coerce_agent_result`; `_coerce_bool_signal` with string spellings; downgrade any status to PARTIAL with `original_schema_version` in metadata; `_safe_*` helpers fold warnings + `parse_mode` into stored envelopes; `_safe_repr`/`_safe_get` make `agent_result_from_dict` total. Six regression tests added (184 passing).
- **Remember:** Total functions must distrust `repr`/`str` of caller-controlled values; warning paths are crash paths too.

### 2026-09-15 — FK refs break crash-recovery event recording (Phase 4)
- **Symptom:** New `typed_events`/`artifacts` tests failed with `FOREIGN KEY constraint failed` when recording events for workflow ids with no `workflows` row.
- **Root cause:** `REFERENCES workflows(id)` with `foreign_keys=ON` rejects evidence for runs/workflows that were never persisted — exactly the crash-recovery case.
- **Solution:** Plain TEXT refs without REFERENCES (same fix as the `failures` table lesson 2026-09-14). Lesson now applied proactively: event/artifact tables were designed FK-free after the first test failure.
- **Remember:** Any table that stores recovery/crash evidence must not FK-constrain the thing it reports on.

### 2026-09-15 — Root-level unittest invocation shadows the package (Phase 4)
- **Symptom:** `python -m unittest discover -s agentops/tests` from the repo root ran 128 tests with 8 import errors; the documented `cd agentops` invocation runs 184+ clean.
- **Root cause:** The root `agentops/` project folder shadows the `agentops` package for namespace resolution.
- **Solution:** Always run the suite from `agentops/`; recorded in project memory.
- **Remember:** After the single-repo fold, the suite only runs from the `agentops/` directory.

### 2026-09-15 — Concurrent first-open SQLITE_LOCKED + leaked handles (Phase 4)
- **Symptom:** New concurrent-migration stress test flaked ~25% with `database is locked` at `PRAGMA journal_mode=WAL`, plus Windows file-cleanup failures from unclosed handles.
- **Root cause:** (1) Retry covered only migrations, not the WAL pragma (busy_timeout does not cover SQLITE_LOCKED snapshot/mode conflicts); (2) failed `__init__` leaked the connection (test `finally` skipped close when `store` stayed None).
- **Solution:** Retry the whole open (8 attempts, backoff, close-on-failure); test closes handles in `finally`. 10/10 stress runs clean.
- **Remember:** Retry must cover connection setup through migrations, and every failed open must close its handle — especially on Windows where open files block cleanup.

### 2026-09-15 — Phase 1/3 test fallout (2 fixes)
- **Symptom:** New run failed: `IndentationError` in `cli.py status` (worktree-ref print spliced inside the failure loop) + `test_concurrent_migration_single_version` asserting `[1..5]`.
- **Root cause:** Edit inserted the provenance print at the wrong indent; schema v6 legitimately adds a 6th migration version.
- **Solution:** Re-indented CLI block (failures loop intact, provenance after); updated test to `[1..6]`. Suite 224 OK.
- **Remember:** When splicing prints into a loop body, verify the `for` suite boundary; bump migration-version assertions with every additive schema change.

### 2026-09-15 — Validators vs suite: treat violations as (refined) bugs (A1)
- **Symptom:** New A1 validators broke 5 then 1 existing tests: legacy-verified tasks carry no `verification_run_id`; `verification_evidence()` was kernel-only; `Verifier(())` vacuous-passed.
- **Root cause:** Invariants written kernel-first ignored the preserved legacy contract (output IS the evidence) — and the legacy contract contained one real fabrication (empty suite passes).
- **Solution:** Evidence = kernel run OR non-empty transcript (documented in matrix); `verification_evidence()` unified; empty suites fail at the source (`bool(results) and all(...)`); Review #2 test renamed with supersede note. Suite 251 OK (27 new).
- **Remember:** When a new invariant breaks old tests, check whether the invariant or the test encodes the bug — here it was both (refine the rule, fix the fabrication), and record superseded decisions explicitly so the user can overrule.

### 2026-09-15 — Optional failures legally coexist with PASSED (A1R self-found)
- **Symptom:** `assert_report_consistent` rejected any PASSED report with `failed_checks > 0`; but `_summarize_counts` counts optional-check failures in `failed`, and overall PASSED depends only on `required_failures == 0` — a PASSED-with-optional-failures report is legitimate and would have hard-aborted a healthy workflow.
- **Root cause:** Validator written against the strict reading ("zero failed checks") instead of the kernel's actual rule (required-failures decide).
- **Solution:** PASSED requires `required_failures == 0` + `passed_checks >= 1` (also closes the all-skipped hole); optional-failure regression test added.
- **Remember:** Validators must be checked against the producer's real semantics (`_summarize_counts`), not the docstring idealization — especially for counters with mixed populations (required vs optional).

### 2026-09-15 — Local import breaks a config↔adapter cycle (A2)
- **Symptom:** `config.py` needs the `Capability` vocabulary (warn on unknown names); `agent_adapter.py` needs `AgentConfig` (wraps it) — module-level imports cycle.
- **Root cause:** Vocabulary ownership (adapter) vs validation site (config) pull opposite directions.
- **Solution:** Function-local import inside `load_config` with a comment (deferred to call time, both modules loaded). Keeps the correct ownership (adapter owns vocabulary) without restructuring.
- **Remember:** Overlay direction (adapter→config) forbids config→adapter at module level; localize the import and say why.

### 2026-09-15 — Windowed exe flashes consoles for every child process
- **Symptom:** User saw "tons of powershell commands" + desktop hogging + slow UI from the exe.
- **Root cause:** A windowless parent (pythonw/PyInstaller-windowed) gives every console child a visible window unless `CREATE_NO_WINDOW` is set. Git spawns sit on hot paths (1s status poll → `repository_root` → git every second; metadata collector; agent/kernel spawns), and the poll also rebuilt the whole task tree + full workflow payload on the Tk thread.
- **Solution:** Central `_no_window_kwargs()` in `git.py`, applied at all spawn sites (git, collector, runner OR-ed with NEW_PROCESS_GROUP, legacy verifier, kernel `_default_spawn` wrapper so injected test factories keep working); controller caches git roots (never failures); `_update_tasks` signature-gated. 7 regression tests; suite 285 OK.
- **Remember:** Every new subprocess spawn in AgentOps must consider the windowed-build case — no console child may flash. Test flags via mocks on each site.

### 2026-09-15 — Weight-only-one-row starves fixed-height siblings (Tk grid)
- **Symptom:** After adding scrollbars, the prompt text box was literally unmapped (0px cell); pre-change probe showed it was a 4px sliver — already broken, my change just finished it.
- **Root cause:** Only the notebook row had grid weight; fixed-height siblings (trees, output text) claimed the window first, leaving the notebook ~99px. Weight distributes surplus AND deficit — a single weighted row absorbs all shortfall.
- **Solution:** Weights on all three body rows (2/1/1) + trim fixed heights (trees 6→5, output 8→6) + taller default geometry (740). Diagnose with `grid_bbox` row heights on a mapped window, and always A/B against the parent commit via a temp worktree.
- **Remember:** Never ship a Tk layout change without mapping a real window and measuring cells — headless `grid_info` assertions cannot catch starvation.

## Environment-specific problems

- Repo root `D:/admin/code/projects`; no stack/test runner configured yet — record pitfalls here once encountered.
