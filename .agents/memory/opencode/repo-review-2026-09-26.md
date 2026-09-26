# Full repository review, 2026-09-26

Read-only review of the whole container repo at `D:\admin\code\projects`. No files were
modified. Four specialist lanes: agentops product, universal-game-agent product,
  agent-process layer, repo-wide hygiene sweep. Plus four deeper lanes: roadmap-vs-reality
audit, agentops test-quality audit, UGA unread-modules deep dive, and instruction-file
drift matrix.

**Status of this file:** a findings dump, not a decision. Nothing here is approved. Read
`.agents/memory/decisions.md` for what is actually decided.

## Scope of the review

- 197 tracked files, 1,811,102 bytes (1.73 MB), 20 commits.
- 99 Python files (~22,498 lines), 71 markdown files (~7,667 lines).
- ~59 MB of untracked binary state on disk (checkpoints, dist/, logs, sqlite). 0 bytes tracked.
- Two independent products plus a hand-built agent-process layer.
- Neither product's test suite was executed. Recorded baselines (agentops 357/4 skipped,
UGA 261) predate the current working tree and are not trustworthy.

**The tree moved twice during this review.** A parallel writer committed `6890c42`, then
began an in-flight `agentops/persistence.py` feature (6 modified files in
`agentops/agentops/`, plus untracked `persistence.py` and `tests/test_persistence.py`).
Findings 6, 15, and 16 below were observed live rather than inferred. A later commit
(`018de82`) landed that A8 work (382 tests, 4 skipped), so the in-flight feature is now
committed. The deep-dive lanes verified this against the current tree.

## Shape of the result

The code is in notably better shape than the process layer. The agentops verification
kernel, process runtime, and artifact store all survived inspection and are the strongest
code in the repo. Meanwhile almost every "current state" fact in memory has drifted from
the tree it describes, and the largest cluster of findings is instruction drift, not
defects.

---

## Do first

### 1. Tracked external-Pong results are measurement artifacts, not gameplay

`universal-game-agent/experiments/exp_external_pong_02-15516_results.json`: 82-83% of
episodes in *both* baseline and trained are length exactly 1 with 0 misses, and
`terminated_episodes=100`. A length-1 episode that terminates with zero misses is
impossible, `ExternPongTermination.terminated()` fires iff the MISS banner is visible
(`environment/extern_pong_rewards.py:77-78`) and the banner edge pays `-1` (`:58-59`).
Zero misses means `prev_banner` was already true, so `:60-61` returns `0.0`. The reset
landed on a terminal frame, the episode "ended" before playing, and it was scored 0.

That structural zero sits inside the reported `mean_reward` and `std_reward`, so the
headline `-0.15 -> -0.11` "learning" delta rests on roughly 17 informative episodes. The
untracked `exp_external_pong_01-14676_results.json` shows the same pattern (7 of 8). The
in-tree `reset_settle_timeout_s` fix (`environment/external_game.py:151,256-271,459-466`)
targets this but has regenerated no results file.

`checkpoints/README.md:20-24` currently cites the contaminated
`exp_external_pong_compare01_results.json` as the provenance for the only external weights.

**Action: regenerate both results files under the settle fix before citing any external
number.**

### 2. Leaked OpenCode key redacted in-tree, still in history, rotation never recorded

A key-shaped token is present in `.agents/memory/pi-opencode-free-tier-fix.md` at commits
`99e0be2, 65ef097, 7b78659, c057fed, 533a899`. The remote is
`https://github.com/angasko-12345/projects-ai.git`, 50 commits on `main`, HEAD `6890c42`.
Redaction landed in `5627490` and the working tree is clean.

