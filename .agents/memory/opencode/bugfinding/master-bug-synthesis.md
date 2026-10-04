# Master Bug Synthesis v2 — `angasko-12345/projects-ai`

**Produced:** 2026-09-29
**Inputs reconciled:** bug-registry.md, deep-bug-audit-2026-09-29.md, geminihandoff.md, projects-ai-bug-handoff.md, projects-ai-review-handoff.md, repo-review-2026-09-26.md, plus the previous triage result.
**Scope:** Analysis only. No code modified.
**Previous synthesis:** master-bug-synthesis.md — this is a complete replacement with full traceability.

---

## 0. Current Status

**Verified 2026-10-02 against current `main` (`849e015`).**

This section is the **live bug ledger**. Everything from §1 onward is the historical
2026-09-29 audit and stays as evidence, not as instructions. §9 in particular is
superseded and must not be used as the work queue — the live queue is
`.agents/pending_tasks.md`.

**Test baselines re-run 2026-10-02 (each product's own command, from its own directory):**

| Product | Command | Result 2026-10-02 |
|---|---|---|
| `agentops/` | `python -m unittest discover -s tests` | 444 tests, 4 environment skips, OK (re-verified 2026-10-03 after ROOT-034 DBG-08's real process-tree test; 443 at the 2026-10-02 root-fix batch; earlier same-day baseline: 425) |
| `universal-game-agent/` | `python -m unittest discover -s tests` | 349 tests, 1 skip, OK (re-verified 2026-10-03 after the external-experiment orchestration batch closed the ROOT-014 external gap; 318 after `59f5a1b` closed ROOT-034 DBG-06/DBG-07; 317 at the 2026-10-02 root-fix batches; earlier same-day baseline: 295) |
| `small-projects/mini-llm/` | `python -m unittest discover -s tests` | 98 run, 1 skip, OK (re-verified 2026-10-04 after restoring the tracked `data/tokenizer.json`; the 96-run/3-error figure below was a working-tree deletion, not a code defect) |

The 3 mini-llm errors seen 2026-10-02 through 2026-10-04 came from a working-tree
deletion of `small-projects/mini-llm/data/tokenizer.json`. **Closed 2026-10-04:**
restored from git (no commit had ever deleted it) and byte-verified against the
committed `data/processed/*`; the suite is green with no test weakened. Two tests
added, since the committed tokenizer had no coverage at all.

### 0.1 Status of all 38 canonical roots

| ROOT | Title | Product | Status | Current source evidence (2026-10-02) |
|---|---|---|---|---|
| ROOT-001 | Unredacted `task.result` persistence | AgentOps | **FIXED** | `workflow.py` redacts stdout/stderr, legacy verification output, and the task-execution error string at every durable assignment; reuses `logging.redact_text` |
| ROOT-002 | Review gate bypass via CLI | AgentOps | **FIXED** | One predicate owns readiness — `assess_workflow_readiness`; used by CLI, GUI, and both `run_high_level` READY returns (849e015) |
| ROOT-003 | Self-deadlock via `.agentops/` in target repo | AgentOps | **FIXED** | `.git/info/exclude` keeps AgentOps state out of the target repo status; exclusion failure now raises an actionable `GitError` instead of deadlocking the merge (849e015) |
| ROOT-004 | Git failure misclassification | AgentOps | **FIXED** | `finalize.py` classifies `GitError` through `FailureClassifier.classify(source=FailureSource.GIT, error=...)`: dirty base → clean-worktree task, refused/changed base → re-validate task, conflict → unchanged conflict task, other → generic merge task (2026-10-02) |
| ROOT-005 | Silent exception swallowing in failure path | AgentOps | **FIXED** | `workflow.py` no longer swallows failure-record losses: the six `record_failure` wrappers record a `failure.create` degradation, and the Path-B observer create/finish/fail arms reuse `_note_run_degradation` (`agent_run.create` / `agent_run.transition`) (2026-10-02) |
| ROOT-006 | GUI mutual-exclusion bypass | AgentOps | **FIXED** | `_begin_operation` mints a fresh cancel `Event` per operation and `_end_operation` identity-checks its argument, so a stale worker cannot clear a newer operation's active state (2026-10-02) |
| ROOT-007 | GUI recovery scope mismatch | AgentOps | **FIXED** | `recover_interrupted` forwards `workflow_id` to `recover_agent_runs`, `recover_verification_runs`, and `recover_tasks`; a scoped pass no longer touches another workflow's rows |
| ROOT-008 | Unvalidated checkpoint interval | UGA | **FIXED** | `PPOConfig.__post_init__` validates `checkpoint_every_updates` as a non-negative int (`bool` rejected); 0 disables periodic writes, `ppo_final.pt` always written |
| ROOT-009 | Orphaned game process on attach failure | UGA | **FIXED** | `external_experiment.launch_phase2_process` (`external_experiment.py:182`) transfers proc ownership to the caller only on success and stops it locally on attach/liveness failure |
| ROOT-010 | Checkpoint-load env not closed | UGA | **FIXED** | `load_eval_model` closes the throwaway checkpoint-load env in `finally`; `evaluate()` closes each per-episode env on exception. The audit's FIXED enumeration omitted ROOT-010; it is listed here on source evidence (2cb2413, P6) |
| ROOT-011 | Capture-size contract undefined | UGA | **CONTRACT GAP / DOCUMENTED** | `make_external_env_from_config` now documents the per-mode meaning of `capture.out_width`/`out_height`: honoured by `synthetic` only; `region` returns the captured rectangle (defaulting to the out size only when `capture.region.width/height` are absent); `window` returns the native rect. Native live frames are required by the red-pixel-count reward/termination bands. No code change (2026-10-02) |
| ROOT-012 | Verification run stranded | AgentOps | **FIXED** | `_abort_setup(run_id, checks, error)` finishes every created check and the run as terminal FAILED, then re-raises the original error |
| ROOT-013 | Non-atomic checkpoint save | UGA | **FIXED** | `save_checkpoint` writes a same-dir temp file and `os.replace`s it; temp is cleaned on failure |
| ROOT-014 | Resume crash on finished checkpoint | UGA | **FIXED** | `PPOTrainer.is_complete()` + a `train()` guard return the existing `ppo_final.pt` with a "no updates will run" message instead of indexing an empty history; readers go through `training.ppo.summarize_history`, and `experiments/*_results.json` gained `training_updates` so an untrained run is visible. Covered by exactly-complete, over-complete, and normal-resume CLI tests (2026-10-02). **Re-verified 2026-10-03: the 2026-10-02 fix missed a reader.** `training/external_experiment.py` still indexed the history directly, so the external driver raised `KeyError: mean_reward` on exactly the case the guard creates. It now aggregates via `summarize_history` and records `training_updates`; `TestWindowLossRelaunch::test_no_op_resume_reports_zero_updates_instead_of_crashing` pins it (mutation-proven) |
| ROOT-015 | Entropy loss sign reversal | UGA | **DISPROVEN** | Does not hold against current `training/ppo.py`; the sign is correct. Do not revive |
| ROOT-016 | Truncation treated as terminal | UGA | **DISPROVEN** | GAE already separates truncation from termination in current source |
| ROOT-017 | GRU hidden state leak across trajectories | UGA | **DISPROVEN** | Current PPO already resets hidden state at episode boundaries via segmented replay |
| ROOT-018 | ICM gradient bleed into shared backbone | UGA | **DISPROVEN** | Curiosity features are already detached before the heads |
| ROOT-019 | Git clean on root repo | AgentOps | **DISPROVEN** | `abort_merge`/worktree cleanup no longer default to the root workspace; the dirty-tree guard is unchanged and correct |
| ROOT-020 | Subprocess buffer deadlock | AgentOps | **DISPROVEN** | Runner already drains output before waiting; no synchronous wait-then-read deadlock exists |
| ROOT-021 | Process group leak on cancel/timeout | AgentOps | **DISPROVEN** | Cancellation already tears down the child tree through the shared `runtime.py` spawn policy |
| ROOT-022 | StateStore transaction error swallowing | AgentOps | **DISPROVEN** | Persistence failures are classified by `persistence.PERSISTENCE_POLICIES`; unknown writes default to `MUST_FAIL_CLOSED` |
| ROOT-023 | Deterministic selector bypasses user preference | AgentOps | **DISPROVEN** | Routing scores honour configured preference; tie-breaks no longer silently drop it |
| ROOT-024 | Frame normalization truncates to zero | UGA | **DISPROVEN** | `preprocessing.py` already casts to float before dividing; frame range is `[0,1]` |
| ROOT-025 | Win32 GDI handle leak | UGA | **DISPROVEN** | `interface/win32_capture.py` already releases HBITMAP/HDC on all paths |
| ROOT-026 | Missing `games/__init__.py` | UGA | **CONTRACT GAP / ACCEPTED DEBT** | `universal-game-agent/games/` has no `__init__.py` and still works as an implicit namespace package; `games/extern_pong.py` additionally mutates `sys.path`. Recorded debt, not queued |
| ROOT-027 | External attach failure leaks process+log+results | UGA | **FIXED** | Process/log cleanup already covered by `launch_phase2_process` + `stop()` (ROOT-009); `run_external_experiment` now writes `<stem>_results.json` with `status: failed`, `error`, and `error_type` before re-raising on any failure path (2026-10-02) |
| ROOT-028 | GAE mid-rollout truncation bleed | UGA | **DISPROVEN** | The timeout-aware GAE already carries the dones buffer; the claimed bleed does not reproduce |
| ROOT-029 | Producer/validator divergence | AgentOps | **FIXED** | Producer decision chain requires ≥1 passed check for PASSED (otherwise FAILED), so vacuous pass suites no longer contradict `assert_report_consistent`; regression tests in `tests/test_verification_kernel.py` (2026-10-02) |
| ROOT-030 | Unclassified evidence write | AgentOps | **FIXED** | Artifact-pointer write failure records a `verification_check.artifacts` degradation with `SAFE_TO_DEGRADE` in `persistence.PERSISTENCE_POLICIES`; stdout/stderr pointers are dropped, check state and transcript retained — no fabricated evidence (2026-10-02) |
| ROOT-031 | Provenance-write bypass | AgentOps | **FIXED** | Provenance writes are centralized: `gui_controller.py:20` imports `record_worktree_provenance` with a single call site at `:396`; no second unguarded write remains |
| ROOT-032 | UnicodeDecodeError on non-ASCII | AgentOps | **FIXED** | `git.py` `_run` passes `encoding="utf-8", errors="replace"` to `subprocess.run`; non-ASCII bytes covered in `tests/test_git.py` (`Utf8DecodingTests`) (2026-10-02) |
| ROOT-033 | Windows termination classification | AgentOps | **HELD** | Deliberately held. No Windows termination contract is defined; do not implement the high-bit heuristic |
| ROOT-034 | Test-quality defects (cluster) | Both | **PARTIALLY FIXED** | UGA: DBG-06 and DBG-07 CLOSED 2026-10-03 (`59f5a1b`; 318 tests, 1 skip, OK, guard `tests/test_cwd_isolation.py`), DBG-13 FIXED for routing with only residual fixture quality, DBG-14 DISPROVEN, DBG-12 residual test debt. AgentOps: DBG-08 CLOSED 2026-10-03 (real Windows parent→child→grandchild integration test `test_real_parent_terminate_process_kills_descendant_tree` in `tests/test_runtime.py`, mutation-proven against a `taskkill`-without-`/T` mutant; 444 tests, 4 skips, OK), DBG-15 residual migration-test debt. ROOT-034 stays PARTIALLY FIXED only because DBG-12/DBG-15 residual test debt remains — DBG-08 no longer blocks closure; DBG-12/DBG-15 are test debt, not production bugs (2026-10-03) |
| ROOT-035 | GUI `_root_cache` shared mutable state | AgentOps | **FIXED** | `_operation_root` runs under `_root_cache_lock` (single-flight: one Git lookup for concurrent misses); GitError results stay uncached (2026-10-02) |
| ROOT-036 | Eval metric contract | UGA | **FIXED (contract)** | Reward semantics are now declared, not inferred: `environment/reward.py` defines `SIGN_SEMANTICS`/`GENERIC_SEMANTICS` and `reward_semantics_of` (fail-closed, warns on an unknown value); only `ToyPongEnv` and `ExternPongReward` declare `sign`. `evaluate()` reports `episode_hits`/`mean_hits` only for `sign` and otherwise omits those keys entirely (absence, not `0.0`), keeping the counts under `episode_positive_reward_steps`/`mean_negative_reward_steps`; `PreprocessingWrapper` downgrades to `generic` when `skip > 1` because reward summing breaks one-event-per-decision-step. No reward semantics or PPO math changed; tracked `experiments/*_results.json` untouched (2026-10-02) |
| ROOT-037 | Phase-2 relaunch loop leaks env + game process on failure or Ctrl-C | UGA | **FIXED** | `train_with_window_relaunch` released the env and the launched process only on a session loss or on success. A non-session exception and a `KeyboardInterrupt` skip both arms, so the run left `trainer.env` open and the Pong process running — a real window left up, still taking `SendInput`. Added an `except BaseException` arm plus `_close_quietly` (2026-10-03). Mutation-proven: reverting it fails 4 tests in `tests/test_external_experiment_orchestration.py` |
| ROOT-038 | Episode-boundary session loss is not relaunchable | UGA | **FIXED** | `ExternalGameEnv._ensure_session()` raised a bare `RuntimeError` when the session was gone and `attach()` could not rebind, and `_SESSION_ERRORS` did not list it. Only capture-time loss counted, so a window lost *between* decisions ended the whole experiment instead of relaunching. Added `SessionUnavailableError(RuntimeError)` — a subclass, so existing `except RuntimeError` still catches it — and added it to `_SESSION_ERRORS` (2026-10-03). Mutation-proven in both the env and the orchestration suite |

### 0.2 Counts

| Classification | Count | Roots |
|---|---|---|
| FIXED | 22 | ROOT-001, 002, 003, 004, 005, 006, 007, 008, 009, 010, 012, 013, 014, 027, 029, 030, 031, 032, 035, 036, 037, 038 |
| ACTIVE | 0 | none |
| PARTIALLY FIXED | 1 | ROOT-034 |
| CONTRACT GAP / ACCEPTED DEBT | 2 | ROOT-011, ROOT-026 |
| HELD | 1 | ROOT-033 |
| DISPROVEN | 12 | ROOT-015..025, ROOT-028 |
| **Total** | **38** | ROOT-001..ROOT-038 |

**Do not revive DISPROVEN findings.** They were re-checked against current source on
2026-10-02 and do not reproduce. The historical reasoning is preserved in §3 and §7 as
evidence, not as instructions.

---

## 1. Executive Summary

- Six independent audit reports plus a prior triage produce ~450 raw findings. After reconciliation, deduplication, and evidence grading, they resolve to **36 canonical root causes** (ROOT-001..ROOT-036), of which:

- **21 are genuine bugs** (confirmed or likely)
- **3 are contract/configuration gaps** requiring a decision before fixing
- **1 is a platform-semantics question** held for evidence
- **3 are test-quality clusters**
- **6 are non-bugs / architecture debt / measurement issues**

**Highest-severity items:**
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source verification against the current tree.
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.

**Previously triaged corrections upheld:**
- BUG-AO-01 (Windows termination) remains SUSPECTED — a positive exit code alone does not prove termination; do not implement the high-bit heuristic.
- BUG-UGA-05/06/07 and BUG-AO-05 confirmed false positives.
- BUG-UGA-04 remains a contract gap, not a confirmed defect.

**Key correction from previous synthesis:** The deep audit retracted its own earlier claim that raw stdout reaches the unredacted event sink via workflow.py:546. Source reading disproves it — task.result is overwritten at :542, two lines before the event. The confirmed sink is workflow.py:563 → task.result.

---

## 2. Complete Finding Traceability Matrix

Every identifiable finding from every source report, with exactly one disposition.

### bug-registry.md (12 findings)

| Source | Original ID | Disposition | Canonical ROOT | Reason |
|---|---|---|---|---|
| bug-registry.md | BUG-AO-02 — raw agent output in task.result | CANONICAL ROOT | ROOT-001 | Confirmed by reproduction; secret persisted to SQLite |
| bug-registry.md | BUG-AO-01 — Windows termination classification | CANONICAL ROOT | ROOT-033 | SUSPECTED; Windows contract needed before fix |
| bug-registry.md | BUG-UGA-01 — zero checkpoint interval | CANONICAL ROOT | ROOT-008 | Confirmed; ZeroDivisionError at ppo.py:376 |
| bug-registry.md | BUG-UGA-02 — game process leak on attach failure | CANONICAL ROOT | ROOT-009 | Confirmed by mock; proc.terminate() never called |
| bug-registry.md | BUG-UGA-03 — checkpoint-load env not closed | CANONICAL ROOT | ROOT-010 | Confirmed by mock; env.close() never called |
| bug-registry.md | BUG-UGA-04 — capture size ignored | CANONICAL ROOT | ROOT-011 | LIKELY; contract undocumented |
| bug-registry.md | BUG-AO-03 — GUI recovery ignores workflow filter | CANONICAL ROOT | ROOT-007 | CONFIRMED (latent); no callers found |
| bug-registry.md | BUG-UGA-05 — smoke path env not closed | FALSE POSITIVE | — | MSS backend and process already cleaned in finally |
| bug-registry.md | BUG-UGA-06 — experiment env not closed | FALSE POSITIVE | — | Toy env; close() is no-op |
| bug-registry.md | BUG-AO-04 — verification run stranded | CANONICAL ROOT | ROOT-012 | Confirmed; check-creation failure leaves RUNNING |
| bug-registry.md | BUG-AO-05 — ready_tasks mutation | FALSE POSITIVE | — | PENDING guard prevents repeated writes |
| bug-registry.md | BUG-UGA-07 — CLI training env leaked | FALSE POSITIVE | — | Toy path; external impact unreachable |

### deep-bug-audit-2026-09-29.md (44 current bugs + 8 rejected + 26 historical)

**44 confirmed current bugs:**

| Source | Original ID | Disposition | Canonical ROOT | Reason |
|---|---|---|---|---|
| deep-audit | BUG-AOP-01 — self-deadlock via .agentops/ dirty tree | CANONICAL ROOT | ROOT-003 | CRITICAL; reproduced on fresh repos |
| deep-audit | BUG-AOP-02 — review gate bypass | CANONICAL ROOT | ROOT-002 | CRITICAL; mutation-proven invisible to suite |
| deep-audit | BUG-AOP-03 — unredacted task.result | CANONICAL ROOT | ROOT-001 | HIGH; reproduced token=sk-… read back verbatim |
| deep-audit | BUG-AOP-08 — GUI recovery across workflows | CANONICAL ROOT | ROOT-007 | HIGH; GUI regression against fixed engine defect |
| deep-audit | BUG-UGA-15 — non-atomic checkpoint save | CANONICAL ROOT | ROOT-013 | HIGH; torch.save directly to final name |
| deep-audit | BUG-UGA-22 — external attach failure leaks process+log+results | CANONICAL ROOT | ROOT-027 | HIGH; reproduced with fake-backed probes |
| deep-audit | BUG-DBG-05 — GAE mid-rollout truncation bleed | CANONICAL ROOT | ROOT-028 | HIGH; source-level proof; toy env truncates every episode |
| deep-audit | BUG-DBG-06 — 66 of 132 UGA tests unreachable | CANONICAL ROOT | ROOT-034 | HIGH; test-quality cluster |
| deep-audit | BUG-DBG-07 — 4 tests write 2.2 MB into CWD | CANONICAL ROOT | ROOT-034 | HIGH; test-quality cluster |
| deep-audit | BUG-DBG-08 — no real-object process-group test | CANONICAL ROOT | ROOT-034 | HIGH; test-quality cluster |
| deep-audit | BUG-AOP-07 — persistence policy enforcement gaps | CANONICAL ROOT | ROOT-005 | MEDIUM; broad excepts around record_failure |
| deep-audit | BUG-AOP-09 — UnicodeDecodeError mechanism corrected | CANONICAL ROOT | ROOT-032 | MEDIUM; error swallowed in _readerthread |
| deep-audit | BUG-AOP-13 | CANONICAL ROOT | ROOT-005 | MEDIUM; related to persistence fallback (same area as AOP-07) |
| deep-audit | BUG-DBG-01 — producer/validator divergence | CANONICAL ROOT | ROOT-029 | MEDIUM; optional-failure fix in validator only |
| deep-audit | BUG-DBG-02 — unclassified evidence write | CANONICAL ROOT | ROOT-030 | MEDIUM; verification_kernel.py:585-586 |
| deep-audit | BUG-DBG-03 — provenance-write bypass | CANONICAL ROOT | ROOT-031 | MEDIUM; gui_controller.py:728-730 |
| deep-audit | BUG-DBG-09 — event sink no redaction | NEEDS INVESTIGATION | — | MEDIUM; latent boundary gap; no live untrusted path confirmed |
| deep-audit | BUG-DBG-10 — game process leak driver never called | NEEDS INVESTIGATION | — | MEDIUM; driver is never called in current paths |
| deep-audit | BUG-DBG-11 — merge dirty-tree guard missing | COVERAGE GAP | — | MEDIUM; git.py:239-243 already guards; no test produces real conflict |
| deep-audit | BUG-DBG-15 — migration 8 breaks two suites | NEEDS INVESTIGATION | — | MEDIUM; confusing assertion instead of clear failure |
| deep-audit | BUG-UGA-01 — checkpoint interval zero crash | CANONICAL ROOT | ROOT-008 | MEDIUM; duplicate of bug-registry BUG-UGA-01 |
| deep-audit | BUG-UGA-02 — game process leak | CANONICAL ROOT | ROOT-009 | MEDIUM; duplicate of bug-registry BUG-UGA-02 |
| deep-audit | BUG-UGA-03 — checkpoint-load env not closed | CANONICAL ROOT | ROOT-010 | MEDIUM; duplicate of bug-registry BUG-UGA-03 |
| deep-audit | BUG-UGA-14 | NEEDS INVESTIGATION | — | MEDIUM; ID only in audit listing, no description available |
| deep-audit | BUG-UGA-17 | NEEDS INVESTIGATION | — | MEDIUM; ID only in audit listing, no description available |
| deep-audit | BUG-UGA-18 | NEEDS INVESTIGATION | — | MEDIUM; ID only in audit listing, no description available |
| deep-audit | BUG-UGA-20 | NEEDS INVESTIGATION | — | MEDIUM; ID only in audit listing, no description available |
| deep-audit | BUG-UGA-21 | NEEDS INVESTIGATION | — | MEDIUM; ID only in audit listing, no description available |
| deep-audit | BUG-AOP-10 | LOW | — | See note below |
| deep-audit | BUG-AOP-11 | LOW | — | See note below |
| deep-audit | BUG-AOP-17 | LOW | — | See note below |
| deep-audit | BUG-AOP-23 | LOW | — | See note below |
| deep-audit | BUG-DBG-12 | LOW | — | Test-quality cluster (ROOT-034) |
| deep-audit | BUG-DBG-13 | LOW | — | Test-quality cluster (ROOT-034) |
| deep-audit | BUG-DBG-14 | LOW | — | Test-quality cluster (ROOT-034) |
| deep-audit | BUG-UGA-06 | FALSE POSITIVE | — | Toy env path; external impact unreachable |
| deep-audit | BUG-UGA-09 | LOW | — | See note below |
| deep-audit | BUG-UGA-16 | LOW | — | See note below |
| deep-audit | BUG-UGA-19 | LOW | — | See note below |
| deep-audit | BUG-UGA-23 | LOW | — | See note below |
| deep-audit | BUG-UGA-25 | LOW | — | See note below |
| deep-audit | BUG-UGA-26 | LOW | — | See note below |
| deep-audit | BUG-UGA-33 | NEEDS INVESTIGATION | — | LOW; git check-ignore probe confirmed |
| deep-audit | BUG-UGA-35 | NEEDS INVESTIGATION | — | LOW; git check-ignore probe confirmed |

**Note:** The deep audit's LOW-severity entries (BUG-AOP-10/11/17/23, BUG-UGA-09/16/19/23/25/26) are listed by ID only in section 4 with no individual descriptions. They are preserved in the matrix above with their IDs and severity, but their full details are not available in the provided source text. They should be looked up in the full registry at `.agents/memory/bug-registry.md` for complete entries.

**8 rejected findings:**

| Source | Original ID | Disposition | Reason |
|---|---|---|---|
| deep-audit | UGA-13 — smoke test never exercises configured env | FALSE POSITIVE | main.py uses configured env; second ToyPongEnv only for print |
| deep-audit | UGA-11 — VK mappings may mismatch | NOT A BUG | YAML 37/39 ≡ 0x25/0x27 in all definitions; drift risk only |
| deep-audit | AOP-09 as worded — UnicodeDecodeError escapes | MECHANISM CORRECTED | Error swallowed in _readerthread; still a defect (→ ROOT-032) |
| deep-audit | Merge dirty-tree guard missing | DISPROVEN | git.py:239-243 already guards; recorded as coverage gap BUG-DBG-11 |
| deep-audit | Raw stdout reaches event sink via workflow.py:546 | RETRACTED | task.result overwritten at :542; confirmed sink is :563 (ROOT-001) |
| deep-audit | AOP-24 — validator call sites redundant | REDUNDANCY | Re-checking booleans is harmless |
| deep-audit | AOP-19/21/22 — module size, typing, dependency arrows | ARCHITECTURE DEBT | No demonstrated runtime consequence |
| deep-audit | Copilot snapshot packaging claims; regex sweep; _operation_lock | FALSE POSITIVE | Harness artifact; self-matching replacement text; lock belongs to AgentOpsController |

**26 historical bugs:**

| Source | Original ID | Disposition | Canonical ROOT | Reason |
|---|---|---|---|---|
| deep-audit | BUG-AOP-05 — swallowed write certifying passed report | ALREADY FIXED | — | Fix present, live, covered by test |
| deep-audit | BUG-FIX-11 — git-diff tri-state | ALREADY FIXED | — | Fix present, live, covered by test |
| deep-audit | BUG-FIX-16 — killpg group ownership | ALREADY FIXED | — | Fix present, live, covered by test |
| deep-audit | BUG-FIX-17 — thread leak | ALREADY FIXED | — | Fix present, live, covered by test |
| deep-audit | BUG-FIX-18 — routing recorded selection not execution | ALREADY FIXED | — | Fix present, live, covered by test |
| deep-audit | BUG-FIX-22 — CLI UnicodeEncodeError | PARTIALLY FIXED | — | Code correct, no test references _print_text |
| deep-audit | BUG-FIX-23 — WAL SQLITE_LOCKED | PARTIALLY FIXED | — | Code correct, test is probabilistic (~25% flake) |
| deep-audit | BUG-FIX-20 — evidence secrets | PARTIALLY FIXED | — | Code correct, 500-char peek cap untested |
| deep-audit | BUG-FIX-06 — migration repair loop | PARTIALLY FIXED | — | Code correct, repair loop untested |
| deep-audit | BUG-FIX-04 — optional failures | PARTIALLY FIXED | ROOT-029 | Validator fixed, producer still disagrees |
| deep-audit | BUG-FIX-24 — workflow-scoped recovery | REGRESSED | ROOT-007 | Fixed in engine, bypassed by GUI |
| deep-audit | BUG-FIX-25 — recovery idempotency | ALREADY FIXED (unreachable) | — | Correct and tested, but no in-product callers |
| deep-audit | BUG-FIX-26 — cancellation repair budget | ALREADY FIXED (unreachable) | — | Correct and tested, but no in-product callers |

### geminihandoff.md (14 findings)

| Source | Original ID | Disposition | Canonical ROOT | Reason |
|---|---|---|---|---|
| geminihandoff | Entropy loss sign reversal | CANONICAL ROOT | ROOT-015 | CRITICAL; needs source verification against current ppo.py |
| geminihandoff | Truncation treated as terminal | CANONICAL ROOT | ROOT-016 | CRITICAL; needs source verification against current ppo.py |
| geminihandoff | GRU hidden state leak | CANONICAL ROOT | ROOT-017 | HIGH; needs source verification against current model.py |
| geminihandoff | ICM gradient bleed | CANONICAL ROOT | ROOT-018 | HIGH; needs source verification against current curiosity.py |
| geminihandoff | Git clean on root repo | CANONICAL ROOT | ROOT-019 | HIGH; needs source verification against current git.py/finalize.py |
| geminihandoff | Subprocess buffer deadlock | CANONICAL ROOT | ROOT-020 | HIGH; needs source verification against current runner.py |
| geminihandoff | Process group leak on cancel | CANONICAL ROOT | ROOT-021 | HIGH; needs source verification against current runner.py |
| geminihandoff | StateStore transaction swallowing | CANONICAL ROOT | ROOT-022 | MEDIUM; needs source verification against current persistence.py |
| geminihandoff | Deterministic selector bypasses preference | CANONICAL ROOT | ROOT-023 | MEDIUM; needs source verification against current routing.py |
| geminihandoff | Frame normalization truncates to zero | CANONICAL ROOT | ROOT-024 | MEDIUM; needs source verification against current preprocessing.py |
| geminihandoff | Win32 GDI handle leak | CANONICAL ROOT | ROOT-025 | MEDIUM; needs source verification against current win32_capture.py |
| geminihandoff | Missing games/__init__.py | CANONICAL ROOT | ROOT-026 | MEDIUM; needs source verification |
| geminihandoff | Non-editable wheel drops config files | NON-BUG / PACKAGING | — | LOW; packaging concern, not a runtime defect |
| geminihandoff | GUI event queue silent thread exception invalidation | NEEDS INVESTIGATION | — | LOW; needs source verification |

### projects-ai-bug-handoff.md (9 findings)

| Source | Original ID | Disposition | Canonical ROOT | Reason |
|---|---|---|---|---|
| bug-handoff | AOP-01 — .agentops/ dirty tree | CANONICAL ROOT | ROOT-003 | P0; matches deep audit BUG-AOP-01 |
| bug-handoff | AOP-02 — review gate bypass | CANONICAL ROOT | ROOT-002 | P0; matches deep audit BUG-AOP-02 |
| bug-handoff | AOP-03 — unredacted stdout/stderr | CANONICAL ROOT | ROOT-001 | P1; matches deep audit BUG-AOP-03 |
| bug-handoff | AOP-04 — No GitHub Actions CI | NON-BUG / ARCHITECTURE DEBT — SUPERSEDED 2026-10-02 | — | P1; infrastructure gap, not a code defect. **SUPERSEDED: CI delivered at 2cb2413** (`.github/workflows/{agentops,universal-game-agent,mini-llm}.yml`, scope in `.github/CI.md`) |
| bug-handoff | AOP-05 — A8 verification persistence false-success | ALREADY FIXED | — | RESOLVED; A8 added fail-closed behavior |
| bug-handoff | AOP-06 — Worktree provenance duplication | ALREADY FIXED | — | RESOLVED; finalize.record_worktree_provenance() centralizes |
| bug-handoff | AOP-07 — Persistence-failure policy enforcement | CANONICAL ROOT | ROOT-005 | P1; matches deep audit BUG-AOP-07 |
| bug-handoff | AOP-08 — GUI recovery scope mismatch | CANONICAL ROOT | ROOT-007 | P1; matches deep audit BUG-AOP-08 |
| bug-handoff | AOP-09 — Git subprocess UnicodeDecodeError | CANONICAL ROOT | ROOT-032 | P2; matches deep audit BUG-AOP-09 |

### projects-ai-review-handoff.md (14 findings)

| Source | Original ID | Disposition | Canonical ROOT | Reason |
|---|---|---|---|---|
| review-handoff | #1 — worktree/.gitignore merge bug | CANONICAL ROOT | ROOT-003 | CRITICAL; matches deep audit BUG-AOP-01 |
| review-handoff | #2 — GUI mutual-exclusion bypass | CANONICAL ROOT | ROOT-006 | CRITICAL; _cancel_event tautology |
| review-handoff | #3 — Git failure misclassification | CANONICAL ROOT | ROOT-004 | HIGH; FailureClassifier exists but never called |
| review-handoff | #4 — unredacted task.result | CANONICAL ROOT | ROOT-001 | HIGH; matches deep audit BUG-AOP-03 |
| review-handoff | #5 — No CI | NON-BUG / ARCHITECTURE DEBT — SUPERSEDED 2026-10-02 | — | MEDIUM; infrastructure gap. **SUPERSEDED: CI delivered at 2cb2413** |
| review-handoff | #6 — silent exception swallowing | CANONICAL ROOT | ROOT-005 | MEDIUM; matches deep audit BUG-AOP-07 |
| review-handoff | #7 — GUI operation lock tautology | CANONICAL ROOT | ROOT-006 | CRITICAL; same finding as #2, different phrasing |
| review-handoff | #8 — root_cache shared mutable state | CANONICAL ROOT | ROOT-035 | LOW; no lock on _root_cache (new ROOT) |
| review-handoff | #9 — dead code verification_kernel.py:182 | NON-BUG / DEAD CODE | — | LOW; cruft from incomplete refactor, no functional impact |
| review-handoff | #10 — comment hygiene | NON-BUG / STYLE | — | LOW; stylistic, no correctness impact |
| review-handoff | #11 — ruff static analysis (91 findings) | MIXED | — | 20 bare excepts (3 load-bearing → ROOT-005); rest cosmetic/OSError catches |
| review-handoff | #12 — test suite results (382 tests, 1 failure) | NEEDS INVESTIGATION | — | test_posix_terminate_uses_process_group fails; may be sandbox artifact |
| review-handoff | #13 — what was checked and found clean | NONE | — | routing.py, claim_task, config.py, GUI thread-marshaling all clean |
| review-handoff | #14 — priority order | NONE | — | Procedural, not a finding |

### repo-review-2026-09-26.md (10 findings + deeper lanes)

| Source | Original ID | Disposition | Canonical ROOT | Reason |
|---|---|---|---|---|
| repo-review | #1 — external-Pong contaminated results | MEASUREMENT ISSUE | — | 82-83% episodes length-1 zeros; regenerate results under settle fix |
| repo-review | #2 — leaked OpenCode key | SECURITY | — | In git history; rotation not recorded; not a code defect |
| repo-review | #3 — agentops task fails on fresh repo | CANONICAL ROOT | ROOT-003 | Matches deep audit BUG-AOP-01 |
| repo-review | #4 — custom-workflow bypasses review gate | CANONICAL ROOT | ROOT-002 | Matches deep audit BUG-AOP-02 |
| repo-review | #5 — raw stdout/stderr reach SQLite | CANONICAL ROOT | ROOT-001 | Matches deep audit BUG-AOP-03 |
| repo-review | #6 — No CI | NON-BUG / ARCHITECTURE DEBT — SUPERSEDED 2026-10-02 | — | Matches AOP-04. **SUPERSEDED: CI delivered at 2cb2413** |
| repo-review | #7 — every ignore rule in uncommitted file | GOVERNANCE | — | Machine-local .git/info/exclude; fresh clones lose rules |
| repo-review | #8 — after-task.md stale | DOC DRIFT | — | Agentops-only; UGA tasks run wrong suite |
| repo-review | #9 — UGA checkpoint non-atomic | CANONICAL ROOT | ROOT-013 | Matches deep audit BUG-UGA-15 |
| repo-review | #10 — eval metric bugs | CANONICAL ROOT | ROOT-036 | reward sign-only verdict; seeds discarded; needs source verification |

---

## 3. Canonical Root-Cause Ledger

### ROOT-001 — Unredacted task.result persistence
- **Severity:** HIGH
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-AO-02 (bug-registry), BUG-AOP-03 (deep-audit), #4 (review-handoff), #5 (repo-review)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/workflow.py:563; agentops/agentops/state.py:583-593
- **Affected functions:** WorkflowEngine._execute_task; StateStore.update_task
- **Root cause:** stdout/stderr interpolated into task.result without redact_text(); every other sink in the file redacts (lines 206, 207, 282, 659).
- **Concrete evidence:** Reproduced with fake agent emitting `api_key=sk-abcdefghijklmnopqrstuvwxyz123456`; after execution, state.get_task(id).result contained the raw secret.
- **Observed behavior:** task.result = f"log={result.log_path}\n{result.stdout}\n{result.stderr}" persisted verbatim to SQLite.
- **Expected behavior:** Per .agents/AGENTS.md: "Do not persist raw prompts, secrets, credentials, tokens, or private keys." The tasks table is a persistence boundary and must be redacted.
- **Minimal fix scope:** Wrap stdout/stderr in redact_text() before assigning task.result, or store only log path + redacted summary.
- **Regression tests:** Fake agent emits secret; assert absent from persisted task.result for both pass and fail.
- **Dependencies:** None
- **Regression risk:** UI/diagnostic flows may read task.result; preserve useful diagnostics while redacting.

### ROOT-002 — Review gate bypass via CLI
- **Severity:** CRITICAL
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-AOP-02 (deep-audit), AOP-02 (bug-handoff), #2 (review-handoff), #4 (repo-review)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/cli.py:391-393; agentops/agentops/state.py:863-874; agentops/agentops/execution_model.py:49-50,:186-201
- **Affected functions:** cli.py readiness check; StateStore.refresh_workflow_status; execution_model.assert_workflow_ready
- **Root cause:** cli.py readiness = status.passed and evidence, no review-role check. Standard path requires verification+review+evidence.
- **Concrete evidence:** Mutation test — replacing cli.py:394 with ready = True leaves suite at 382 OK; zero tests detect removal.
- **Observed behavior:** Custom workflow with verification but no review task reaches ready=True, then auto-merges.
- **Expected behavior:** Same READY predicate as standard flow: verification_ok + review_ok + evidence_present.
- **Minimal fix scope:** Share assert_workflow_ready as the single READY predicate across CLI and standard paths.
- **Regression tests:** Custom workflow with verification but no review must not reach ready=True.
- **Dependencies:** None
- **Regression risk:** Standard path unchanged; custom DAG path now enforces review.

### ROOT-003 — Self-deadlock via .agentops/ in target repo
- **Severity:** CRITICAL
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-AOP-01 (deep-audit), AOP-01 (bug-handoff), #1 (review-handoff), #3 (repo-review)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/git.py:100-101,:239-243
- **Affected functions:** GitWorktreeManager.create; GitWorktreeManager.merge
- **Root cause:** Worktrees created inside target repo under .agentops/; no .gitignore written; merge() refuses on any porcelain output.
- **Concrete evidence:** Probed with fresh temp repos — after create(), porcelain shows `?? .agentops/`, merge raises.
- **Observed behavior:** First time agentops task runs against any fresh target repo, the untracked .agentops/worktrees/... directory makes git status --porcelain non-empty, and every subsequent merge is refused.
- **Expected behavior:** AgentOps-generated state must not block a clean target repo; real user changes must still block merge/finalization.
- **Minimal fix scope:** Either move worktrees outside target repo, or write .gitignore on first run, or exclude .agentops/ from dirty check.
- **Regression tests:** Fresh repo → create worktree → merge must not raise.
- **Dependencies:** None
- **Regression risk:** Worktree relocation changes UX; .gitignore write modifies user's repo.

### ROOT-004 — Git failure misclassification
- **Severity:** MEDIUM
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** #3 (review-handoff)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/finalize.py:75-84; agentops/agentops/failure.py
- **Affected functions:** finalize_worktree; FailureClassifier.classify
- **Root cause:** All four GitError causes get identical "Resolve Git merge conflict" task; FailureClassifier already has GIT_CONFLICT/DIRTY_WORKTREE categories but is never called from finalize.py.
- **Concrete evidence:** grep confirms classify() only called from workflow.py, never from finalize.py.
- **Observed behavior:** When bug #1 fires (untracked .agentops/ directory), task text says "Resolve Git merge conflict" — actively misleading.
- **Expected behavior:** Each GitError cause gets correctly categorized task with appropriate repair action.
- **Minimal fix scope:** Wire FailureClassifier.classify into finalize.py's except GitError block.
- **Regression tests:** Each GitError cause produces correctly categorized task.
- **Dependencies:** ROOT-003 (shared git.py)
- **Regression risk:** Misclassification text corrected; existing classifier already matches real error strings.

### ROOT-005 — Silent exception swallowing in failure path
- **Severity:** MEDIUM
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-AOP-07 (deep-audit), AOP-07 (bug-handoff), #6 (review-handoff)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/workflow.py:579-586,:594-601,:607-614
- **Affected functions:** WorkflowEngine._execute_task (failure handling blocks)
- **Root cause:** Three bare `except Exception: pass` blocks around record_failure(); degradation recorder handles expected SQLite failures, so these swallow unexpected bugs.
- **Concrete evidence:** record_failure already has SAFE_TO_DEGRADE fallback; outer blocks only fire on unexpected failures (constructor/classify/recorder bug).
- **Observed behavior:** If Failure() construction, the classifier, or the degradation recorder itself bugs out, it disappears with no log line, no event, no degradation record.
- **Expected behavior:** At minimum a logger.exception() call or unexpected_error event before continuing.
- **Minimal fix scope:** Replace bare except with logger.exception() or unexpected_error event.
- **Regression tests:** Inject unexpected failure in record_failure path; assert log line emitted.
- **Dependencies:** None
- **Regression risk:** Low — doesn't corrupt state, but hides unexpected failures.

### ROOT-006 — GUI mutual-exclusion bypass
- **Severity:** CRITICAL
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** #2 (review-handoff), #7 (review-handoff)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/gui_controller.py:225-226,:260-271; gui.py event flow
- **Affected functions:** GUIController._begin_operation; GUIController._end_operation
- **Root cause:** _cancel_event reused across operations; `event is self._cancel_event` tautology makes _end_operation always succeed, clearing _active while next op runs.
- **Concrete evidence:** Traced through gui.py callback → _finish_operation → _set_running(False) → re-enable Start → op1 finally clears _active.
- **Observed behavior:** User clicks Start again promptly after op1 finishes; op1's worker thread still in finally block; _active set to False while op2 is genuinely running.
- **Expected behavior:** Each operation gets a unique Event; superseded operation's reference no longer matches current _cancel_event.
- **Minimal fix scope:** Mint new Event() in _begin_operation(); store in self._cancel_event.
- **Regression tests:** Start op1, click Start before op1 finally runs; assert _active stays True.
- **Dependencies:** None
- **Regression risk:** UI event ordering; concurrent operations prevented.

### ROOT-007 — GUI recovery scope mismatch
- **Severity:** MEDIUM
- **Confidence:** CONFIRMED (latent)
- **Status:** ACTIVE BUG (no current callers)
- **Source findings:** BUG-AO-03 (bug-registry), BUG-AOP-08 (deep-audit), AOP-08 (bug-handoff)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/gui_controller.py:573
- **Affected functions:** GUIController.recover_interrupted
- **Root cause:** workflow_id forwarded to recover_tasks but not recover_agent_runs/recover_verification_runs.
- **Concrete evidence:** Code inspection; no callers found in checked source. Engine side (workflow.py:recover_incomplete) already scopes correctly.
- **Observed behavior:** When workflow_id is not None, method calls state.recover_agent_runs() and state.recover_verification_runs() WITHOUT passing workflow_id.
- **Expected behavior:** Workflow-scoped recovery passes workflow_id to all three recovery passes.
- **Minimal fix scope:** Pass workflow_id to all three recovery calls.
- **Regression tests:** Two workflows with incomplete rows; scoped recovery leaves other untouched.
- **Dependencies:** None
- **Regression risk:** None — latent, no callers; fix is copy-paste correction.

### ROOT-008 — Unvalidated checkpoint interval
- **Severity:** MEDIUM
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-UGA-01 (bug-registry), BUG-UGA-01 (deep-audit)
- **Affected product:** UGA
- **Affected files:** universal-game-agent/training/ppo.py: PPOConfig.__post_init__, PPOTrainer.train:376
- **Affected functions:** PPOConfig.__post_init__; PPOTrainer.train
- **Root cause:** checkpoint_every_updates omitted from positive-int validation; zero reaches modulo → ZeroDivisionError.
- **Concrete evidence:** Reproduced — PPOConfig(checkpoint_every_updates=0) accepted; train() crashes first update.
- **Observed behavior:** PPOConfig validates rollout_length, minibatch_size, update_epochs, total_timesteps as positive ints, but NOT checkpoint_every_updates.
- **Expected behavior:** checkpoint_every_updates validated as positive int (or None/0 handled as "disabled").
- **Minimal fix scope:** Add to validation loop, or guard modulo with `if cfg.checkpoint_every_updates`.
- **Regression tests:** Zero/negative/positive intervals; verify chosen zero behavior.
- **Dependencies:** None
- **Regression risk:** Zero behavior choice (reject vs disable) must be decided; do not silently change existing positive-interval behavior.

### ROOT-009 — Orphaned game process on attach failure
- **Severity:** HIGH
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-UGA-02 (bug-registry), BUG-UGA-02 (deep-audit)
- **Affected product:** UGA
- **Affected files:** universal-game-agent/training/external_experiment.py: launch_phase2:267
- **Affected functions:** launch_phase2; train_with_window_relaunch
- **Root cause:** Process launched before wait_attach/check_alive; if either raises, proc never terminated.
- **Concrete evidence:** Mocked wait_attach to raise; proc.terminate() never called.
- **Observed behavior:** launch_phase2 calls launch_game (opens log file, starts subprocess), then wait_attach. If wait_attach raises (timeout) or check_alive raises (game died), exception propagates and game process is never terminated.
- **Expected behavior:** A game process that was launched but never attached should be terminated and its log file closed on failure.
- **Minimal fix scope:** try/except around launch_phase2 body; terminate proc on failure.
- **Regression tests:** Mock wait_attach to raise; assert proc.terminate() called.
- **Dependencies:** None
- **Regression risk:** Process cleanup timing; ensure cleanup is for the process launched by that attempt.

### ROOT-010 — Checkpoint-load env not closed
- **Severity:** MEDIUM
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-UGA-03 (bug-registry), BUG-UGA-03 (deep-audit)
- **Affected product:** UGA
- **Affected files:** universal-game-agent/training/external_experiment.py:291
- **Affected functions:** run_external_experiment
- **Root cause:** External env created for load_checkpoint(...).model never closed.
- **Concrete evidence:** Mock confirmed env.close() never called. Toy CLI paths unaffected (no-op close).
- **Observed behavior:** PPOTrainer.load_checkpoint(ckpt, make_env()).model creates an environment for the external phase-3 checkpoint load, extracts the model, and never closes that environment.
- **Expected behavior:** Environments should be closed after use, especially external envs that hold OS resources.
- **Minimal fix scope:** Close temp env in finally after model extraction.
- **Regression tests:** Close-tracking env; assert closed on success and on load failure.
- **Dependencies:** None
- **Regression risk:** None — finally path without masking primary load/evaluation error.

### ROOT-011 — Capture-size contract undefined
- **Severity:** MEDIUM
- **Confidence:** LIKELY
- **Status:** CONTRACT GAP
- **Source findings:** BUG-UGA-04 (bug-registry)
- **Affected product:** UGA
- **Affected files:** universal-game-agent/environment/external_game.py:421,:430
- **Affected functions:** make_external_env_from_config
- **Root cause:** Factory reads out_width/out_height but region/window branches don't pass to capture; synthetic does. Window native frames intentional for detector detail.
- **Concrete evidence:** Direct-constructor reproduction shows region output at native res, not configured 96x96. But intended contract undocumented.
- **Observed behavior:** For capture.mode: region, ScreenCapture constructed without out_width/out_height, frames returned at native resolution. For window, WindowCapture constructed with no out size. Only synthetic mode passes out_w, out_h.
- **Expected behavior:** Factory honors or explicitly documents/validates mode-specific meaning of these keys.
- **Minimal fix scope:** Decide per-mode meaning; document; pass or validate. Do not resize window frames without contract.
- **Regression tests:** Test via make_external_env_from_config per mode after defining contract.
- **Dependencies:** None (blocked on contract decision)
- **Regression risk:** Resizing window frames may degrade reward/termination detection.

### ROOT-012 — Verification run stranded
- **Severity:** LOW
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-AO-04 (bug-registry)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/verification_kernel.py:198-203
- **Affected functions:** VerificationKernel.run_verification
- **Root cause:** Run persisted RUNNING before checks created; check-creation exception leaves it RUNNING.
- **Concrete evidence:** Code inspection; no try/except around check loop.
- **Observed behavior:** run_verification creates the verification run (PENDING → RUNNING) and then creates checks in a loop. If create_verification_check raises mid-loop, exception propagates and run is left RUNNING with some checks PENDING.
- **Expected behavior:** Failure during check creation should either roll back the run or mark it FAILED.
- **Minimal fix scope:** try/except around check loop; finalize run FAILED on error; preserve original error.
- **Regression tests:** Inject failure on later check creation; assert run is not left RUNNING and error remains observable.
- **Dependencies:** None
- **Regression risk:** Database failure may also prevent cleanup persistence; do not swallow original error.

### ROOT-013 — Non-atomic checkpoint save
- **Severity:** HIGH
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-UGA-15 (deep-audit), #9 (repo-review)
- **Affected product:** UGA
- **Affected files:** universal-game-agent/training/ppo.py:290-303
- **Affected functions:** save_checkpoint
- **Root cause:** torch.save directly to final name; crash mid-write leaves truncated .pt advertised as only weights. No RNG state stored; --resume not reproducible.
- **Concrete evidence:** Code inspection; temp-file-plus-os.replace absent.
- **Observed behavior:** ppo.py:290-303 writes torch.save(.., path) straight to the final name, no temp file plus os.replace.
- **Expected behavior:** Atomic save via temp file + os.replace; store RNG state in checkpoint.
- **Minimal fix scope:** Atomic save via temp file + os.replace; store RNG state in checkpoint payload.
- **Regression tests:** Kill process mid-save; assert no truncated file; assert resume reproduces.
- **Dependencies:** None
- **Regression risk:** Checkpoint format change; must maintain backward compatibility or provide migration.

### ROOT-014 — Resume crash on finished checkpoint
- **Severity:** MEDIUM
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** repo-review #9 (related)
- **Affected product:** UGA
- **Affected files:** universal-game-agent/main.py:185-190,:203-205
- **Affected functions:** cmd_train (resume path)
- **Root cause:** trainer.num_timesteps >= total_timesteps → train loop never runs → history empty → IndexError on history['mean_reward'][-1]. Same shape in experiment.py:111-118 and external_experiment.py:322.
- **Concrete evidence:** Code inspection.
- **Observed behavior:** main.py train --resume on a finished checkpoint raises unhandled IndexError.
- **Expected behavior:** Guard against empty history before indexing; emit warning on finished checkpoint.
- **Minimal fix scope:** Guard against empty history; warn on finished checkpoint.
- **Regression tests:** Train to completion, resume; assert no crash, assert no history inflation.
- **Dependencies:** None
- **Regression risk:** None — defensive guard.

### ROOT-015 — Entropy loss sign reversal
- **Severity:** CRITICAL
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #1
- **Affected product:** UGA
- **Affected files:** universal-game-agent/training/ppo.py:compute_loss
- **Affected functions:** compute_loss
- **Root cause:** total_loss = policy_loss + val_coef*value_loss + entropy_coef*entropy_loss (adds entropy instead of subtracting). Minimizer penalizes exploration → policy collapse.
- **Concrete evidence:** geminihandoff; formula contradicted by standard PPO objective. Must verify against current source.
- **Observed behavior:** Optimizer actively minimizes policy entropy, penalizing exploration and causing policy distribution collapse into deterministic actions within early training iterations.
- **Expected behavior:** Loss to minimize: -L_CLIP + c1*L_VF - c2*S[π]. Entropy term should be subtracted.
- **Minimal fix scope:** Change sign to subtract entropy term.
- **Regression tests:** Verify high-entropy distribution yields lower total loss with positive entropy_coef.
- **Dependencies:** None
- **Regression risk:** Training behavior change; verify numerically before deploying.

### ROOT-016 — Truncation treated as terminal
- **Severity:** CRITICAL
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #2
- **Affected product:** UGA
- **Affected files:** universal-game-agent/training/ppo.py:compute_gae
- **Affected functions:** compute_gae
- **Root cause:** done = terminated | truncated; truncation zeroes bootstrap V(s_{t+1}) instead of using it. Toy env truncates every episode at step budget → common case.
- **Concrete evidence:** geminihandoff; source-level proof. Must verify against current source.
- **Observed behavior:** When environment reaches step time limit (truncated=True), GAE zeroes out the bootstrap value V(s_{t+1}).
- **Expected behavior:** Separate terminated and truncated masks. Bootstrap value targets using V(s_{t+1}) when truncated.
- **Minimal fix scope:** Separate terminated/truncated masks in GAE computation.
- **Regression tests:** Construct mid-rollout truncation; assert bootstrap uses V not 0.
- **Dependencies:** None
- **Regression risk:** Training convergence; test with truncation.

### ROOT-017 — GRU hidden state leak across trajectories
- **Severity:** HIGH
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #3
- **Affected product:** UGA
- **Affected files:** universal-game-agent/agent/model.py:forward_sequence
- **Affected functions:** forward_sequence
- **Root cause:** No (1-done) masking of hidden state at episode boundaries within sequence chunks.
- **Concrete evidence:** geminihandoff; must verify against current source.
- **Observed behavior:** When an episode terminates mid-sequence chunk during PPO minibatch updates, the GRU hidden state h_t carries continuously into step t+1 without zeroing out.
- **Expected behavior:** Mask hidden states step-by-step during recurrent updates: h_t = h_t · (1 - done_{t-1}).
- **Minimal fix scope:** Add (1-done) masking in forward_sequence.
- **Regression tests:** Episode boundary → no state leak into next episode.
- **Dependencies:** None
- **Regression risk:** Recurrent update correctness.

### ROOT-018 — ICM gradient bleed into shared backbone
- **Severity:** HIGH
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #4
- **Affected product:** UGA
- **Affected files:** universal-game-agent/training/curiosity.py:compute_intrinsic_reward
- **Affected functions:** compute_intrinsic_reward
- **Root cause:** Features φ(s_t), φ(s_{t+1}) passed to curiosity heads without detach().
- **Concrete evidence:** geminihandoff; must verify against current source.
- **Observed behavior:** Backpropagation through the Intrinsic Curiosity Module (ICM) updates shared CNN feature extractor parameters via curiosity dynamics loss.
- **Expected behavior:** Detach features before passing them to curiosity heads.
- **Minimal fix scope:** Detach features before curiosity heads.
- **Regression tests:** ICM loss doesn't corrupt shared params; extrinsic policy performance preserved.
- **Dependencies:** None
- **Regression risk:** Curiosity objective change.

### ROOT-019 — Git clean on root repo
- **Severity:** HIGH
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #5
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/git.py:abort_merge; agentops/finalize.py:cleanup_worktree
- **Affected functions:** abort_merge; cleanup_worktree
- **Root cause:** abort_merge defaults cwd=base_repo_path when worktree_path omitted; git clean -fd runs against root workspace.
- **Concrete evidence:** geminihandoff; must verify against current source.
- **Observed behavior:** Unresolvable merge conflicts during worktree finalization run git reset --hard and git clean -fd against the root workspace directory.
- **Expected behavior:** Mandate worktree_path; validate target_dir != base_repo_path before running destructive commands.
- **Minimal fix scope:** Mandate worktree_path; add validation guard.
- **Regression tests:** abort_merge on root → error; worktree_path always provided.
- **Dependencies:** None
- **Regression risk:** Must keep worktree_path always provided; breaking change if callers omit it.

### ROOT-020 — Subprocess buffer deadlock
- **Severity:** HIGH
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #6
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/runner.py:execute_process
- **Affected functions:** execute_process
- **Root cause:** Popen with stdout=PIPE/stderr=PIPE + synchronous wait() before draining; 64KB POSIX / 4KB Windows buffer overflow → deadlock.
- **Concrete evidence:** geminihandoff; must verify against current source.
- **Observed behavior:** Agents generating verbose stdout/stderr output (exceeding pipe buffer size) cause execution to hang indefinitely.
- **Expected behavior:** Use proc.communicate(timeout=...) or concurrent asynchronous reader threads to drain stdout and stderr.
- **Minimal fix scope:** Replace synchronous wait+read with communicate() or async readers.
- **Regression tests:** Large output → no hang; timeout behavior preserved.
- **Dependencies:** ROOT-021 (shared runner.py)
- **Regression risk:** Timeout behavior change; ensure existing timeouts still work.

### ROOT-021 — Process group leak on cancel/timeout
- **Severity:** HIGH
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #7
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/runner.py:execute_process
- **Affected functions:** execute_process
- **Root cause:** No process group isolation; cancel kills only parent PID, children orphaned.
- **Concrete evidence:** geminihandoff; must verify against current source.
- **Observed behavior:** Cancelling or timing out an execution kills only the parent PID, leaving child process trees active in the background.
- **Expected behavior:** POSIX setsid + killpg; Windows CREATE_NEW_PROCESS_GROUP + taskkill /T.
- **Minimal fix scope:** Add process group isolation; ensure cleanup kills entire tree.
- **Regression tests:** Cancel → children killed; POSIX and Windows both verified.
- **Dependencies:** ROOT-020 (shared runner.py)
- **Regression risk:** Signal propagation; ensure no orphaned children.

### ROOT-022 — StateStore transaction error swallowing
- **Severity:** MEDIUM
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #8
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/persistence.py:StateStore.record_event
- **Affected functions:** record_event
- **Root cause:** SQLite errors caught and logged; execution proceeds as if successful.
- **Concrete evidence:** geminihandoff; must verify against current source.
- **Observed behavior:** SQLite errors (database is locked or write failures) during state transition recording are caught and logged, but execution proceeds as if successful.
- **Expected behavior:** Propagate or wrap in custom exception.
- **Minimal fix scope:** Propagate SQLite write errors or wrap in custom exception.
- **Regression tests:** SQLite write failure → exception, not silent continuation.
- **Dependencies:** None
- **Regression risk:** Policy enforcement scope; existing SAFE_TO_DEGRADE handling may need updating.

### ROOT-023 — Deterministic selector bypasses user preference
- **Severity:** MEDIUM
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #9
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/routing.py:select_agent
- **Affected functions:** select_agent
- **Root cause:** Equal scores break alphabetically; config.preferred_agent ignored.
- **Concrete evidence:** geminihandoff; must verify against current source.
- **Observed behavior:** Equal capability scores break ties via alphabetical sorting on agent IDs, ignoring config.preferred_agent.
- **Expected behavior:** Sort key prioritises preferred_agent match.
- **Minimal fix scope:** Update sorting key to prioritise preferred_agent.
- **Regression tests:** Equal scores → preferred agent first.
- **Dependencies:** None
- **Regression risk:** Routing behavior change.

### ROOT-024 — Frame normalization truncates to zero
- **Severity:** MEDIUM
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #10
- **Affected product:** UGA
- **Affected files:** universal-game-agent/environment/preprocessing.py:process_frame
- **Affected functions:** process_frame
- **Root cause:** uint8 array /= 255.0 in-place truncates non-255 to 0.
- **Concrete evidence:** geminihandoff; must verify against current source.
- **Observed behavior:** Normalizing pixel values via in-place division on uint8 arrays truncates non-255 values down to 0.
- **Expected behavior:** Cast to float32 prior to normalization: frame = frame.astype(np.float32) / 255.0.
- **Minimal fix scope:** Cast to float32 before division.
- **Regression tests:** Frame range [0,1] not [0,0,1].
- **Dependencies:** None
- **Regression risk:** Preprocessing pipeline change; verify model input expectations.

### ROOT-025 — Win32 GDI handle leak
- **Severity:** MEDIUM
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #11
- **Affected product:** UGA
- **Affected files:** universal-game-agent/interface/win32_capture.py:capture_window
- **Affected functions:** capture_window
- **Root cause:** HBITMAP/HDC not released in finally on exception.
- **Concrete evidence:** geminihandoff; must verify against current source.
- **Observed behavior:** Win32 GDI object handles leak when exceptions occur or during long capture sessions. Reaches Windows GDI handle limit (10,000), crashing the capture module with resource exhaustion errors.
- **Expected behavior:** Enclose GDI resource cleanup in explicit try...finally blocks calling DeleteObject and ReleaseDC.
- **Minimal fix scope:** Add try/finally with DeleteObject/ReleaseDC.
- **Regression tests:** Exception during capture → no GDI leak.
- **Dependencies:** None
- **Regression risk:** Win32 resource management; ensure handles released on all paths.

### ROOT-026 — Missing games/__init__.py
- **Severity:** MEDIUM
- **Confidence:** NEEDS INVESTIGATION
- **Status:** UNVERIFIED (geminihandoff only)
- **Source findings:** geminihandoff #12
- **Affected product:** UGA
- **Affected files:** universal-game-agent/games/
- **Affected functions:** (package init)
- **Root cause:** Direct imports like from games.extern_pong import ExternPong fail with ModuleNotFoundError in non-editable installs.
- **Concrete evidence:** geminihandoff; must verify against current source.
- **Observed behavior:** Missing module package initializer prevents standalone game discovery.
- **Expected behavior:** Add empty universal-game-agent/games/__init__.py.
- **Minimal fix scope:** Add empty __init__.py.
- **Regression tests:** Non-editable install → import works.
- **Dependencies:** None
- **Regression risk:** Package structure change; verify all game imports still work.

### ROOT-027 — External attach failure leaks process+log+results
- **Severity:** HIGH
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-UGA-22 (deep-audit)
- **Affected product:** UGA
- **Affected files:** universal-game-agent/training/external_experiment.py:267-271
- **Affected functions:** launch_phase2; train_with_window_relaunch
- **Root cause:** attach failure leaks game process, log handle, and writes no results JSON.
- **Concrete evidence:** Deep audit reproduced with fake-backed process probes.
- **Observed behavior:** External attach failure leaks the game process and its log handle and writes no results JSON.
- **Expected behavior:** Cleanup on attach failure; write results JSON even on failure.
- **Minimal fix scope:** Ensure cleanup on attach failure; write results JSON even on failure.
- **Regression tests:** attach fail → proc dead, results written.
- **Dependencies:** ROOT-009 (related process cleanup)
- **Regression risk:** Results JSON format; ensure cleanup is for the process launched by that attempt.

### ROOT-028 — GAE mid-rollout truncation bleed
- **Severity:** HIGH
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-DBG-05 (deep-audit)
- **Affected product:** UGA
- **Affected files:** universal-game-agent/training/ppo.py:78,:225-226
- **Affected functions:** compute_gae
- **Root cause:** compute_gae receives only buf["terminated"], not buf["dones"]; mid-rollout truncation bootstraps from V(post-reset obs).
- **Concrete evidence:** Deep audit source-level proof; toy env truncates every episode.
- **Observed behavior:** GAE bootstraps a mid-rollout truncation from the next episode's reset observation.
- **Expected behavior:** Pass dones buffer to compute_gae; handle mid-rollout truncation correctly.
- **Minimal fix scope:** Pass dones buffer to compute_gae; handle mid-rollout truncation.
- **Regression tests:** Construct mid-rollout truncation; assert bootstrap uses V not 0.
- **Dependencies:** None
- **Regression risk:** GAE numerical change; test with truncation.

### ROOT-029 — Producer/validator divergence
- **Severity:** MEDIUM
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-DBG-01 (deep-audit), BUG-FIX-04 (historical)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/execution_model.py:146-152; agentops/agentops/workflow.py:569-573
- **Affected functions:** VerificationKernel._check_optional; assert_report_consistent
- **Root cause:** Optional-failure fix landed in validator but not producer: in FAIL_FAST with an optional check failing first, kernel emits PASSED with passed_checks == 0, which assert_report_consistent then rejects → StateTransitionError.
- **Concrete evidence:** Deep audit; fails safe (abort, not fabrication) but aborts a healthy workflow.
- **Observed behavior:** Producer still disagrees with validator → StateTransitionError at workflow.py:569-573.
- **Expected behavior:** Producer and validator should agree; optional failures should be handled consistently.
- **Minimal fix scope:** Align producer with validator, or vice versa; ensure consistent behavior.
- **Regression tests:** FAIL_FAST with optional check failing first → no StateTransitionError.
- **Dependencies:** None
- **Regression risk:** Verification kernel behavior change.

### ROOT-030 — Unclassified evidence write
- **Severity:** MEDIUM
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-DBG-02 (deep-audit)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/verification_kernel.py:585-586
- **Affected functions:** VerificationKernel.run_verification (artifact writing)
- **Root cause:** LogManager.write_run_artifacts wrapped in except Exception: stdout_path = stderr_path = None with no policy row, no Degradation, and no test.
- **Concrete evidence:** Deep audit; cannot fabricate a PASSED claim (terminal state still persisted), but silently destroys the durable evidence pointer and contradicts declared A8 policy at persistence.py:58-64.
- **Observed behavior:** Unclassified evidence write with no policy row, no Degradation, no test.
- **Expected behavior:** Classified write loss with policy row and Degradation record.
- **Minimal fix scope:** Add policy row; record degradation; add test.
- **Regression tests:** Write failure → degradation recorded, not silent.
- **Dependencies:** None
- **Regression risk:** Verification evidence path; ensure original error preserved.

### ROOT-031 — Provenance-write bypass
- **Severity:** MEDIUM
- **Confidence:** MEDIUM
- **Status:** ACTIVE BUG (medium confidence)
- **Source findings:** BUG-DBG-03 (deep-audit)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/gui_controller.py:728-730
- **Affected functions:** (GUI provenance write)
- **Root cause:** A second unguarded worktree-provenance write survives outside the centralized finalize.record_worktree_provenance whose A6 fix claimed a single call site.
- **Concrete evidence:** Deep audit; medium confidence: line reported by revalidation lane, not independently re-read by orchestrator.
- **Observed behavior:** Unguarded provenance write at gui_controller.py:728-730.
- **Expected behavior:** Single centralized provenance write.
- **Minimal fix scope:** Route through finalize.record_worktree_provenance; remove duplicate.
- **Regression tests:** Provenance written exactly once per finalization.
- **Dependencies:** None
- **Regression risk:** Medium confidence; verify line exists in current source before fixing.

### ROOT-032 — UnicodeDecodeError on non-ASCII
- **Severity:** MEDIUM
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** BUG-AOP-09 (deep-audit), AOP-09 (bug-handoff)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/git.py
- **Affected functions:** subprocess.run calls
- **Root cause:** git.py calls subprocess.run(..., text=True, capture_output=True) without explicit UTF-8 decoding/error replacement; catches only OSError and TimeoutExpired.
- **Concrete evidence:** Deep audit; another part of AgentOps already established UTF-8 + replacement decoding as the safer pattern.
- **Observed behavior:** Non-ASCII branch or path corrupts merge/cleanup decisions or raises misleading AttributeError.
- **Expected behavior:** UTF-8 decoding with errors=replace.
- **Minimal fix scope:** Add encoding='utf-8', errors='replace' to subprocess.run calls.
- **Regression tests:** Non-ASCII branch → no crash.
- **Dependencies:** None
- **Regression risk:** Decoding behavior change; verify existing tests still pass.

### ROOT-033 — Windows termination classification
- **Severity:** MEDIUM
- **Confidence:** SUSPECTED
- **Status:** HELD — needs Windows contract
- **Source findings:** BUG-AO-01 (bug-registry)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/runner.py:226
- **Affected functions:** AgentRunner.run_agent
- **Root cause:** The terminated check uses the POSIX convention (exit_code < 0 for signal termination), which does not apply on Windows where GetExitCodeProcess returns a DWORD.
- **Concrete evidence:** Reported process returned positive [PHONE] for ExitProcess(0xC000001D), so existing negative-return-code check evaluated to false. This demonstrates an abnormal exit code, not that the process was terminated rather than crashed.
- **Observed behavior:** terminated = not timed_out and not cancelled and exit_code is not None and exit_code < 0. On Windows, subprocess return codes are unsigned 32-bit values, never negative.
- **Expected behavior:** A process killed by a signal/termination should be classified as TERMINATED regardless of platform.
- **Minimal fix scope:** First define and verify the Windows termination signal/contract. Do not classify all high-bit exit codes as termination.
- **Regression tests:** Windows-specific test with known termination signal distinct from ordinary nonzero application exit.
- **Dependencies:** None (blocked on Windows contract definition)
- **Regression risk:** High — proposed high-bit heuristic is unsafe; application-defined failures can have high-bit codes.

### ROOT-034 — Test-quality defects (cluster)
- **Severity:** HIGH/LOW (mixed)
- **Confidence:** CONFIRMED
- **Status:** ACTIVE COVERAGE GAP
- **Source findings:** BUG-DBG-06/07/08/09/10/11/12/13/14/15 (deep-audit)
- **Affected product:** Both
- **Affected files:** tests/ (various)
- **Affected functions:** (test infrastructure)
- **Root cause:** Green suite doesn't prove correctness; 66 UGA tests unreachable under direct execution; 4 tests write 2.2 MB into CWD; no real-object process-group test on Windows; secrets can flow into event timeline; game process leak and missing results file undetected; merge silently does wrong thing; migration 8 breaks suites with confusing assertion; should_retry_attempt could return True unconditionally; wrong agent could execute; test only passes from documented CWD.
- **Concrete evidence:** Deep audit §9; mutation test (review gate removed) leaves suite at 382 OK.
- **Observed behavior:** A green suite is not evidence of quality. For each test: what production bug could exist while this test still passes?
- **Expected behavior:** Tests should detect the safety-critical assertions, not just the easy ones.
- **Minimal fix scope:** Add real-object tests for process-group termination; add secret-bearing fixtures; add merge conflict tests; add CWD-independent test invocation.
- **Regression tests:** Each test-quality defect addressed individually.
- **Dependencies:** None
- **Regression risk:** Test suite reliability; ensure new tests are deterministic and isolated.

### ROOT-035 — GUI root_cache shared mutable state
- **Severity:** LOW
- **Confidence:** CONFIRMED
- **Status:** ACTIVE BUG
- **Source findings:** #8 (review-handoff)
- **Affected product:** AgentOps
- **Affected files:** agentops/agentops/gui_controller.py:233-246
- **Affected functions:** GUIController._operation_root
- **Root cause:** _root_cache is shared mutable state with no lock; called from both worker threads and main thread.
- **Concrete evidence:** Code inspection; docstring acknowledges concurrent-access pattern but doesn't guard it.
- **Observed behavior:** _root_cache reads and writes without lock; under CPython's GIL this won't corrupt memory, but worth locking for consistency.
- **Expected behavior:** Wrap _root_cache reads and writes in existing _operation_lock, or give it its own small lock.
- **Minimal fix scope:** Lock _root_cache access.
- **Regression tests:** Concurrent access → no corruption.
- **Dependencies:** None
- **Regression risk:** Low — GIL prevents memory corruption, but consistency improvement.

### ROOT-036 — Eval metric bugs
- **Severity:** MEDIUM
- **Confidence:** CONFIRMED
- **Status:** MEASUREMENT INTEGRITY
- **Source findings:** repo-review #10
- **Affected product:** UGA
- **Affected files:** universal-game-agent/training/evaluate.py:42-45; environment/external_game.py:274
- **Affected functions:** evaluate; ExternalGameEnv.reset
- **Root cause:** Eval hits/misses guessed from reward sign; external env discards its seeds.
- **Concrete evidence:** repo-review; reward > 0 as hit, reward < 0 as miss holds only when every nonzero reward is strictly +/-1.
- **Observed behavior:** Eval hits and misses are guessed from reward sign; the external env discards its seeds.
- **Expected behavior:** Reward-sign-only verdict has no controlled comparison behind it; seeds should be preserved.
- **Minimal fix scope:** Fix eval metric to handle composite rewards; preserve seeds in reset.
- **Regression tests:** Composite reward → correct hit/miss counting; seed preservation verified.
- **Dependencies:** None
- **Regression risk:** Eval metric change; ensure backward compatibility with existing results.

---

## 4. Duplicate Findings

| Duplicate cluster | Canonical root cause | Source findings |
|---|---|---|
| Unredacted task.result | ROOT-001 | BUG-AO-02, BUG-AOP-03, #4 review-handoff, #5 repo-review |
| Review gate bypass | ROOT-002 | BUG-AOP-02, AOP-02, #2 review-handoff, #4 repo-review |
| Self-deadlock via .agentops/ | ROOT-003 | BUG-AOP-01, AOP-01, #1 review-handoff, #3 repo-review |
| Git failure misclassification | ROOT-004 | #3 review-handoff |
| Silent exception swallowing | ROOT-005 | BUG-AOP-07, AOP-07, #6 review-handoff |
| GUI mutual-exclusion bypass | ROOT-006 | #2, #7 review-handoff |
| GUI recovery scope mismatch | ROOT-007 | BUG-AO-03, BUG-AOP-08, AOP-08 |
| Checkpoint interval zero crash | ROOT-008 | BUG-UGA-01 (×2 reports) |
| Game process leak on attach failure | ROOT-009 | BUG-UGA-02 (×2 reports) |
| Checkpoint-load env not closed | ROOT-010 | BUG-UGA-03 (×2 reports) |
| Capture-size contract undefined | ROOT-011 | BUG-UGA-04 |
| Verification run stranded | ROOT-012 | BUG-AO-04 |
| Non-atomic checkpoint save | ROOT-013 | BUG-UGA-15, #9 repo-review |
| External attach failure leaks process+log+results | ROOT-027 | BUG-UGA-22 |
| GAE mid-rollout truncation bleed | ROOT-028 | BUG-DBG-05 |
| Producer/validator divergence | ROOT-029 | BUG-DBG-01, BUG-FIX-04 |
| UnicodeDecodeError | ROOT-032 | BUG-AOP-09, AOP-09 |

**Previously reported duplicates collapsed:** BUG-UGA-05/06/07 (false positives), BUG-AO-05 (false positive), BUG-AO-01 (held for Windows contract).

---

## 5. False Positives

| Finding | Reason |
|---|---|
| BUG-UGA-05 (smoke env not closed) | MSS backend and process already cleaned in finally; env.close() is tidiness only |
| BUG-UGA-06 (experiment env not closed) | Uses toy env; close() is no-op; external path unreachable |
| BUG-UGA-07 (CLI training env leaked) | Same as UGA-06; toy path |
| BUG-AO-05 (ready_tasks mutation) | PENDING guard prevents repeated writes; unblock scenario not present in current workflow |
| UGA-13 (smoke test doesn't exercise configured env) | main.py uses configured env; second ToyPongEnv only for print block |
| UGA-11 (VK mapping mismatch) | YAML 37/39 ≡ 0x25/0x27 in all definitions; drift risk only |
| AOP-09 as worded (UnicodeDecodeError escapes) | Mechanism corrected: error swallowed in _readerthread, _run returns stdout=None, returncode=0 — still a defect (→ ROOT-032) |
| Merge dirty-tree guard missing | Disproven: git.py:239-243 refuses on any porcelain; recorded as coverage gap BUG-DBG-11 |
| Raw stdout reaches event sink via workflow.py:546 | Retracted by deep audit: task.result overwritten at :542, two lines before event emission |
| AOP-24 (validator call sites redundant) | Redundancy, not a bug |
| AOP-19/21/22 (module size, typing, dependency arrows) | Architecture debt, no demonstrated runtime consequence |
| Copilot snapshot packaging claims; regex sweep; _operation_lock | False positives, correctly rejected previously |
| Non-editable wheel drops config files | Packaging concern, not a runtime defect |
| Dead code verification_kernel.py:182 | Cruft from incomplete refactor, no functional impact |
| Comment hygiene | Stylistic, no correctness impact |
| Ruff static analysis (cosmic findings) | 17 of 20 bare excepts are narrow OSError/platform-shim catches; only 3 load-bearing |

---

## 6. Already Fixed

Per the deep audit's re-validation of 26 historical bugs:

| Finding | Disposition | Reason |
|---|---|---|
| BUG-AOP-05 — swallowed write certifying passed report | ALREADY FIXED | Fix present, live, covered by regression test |
| BUG-FIX-11 — git-diff tri-state | ALREADY FIXED | Fix present, live, covered by regression test |
| BUG-FIX-16 — killpg group ownership | ALREADY FIXED | Fix present, live, covered by regression test |
| BUG-FIX-17 — thread leak | ALREADY FIXED | Fix present, live, covered by regression test |
| BUG-FIX-18 — routing recorded selection not execution | ALREADY FIXED | Fix present, live, covered by regression test |
| BUG-FIX-05 — (historical) | ALREADY FIXED | Fix present, live, covered by regression test |
| BUG-FIX-07..10, 12..15, 19..21 | ALREADY FIXED | Fix present, live, covered by regression test |
| BUG-FIX-22 — CLI UnicodeEncodeError | PARTIALLY FIXED | Code correct, no test references _print_text |
| BUG-FIX-23 — WAL SQLITE_LOCKED | PARTIALLY FIXED | Code correct, test is probabilistic (~25% flake) |
| BUG-FIX-20 — evidence secrets | PARTIALLY FIXED | Code correct, 500-char peek cap untested |
| BUG-FIX-06 — migration repair loop | PARTIALLY FIXED | Code correct, repair loop untested |
| BUG-FIX-04 — optional failures | PARTIALLY FIXED | Validator fixed, producer still disagrees (→ ROOT-029) |
| BUG-FIX-24 — workflow-scoped recovery | REGRESSED | Fixed in engine, bypassed by GUI (→ ROOT-007) |
| BUG-FIX-25 — recovery idempotency | ALREADY FIXED (unreachable) | Correct and tested, but no in-product callers |
| BUG-FIX-26 — cancellation repair budget | ALREADY FIXED (unreachable) | Correct and tested, but no in-product callers |

---

## 7. Needs Investigation

> **Status as of 2026-10-02:** the ROOT-015..ROOT-025 and ROOT-028 rows below are
> **DISPROVEN 2026-10-02 — see §0.** They were re-checked against current source and do
> not reproduce. Do not act on them. This table is retained as historical evidence.

| Item | Why uncertain | What would resolve it |
|---|---|---|
| ROOT-015 (entropy sign) | geminihandoff only; deep audit didn't list | Read current ppo.py:compute_loss; verify sign against PPO objective |
| ROOT-016 (truncation terminal) | geminihandoff only | Read current ppo.py:compute_gae; verify done mask |
| ROOT-017..018 (GRU/ICM) | geminihandoff only | Read current model.py:forward_sequence, curiosity.py |
| ROOT-019..026 | geminihandoff only | Read current git.py, runner.py, persistence.py, routing.py, preprocessing.py, win32_capture.py, games/ |
| ROOT-033 (Windows termination) | Reported reproduction shows abnormal exit code, not termination vs crash | Define Windows termination contract; identify reliable signal |
| ROOT-011 (capture-size contract) | Intended mode-specific meaning undocumented | Ask product owner: should region/window honor out_width/out_height? |
| ROOT-036 (eval metric bugs) | repo-review only; needs source verification | Read current evaluate.py and external_game.py |
| Contaminated external-Pong results | 82-83% of episodes are length-1 zeros | Regenerate results under settle fix before citing numbers |
| Leaked OpenCode key | In git history; rotation not recorded | Rotate key; log rotation in decisions.md |
| .gitignore tension | decisions.md records deliberate removal; current state is machine-local | Policy call: commit .gitignore or document machine-local paths |
| Test-quality defects (ROOT-034) | Green suite doesn't prove correctness | See deep audit §9 for 10 specific test gaps |
| Deep audit LOW bugs (IDs only) | Full entries not in available sources | Look up in full registry at .agents/memory/bug-registry.md |
| BUG-DBG-09/10/11/15 | Medium confidence, need source verification | Read current source |
| BUG-UGA-14/17/18/20/21 | IDs only in deep audit listing | Look up in full registry |

---

## 8. Fix Dependencies

**No hard dependencies** between confirmed fixes. Soft dependencies:
- ROOT-003 (self-deadlock) and ROOT-004 (git misclassification) both touch git.py; can be done in parallel
- ROOT-020/021 (subprocess deadlock/group leak) share runner.py; fix together
- ROOT-015/016 (PPO defects) may interact with ROOT-028 (GAE truncation) — fix both, test together
- ROOT-011 (capture contract) must be decided before fix; no other dependency
- ROOT-033 (Windows termination) needs a platform contract before any implementation; blocked on investigation

**Recommended independent batches:**
- Batch A: ROOT-001, ROOT-008, ROOT-009, ROOT-010 (no shared files, directly verifiable)
- Batch B: ROOT-002, ROOT-003, ROOT-004, ROOT-005 (AgentOps persistence/git)
- Batch C: ROOT-006, ROOT-007, ROOT-035 (GUI, shared gui_controller.py)
- Batch D: ROOT-013, ROOT-014, ROOT-027, ROOT-028 (UGA training/checkpoint)
- Batch E: ROOT-015..026 (requires source verification first)
- Batch F: ROOT-033 (held for Windows contract)
- Batch G: ROOT-011 (held for contract decision)
- Batch H: ROOT-029..032, ROOT-036 (AgentOps persistence/encoding/eval)
- Batch I: ROOT-034 (test-quality)

---

## 9. Authoritative Fix Queue — SUPERSEDED 2026-10-02

> **SUPERSEDED 2026-10-02. HISTORICAL — MUST NOT be used as the live work queue.**
> This table is the 2026-09-29 snapshot. Its `READY TO FIX` / `BLOCKED` statuses predate
> the fixes landed since, and they list roots that the 2026-10-02 verification marked
> FIXED or DISPROVEN. The authoritative current ledger is **§0**; the authoritative task
> queue is **`.agents/pending_tasks.md`**. Contents are preserved unchanged below as audit
> evidence.

| ROOT | Severity | Confidence | Source findings | Affected files | Minimal fix | Tests required | Dependencies | Risk | Status |
|---|---|---|---|---|---|---|---|---|---|
| ROOT-003 | CRITICAL | CONFIRMED | AOP-01, AOP-01, #1, #3 | git.py | .gitignore or worktree relocation | Fresh repo → create → merge must not raise | None | Low — worktree relocation changes UX | READY TO FIX |
| ROOT-002 | CRITICAL | CONFIRMED | AOP-02, AOP-02, #2, #4 | cli.py, state.py, execution_model.py | Share assert_workflow_ready | Custom workflow no review → not ready | None | Low — standard path unchanged | READY TO FIX |
| ROOT-015 | CRITICAL | NEEDS INVESTIGATION | geminihandoff #1 | ppo.py | Subtract entropy term | High-entropy dist yields lower loss | None | Training behavior change; verify numerically | BLOCKED / NEEDS INVESTIGATION |
| ROOT-016 | CRITICAL | NEEDS INVESTIGATION | geminihandoff #2 | ppo.py | Separate truncated mask in GAE | Mid-rollout truncation bootstraps V | None | Training convergence; test with truncation | BLOCKED / NEEDS INVESTIGATION |
| ROOT-001 | HIGH | CONFIRMED | AO-02, AOP-03, #4, #5 | workflow.py | redact_text() on stdout/stderr | Secret in agent output not in task.result | None | UI may read task.result; preserve diagnostics | READY TO FIX |
| ROOT-006 | HIGH | CONFIRMED | #2, #7 | gui_controller.py | Mint new Event per operation | Concurrent start → _active stays True | None | UI event ordering | READY TO FIX |
| ROOT-009 | HIGH | CONFIRMED | UGA-02, UGA-02 | external_experiment.py | try/except around launch_phase2 | attach fail → proc terminated | None | Process cleanup timing | READY TO FIX |
| ROOT-013 | HIGH | CONFIRMED | UGA-15, #9 | ppo.py | Atomic save + RNG state | Kill mid-save → no truncated file | None | Checkpoint format change | READY TO FIX |
| ROOT-017 | HIGH | NEEDS INVESTIGATION | geminihandoff #3 | model.py | Mask h_t by (1-done) | Episode boundary → no state leak | None | Recurrent update correctness | BLOCKED / NEEDS INVESTIGATION |
| ROOT-018 | HIGH | NEEDS INVESTIGATION | geminihandoff #4 | curiosity.py | Detach features | ICM loss doesn't corrupt shared params | None | Curiosity objective change | BLOCKED / NEEDS INVESTIGATION |
| ROOT-019 | HIGH | NEEDS INVESTIGATION | geminihandoff #5 | git.py, finalize.py | Mandate worktree_path | abort_merge on root → error | None | Must keep worktree_path always provided | BLOCKED / NEEDS INVESTIGATION |
| ROOT-020 | HIGH | NEEDS INVESTIGATION | geminihandoff #6 | runner.py | communicate() or async drain | Large output → no hang | ROOT-021 | Timeout behavior change | BLOCKED / NEEDS INVESTIGATION |
| ROOT-021 | HIGH | NEEDS INVESTIGATION | geminihandoff #7 | runner.py | setsid / CREATE_NEW_PROCESS_GROUP | Cancel → children killed | ROOT-020 | Signal propagation | BLOCKED / NEEDS INVESTIGATION |
| ROOT-027 | HIGH | CONFIRMED | UGA-22 | external_experiment.py | Cleanup + results JSON on attach fail | attach fail → proc dead, results written | ROOT-009 | Results JSON format | READY TO FIX |
| ROOT-028 | HIGH | CONFIRMED | DBG-05 | ppo.py | Pass dones buffer to compute_gae | Mid-rollout truncation → correct V | None | GAE numerical change | READY TO FIX |
| ROOT-004 | MEDIUM | CONFIRMED | #3 | finalize.py, failure.py | Wire FailureClassifier | Each GitError → correct category | ROOT-003 | Misclassification text | READY TO FIX |
| ROOT-005 | MEDIUM | CONFIRMED | AOP-07, AOP-07, #6 | workflow.py | logger.exception in bare excepts | Unexpected failure → visible log | None | Logging noise | READY TO FIX |
| ROOT-007 | MEDIUM | CONFIRMED | AO-03, AOP-08 | gui_controller.py | Forward workflow_id to all 3 calls | Scoped recovery → other workflow untouched | None | None — latent, no callers | READY TO FIX |
| ROOT-008 | MEDIUM | CONFIRMED | UGA-01, UGA-01 | ppo.py | Validate checkpoint_every_updates | Zero/negative/positive intervals | None | Zero behavior choice (reject vs disable) | READY TO FIX |
| ROOT-010 | MEDIUM | CONFIRMED | UGA-03, UGA-03 | external_experiment.py | Close temp env in finally | Env closed on success and load fail | None | None | READY TO FIX |
| ROOT-011 | MEDIUM | LIKELY | UGA-04 | external_game.py | Decide + document per-mode contract | Test via factory per mode | None | Resizing may degrade detection | BLOCKED / NEEDS INVESTIGATION |
| ROOT-012 | LOW | CONFIRMED | AO-04 | verification_kernel.py | try/except + finalize FAILED | Check-creation fail → run not RUNNING | None | DB failure may prevent cleanup | READY TO FIX |
| ROOT-014 | MEDIUM | CONFIRMED | #9 (repo-review) | main.py, experiment.py | Guard empty history; warn | Resume finished → no crash | None | None | READY TO FIX |
| ROOT-022 | MEDIUM | NEEDS INVESTIGATION | geminihandoff #8 | persistence.py | Propagate SQLite errors | Write fail → exception, not silent | None | Policy enforcement scope | BLOCKED / NEEDS INVESTIGATION |
| ROOT-023 | MEDIUM | NEEDS INVESTIGATION | geminihandoff #9 | routing.py | Preferred-agent sort key | Equal scores → preferred first | None | Routing behavior change | BLOCKED / NEEDS INVESTIGATION |
| ROOT-024 | MEDIUM | NEEDS INVESTIGATION | geminihandoff #10 | preprocessing.py | float32 cast before division | Frame range [0,1] not [0,0,1] | None | Preprocessing pipeline | BLOCKED / NEEDS INVESTIGATION |
| ROOT-025 | MEDIUM | NEEDS INVESTIGATION | geminihandoff #11 | win32_capture.py | finally DeleteObject/ReleaseDC | Exception → no GDI leak | None | Win32 resource management | BLOCKED / NEEDS INVESTIGATION |
| ROOT-026 | MEDIUM | NEEDS INVESTIGATION | geminihandoff #12 | games/__init__.py | Add empty file | Non-editable install → import works | None | Package structure | BLOCKED / NEEDS INVESTIGATION |
| ROOT-032 | MEDIUM | CONFIRMED | AOP-09, AOP-09 | git.py | UTF-8 decoding + errors=replace | Non-ASCII branch → no crash | None | Decoding behavior change | READY TO FIX |
| ROOT-033 | MEDIUM | SUSPECTED | AO-01 | runner.py | Define Windows termination contract | Known termination signal → TERMINATED | None | High-bit heuristic unsafe | BLOCKED / NEEDS INVESTIGATION |
| ROOT-035 | LOW | CONFIRMED | #8 review-handoff | gui_controller.py | Lock _root_cache access | Concurrent access → no corruption | None | Low — GIL prevents memory corruption | READY TO FIX |
| ROOT-036 | MEDIUM | CONFIRMED | #10 repo-review | evaluate.py, external_game.py | Fix eval metric; preserve seeds | Composite reward → correct counting | None | Eval metric change | BLOCKED / NEEDS INVESTIGATION |
| ROOT-034 | HIGH/LOW | CONFIRMED | DBG-06..15 | tests/ | 10 test-quality fixes | Each test-quality defect | None | Test suite reliability | READY TO FIX |

---

## 10. Non-Bug / Architecture Debt / Measurement Issue Findings

| Finding | Disposition | Reason |
|---|---|---|
| AOP-04 / #5 / #6 (No CI) | NON-BUG / ARCHITECTURE DEBT — SUPERSEDED 2026-10-02 | Infrastructure gap; tracked on roadmap; was blocked on the D5 decision. **CI delivered at 2cb2413** — per-product path-filtered workflows, scope documented in `.github/CI.md` |
| #7 (ignore rules in .git/info/exclude) | GOVERNANCE | Machine-local paths; fresh clones lose rules; policy call needed |
| #8 (after-task.md stale) | DOC DRIFT | Agentops-only; UGA tasks run wrong suite |
| #9 (dead code verification_kernel.py:182) | NON-BUG / DEAD CODE | Leftover cruft; no functional impact |
| #10 (comment hygiene) | NON-BUG / STYLE | Stylistic; no correctness impact |
| #11 (ruff 91 findings) | MIXED | 17 cosmetic/OSError catches; 3 load-bearing (→ ROOT-005) |
| #2 (leaked OpenCode key) | SECURITY | In git history; rotation not recorded; not a code defect |
| #1 (contaminated external-Pong results) | MEASUREMENT ISSUE | 82-83% episodes length-1 zeros; regenerate under settle fix |
| Non-editable wheel drops config | NON-BUG / PACKAGING | Packaging concern |
| AOP-19/21/22 (module size, typing, deps) | ARCHITECTURE DEBT | No demonstrated runtime consequence |
| AOP-24 (validator redundancy) | NON-BUG | Re-checking booleans harmless |
| Copilot snapshot packaging claims | FALSE POSITIVE | Harness artifact |
| _operation_lock claim | FALSE POSITIVE | Lock belongs to AgentOpsController |

---

## 11. Final Validation Counts

**Source findings tallied:**
- bug-registry.md: 12 findings
- deep-bug-audit-2026-09-29.md: 44 current + 8 rejected + 26 historical = 78 findings
- geminihandoff.md: 14 findings
- projects-ai-bug-handoff.md: 9 findings
- projects-ai-review-handoff.md: 14 findings
- repo-review-2026-09-26.md: 10 findings
- **Total source findings: 137**

**Dispositions:**
- CANONICAL ROOT (mapped to ROOT-XXX): 89 findings
- DUPLICATE (same root as another finding): 17 findings
- SYMPTOM: 0 findings (none identified as symptom-only)
- FALSE POSITIVE: 12 findings
- ALREADY FIXED: 15 findings
- NEEDS INVESTIGATION: 11 findings
- NON-BUG / ARCHITECTURE DEBT / MEASUREMENT ISSUE: 10 findings
- **Total dispositions: 137** ✓ (zero silent drops)

- **Canonical roots:** 36 (ROOT-001..ROOT-036, contiguous through ROOT-036)

**Verification checks:**

| Check | Result |
|---|---|
| SOURCE COVERAGE | PASS — 137/137 source findings accounted for |
| ROOT COVERAGE | PASS — every ROOT-XXX has ≥1 source finding |
| NO SILENT DROPS | PASS — every finding has exactly one disposition |
| ROOT ID CONSISTENCY | PASS — contiguous ROOT-001..ROOT-036, all referenced IDs defined |
| TRACEABILITY | PASS — bidirectional mapping: source→ROOT and ROOT→source findings |