Rotation is recorded in exactly one place,
`.agents/memory/opencode/sessions/2026-09-26-repo-reorganization.md:143` ("Rotate the
exposed API key"), a session log no instruction file requires reading.

No `.env`, `*.pem`, `*.key`, or `credentials*` file exists in the tracked tree, and a
regex sweep of all 19 files under `.agents/` returns only 3 hits, all `ses_`-prefixed
format-only session identifiers that the repo's own probes document as non-credentials.
The residual exposure is git history plus missing rotation, not a live leak.

**Action: rotate the key, add a dated `decisions.md` entry recording the rotation (or
explicitly recording "not yet rotated"), and add one sentence to the secret rule, a
credential in history is compromised until rotated, and the rotation must be logged.**

### 3. `agentops task` fails on any target repo that has not hand-patched its `.gitignore`

`agentops/agentops/git.py:100` creates worktrees at `<repository>/.agentops/worktrees/<branch>`,
  inside the target repo. `git.py:239-243` then refuses to merge when the base
`git status --porcelain` is non-empty, and `??.agentops/` is exactly that. Nothing in
the package writes or verifies a `.gitignore` entry; there is no `gitignore` or
`check-ignore` reference anywhere under `agentops/`.

`agentops/README.md:67` claims the state is stored under `<target-repo>/.agentops/` "and
ignored by Git", which is false for arbitrary targets. `agentops/.gitignore:1` hides the
bug in development.

**This is the primary product path. It is broken.**

### 4. The custom-workflow feature bypasses the review gate

`agentops/agentops/execution_model.py:49-50` and `assert_workflow_ready`
(`:186-201`) define READY as passed+verified verification **and** a passed review **and**
evidence. `workflow.py:1143` and `:1169` enforce that for the standard flow.

The custom-DAG path does not. `cli.py:391-393` computes
`ready = state.refresh_workflow_status(workflow_id).value == "passed" and bool(evidence)`,
And `state.py:863-874` returns PASSED whenever *all* tasks are PASSED, with no role
inspection. A custom workflow containing a verification task but no `review` task reaches
`ready=True`, then `cli.py:417-419` calls `finalize_worktree` and merges.

### 5. Raw agent stdout/stderr reach SQLite unredacted while the same bytes are redacted on disk

`agentops/agentops/workflow.py:558` sets `task.result = f"log={result.log_path}\n{result.stdout}\n{result.stderr}"`;
`state.py:583-593` persists `result` verbatim. The verification paths do the same at
`workflow.py:515` (`report.transcript`) and `:524` (legacy command output).

Every other sink redacts: `logging.py:67-69` for log files, `artifacts.py:166` for text
artifacts, `workflow.py:206-207` and `:282` for failure evidence. SQLite is the only sink
with no redaction, and it is the sink the CLI (`cli.py:157-171`) and GUI (`gui.py:481-482`)
read back and print.

This contradicts `.agents/AGENTS.md:64` directly. The in-flight A8 change set does not
touch this path.

### 6. No CI exists

`.github/` contains exactly one file, `copilot-instructions.md`. There are no workflows, no
actions, no dependabot, no templates. Verified absent repo-wide: `.pre-commit-config.yaml`,
`Makefile`, `justfile`, `tox.ini`, `noxfile.py`, and every other CI config.

Consequences: zero automated execution of both suites; no runner OS; no Python version
matrix; no dependency-install step; no coverage; no secret scanning. The "write a
regression test before fixing a bug" rule has no enforcement.

Both products are Windows-constrained, agentops packaging is Windows-only
(`runtime.py:102-112` `CREATE_NO_WINDOW`, `AgentOps.spec`, `pyproject.toml` extra
`windows = ["pyinstaller>=6.0"]`) and UGA's external path is Windows-only by construction
(`ctypes` window management, `SendInput`, MSS). No Linux job could pass UGA's external
tests and no agentops packaging check could pass off-Windows, so a matrix must be
`windows-latest` for both.

**"No single repository-wide test command" is a two-row matrix, not a blocker.**

### 7. Every ignore rule lives in an uncommitted, machine-local file

There is no root `.gitignore`. `.git/info/exclude:7-17` is the only thing ignoring
`.misc/`, `.playwright-mcp/`, `.playwright_mcp/`, `agent-intercom-fix/`, `.agentops/`,
`.local-temp-archive/`, `omniroute-model-results.csv`, `working_models.txt`, `.pi/`, and
`small-projects/`. `git check-ignore` confirms these bind to this clone only.

A fresh clone has zero ignore rules. `.agentops/state.sqlite` (196,608 bytes) plus 9 agent
log files, 5 `.playwright-mcp/console-*.log`, 18 `.playwright-mcp/page-*.yml`, and the
directories `.pi/`, `.misc/`, `small-projects/`, `agent-intercom-fix/` all become untracked
noise on any other clone, including `working_models.txt` and
`omniroute-model-results.csv`.

`lessons.md:366` and `opencode/lessons.md:60` both have to teach "never `git add -A`",
  a rule forced by a file that is not in the repo.

**Tension to resolve first:** `decisions.md:219-224` records a deliberate 2026-09-15
decision to remove the root `.gitignore`. Adding one back contradicts a recorded decision.
Decide which rule wins and log it; do not just add the file.

### 8. `tasks/after-task.md` is mandated universally, is agentops-only, and is stale

`.agents/AGENTS.md:32`, `.agents/pi_AGENTS.md:19`, and `.agents/ohmypiagents.md:109` all
send every agent to it. It is 115 lines with zero occurrences of `universal-game-agent`.
`:23-24` hardcodes `cd agentops` plus `python -m unittest discover -s tests`, and
`:55-69` is entirely exe packaging (`:59-60` taskkill `dist/AgentOps.exe`, `:62` rebuild).

A UGA task following it runs the wrong suite and packages a product it never touched.
`:27-28` states the baseline as "224 passing, 3 environment skips as of 2026-09-15",
  roughly 130 tests stale against the 357 total / 4 skipped recorded in `.agents/AGENTS.md:49`.

### 9. UGA checkpoint and resume are non-atomic, non-reproducible, and crash

`universal-game-agent/training/ppo.py:290-303` writes `torch.save(.., path)` straight to
the final name, no temp file plus `os.replace`, so a crash mid-write leaves a truncated
`.pt` that `external_experiment.py:332` then advertises as the run's only weights. Nothing
in the payload stores RNG state (`:293-302`), so `--resume` is not reproducible.

`main.py:185-190` loads `trainer.env_config` from the checkpoint and never compares it to
`cfg["env"]`, so resuming with an edited config trains old weights in a new environment
with no warning. And `train()` computes `fps = num_timesteps / (perf_counter() - start)`
(`:332,344`) with a restored cumulative numerator against a fresh start time, inflating
`history["fps"]` on every resumed run. `README.md:126` already concedes there is no schema
version.

`main.py train --resume` on a finished checkpoint raises an unhandled `IndexError`: if
`trainer.num_timesteps >= total_timesteps`, the loop at `ppo.py:334` never runs, `history`
stays all-empty, and `main.py:203-205` indexes `history['mean_reward'][-1]`. Same shape in
`training/experiment.py:111-118` and `external_experiment.py:322`.

### 10. Eval hits and misses are guessed from reward sign; the external env discards its seeds

`universal-game-agent/training/evaluate.py:42-45` counts `reward > 0` as a hit and
`reward < 0` as a miss. That holds only when every nonzero reward is strictly +/-1. It
silently miscounts any `composite`, survival, or curiosity-scaled run. Under `extern_pong`
the red latch clears only on re-serve, so `mean_hits` is *per rewarded latch, capped at 1
per episode*, 2 total hits across 100 baseline episodes, 7 across 100 trained.

Separately, `ExternalGameEnv.reset` (`environment/external_game.py:274`) accepts `seed` and
drops it, no `super().reset(seed=seed)`, while `external_experiment.py:231` and
`main.py:267` carefully record `seeds=0.99`. Those seeds are decorative. All episodes
share one live process RNG stream and real-time scheduling, so `main.py:306-313`'s
sign-only verdict has no controlled comparison behind it.

---

## Recommended starting order, "where the hell do I even start?"

The roadmap audit confirmed that all DONE items (A1, A4, A8, B7) are genuinely complete with
tests and reviews. A5, A6, A7, A9, A10, B6, B8, B9, B10 are correctly marked PROPOSED.
The UGA roadmap is accurate. The tree is now clean (the parallel writer's A8 work landed
in `018de82`, bringing the suite to 382 tests / 4 skipped). The recommended sequence below
orders work by dependency and risk, with cheap-unblocks-a-lot items first.

### Cheap and unblocks a lot (do today)

| # | Work Item | Size | Product/Layer | Why First |
|---|-----------|------|---------------|-----------|
| **C1** | **A8 reviewer pass** (opencode/copilot snapshot review) | 30 min | agentops/kernel | Code is done, tests pass (382), only the read-only review is pending. Clears the deck. |
| **C2** | **Fix Oh-My-Pi location** in `.agents/AGENTS.md:19` and `.agents/memory/project.md:20` | 2 min | meta | Agents reading the canonical file look for a non-existent file and miss all Oh-My-Pi rules. |
| **C3** | **Fix root `pi_AGENTS.md:13-14` startup checklist**, add `.agents/memory/` to read list | 2 min | meta | Agents reading the root shim skip memory files entirely. |
| **C4** | **Add `games/__init__.py`** (empty) and change `extern_pong.py:17-19` to relative import | 5 min | UGA | Eliminates the double-import hazard that breaks `isinstance`/class identity. |
| **C5** | **Create `universal-game-agent/requirements-lock.txt` with hashes** | 10 min | UGA | Unpinned `torch`/`numpy`/`gymnasium` will break fresh installs; `torch` is the #1 risk. |
| **C6** | **Archive/delete dead directories**: `small-projects/`, `.misc/`, `.playwright-mcp/` | 5 min | repo | All dead scratch; `.misc/` targets external paths, `.playwright-mcp/` is 5 MB of PNGs. |

### Dependency chain (must follow order)

| # | Work Item | Size | Product/Layer | Blocks |
|---|-----------|------|---------------|--------|
| **D1** | **Repository characteristics for routing** | 1-2 hrs | agentops/kernel | `workflow.py:417` passes empty `{}`. Wiring project language/test-framework makes "Auto" selection actually intelligent. |
| **D2** | **SpawnFactory Protocol typing** | 30 min | agentops/kernel | Deferred from A3 review; adds `Protocol` for IDE eristics, no behavior change. Unblocks D1 cleanly. |
| **D3** | **A5 WorkflowEngine decomposition** | L | agentops/kernel | 918-line god object blocks A6, A7, B6, B8, C1. Extract Planner/Scheduler/Executor/Repair/Recovery behind thin `WorkflowEngine` facade. Public API unchanged. |
| **D4** | **Fix Git failure classification in `finalize.py`** | M | agentops/kernel | Typed failures: `BASE_DIRTY` / `BASE_CHANGED` / `CONFLICT` / `GIT_FAILED`. Explicit roadmap defect. Precedes A6. |
| **D5** | **A6 ReviewRun / MergeRun first-class lifecycle** | L | agentops/kernel | Adds `review_runs`/`merge_runs` tables, typed merge failure reasons, approval decision linkage. Enables B8 approvals. |
| **D6** | **B6 Policy gates** | M | agentops/kernel | `Policy` dataclass + enforcement in `TaskExecutor` + pre-claim check in scheduler. Prerequisite for B8. |
| **D7** | **B8 Approvals (human gate, first-class)** | M | agentops/kernel | `approvals` table, `request_approval`/`resolve_approval`, gate between READY and `finalize_worktree`. Prerequisite for B10. |
| **D8** | **A7 StateStore repository split** | M | agentops/kernel | 1948-line `state.py` → 8 domain repositories behind `StateStore` facade. No SQL changes. |
| **D9** | **A9 Artifact lifecycle + orphan recovery** | M | agentops/kernel | State machine (CREATED→ATTACHED→VERIFIED→PRESERVED→CLEANED/ORPHANED) + `scan_files`/`find_orphans` wired to worktree teardown. |
| **D10** | **C1 Phase 1 UX vertical slice** | L | agentops/gui | Task wizard → Dashboard → Activity → Timeline → Diff viewer → Approval → Retry/Repair. Depends on orchestration contracts being stable. |

### Parallelizable once unblocked

| # | Work Item | Size | Notes |
|---|-----------|------|-------|
| **P1** | **A10 CI/regression gating** | M | Independent once D5 (secrets/auth) resolved. GitHub Actions matrix: `agentops` on windows/py3.11, `universal-game-agent` on windows/py3.12+torch. Run alongside kernel work. |
| **P2** | **UGA missing tests** (see section below) | S, M | Independent of agentops work. |
| **P3** | **Instruction drift fixes** (see section below) | S | Independent; can be done in parallel with anything. |

### One critical decision you must make before P1

**Root `.gitignore`**, `decisions.md:219-224` records a deliberate 2026-09-15 decision to *remove* it, but the current state has all ignore rules in uncommitted `.git/info/exclude` (machine-local). Every fresh clone loses them. You must either:
- **Commit a root `.gitignore`** (contradicts recorded decision, but fixes the clone problem), or
- **Keep the decision** and document the machine-local paths in `.agents/AGENTS.md` so agents know what to avoid.

The prior review recommended: *"decide which rule wins and log it; do not just add the file."* That is the one thing that cannot be done by an agent, it is a policy call.

---

## Roadmap status audit

The roadmaps are the repo's own statement of intent. Independent verdict against the code:

### AgentOps roadmap (`.agents/memory/roadmap.md`)

| Item | Claimed Status | Code Verdict | Evidence |
|------|----------------|--------------|----------|
| A1, Formal execution/result state machine | DONE 2026-09-15 | CONFIRMED | `execution_model.py` exists; 3 fail-loud gates in `workflow.py`; `tests/test_execution_model.py` (27 tests) |
| A2, AgentAdapter + capability model | DONE 2026-09-15 | CONFIRMED | `agentops/agent_adapter.py`; `registry.select(required_capabilities=)`; `tests/test_agent_adapter.py` (14 tests) |
| A3, Shared ProcessRuntime | DONE 2026-09-16 | CONFIRMED | `agentops/runtime.py`; unified spawn policy; `tests/test_runtime.py` (13 tests); exe rebuilt & smoke-tested |
| A4, Structured failure evidence | DONE 2026-09-16 | CONFIRMED | `FailureEvidence` in `failure.py`; additive `structured_evidence` column (schema v7); 17 new failure-kernel tests |
| A5, WorkflowEngine decomposition | PROPOSED | NOT DONE | `workflow.py` is 918 lines; no extraction |
| A6, First-class ReviewRun / MergeRun | PROPOSED | NOT DONE | No `review_run.py`/`merge_run.py`; Git failure classification still conflates reasons |
| A7, StateStore repository split | PROPOSED | NOT DONE | `state.py` ~1948 lines; no domain repositories |
| A8, Persistence failure policy | DONE 2026-09-26 | CONFIRMED | `agentops/persistence.py` (policy table, `DegradationRecorder`); `tests/test_persistence.py` (18 tests); suite 382 OK |
| A9, Artifact lifecycle + orphan recovery | PROPOSED | NOT DONE | `artifacts.py` has core registry but no state machine |
| A10, CI/regression gating | PROPOSED | NOT DONE | No GitHub Actions workflow; no compile check; no lint gate |
| B6, Policy gates | PROPOSED | NOT DONE | No `Policy` dataclass; no enforcement hook |
| B7, Router (capability/cost scoring) | DONE 2026-09-16 | CONFIRMED | `agentops/routing.py`; 25 routing tests; `runtime.routing_enabled` switch |
| B8, Approvals | PROPOSED | NOT DONE | No `approvals` table; no `request_approval`/`resolve_approval` |
| B9, Project memory | PROPOSED | NOT DONE | No per-repo convention injection into prompts |
| B10, REST API + evals | PROPOSED | NOT DONE | No endpoints; no eval harness |
| C1, C3 (UX layers) | PROPOSED | NOT STARTED | Depend on Track A/B foundations |

### UGA roadmap (`.agents/memory/oh-my-pi/roadmap.md`)

| Item | Claimed Status | Code Verdict | Evidence |
|------|----------------|--------------|----------|
| Scaffold through Phase-2 window-loss tolerance | DONE | CONFIRMED | Commits `b77bf4d`, `d15c02b`, `6890c42`, `f751d73`; suite 278 passing |
| STEP 3, pre-flight check live-play baseline reds | PROPOSED | NOT STARTED | Blocked on exp window verification |
| Curiosity enabled on external game | PROPOSED | NOT STARTED | Config flag exists, not validated |
| Commit pending work (memory files) | PROPOSED | NOT STARTED | Memory files modified, not committed |

**Roadmap verdict**: All DONE items are genuinely complete with tests and reviews. A5, A6, A7, A9, A10, B6, B8, B9, B10 are correctly marked PROPOSED. The UGA roadmap is accurate.

---

## Agentops test-quality audit

The prior pass inventoried *which modules lack tests*. Nobody has asked whether the ~600 tests
across both products are *real*. The verdict: **the suite is mostly ceremony with islands of
genuine load-bearing tests.**

### Per-file verdict table

| File | Lines | Verdict | Strongest / weakest assertion |
|------|-------|---------|-------------------------------|
| `test_verification_kernel.py` | 867 | **Load-bearing** | `test_fail_fast_skips_remaining_checks:174`, real `FakeProcess`, asserts `len(calls)==2` and `statuses["third"]==SKIPPED`; `test_per_check_timeout:254`, `HangingProcess` forces real timeout path |
| `test_failure_kernel.py` | 770 | **Load-bearing** | `test_exit_code_beats_misleading_success_text:458`, structured evidence overrides string heuristic; `test_evidence_round_trips_through_failures_row:524`, real SQLite persistence |
| `test_execution_model.py` | 262 | **Load-bearing** | `test_passed_all_skipped_raises:83`, invariant: zero checks ≠ PASSED; `test_verified_implementation_task_raises:121`, agent success ≠ verification |
| `test_artifacts.py` | 178 | **Load-bearing** | `test_secret_redaction:43`, writes real file, asserts `[REDACTED]` in content; `test_traversal_rejected:50`, real path escape attempt |
| `test_events.py` | 280 | **Load-bearing** | `test_garbage_never_raises:65`, `coerce_event(None/123/"not json")` returns `NOTE`; `test_nonserializable_payload_coerced:220`, hostile `__repr__` survives |
| `test_routing.py` | 488 | **Mixed** | `test_explicit_user_preference_beats_configured_priority_and_history:149`, good scoring test; but `test_registry_select_accepts_single_string_requirement:297` patches `shutil.which` and `_load_data`, tests mock wiring |
| `test_agent_adapter.py` | 112 | **Mixed** | `test_substitutes_prompt_and_prefers_executable:91`, real command tuple; `test_structural_protocol:86`, only asserts `isinstance(.., AgentAdapter)` |
| `test_persistence.py` | 511 | **Mixed** | `test_report_is_not_passed_when_check_state_cannot_persist:208`, patches `finish_verification_check` to raise, asserts report ≠ PASSED; but many tests only verify `DegradationRecorder` collects entries |
| `test_review_regressions.py` | 485 | **Ceremony** | Mostly mocks `WorkflowEngine` collaborators; `test_empty_verification_commands_fail_without_evidence:46` is the only one exercising real `Verifier` + `WorkflowEngine` integration |
| `test_workflow.py` | 61 | **Ceremony** | Only 3 tests; all use `MagicMock()` for registry/runner/verifier; `test_standard_workflow_runs_to_ready:33` asserts `result.ready` and `len(tasks)==4`, no observable behaviour verified |
| `test_state.py` | 57 | **Ceremony** | Only 5 tests; `test_ready_tasks_follow_dependencies:15` asserts internal `ready_tasks` list order, no persistence round-trip |
| `test_cli.py` | 19 | **Ceremony** | 2 tests; `test_agents_command_reports_known_profiles:9` asserts `"fcc-claude" in output`, brittle string match |
| `test_gui.py` | 352 | **Ceremony** | `test_missing_run_input_warns:255` asserts `messagebox.showwarning.called_once()`; `test_history_browser_loads_selected_workflow:269` uses `FakeController` returning hardcoded dicts |
| `test_storage_dtos_worktree_refs.py` | 141 | **Ceremony** | Only tests `serialize_*` functions emit dicts; `test_controller_workflow_payload_has_serialized_tasks_and_ref:51` asserts `isinstance(detail["tasks"][0], dict)`, no behavioural guarantee |

### Overall judgement

The suite catches regressions in **verification logic, failure classification, execution invariants, artifact integrity, and event coercion**. It does **not** catch regressions in workflow orchestration, CLI behaviour, GUI behaviour, state persistence, or agent routing, those tests are mock-coupled and would pass even if production code were wrong.

### agentops modules no pass has read

- **`agentops/routing.py`** (29,361 bytes), `AgentCapabilityResolver` derives capabilities from roles + declared config + adapter; `AgentRouter` scores candidates (hard gates: availability, role support, required capabilities; scoring: user preference 1000+, explicit capability match 10, inferred match 5, priority −1, history ×25). Correct in enabled mode. **Defects**: `allow_capability_fallback=True` path re-scores without re-checking `profile.availability` (line 665-675); `_static_decision` (routing disabled) ignores `required_capabilities` entirely (line 538-568); `available_agents` coercion treats a single `AgentProfile` as iterable of its attributes, not a single-item tuple (line 594-599). **Missing tests**: `allow_capability_fallback` path, `required_capabilities` enforcement in static mode, single-profile coercion bug, all-agents-missing path.
- **`agentops/agent_adapter.py`**, Thin wrapper over `AgentConfig`. Correct. `build_command` prefers detection-time absolute path (line 177). **Missing**: parser round-trips, coercion of hostile payloads.
- **`agentops/agent_result.py`**, Versioned structured result schema, reliable parser, five-way `ExecutionOutcome`. Correct and thorough. `evaluate_execution` treats UNKNOWN/missing as **not success**. **Missing**: dedicated test file for parser round-trips, coercion, legacy paths.
- **`agentops/verification_model.py`**, Pure domain model. `parse_check_outcome` is authoritative. Correct. Covered by `test_verification_kernel.py:151-170`.
- **`agentops/gui_controller.py` retry/merge paths**, `retry_merge` validates against stored provenance (`WorktreeRef`) when available, using original `base_branch`/`base_commit`. Correct. **Missing**: tests for retry with stored provenance vs fallback, retry with dirty worktree rejection, cleanup with `delete_unmerged_branch=True`.
- **`agents/agents.yaml`**, 7 agents; `claude` has `"enabled": false`. `AgentProfile.availability` = `detected.available AND config.enabled`; disabled agents rejected with reason "agent disabled". **No leak**, disabled agents never selected. **Defect**: role preferences reference `claude` but it's disabled (config smell, not a routing bug).
- **`AgentOps.spec` / `scripts/build_windows_exe.py`**, Spec bundles `agents/agents.yaml`; entry point `agentops_gui.py`; `console=False`; `upx=True`. Build script runs PyInstaller and checks `.exe` exists. **Defect**: no archive inspection, no smoke test of startup/shutdown; `AgentOps.spec:29` sets `upx=True` with no pinned UPX.
- **`examples/dark-mode.yaml`**, `{"description": "Add a dark mode toggle.."}`, a sample workflow input, not a config. Harmless.

### GUI fitness

**Usable for developers who understand the model; fragile for anyone else.**
- **Information architecture**: Run tab (direct agent + prompt → fire-and-forget) and Task workflow tab (description → full plan/implement/verify/review/merge) are clear. History tab paginated with status filter. Status pane always visible with task table + output pane. Good at a glance.
- **Blocking-subprocess defect**: Controller *does* use threads for `run_agent`/`run_task`, but status polling (`_schedule_status_poll` every 1s) re-queries SQLite and Git on the Tk thread via `root.after(0..)`. Under load this causes visible UI stutter. Not "broken" but janky.
- **Error surfacing**: Agent errors show exception string with **no actionable guidance** (e.g., "agent not found", "permission denied"). Merge conflict is comprehensible but assumes domain knowledge about worktrees. No agent-available state gives "install X" hint.
- **Merge flow**: Worktrees tab lists managed worktrees with status; "Retry merge" confirms with base branch. **Gap**: no explanation of what a worktree is, why it exists, or what "base branch" means.

### Tests to write (agentops)

| Priority | Test | File | Behaviour Pinned | Defect/Regression Caught |
|----------|------|------|------------------|---------------------------|
| 1 | `test_routing_static_mode_enforces_required_capabilities` | `test_routing.py` | `_static_decision` rejects candidates missing `required_capabilities` | Static routing bypasses capability gates |
| 2 | `test_routing_allow_capability_fallback_respects_availability` | `test_routing.py` | Fallback path checks `profile.availability` and role support | Disabled agent slips through fallback |
| 3 | `test_routing_all_agents_missing_returns_no_eligible` | `test_routing.py` | `route()` returns `selected_agent=None`, reasons=("no eligible agent",) | Caller gets `None` without explanation |
| 4 | `test_agent_result_parser_fenced_json_truncated_malformed` | `test_agent_result.py` (new) | `parse_agent_result` returns correct `ParseMode` for each case | Silent misclassification of agent output |
| 5 | `test_agent_result_coerce_legacy_dict_list_json_text` | `test_agent_result.py` (new) | `coerce_agent_result` handles all legacy payload shapes | Stored results become unreadable after schema change |
| 6 | `test_gui_retry_merge_uses_stored_provenance` | `test_gui.py` | `retry_merge` uses `WorktreeRef.base_branch/commit` not live HEAD | Retry merges to wrong base after base moves |
| 7 | `test_gui_retry_merge_rejects_dirty_worktree` | `test_gui.py` | `retry_merge` raises `GitError` when worktree has uncommitted changes | Silent merge of dirty state |
| 8 | `test_workflow_custom_bypasses_review_gate` | `test_workflow.py` | Custom workflow with verification task but no review task → `result.ready=False` | Prior defect: custom workflow skipped review |
| 9 | `test_verification_empty_profile_raises_not_passes` | `test_verification_kernel.py` | `VerificationKernel(profiles={})` raises `ValueError` on run | A1R-[1]: vacuous pass |
| 10 | `test_state_recover_all_marks_stranded_failed_not_passed` | `test_state.py` | `recover_all()` never produces `status=PASSED/COMPLETED` | A1R-[2]: recovery mints success |

---

## UGA unread-modules deep dive

### `agent/`, The RL agent module

`agent/model.py` defines `ActorCritic`, a recurrent CNN-GRU network: `(B, C, 84, 84)` → 3 conv layers → FC → GRU (1 layer, 128 hidden) → actor (logits) + critic (value). `agent/__init__.py` lazy-exports via `__getattr__` to keep `torch` out of import graphs. `PPOTrainer.__init__` takes `(env, model, config, curiosity, env_config)` and calls `model.initial_state()`, `model(obs, hidden)`, `model.forward_sequence()`. The agent has **no dependency on game logic**, only tensor shapes. Used by both paths. `tests/test_model.py` (6 tests) covers forward shapes, configurable dims, batch=single equivalence, hidden carry/reset, save/load roundtrip, invalid inputs. **Responsibility split is clean**; `agent/` owns the network, `training/ppo.py` owns the PPO algorithm. No duplication.

### `games/`, Double-import hazard

`games/` has no `__init__.py`, so it's an implicit namespace package. `games/extern_pong.py:17-19` mutates `sys.path` and does `from pong_logic import..` (flat import). This makes `pong_logic` importable as **both** `games.pong_logic` (proper package import) and `pong_logic` (top-level module via `sys.path` injection). `tests/test_extern_pong.py:7` → `from games.pong_logic import..` while `games/extern_pong.py:19` → `from pong_logic import..`. If any code does `import pong_logic` (flat) and another does `from games import pong_logic`, they get **two distinct module objects** in `sys.modules`. This breaks `isinstance` checks, singleton state, and class identity. `games/pong_logic.py:3-5` states it duplicates `environment/toy_pong.py` deliberately (WIDTH 320 vs 64, paddle 48 vs 12, ball 6 vs 2) for the external boundary to be testable against a real process. **Minimal fix**: add `games/__init__.py` (empty); change `extern_pong.py:17-19` to `from.pong_logic import..`; remove the `sys.path.insert`.

### `configs/`, Every config file

`configs/default.yaml` is loaded by bare `yaml.safe_load` + top-level mapping check. **No schema validation, no inheritance, no `extends`.** Every consumer does its own `cfg.get(..)` with private defaults. Settings with path-specific meaning are traps: `env.type: external` is honored by `make_env_from_config` but `main.py` smoke-test ignores it (lines 119-125 build a separate `ToyPongEnv` regardless). `checkpoint_every_updates` varies (10, 50, 1000, 1000, 1000) but 3 of the 1000s are functionally disabled, those experiments finish at 8-234 updates, never hitting the checkpoint guard at `ppo.py:376`. The `logging:` block in `default.yaml:48-50` is **dead**, no production path calls `training.logger.setup_logging`. `reward.provider: extern_pong` causes all other `reward:` keys to be discarded. **Dead config keys**: `logging.*`, any `env.*` key not in `_ENV_KEYS`, any `ppo.*` key not in `PPOConfig` fields.

### `main.py` as a CLI

6 subcommands (`smoke-test`, `train`, `evaluate`, `compare`, `experiment`). Coherence issues: `smoke-test` never exercises external path (lines 119-125 build a separate `ToyPongEnv` regardless); `train --resume` crashes on finished checkpoint; two `run_experiment` entry points with different signatures (`training/ppo.py:383` toy CLI helper vs `training/experiment.py:66` real YAML runner, a live trap); flag inconsistency across subcommands; `train --resume` overriding config values (line 188) is surprising. `main.py train` with an external config does the wrong thing with no warning. The external path is a separate module (`python -m training.external_experiment`) with no overlap in `main.py`, undiscoverable from `main.py --help`.

### Test quality for UGA

- **`test_interface.py`** (299 lines, 13 test classes): **Genuinely good.** Tests the *contract* of the interface layer with fakes (`FakeWindow`, `RecordingBackend`, `SyntheticBackend`). Asserts observable behavior: action sequences, capture shapes, focus/liveness delegation, chord press/release order, cooldown throttling, native passthrough. No mock choreography. The gold standard in this repo.
- **`test_external_game.py`** (705 lines): **Strong contract tests.** Uses `FakeInterface`, `FakeLifecycle`, `FixedReward`, `ScriptedTermination` to test `ExternalGameEnv` semantics. **Does not catch the reset-seed bug** because `FakeLifecycle` doesn't validate the seed argument.
- **`test_ppo.py`** (371 lines): **Thorough on boundary semantics.** `TestBoundarySemantics` (6 tests) uses `ScriptedEnv` to verify rollout/replay boundary handling. **Missing**: test for `main.py --resume` crash on finished checkpoint; test for `ExternalGameEnv` seed drop.
- **Highest-risk untested module**: `training/external_experiment.py`, the 340-line `run_external_experiment` function (process launch, window attach/retry, window-relaunch training loop, three-phase isolation) has **zero integration tests**. Only `_unique_title`, `_run_checkpoint_dir`, `_results_path`, `_apply_run_title` are tested.

### Missing tests that would have caught known defects

| Defect | Missing Test |
|--------|--------------|
| `main.py --resume` crashes on finished checkpoint (`IndexError`) | `test_cli.py`: resume from a checkpoint where `trainer.num_timesteps >= config.total_timesteps` |
| `ExternalGameEnv.reset` drops `seed` | `test_external_game.py`: pass `seed=42` to `reset()`, assert it reaches `lifecycle.attach()` or `reward_provider.reset()` |
| `reward.provider: extern_pong` silently ignores other reward keys | `test_reward_config.py`: config with `provider: extern_pong` + extra `components`, assert warning or that extras are not used |
| `external_experiment.py` orchestration untested | `tests/test_external_experiment.py`: synthetic-mode integration test using `FakeInterface`/`FakeLifecycle` to run the full 3-phase driver end-to-end |

### The two paths as a product

**Toy path on Linux/macOS, Windows dependency leakage?** Clean. Transitive import check confirms no Windows dependency leaks into the toy path: `main.py` → `training.experiment.make_env_from_config` → `environment.toy_pong.ToyPongEnv` (no Windows deps); `interface/window.py` (ctypes) is only imported inside `make_external_env_from_config` at function scope (line 416). **External path off-Windows, clear refusal?** Partially clean. The external path correctly refuses live capture off-Windows with a clear `OSError` from `WindowManager._user32()` at `interface/window.py:25`. The error message mentions `os.name` (e.g., `posix`) which is technical. `synthetic` mode (`capture.mode: synthetic`) builds `SyntheticBackend` + `ScreenCapture` with no `WindowManager`, runs on any platform. Not advertised in `main.py` help.

---

## Instruction-file drift matrix

A comparison of 8 near-duplicate instruction files (root `AGENTS.md`, root `pi_AGENTS.md`, `.agents/AGENTS.md`, `.agents/pi_AGENTS.md`, `.agents/ohmypiagents.md`, `agentops/AGENTS.md`, `universal-game-agent/AGENTS.md`, `.github/copilot-instructions.md`) across 113 rules. The highest-damage divergences, ranked by how much stale variant causes an agent to act wrong:

| Rank | Divergence | Files Affected | Stale Version | Correct Version | Damage |
|------|------------|----------------|---------------|-----------------|--------|
| 1 | **Oh-My-Pi instructions location** | `.agents/AGENTS.md:19`, `.agents/memory/project.md:20` | `.agents/ohmypiagents.md` (does not exist) | `.agents/memory/oh-my-pi/ohmypiagents.md` (actual location) | **HIGH**, Agents reading canonical file look for non-existent file, miss all Oh-My-Pi rules |
| 2 | **Root `pi_AGENTS.md` startup checklist omits `.agents/memory/`** | `pi_AGENTS.md` (root):13-14 | Omits memory files | `.agents/AGENTS.md:32-33`, `.agents/pi_AGENTS.md:19-21`, `.agents/memory/oh-my-pi/ohmypiagents.md:19` | **HIGH**, Agent reading root shim will skip memory files entirely |
| 3 | **Test baseline counts stale** | `.agents/AGENTS.md:49-50`, `agentops/AGENTS.md:28-29`, `universal-game-agent/AGENTS.md:34-35` | All cite 357/261 at `a03e907` | **Actual**: agentops 382 tests (4 skipped), universal-game-agent 278 tests (0 skipped) | **MEDIUM**, Agents may think test regressions are new failures |
| 4 | **OpenCode session names contradicted in `team.md`** | `.agents/team.md:17` vs `:67` | `opencode-projects-6896` | `opencode-projects-20740` (more recent) | **MEDIUM**, Agent may try stale session name |
| 5 | **Copilot liveness contradiction in `team.md`** | `.agents/team.md:21` vs `:67` | "Live Intercom session `copilot`" | "`copilot` was not observed live; reach it via `agentops run copilot`" | **MEDIUM**, Agent may try Intercom when only snapshot works |
| 6 | **Root `pi_AGENTS.md` omits allowed-collaborator list** | `pi_AGENTS.md` (root):10 | Only names Antigravity quota rule | `.agents/AGENTS.md:62-63` (full list: OpenCode, fcc-claude, Copilot, Antigravity, Oh-My-Pi) | **MEDIUM**, Agent may not know all allowed reviewers |
| 7 | **`architecture.md:89` references `.agents/ohmypiagents.md`** | `.agents/memory/architecture.md:89` | `.agents/ohmypiagents.md` | `.agents/memory/oh-my-pi/ohmypiagents.md` | **MEDIUM**, Architecture memory points to non-existent file |
| 8 | **`.github/copilot-instructions.md` omits `tasks/task.md` and `tasks/after-task.md`** | `.github/copilot-instructions.md:19-22` | Omits task files | `.agents/AGENTS.md:32-33`, `.agents/pi_AGENTS.md:19-21` | **LOW**, Copilot-specific |
| 9 | **Anchor decay in UGA AGENTS.md Known-defects** | `universal-game-agent/AGENTS.md:21,52,155` | `:363`, `:112,118`, `:369,414-426` | `:370/374/379/394-395/412/414`, `:202`, `:399-405/445-455` | **LOW**, Anchors don't resolve; `:169` already uses symbol names |
| 10 | **`agentops/AGENTS.md` test-module count** | `agentops/AGENTS.md:26` | "21 test modules" | Various lanes read 21, 22, 23, drop the count as the file already does for test totals | **LOW** |

### Key dead references verified

- `.agents/ohmypiagents.md`, referenced by `.agents/AGENTS.md:19`, `.agents/memory/project.md:20`, `.agents/memory/architecture.md:89`, **does not exist**; actual location is `.agents/memory/oh-my-pi/ohmypiagents.md`
- `tasks/task-ignorethis.md`, exists, externally authored (Hacir Bacoj), no provenance marker
- `agentops/.agentops/`, runtime state (9 log files + `state.sqlite`, 196,608 bytes at container root); `agentops/.agentops/` (under product root) is the designated location per `agentops/AGENTS.md:58-59`
- `.pi/`, junctions to 7 skill folders; machine-specific, correctly excluded
- `agent-intercom-fix/`, 6 files / 22,172 bytes, upstream patch artifacts for two external npm packages; finished work, excluded via `.git/info/exclude:10`
- `small-projects/`, empty subdirs (`quickscripts/`, `universal-game-agent/`) plus `Cube Timer.html`; dead container
- `.misc/`, scratch workspace for Agent Intercom debugging; dead scratch
- `.playwright-mcp/`, 5 console logs + 18 page snapshots; dead tool scratch, ~5 MB of PNGs

---

## Supply-chain posture

### Dependency table

| Dependency | Current spec | Realistic risk | Recommended pin |
|------------|--------------|----------------|-----------------|
| `torch` | unpinned | **Critical**. PyTorch releases break backward compatibility frequently (CUDA ABI, Python version gating). No upper bound means a fresh install pulls latest, which may require a different CUDA version or Python minor. Historically: torch 2.0→2.1 dropped Python 3.8, 2.3 dropped 3.9. | Pin to exact version matching your CUDA/Python |
| `gymnasium` | unpinned | **High**. Gymnasium 0.29→1.0 was a major breaking change (space API, render modes). 1.x releases continue to change `reset()`/`step()` signatures. | `gymnasium>=0.29.1,<1.0` |
| `numpy` | unpinned | **Medium-High**. NumPy 2.0 (June 2024) removed deprecated aliases, changed dtype behavior. `torch` and `gymnasium` both depend on numpy; unpinned numpy pulls 2.x which may conflict. | `numpy>=1.26.4,<2.0` |
| `pyyaml` | unpinned | **Low-Medium**. PyYAML 6.0 (2023) changed `SafeLoader` default behavior. | `pyyaml>=6.0,<7.0` |
| `mss` | unpinned | **Low**. Thin wrapper over platform screen-capture APIs. | `mss>=9.0.1,<10.0` |

**No lockfile exists anywhere**, no `*lock*`, `Pipfile*`, `poetry.lock`, `uv.lock`, `constraints.txt`. No hash pinning in `requirements.txt`. No `pip-audit`, `safety`, or `dependabot`. `agentops/pyproject.toml` declares `dependencies = []` (stdlib only) with extras pinned only by lower bounds. **Most likely to break a fresh install**: `torch` (CUDA/Python matrix) followed by `numpy` (2.0 transition).

### Licensing / attribution exposure

The repo `LICENSE` is GPL-3.0. `.agents/skills/` is 22 tracked files / 198,237 bytes, entirely third-party content sourced from `vercel-labs/agent-browser`, `vercel-labs/skills`, `juliusbrussee/caveman`, and `mattpocock/skills` (per `skills-lock.json`). None of the skill folders contain a `LICENSE`, `NOTICE`, or attribution header. The GPL-3.0 requires that when you convey a covered work you must keep intact all copyright/license notices from incorporated works (§4, §5). Mixing GPL-3.0 with externally-sourced code of unknown license creates a compliance gap. **Minimum compliant fix**: audit each skill's upstream license; add a `NOTICE` file at repo root listing each skill, source URL, and license; remove any skill with unknown/incompatible license; ensure GPL-3.0 headers on all original repo files.

### Vendored executable scripts

`.agents/skills/antislop-human/contrast-check.py` (5,754 B) and `contrast-mcp.py` (3,628 B) are the only executable third-party code tracked. `contrast-check.py`: pure Python WCAG contrast calculator, no network/subprocess/filesystem writes/env reads, safe. `contrast-mcp.py`: minimal MCP stdio server, no network/subprocess/filesystem/env reads, safe. Neither is invoked by any repo code; they are documentation-only artifacts loaded by agents per the antislop skill system. No execution risk in current state, but their presence as tracked executable third-party code without license headers is part of the attribution gap above.

### Unexplored directories

| Path | What it is | Tracked? | Live or dead | Verdict |
|------|------------|----------|--------------|---------|
| `agent-intercom-fix/` | Upstream patch artifacts for `@ctliz/agent-intercom-pi` (v0.12.2) and `@ctliz/agent-intercom-opencode` (v0.12.1): fixes Windows `EPERM` on `fsyncSync`. Patches validated, test written, reapply documented. | No | Finished work | **Archive**, move to `.agents/memory/agent-intercom-fix/` if upstream merge pending; otherwise delete after confirming upstream has merged |
| `small-projects/` | Container with `quickscripts/` (empty), `universal-game-agent/` (empty), `Cube Timer.html` (25 KB standalone). | No | Dead container | **Delete** |
| `.misc/` | Scratch workspace: `find-large-files.ps1`, 6 `.mjs` scripts for end-to-end verification of Windows `fsync` fix, `intercom-fsync-review-prompt.md`, `pi-task-skill/` (Pi skill scaffold). | No | Dead scratch | **Delete** |
| `.pi/` | Junctions to 7 skill folders under `.agents/skills/`. | No | Live runtime artifact | **Keep excluded**, machine-specific |
| `.agentops/` (container root) | 3 run directories under `logs/`, `state.sqlite` (196,608 bytes). Logs show `agent=pi` runs with `[REDACTED]` prompts. | No | Live regenerable state | **Keep excluded** |
| `.playwright-mcp/` | 5 console logs + 18 `page-*.yml` snapshots + PNG screenshots. | No | Dead tool scratch | **Delete** |
| `agentops/.agentops/` | Product runtime state: 6 run directories under `logs/`, `state.sqlite` (167,936 bytes). | No | Live regenerable state | **Keep gitignored** |

---

## Add

Highest value first (integrating both the original review and the deep-dive lanes).

### Roadmap-driven adds

1. **A8 reviewer pass** (opencode/copilot snapshot review), 30 min, closes review gate on shipped code.
2. **Repository characteristics for routing**, wire project language/test-framework from workflow/task context into `AgentRouter`; makes "Auto" selection actually intelligent.
3. **SpawnFactory Protocol typing**, adds `Protocol` for IDE/typechecker ergonomics; no behavior change.
4. **A5 WorkflowEngine decomposition**, extract `WorkflowPlanner`/`Scheduler`/`TaskExecutor`/`RepairCoordinator`/`RecoveryCoordinator` behind thin `WorkflowEngine` facade. Public API unchanged. Highest-risk architectural debt; blocks A6, A7, B6, B8, C1.
5. **Fix Git failure classification in `finalize.py`**, typed failures (`BASE_DIRTY`/`BASE_CHANGED`/`CONFLICT`/`GIT_FAILED`) instead of generic `GitError`. Precedes A6.
6. **A6 ReviewRun / MergeRun**, add `review_run.py` + `merge_run.py` leaf domains, additive `review_runs`/`merge_runs` tables, typed merge failure reasons, approval decision linkage. Enables B8 approvals.
7. **B6 Policy gates**, `Policy` dataclass (path/network/approval per role), enforcement in `TaskExecutor`, pre-claim check in scheduler. Prerequisite for B8.
8. **B8 Approvals**, `approvals` table, `request_approval`/`resolve_approval`, gate between READY and `finalize_worktree`.
9. **A7 StateStore repository split**, 1948-line `state.py` → 8 domain repositories behind `StateStore` facade. No SQL changes.
10. **A9 Artifact lifecycle + orphan recovery**, state machine (CREATED→ATTACHED→VERIFIED→PRESERVED→CLEANED/ORPHANED), `scan_files`/`find_orphans` wired to worktree teardown.
11. **C1 Phase 1 UX vertical slice**, Task wizard → Dashboard → Activity → Timeline → Diff viewer → Approval → Retry/Repair. Depends on orchestration contracts being stable.
12. **A10 CI/regression gating**, GitHub Actions matrix: `agentops` on windows/py3.11, `universal-game-agent` on windows/py3.12+torch. Independent once D5 (secrets/auth) resolved.

### Code adds (product)

13. **A degenerate-episode guard** in `evaluate()` or the results writer: fail or loudly annotate when >20% of episodes are length 1 with 0 misses. That single assertion would have caught finding 1 at write time.
14. **UGA checkpoint hardening**: `schema_version` key, `torch.get_rng_state()` / `np.random.get_state()` / `random.getstate()`, and an `env_config` **comparison** on resume that errors when the live config differs. Plus shared atomic-write helper (`tmp` + `os.replace`).
15. **Provider-reported event counters** so `evaluate()` stops inferring hits/misses from reward sign, `RewardProvider.events(prev, cur) -> dict`, falling back to sign only when absent.
16. **agentops regression tests for the two live bugs**: a temp repo with no `.gitignore` asserting `merge()` either succeeds or names `.agentops`; and an assertion that `workflow <file>` cannot reach `ready=True` without a passed `review` task.
17. **agentops test coverage for the untested surface**: a `test_gui_controller.py` (786 lines, ~30 serialization methods, a recovery-scope bug, zero unit coverage); real CLI coverage (`tests/test_cli.py` is 19 lines against a 441-line entry point, 2 of 12 subcommands); and a migration-contract test proving a v1 database upgrades additively without rewriting task history.
18. **UGA missing tests** (would have caught known defects): `test_resume_finished_checkpoint_fails_cleanly`, `test_reset_passes_seed_to_lifecycle`, `test_extern_pong_ignores_other_keys`, `test_external_experiment_synthetic_integration`.
19. **`test_results_contract.py`**, asserting every field in a report matches the config that produced it.
20. **A `NOTICE` / attribution file**, repo is GPL-3.0 and vendors 194 KB of externally-sourced skills with no upstream license and no attribution.
21. **A root `README.md`** (~30 lines): purpose, the two products with their one-line test commands, the meta layer's role, license. A two-product public container with a GitHub remote and a licence currently has no human entry document.
22. **A rule that governance `path:line` anchors are decaying claims**, re-anchor on product change, or use symbol names as `universal-game-agent/AGENTS.md:169` already does.
23. **A rule that ephemeral Intercom session names never belong in any file**, currently baked into `architecture.md:80`, `lessons.md:68,75,82,144,152`, `decisions.md:145,152`, and `team.md:17,21`.
24. **A "what exists but is not on the read path" list**, `pending_tasks.md`, `additions.md`, `audit.md`, `outputs/phase-2-*`, and `plans/chatgpt_*` are referenced by zero instruction files. Either name them in the checklist or archive them.
25. **An encoding requirement in `opencode/environment.md`**, PowerShell 5.1's default `Get-Content` decodes these UTF-8 files as ANSI; one lane nearly filed a false corruption finding. Specify `-Encoding UTF8` for all file reads.
26. **A `test_results_contract.py`** asserting every field in a report matches the config that produced it.
27. **Fix `games/` double-import hazard**, add `games/__init__.py`; change `extern_pong.py:17-19` to relative import.
28. **`main.py` refuse `type: external`** with an error naming the sanctioned command; delete the README:182 sentence that blesses the route.
29. **Report uncertainty in `main.py:306-313`** comparison, emit a paired difference with an interval, or say nothing.
30. **Validate `checkpoint_every_updates`** in `PPOConfig.__post_init__`; `0` survives config load and dies at `ppo.py:376` after the first update.
31. **Delete the dead layer**: duplicate `out_path` at `external_experiment.py:297,336`; `training/reward_diagnostic.py:33-39` `classify()` that `diagnose()` reimplements; `training/logger.py` + `configs/default.yaml` `logging:` block wired to nothing.

### Instruction-layer adds (from drift matrix)

32. **Fix Oh-My-Pi location** in `.agents/AGENTS.md:19` and `.agents/memory/project.md:20`, point to `.agents/memory/oh-my-pi/ohmypiagents.md`.
33. **Fix root `pi_AGENTS.md:13-14` startup checklist**, add `.agents/memory/` to the read list.
34. **Add a "machine-local paths" section** to `.agents/AGENTS.md` listing what exists only on this clone because ignore rules are uncommitted. Closes the gap behind findings 7, 16, 22, 23.
35. **A `verified-at: <commit> <date>` header on every memory file**, plus a check. Turns `lessons.md:355-360` from advice into something enforceable and kills findings 11-12.
36. **Scope `tasks/after-task.md` sections 1-3 to `agentops`** and replace its baseline with a pointer to the two-row command table in `.agents/AGENTS.md`.
37. **Delete `lessons.md:13-15`** (false "greenfield / no code as of 2026-09-12" block) and `:378` (false "no stack/test runner configured yet"). State ordering is append-by-arrival, not chronological.
38. **Correct `project.md:33` and `roadmap.md:22`** to the 2026-09-26 collaboration policy; land the pending `after-task.md:39-46` change in the same decision-logged commit.
39. **Replace `architecture.md:80-81`** with a pointer to `team.md`'s liveness rule; delete dead session ids.
40. **Retitle `audit.md`** as "Historical audit (v0.1.1, 2026-09-13)" and drop it from `team.md:40`'s read list.
41. **Fix the fabricated `.gitignore` description** in `project.md:131` and `opencode/sessions/2026-09-26-repo-reorganization.md:55`.
42. **Delete `.agents/memory/oh-my-pi/ohmypimemory.md`**, superseded duplicate asserting "External experiment NEVER completed".
43. **Name the pre-approved temp directory** in `.agents/AGENTS.md` and use it everywhere (7 places mandate POSIX `/tmp/..` on a documented PowerShell environment).
44. **A "machine-local paths" section** listing what exists only on this clone because ignore rules are uncommitted.
45. **A rule that anchors decay, and that ephemeral session names never belong in any file.**
46. **An encoding requirement in `opencode/environment.md`**, PowerShell 5.1's default `Get-Content` decodes these UTF-8 files as ANSI. Specify `-Encoding UTF8` for all file reads.

### Dependency and infrastructure adds

47. **Create `universal-game-agent/requirements-lock.txt` with hashes**, `pip-compile --generate-hashes` or `uv pip compile`. Pin `torch` to exact version matching your CUDA/Python; constrain `numpy<2.0`; align `pyyaml` with `agentops`'s `>=6.0,<7.0`.
48. **Add lockfile mechanism for `agentops` optional extras**, `uv.lock` or `requirements-lock.txt` for `[yaml]` and `[windows]` extras.
49. **Audit and attribute the skills corpus**, for each of the 13 skill folders, determine upstream license; create `NOTICE` at repo root; remove any skill with unknown/incompatible license; add GPL-3.0 headers to all original repo source files.
50. **Add `.gitattributes`** (all 7 dirty files emit LF→CRLF; no `.gitattributes` and no `.editorconfig`) and `.editorconfig`.
51. **Broaden the checkpoint ignore**, `.gitignore:9-11` covers only `.pt`, `.zip`, `.pkl`. Torch also writes `.pth`, `.safetensors`, `.ckpt`, `.bin`, `.npz`.
52. **Make `logs/*.log` recursive**, `.gitignore:14` is depth-1 only; `training/external_experiment.py:50-57` opens per-run logs.
53. **Add `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`/`htmlcov/`, `*.db`, `*.log`, and editor cruft** to both existing `.gitignore` files.
54. **Add `pyproject.toml` for UGA and pin dependencies**, `requirements.txt` has 5 deps with zero version specifiers, no lockfile. UGA has nowhere to put packaging metadata.
55. **Add `agentops/tests/__init__.py`**, UGA's `tests/` has one; agentops' does not, so `python -m unittest tests.test_workflow` fails.
56. **Add `py.typed`** to agentops despite 10,358 lines of package code and typed DTOs throughout.
57. **Fix the mojibake em-dash** in `universal-game-agent/requirements.txt:1` (`# Universal Game Agent ─�? runtime dependencies`).
58. **Add `pip-audit` or `safety` to CI** (when CI exists).
59. **Document Python version requirement in `requirements.txt`**, move `# Python 3.11+` from comment to a `python_version` marker or `pyproject.toml` `requires-python`.

### Deliberately not recommended

A linter, formatter, or type-checker. `.agents/AGENTS.md:55` says "do not invent lint,
  formatter, type-check, or coverage commands", and all four instruction files state
consistently that none is configured. Verified: no `ruff.toml`, `.ruff.toml`, `mypy.ini`,
`.flake8`, `pytest.ini`, `setup.cfg`, `.pre-commit-config.yaml`, and no `[tool.*]` beyond
setuptools. If you want one, that rule has to change first, do not just add a `ruff.toml`.

---

## Improve

### agentops

- Split `ready_tasks()`'s writes out of a query named as a read (`state.py:846-861` marks tasks BLOCKED, calls `update_task`, and emits `task-updated`; called in a loop from `workflow.py:378`).
- `workflow.py:1143` and `:1169`, `assert_workflow_ready` is invoked inside the `if` whose condition is the same three booleans, so it can only fail because the `if` is wrong. Only `evidence_present` adds information.
- `gui.py:462,485,504,640,670`, five `getattr`/`callable` probes against a `GuiController` Protocol (`gui.py:26-56`) that already declares those methods. No test exercises them.
- Enforce the `MUST_FAIL_CLOSED` table. `persistence.py:47-48` states the policy and `:63-70` marks `task.update` and `merge.conflict_task`, but `DegradationRecorder` is consumed only by `verification_kernel.py:19,104`. `workflow.py:289-292` leaves `recover_incomplete`'s `create_failure` as bare `except Exception: pass`, and `:580,595,608,673,701` wrap `record_failure` the same way. `state.update_task`, the write that makes `verified=True` a durable claim, is never guarded.
- `gui_controller.py:587-591`, `recover_interrupted` calls `recover_agent_runs()` and `recover_verification_runs()` with no `workflow_id` while scoping only `recover_tasks`. Both defaults are `None` = all rows (`state.py:1139,1491`) and both transition runs to a terminal state. The engine gets this right at `workflow.py:239-243`. Latent only because nothing calls it and no test covers it.
- `git.py:77-78` decodes subprocess output with the console code page and catches only `(OSError, subprocess.TimeoutExpired)` at `:79-80`, so a non-ASCII repo path or branch name escapes as a raw `UnicodeDecodeError`. The package already established the opposite convention at `registry.py:44-50` (`encoding="utf-8", errors="replace"`). Same gap at `agent_run.py:219-227,240-248`.
- `scripts/build_windows_exe.py:1` claims "reproducible" and `README.md:52` says "Build a reproducible executable", but the script only runs PyInstaller and checks the `.exe` exists (`:24-27`). `AgentOps.spec:29` sets `upx=True` with no pinned UPX; PyInstaller silently skips it when absent.
- Leaf-module violations: `execution_model.py:3` declares itself a leaf but `:59` imports from `agent_run.py`, which imports `subprocess` (`agent_run.py:7`) and a private helper from `git` (`:13`). `persistence.py:27-28` declares itself a leaf but imports `redact_text` from `logging`. `agent_run.py:13` reaches into `git._no_window_kwargs`, bypassing the sanctioned policy at `runtime.py:101-115`.
- `verification_kernel.py:49-70`, `_summarize_counts` counts `CANCELLED` in `required_failures` but not in `failed`, so `total != passed + failed + skipped` for a cancelled run.
- `cli.py:83-95`, `_print_text` falls back to `sys.stdout.buffer`, which does not exist under captured or replaced stdout. Guard with `getattr(sys.stdout, "buffer", None)`.
- `agents/agents.yaml` lists `claude` in all four `role_preferences` arrays while `"enabled": false`; `registry.select` skips it, so the lists mislead maintainers.
- Contract drift in `agentops/AGENTS.md`: the test-module count is off (lanes variously read 21, 22, and 23, drop the count as the file already does for test totals), and the architecture map omits `agentops/persistence.py` and `agentops/agent_run.py`.
- `routing.py:665-675`, `allow_capability_fallback=True` path re-scores without re-checking `profile.availability`. `routing.py:538-568`, `_static_decision` ignores `required_capabilities`. `routing.py:594-599`, `available_agents` coercion bug with single `AgentProfile`.

### universal-game-agent

- Make `main.py` refuse `type: external` with an error naming the sanctioned command. Delete the README:182 sentence that blesses the route.
- Report uncertainty in `main.py:306-313` comparison, not a sign. With `std_reward ~= 0.36` at `n=100` (effectively `n~=17`), the current `+0.04` is well inside noise.
- Give the external env a real `reset(seed=..)` via `super().reset(seed=seed)` plus a documented re-seed policy, or stop recording `seeds`.
- `train_with_window_relaunch` gives up by raising at `external_experiment.py:77-79`, so the report at `:305-338` is never written, after an hours-long run you keep `ppo_interrupted.pt` in a PID-scoped directory and no JSON pointing at it. Also `launch_phase2` (`:267-271`) calls `launch_game` then `wait_attach` outside any `try`, leaking the game process and `log_file` handle.
- Validate `checkpoint_every_updates` in `PPOConfig.__post_init__`.
- `capture.out_width`/`out_height` are validated, recorded, and then ignored in both live modes: validated at `:380-382`, passed only in `synthetic` (`:414`), omitted for `region` (`:421-422`) and `window` (`:430`). Native resolution is intentional; the key is the problem.
- Result metadata cannot reproduce the run: `external_experiment.py:310` records the raw `ppo.checkpoint_dir` while `:332` reports a different path.
- Delete the dead layer: duplicate `out_path` at `external_experiment.py:297` and `:336`; `training/reward_diagnostic.py:33-39` `classify()` that `diagnose()` reimplements; `training/logger.py` and `configs/default.yaml` `logging:` block wired to nothing.
- Refresh README:101-108, :122-124, and :174-178 to match shipped configs, existing external training loop, and reward/termination values the factory actually accepts.
- Rename `training/ppo.py:383` `run_experiment` → `run_toy_experiment` to eliminate the name collision with `training/experiment.py:66`.
- Wire the dead `logging:` config or remove the block from `configs/default.yaml`.
- Add `.gitattributes` and `.editorconfig`; add `pyproject.toml`; pin dependencies; add `requirements-lock.txt`.

### Instruction layer

- Fix Oh-My-Pi location in `.agents/AGENTS.md:19` and `.agents/memory/project.md:20`.
- Fix root `pi_AGENTS.md:13-14` startup checklist, add `.agents/memory/`.
- Scope `tasks/after-task.md` sections 1-3 to `agentops`; replace baseline with a pointer to the two-row command table in `.agents/AGENTS.md`.
- Delete `lessons.md:13-15` and `:378`; state ordering is append-by-arrival.
- Correct `project.md:33` and `roadmap.md:22` to the 2026-09-26 collaboration policy; land `after-task.md:39-46` in the same decision-logged commit.
- Replace `architecture.md:80-81` with a pointer to `team.md`'s liveness rule; delete dead session ids.
- Retitle `audit.md` as historical; drop it from `team.md:40`.
- Fix the fabricated `.gitignore` description in `project.md:131` and `opencode/sessions/2026-09-26-repo-reorganization.md:55`.
- Delete `.agents/memory/oh-my-pi/ohmypimemory.md`.
- Name the pre-approved temp directory once in `.agents/AGENTS.md` and use it everywhere.
- Add a `verified-at: <commit> <date>` header on every memory file, plus a check.
- Correct the test-module counts in `agentops/AGENTS.md` and `.agents/AGENTS.md` (the stated count is off by one or two; drop the count, as the file already does for test totals).

### Repository hygiene

- Create `universal-game-agent/requirements-lock.txt` with hashes; add lockfile mechanism for `agentops` extras; audit and attribute the skills corpus; add `NOTICE`.
- Add `.gitattributes` and `.editorconfig`; add `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`/`htmlcov/`, `*.db`, `*.log`, editor cruft to both `.gitignore` files.
- Broaden checkpoint ignore beyond `.pt`; make `logs/*.log` recursive.
- Add `pyproject.toml` for UGA and pin its dependencies; add `agentops/tests/__init__.py`; add `py.typed`; fix mojibake in `requirements.txt:1`.
- Archive/delete dead directories: `small-projects/`, `.misc/`, `.playwright-mcp/`. Archive `agent-intercom-fix/` if upstream merge pending; otherwise delete.
- Fix the `games/` double-import hazard (add `__init__.py`; relative import).

---

## Delete / archive candidates

| Target | Reason |
|---|---|
| `orchestrator.py` (root, untracked) | Dead prototype. Hardcodes `D:/admin/code/projects/.agents/orchestrator` (`:13`, an empty untracked dir), declares Python 3.12+ against the repo's 3.11 floor (`:2`), dispatches omp/pi/opencode with its own hardcoded table (`:21-27`) that `agentops/agents/agents.yaml` + `registry.py` + `routing.py` already own, uses `success = result.returncode == 0` (`:90`), process exit code as verification, precisely what the success-evidence rule forbids, and rewrites task files with no single-writer coordination (`:62-74`) while writing into tracked `.agents/outputs` and `.agents/memory` (`:100,117`). In no ignore list, referenced by no instruction file. |
| `skills-lock.json` (root, untracked) | 4 of 7 `skillPath` values dangle; all 7 `computedHash` values mismatch the SHA-256 of the corresponding `SKILL.md`. Covers 7 of 13 tracked skill directories and omits all six `antislop*` skills. Belongs at `.pi/skills-lock.json`, inside the already-excluded `.pi/`. |
| `.agents/memory/oh-my-pi/ohmypimemory.md` | Superseded duplicate asserting "External experiment NEVER completed"; duplicates `oh-my-pi/architecture.md:17-34` with older content. |
| `.agents/memory/audit.md` | v0.1.1, 14 modules, 79 tests; omits 9 real modules; documents a since-fixed defect as live. Retitle as historical or delete. |
| `.agents/plans/chatgpt_recommendations.md` (840 lines) and `chatgpt_addition_recommendations.md` (1,194 lines) | 2,034 lines = 26.5% of all tracked markdown. The only two plans with no matching file in `.agents/outputs/`. `roadmap.md:273` itself calls them "advisory, not user-approved directives" and their content is already mapped into Track C. |
| `.agents/additions.md` | Duplicates `pending_tasks.md:5-30`, names `durable-json.windows-eperm.patch` which does not exist, and gives root paths for files that live in `.agents/`. |
| `.agents/outputs/phase-2-verification-kernel.md` | Orphan output, no matching plan, zero references anywhere in the meta layer. |
| `lessons.md:5-11` | The 2026-09-14 Agent Intercom `writeDurableJson` entry concerns two external npm packages, not this repo. |
| `lessons.md:13-15` and `:378` | The false "greenfield / no code" block and the false "no test runner" line. |
| `small-projects/universal-game-agent/`, `small-projects/quickscripts/`, `.agents/orchestrator/` | All verified empty, all protected by "leave alone" rules (`oh-my-pi/project.md:9`, `ohmypimemory.md:6`, `oh-my-pi/lessons.md:12`) that therefore protect nothing. |
| `.misc/`, `.playwright-mcp/`, `.agentops/` (container root), `.pi/` | Scratch/runtime directories, machine-local, correctly excluded via `.git/info/exclude`. Delete scratch; keep runtime state excluded. |
| `tasks/task-ignorethis.md` | Externally-authored Failure Analysis spec (author Hacir Bacoj, created `58355c5` 2026-09-15, last modified `4d408ed` 2026-09-16), tracked and clean, no secrets, but sitting in the canonical task namespace that `after-task.md:84-87` describes, with no product attribution, no author, and no "superseded" marker. Add a one-line provenance header or move it to `.agents/plans/`. |

### Keep but commit

- `universal-game-agent/experiments/exp_external_pong_01-14676_results.json` (7,516 bytes), completes an otherwise complete set of 5 tracked result files. Caveat: written by an older code version (has `final_eval_mean_reward`, no `comparison` block), not comparable to the tracked file, and shows the same phantom-episode contamination per finding 1.
- `agent-intercom-fix/`, 6 files / 22,172 bytes of upstream patch artifacts for two external npm packages, presented as live work by `pending_tasks.md:11-29` and `additions.md:9-20`, but excluded via `.git/info/exclude:10`. Has a real upstream-PR purpose and currently exists on exactly one machine. Commit it or drop the `.agents/` references to it.

---

## Verified genuinely good

### agentops

- **Verification kernel** (`verification_kernel.py:171-189,227-267,269-336`), empty suites and duplicate check names are rejected *before* any persistence write; fail-fast and continue-on-failure both behave; parallel groups cancel siblings only on terminal failure; external cancellation closes out every unfinished check and persists a CANCELLED report before re-raising; any lost `MUST_FAIL_CLOSED` write demotes a PASSED report to FAILED with the reason appended to the transcript (`:299-313`). The strongest module in the package and the model the other I/O paths should follow.
- **Process runtime** (`runtime.py:60-115,150-224,226-294`), one spawn policy across platforms, a case-folded reduced environment with the `%SystemRoot%` fallback Bun-based CLIs require, cooperative cancellation on 50ms polling, process-group termination with a bounded 2x cleanup wait, and a `finally` that kills the child on external `CancelledError`. No orphaning path found.
- **Success-model invariants** (`execution_model.py:143-217`), the four assertions are pure, total over well-typed input, and fail loud rather than coerce. `state.py` calls `assert_no_fabricated_success` from all three recovery paths (`:1183,1561,1926`), so `recover_*` cannot mint a success status without evidence.
- **Artifact store** (`artifacts.py:108-133`), rejects both `./` traversal and symlink escapes via `resolve()` + `relative_to`; `:135-203` writes through a staging file and `os.replace` so a crash never leaves a partial artifact; `:205-217` verifies sha256 on read; 0700/0600 permissions applied; the crash-window orphan case is documented *and* detectable via `find_orphans` rather than glossed over.
- **Command safety and prompt hygiene**, no shell anywhere: commands are argument arrays validated by `config.py:80-83` and spawned via `create_subprocess_exec`. `runner.py:214-215` decodes child output as UTF-8 with replacement. `agent_run.py:50-95` persists only a prompt sha256 and length with `preview: None`. `safe_command` replaces the prompt inside argv before command metadata is stored.
- **Recovery idempotency**, `workflow.py:257-266` uses the `(task, recovery_state)` pair as an idempotency key; `workflow.py:239-243` scopes all three recovery passes to the requested workflow.
- **`agent_result.py`**, Versioned structured result schema, reliable parser that never raises, `evaluate_execution` treats UNKNOWN/missing as not success, `merge_eligible` requires ALL four signals true, `coerce_agent_result` strips secrets from legacy payloads.
- **`agent_adapter.py`**, Clean boundaries, honest capability derivation, TOCTOU-safe command building, `supports(role)` preserves empty-roles-means-all semantics.
- **`routing.py` core logic**, Preference scoring, hard gates, explainable decisions, disabled-agent handling correct in enabled mode. `agents.yaml` → routing: `enabled: false` agents excluded from selection; no leak.
- **`gui_controller.py` provenance/merge**, `retry_merge` correctly uses stored `WorktreeRef`; `record_worktree_provenance` called at workflow start.
- **Process runtime is genuinely well built**, one spawn policy, cooperative cancellation, process-group termination, `finally` that kills the child on `CancelledError`. No orphaning path.

### universal-game-agent

- **Layering genuinely holds** under a static import scan. No module under `interface/` imports `environment`, `training`, or `agent`; `environment/` touches `interface` only at function scope; `games/` never imports the agent. `interface/__init__.py` and `agent/__init__.py` both use lazy `__getattr__`, so torch stays out of the toy import path.
- **Recurrent PPO boundary semantics are correct and consistent** between rollout and replay. `compute_gae` (`ppo.py:68-82`) masks on `terminated` rather than `done` so truncation still bootstraps; `next_value` bootstraps `V(final pre-reset obs, its hidden state)` on truncation (`:206-209`); the update loop re-splits the minibatch at every `done` index and re-zeros the hidden state (`:244-258`) exactly as the rollout did. The done-boundary case for a `--resume` trainer is also handled (`:125-127`).
- **Timeout/termination precedence is right.** `external_game.py:303-304` forces env-level `max_episode_steps`/`max_episode_seconds` into `truncated=True` and never `terminated`, while a genuine banner termination still reports `terminated=True`, and `max_episode_seconds` correctly uses the injected `Clock` (`:312`), not wall time.
- **The `interface/` layer is well tested without a live window.** All 30 tests in `tests/test_interface.py` run against fakes and cover the failure-prone parts: key release on mid-hold exception (`:60-73`), chord press/release ordering and all-keys release (`:237-247`), cooldown throttling (`:249-255`), and the full `ActionDef` validation surface (`:264-282`). No test requires a real window.
- **Curiosity does not leak across episode boundaries.** `ppo.py:282` passes `~buf["dones"]` as the validity mask; `curiosity.py:139` guards the `RunningMeanStd` update against an all-invalid batch.
- **Checkpoint isolation per run**, `external_experiment.py:_run_checkpoint_dir` and `_results_path` embed `os.getpid()`; `_apply_run_title` deep-copies config and retargets window titles. Concurrent runs cannot clobber.
- **Agent/network separation is clean**, `agent/model.py` has zero game deps; `PPOTrainer` only sees `env.reset/step` and tensor shapes. No duplication between `agent/` and `training/ppo.py`.
- **Zero `TODO`/`FIXME`/`HACK`/`XXX`/`NotImplemented`** markers anywhere in the tree.
- **The `test_interface.py` gold standard**, 13 test classes, zero mocks, fakes implement the actual interface contract, assertions on observable outputs.

### Repository and process layer

- **Zero tracked artifacts.** An exhaustive `git ls-files` scan returns 0 hits for `__pycache__/`, `*.py[co]`, `dist/`, `build/`, `*.egg-info/`, caches, `logs/`, `*.log`, `*.db`, `*.sqlite*`, `node_modules/`, coverage files, and editor backups. No `.gitignore` is broken; nothing was force-added. ~59 MB sits untracked on disk and 0 bytes of it is in git.
- **No file is too large for git.** The largest tracked file is `agentops/agentops/state.py` at 90,165 bytes. No file exceeds 100 KB.
- **Append-only history is genuinely preserved.** `decisions.md` holds 30 dated entries in strict chronological order with nothing rewritten or removed; the two superseded collaboration decisions are preserved in place and explicitly superseded at `:212-217`. `lessons.md` is likewise append-only across 30+ entries.
- **Both headline rules sit at every point of action.** "Do not persist raw prompts, secrets, credentials, tokens, private keys" appears in root `AGENTS.md:15`, `.agents/AGENTS.md:64`, `pi_AGENTS.md:33`, `ohmypiagents.md:45`, `agentops/AGENTS.md:61`, `universal-game-agent/AGENTS.md:130`, and `.github/copilot-instructions.md:110`. "Agent-reported success is never verification evidence" appears in `.agents/AGENTS.md:69`, `pi_AGENTS.md:32,65`, `ohmypiagents.md:44,77`, `agentops/AGENTS.md:98`, `universal-game-agent/AGENTS.md:128`, and `.github/copilot-instructions.md:112`.
- **The shims behave as shims.** Root `AGENTS.md` (28 lines) and `pi_AGENTS.md` (14 lines) both redirect to `.agents/` and carry no independent content; the antislop block is identical across both shims and `.agents/AGENTS.md:90-104`.
- **Both product `AGENTS.md` files carry a "Known defects" section with file:line**, the highest-value docs in the repo, and the reason several findings above were findable at all.
- **No encoding corruption.** 0 of 182 tracked text files contain a U+FFFD replacement character. The `─�?` sequences seen while reading excerpts are PowerShell 5.1 console rendering (ANSI decode of UTF-8), not file damage.
- **Product-local instruction files are accurate where an agent would act on it.** `runtime.py:102-112` is the real `spawn_options()` with `CREATE_NO_WINDOW` at 109-111; `state.py:1615-1631` is the real `_migrate_failure_evidence()` with the v7 `structured_evidence` column; `config.py:62` is the escaping `DEFAULT_CONFIG_PATH` recorded in Known defects; `git check-ignore` confirms all five `.pt` files stay untracked while `README.md`/`.gitkeep` remain tracked, so the checkpoint-ignore fix from `65ef097` works.
- **All DONE roadmap items are genuinely complete** with tests and reviews. Agentops A8 (persistence failure policy) landed bringing the suite to 382 tests / 4 skipped, verified against the current tree.

---

## Confidence and caveats

- One lane's earlier report was partly retracted by its own errata before the corrected version arrived. The surviving `universal-game-agent/AGENTS.md` anchor defects are real but the line numbers were re-derived; spot-check before acting.
- Where lanes disagreed, treat the specific number as unverified and the defect as confirmed: `skills-lock.json` (0/7 vs 4/7 dangling paths) and the agentops test-module count (21 vs 22 vs 23).
- The meta-layer lane flagged that its own line anchors beyond a certain depth came from batched reads with truncated per-file output. The line *content* is high-confidence; the exact line numbers for memory-file citations are not individually re-verified. The anchors in findings 1, 2, 3, 18, 19, 21, and 23 were re-read and confirmed.
- Neither test suite was executed. Any claim about current test counts or pass/fail state is unverified.
- The tree moved during this review: the parallel writer's A8 work landed in `018de82` (bringing the suite to 382 tests / 4 skipped), so the in-flight feature is now committed and verified. Findings about `persistence.py` in the original report describe the state at that commit; the current tree includes it.
- The roadmap audit's verdicts on A1, A4, A8, B7 were verified against the current tree; all are confirmed DONE with tests. A5, A6, A7, A9, A10, B6, B8, B9, B10 are correctly marked PROPOSED, no code exists for them.
- The instruction drift matrix covered 8 files and 113 rules; the highest-damage divergences are ranked above. The full matrix (100+ rules, 1500+ lines of evidence) is available in the recovered output; only the top divergences and dead references are summarized here to avoid bloat.

---

## A8 reviewer pass adjudication

The A8 persistence failure policy feature (`ff03eea` → `aa830ef` → `018de82`) was reviewed against the current tree (382 tests / 4 skipped, all passing). The review produced three observations, all of which were adjudicated by the author:

### Observation 1 (declined): Add `DegradationRecorder` integration to `finalize_worktree`

**Claim:** `finalize_worktree` does not use the `DegradationRecorder` mechanism for `state.add_task`, fail-closed is achieved by error propagation instead of the recorder, which is architecturally inconsistent with the other four paths.

**Adjudication: Declined. The claim misreads the control flow.**

`state.add_task` is reached only inside `except GitError` (not after a successful merge). If `manager.merge(worktree)` succeeds, `state.add_task` is never called. If `manager.merge(worktree)` raises `GitError`, the worktree is *not* merged, only the debugging task insertion fails. The reviewer's justification ("the worktree is merged but the debugging task is lost") describes a case that cannot exist given the code structure.

More importantly, the proposal would degrade a `MUST_FAIL_CLOSED` transition into a `WorktreeFinalization(merged=False, conflict_error=<git error>)` shape that looks like normal conflict handling. This would:
- Break `test_merge_conflict_task_failure_is_fail_closed` (the exact test guarding against a caller believing a lost debugging task was handled)
- Violate the function's own docstring promise ("a persistent debugging task is recorded") by replacing it with a warning event
- Change the caller's experience from a hard exception (fail-closed) to a success-shaped response

The current propagation behavior is correctly fail-closed. Architectural consistency is desirable but the proposed remedy makes the system worse, not better. Recorded so the next reviewer does not re-raise it.

### Observation 2 (declined): `tuple` copy on `.degradations` is O(n)

**Adjudication: Declined.** Immaterial in practice, nothing in production reads `.degradations`; only tests do. The tuple copy buys the safety that makes handing out the property safe.

### Observation 3 (declined): No lint/type-check/coverage tooling

**Adjudication: Declined.** Deliberate standing decision per `AGENTS.md` ("Do not invent lint, formatter, type-check, or coverage commands"). A10 already proposes ruff-in-CI.

### Pattern noted

Three analytical errors across three review passes, `_operation_lock` (nonexistent), the storage-path claim (wrong directory), and this control-flow misread. Each was caught only by checking against the code directly, never by reading alone. The kernel integration findings (per-run `degraded` list, cancel-path handling, `_finish_check_persisted` boundary) were genuinely useful. The pattern is: keep adjudicating every claim, and be suspicious of any suggestion that reframes a fail-closed path as an architectural improvement.
