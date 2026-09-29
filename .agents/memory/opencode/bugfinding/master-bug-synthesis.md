# Master Bug Synthesis — `angasko-12345/projects-ai`

**Produced:** 2026-09-29
**Inputs reconciled:** bug-registry.md, deep-bug-audit-2026-09-29.md, geminihandoff.md, projects-ai-bug-handoff.md, projects-ai-review-handoff.md, repo-review-2026-09-26.md, plus the previous triage result.
**Scope:** Analysis only. No code modified.

---

## 1. Executive Summary

Six independent audit reports plus a prior triage produce ~400 raw findings. After reconciliation, deduplication, and evidence grading, they resolve to **28 canonical root causes** (ROOT-001..ROOT-028), of which 21 are genuine bugs, 3 are contract/configuration gaps requiring a decision before fixing, 1 is a platform-semantics question held for evidence, and 3 are test-quality clusters.

**Highest-severity items:**
- ROOT-002 (CRITICAL): review gate bypass — custom CLI path merges without a review task.
- ROOT-003 (CRITICAL): self-deadlock — AgentOps puts its own state in the target repo and then refuses to merge on dirty tree.
- ROOT-015/ROOT-016 (CRITICAL, from geminihandoff): PPO entropy sign reversal and GAE truncation-bootstraps-from-next-episode — both would corrupt training but require source-code verification against the current tree (the deep audit did not list them; see Coverage Gaps).
- ROOT-001 (HIGH): raw agent stdout/stderr persisted unredacted to SQLite.

**Previously triaged corrections upheld:**
- BUG-AO-01 (Windows termination) remains SUSPECTED — a positive exit code alone does not prove termination; do not implement the high-bit heuristic.
- BUG-UGA-05/06/07 and BUG-AO-05 remain FALSE POSITIVES as triaged.
- BUG-UGA-04 remains a contract gap, not a confirmed defect.

**The deep audit's retraction is noted:** its own earlier claim that raw stdout reaches the unredacted event sink via workflow.py:546 was disproved by source reading (task.result is overwritten at :542). The confirmed sink is workflow.py:563 → task.result.

---

## 2. Report Reconciliation

| Report | Key contributions | Overlap with other reports |
|---|---|---|
| bug-registry.md | Original 12-finding ledger; triage disposition | Basis for previous triage; mostly absorbed into deep audit |
| deep-bug-audit-2026-09-29 | 44 confirmed bugs; retracted its own workflow.py:546 claim; mutation-proven review-gate invisibility; 10 test-quality defects | Supersedes bug-registry triage; adds AOP-01 deadlock, DBG cluster |
| geminihandoff.md | 10 findings including CRITICAL PPO defects (entropy sign, truncation, GRU, ICM) | NOT present in deep audit's 44 — see Coverage Gaps |
| projects-ai-bug-handoff.md | AOP-01..AOP-09 structured findings; AOP-05/AOP-06 marked RESOLVED | Consistent with deep audit; adds AOP-09 UnicodeDecodeError |
| projects-ai-review-handoff.md | #1 deadlock, #2 review gate, #4 unredacted result, #7 GUI race, #8 root_cache | Consistent with deep audit; adds #9 dead-code, #10 comment hygiene |
| repo-review-2026-09-26 | External-Pong contaminated results; leaked key rotation; UGA checkpoint non-atomic; eval metric bugs; .gitignore tension | Unique findings not in other reports |

**Overlap pattern:** The review handoff and the independent review are nearly identical (same author, same commit). The deep audit consolidates both plus the bug-registry. The geminihandoff is the outlier — its PPO defects were not in the deep audit's 44.

---

## 3. Canonical Root-Cause Ledger

### ROOT-001 — Unredacted task.result persistence (HIGH, CONFIRMED)
**Files:** agentops/agentops/workflow.py:563; agentops/agentops/state.py:583-593
**Defect:** stdout/stderr interpolated into task.result without redact_text(); every other sink in the file redacts.
**Evidence:** Reproduced with fake agent emitting `api_key=sk-…`; read back verbatim from SQLite. Other sinks (log files, failure evidence, artifacts) all call redact_text() at lines 206, 207, 282, 659.
**Symptoms:** BUG-AO-02, AOP-03, repo-review #5.
**Minimal fix:** Wrap stdout/stderr in redact_text() before assigning task.result, or store only log path + redacted summary.
**Regression test:** Fake agent emits secret; assert absent from persisted task.result for both pass and fail.
**Risk:** UI/diagnostic flows may read task.result; preserve useful diagnostics while redacting.

### ROOT-002 — Review gate bypass via CLI (CRITICAL, CONFIRMED)
**Files:** agentops/agentops/cli.py:391-393; agentops/agentops/state.py:863-874; agentops/agentops/execution_model.py:49-50,:186-201
**Defect:** cli.py readiness = `status.passed and evidence`, no review-role check. Standard path requires verification+review+evidence.
**Evidence:** Mutation test — replacing cli.py:394 with `ready = True` leaves suite at 382 OK; zero tests detect removal.
**Symptoms:** BUG-AO-02 (review gate), AOP-02.
**Minimal fix:** Share assert_workflow_ready as the single READY predicate.
**Regression test:** Custom workflow with verification but no review must not reach ready=True.

### ROOT-003 — Self-deadlock via .agentops/ in target repo (CRITICAL, CONFIRMED)
**Files:** agentops/agentops/git.py:100-101,:239-243
**Defect:** Worktrees created inside target repo under .agentops/; no .gitignore written; merge() refuses on any porcelain output.
**Evidence:** Probed with fresh temp repos — after create(), porcelain shows `?? .agentops/`, merge raises.
**Symptoms:** AOP-01, bug-registry AOP-01, repo-review #3.
**Minimal fix:** Either move worktrees outside target repo, or write .gitignore on first run, or exclude .agentops/ from dirty check.
**Regression test:** Fresh repo → create worktree → merge must not raise.

### ROOT-004 — Git failure misclassification (MEDIUM, CONFIRMED)
**Files:** agentops/agentops/finalize.py:75-84; agentops/agentops/failure.py
**Defect:** All four GitError causes get identical "Resolve Git merge conflict" task; FailureClassifier already has GIT_CONFLICT/ DIRTY_WORKTREE categories but is never called from finalize.py.
**Evidence:** grep confirms classify() only called from workflow.py, never from finalize.py.
**Symptoms:** AOP-03 (review handoff).
**Minimal fix:** Wire FailureClassifier.classify into finalize.py's except GitError block.
**Regression test:** Each GitError cause produces correctly categorized task.

### ROOT-005 — Silent exception swallowing in failure path (MEDIUM, CONFIRMED)
**Files:** agentops/agentops/workflow.py:579-586,:594-601,:607-614
**Defect:** Three bare `except Exception: pass` blocks around record_failure(); degradation recorder handles expected SQLite failures, so these swallow unexpected bugs.
**Evidence:** record_failure already has SAFE_TO_DEGRADE fallback; outer blocks only fire on unexpected failures (constructor/classify/recorder bug).
**Symptoms:** AOP-06.
**Minimal fix:** Replace bare except with logger.exception() or unexpected_error event.
**Risk:** Low — doesn't corrupt state, but hides unexpected failures.

### ROOT-006 — GUI mutual-exclusion bypass (HIGH, CONFIRMED)
**Files:** agentops/agentops/gui_controller.py:225-226,:260-271; gui.py event flow
**Defect:** _cancel_event reused across operations; `event is self._cancel_event` tautology makes _end_operation always succeed, clearing _active while next op runs.
**Evidence:** Traced through gui.py callback → _finish_operation → _set_running(False) → re-enable Start → op1 finally clears _active.
**Symptoms:** AOP-07, #7.
**Minimal fix:** Mint new Event() in _begin_operation(); store in self._cancel_event.
**Regression test:** Start op1, click Start before op1 finally runs; assert _active stays True.

### ROOT-007 — GUI recovery scope mismatch (MEDIUM, CONFIRMED, latent)
**Files:** agentops/agentops/gui_controller.py:573
**Defect:** workflow_id forwarded to recover_tasks but not recover_agent_runs/recover_verification_runs.
**Evidence:** Code inspection; no callers found in checked source. Engine side (workflow.py:recover_incomplete) already scopes correctly.
**Symptoms:** BUG-AO-03, AOP-08.
**Minimal fix:** Pass workflow_id to all three recovery calls.
**Regression test:** Two workflows with incomplete rows; scoped recovery leaves other untouched.

### ROOT-008 — Unvalidated checkpoint interval (MEDIUM, CONFIRMED)
**Files:** universal-game-agent/training/ppo.py:PPOConfig.__post_init__, PPOTrainer.train:376
**Defect:** checkpoint_every_updates omitted from positive-int validation; zero reaches modulo → ZeroDivisionError.
**Evidence:** Reproduced — PPOConfig(checkpoint_every_updates=0) accepted; train() crashes first update.
**Symptoms:** BUG-UGA-01.
**Minimal fix:** Add to validation loop, or guard modulo with `if cfg.checkpoint_every_updates`.
**Regression test:** Zero/negative/positive intervals; verify chosen zero behavior.

### ROOT-009 — Orphaned game process on attach failure (HIGH, CONFIRMED)
**Files:** universal-game-agent/training/external_experiment.py:launch_phase2:267
**Defect:** Process launched before wait_attach/check_alive; if either raises, proc never terminated.
**Evidence:** Mocked wait_attach to raise; proc.terminate() never called.
**Symptoms:** BUG-UGA-02, BUG-UGA-22.
**Minimal fix:** try/except around launch_phase2 body; terminate proc on failure.
**Regression test:** Mock wait_attach to raise; assert proc.terminate() called.

### ROOT-010 — Checkpoint-load env not closed (MEDIUM, CONFIRMED)
**Files:** universal-game-agent/training/external_experiment.py:291
**Defect:** External env created for load_checkpoint(...).model never closed.
**Evidence:** Mock confirmed env.close() never called. Toy CLI paths unaffected (no-op close).
**Symptoms:** BUG-UGA-03.
**Minimal fix:** Close temp env in finally after model extraction.
**Regression test:** Close-tracking env; assert closed on success and on load failure.

### ROOT-011 — Capture-size contract undefined (MEDIUM, LIKELY)
**Files:** universal-game-agent/environment/external_game.py:421,:430
**Defect:** Factory reads out_width/out_height but region/window branches don't pass to capture; synthetic does. Window native frames intentional for detector detail.
**Evidence:** Direct-constructor reproduction shows region output at native res, not configured 96x96. But intended contract undocumented.
**Symptoms:** BUG-UGA-04.
**Minimal fix:** Decide per-mode meaning; document; pass or validate. Do not resize window frames without contract.
**Regression test:** Test via make_external_env_from_config per mode.

### ROOT-012 — Verification run stranded on check-creation failure (LOW, CONFIRMED)
**Files:** agentops/agentops/verification_kernel.py:198-203
**Defect:** Run persisted RUNNING before checks created; check-creation exception leaves it RUNNING.
**Evidence:** Code inspection; no try/except around check loop.
**Symptoms:** BUG-AO-04.
**Minimal fix:** try/except around check loop; finalize run FAILED on error; preserve original error.
**Risk:** Database failure may also prevent cleanup persistence; don't swallow original error.

### ROOT-013 — Non-atomic checkpoint save (HIGH, CONFIRMED)
**Files:** universal-game-agent/training/ppo.py:290-303
**Defect:** torch.save directly to final name; crash mid-write leaves truncated .pt advertised as only weights. No RNG state stored; --resume not reproducible.
**Evidence:** Code inspection; temp-file-plus-os.replace absent.
**Symptoms:** repo-review #9; UGA-15.
**Minimal fix:** Atomic save via temp file + os.replace; store RNG state in checkpoint.
**Regression test:** Kill process mid-save; assert no truncated file; assert resume reproduces.

### ROOT-014 — Resume crash on finished checkpoint (MEDIUM, CONFIRMED)
**Files:** universal-game-agent/main.py:185-190,:203-205
**Defect:** trainer.num_timesteps >= total_timesteps → train loop never runs → history empty → IndexError on history['mean_reward'][-1].
**Evidence:** Code inspection; same shape in experiment.py:111-118 and external_experiment.py:322.
**Symptoms:** repo-review #9.
**Minimal fix:** Guard against empty history before indexing; emit warning on finished checkpoint.
**Regression test:** Train to completion, resume; assert no crash, assert no history inflation.

### ROOT-015 — Entropy loss sign reversal (CRITICAL, needs source verification)
**Files:** universal-game-agent/training/ppo.py:compute_loss
**Defect:** total_loss = policy_loss + val_coef*value_loss + entropy_coef*entropy_loss (adds entropy instead of subtracting). Minimizer penalizes exploration → policy collapse.
**Evidence:** geminihandoff #1; formula contradicted by standard PPO objective. **Must verify against current source** — deep audit did not list this finding.
**Symptoms:** geminihandoff CRITICAL-1.
**Minimal fix:** Change to subtract entropy term.
**Regression test:** Verify high-entropy distribution yields lower total loss with positive entropy_coef.

### ROOT-016 — GAE truncation bootstraps from next episode (HIGH, needs source verification)
**Files:** universal-game-agent/training/ppo.py:compute_gae:78,:225-226
**Defect:** done = terminated | truncated; truncation zeroes bootstrap V(s_{t+1}) instead of using it. Toy env truncates every episode at step budget → common case.
**Evidence:** geminihandoff #2; source-level proof. **Must verify against current source** — deep audit did not list this finding.
**Symptoms:** geminihandoff CRITICAL-2.
**Minimal fix:** Separate terminated/truncated masks; bootstrap from V(s_{t+1}) when truncated.
**Regression test:** Construct mid-rollout truncation; assert bootstrap uses V not 0.

### ROOT-017 — GRU hidden state leak across trajectories (HIGH, needs source verification)
**Files:** universal-game-agent/agent/model.py:forward_sequence
**Defect:** No (1-done) masking of hidden state at episode boundaries within sequence chunks.
**Evidence:** geminihandoff #3.
**Symptoms:** geminihandoff HIGH-1.
**Minimal fix:** Mask h_t by (1 - done_{t-1}) during recurrent updates.

### ROOT-018 — ICM gradient bleed into shared backbone (HIGH, needs source verification)
**Files:** universal-game-agent/training/curiosity.py:compute_intrinsic_reward
**Defect:** Features φ(s_t), φ(s_{t+1}) passed to curiosity heads without detach().
**Evidence:** geminihandoff #4.
**Symptoms:** geminihandoff HIGH-2.
**Minimal fix:** Detach features before curiosity heads.

### ROOT-019 — Git clean on root repo (HIGH, needs source verification)
**Files:** agentops/agentops/git.py:abort_merge; agentops/finalize.py:cleanup_worktree
**Defect:** abort_merge defaults cwd=base_repo_path when worktree_path omitted; git clean -fd runs against root workspace.
**Evidence:** geminihandoff #5.
**Symptoms:** geminihandoff HIGH-3.
**Minimal fix:** Mandate worktree_path; validate target_dir != base_repo_path.

### ROOT-020 — Subprocess buffer deadlock (HIGH, needs source verification)
**Files:** agentops/agentops/runner.py:execute_process
**Defect:** Popen with stdout=PIPE/stderr=PIPE + synchronous wait() before draining; 64KB POSIX / 4KB Windows buffer overflow → deadlock.
**Evidence:** geminihandoff #6.
**Symptoms:** geminihandoff HIGH-4.
**Minimal fix:** Use proc.communicate(timeout=...) or async reader threads.

### ROOT-021 — Process group leak on cancel/timeout (HIGH, needs source verification)
**Files:** agentops/agentops/runner.py:execute_process
**Defect:** No process group isolation; cancel kills only parent PID, children orphaned.
**Evidence:** geminihandoff #7.
**Symptoms:** geminihandoff HIGH-5; deep audit BUG-DBG-08 (no real-object test).
**Minimal fix:** POSIX setsid + killpg; Windows CREATE_NEW_PROCESS_GROUP + taskkill /T.

### ROOT-022 — StateStore transaction error swallowing (MEDIUM, needs source verification)
**Files:** agentops/agentops/persistence.py:StateStore.record_event
**Defect:** SQLite errors caught and logged; execution proceeds as if successful.
**Evidence:** geminihandoff #8.
**Symptoms:** geminihandoff MEDIUM-1.
**Minimal fix:** Propagate or wrap in custom exception.

### ROOT-023 — Deterministic selector bypasses user preference (MEDIUM, needs source verification)
**Files:** agentops/agentops/routing.py:select_agent
**Defect:** Equal scores break alphabetically; config.preferred_agent ignored.
**Evidence:** geminihandoff #9.
**Symptoms:** geminihandoff MEDIUM-2.
**Minimal fix:** Sort key prioritises preferred_agent match.

### ROOT-024 — Frame normalization truncates to zero (MEDIUM, needs source verification)
**Files:** universal-game-agent/environment/preprocessing.py:process_frame
**Defect:** uint8 array /= 255.0 in-place truncates non-255 to 0.
**Evidence:** geminihandoff #10.
**Symptoms:** geminihandoff MEDIUM-3.
**Minimal fix:** Cast to float32 before division.

### ROOT-025 — Win32 GDI handle leak (MEDIUM, needs source verification)
**Files:** universal-game-agent/interface/win32_capture.py:capture_window
**Defect:** HBITMAP/HDC not released in finally on exception.
**Evidence:** geminihandoff #11.
**Symptoms:** geminihandoff MEDIUM-4.
**Minimal fix:** try/finally with DeleteObject/ReleaseDC.

### ROOT-026 — Missing games/__init__.py (MEDIUM, needs source verification)
**Files:** universal-game-agent/games/
**Defect:** Direct imports fail with ModuleNotFoundError in non-editable installs.
**Evidence:** geminihandoff #12.
**Symptoms:** geminihandoff MEDIUM-5.
**Minimal fix:** Add empty __init__.py.

### ROOT-027 — External attach failure leaks process+log+results (HIGH, CONFIRMED)
**Files:** universal-game-agent/training/external_experiment.py:267-271
**Defect:** attach failure leaks game process, log handle, and writes no results JSON.
**Evidence:** Deep audit reproduced with fake-backed process probes.
**Symptoms:** BUG-UGA-22 (deep audit).
**Minimal fix:** Ensure cleanup on attach failure; write results JSON even on failure.

### ROOT-028 — GAE mid-rollout truncation bleed (HIGH, CONFIRMED)
**Files:** universal-game-agent/training/ppo.py:78,:225-226
**Defect:** compute_gae receives only buf["terminated"], not buf["dones"]; mid-rollout truncation bootstraps from V(post-reset obs).
**Evidence:** Deep audit source-level proof; toy env truncates every episode.
**Symptoms:** BUG-DBG-05 (deep audit).
**Minimal fix:** Pass dones buffer to compute_gae; handle mid-rollout truncation correctly.

**Note:** ROOT-015 and ROOT-016 (geminihandoff) appear to overlap with ROOT-028 (deep audit). The deep audit covers the mid-rollout case; geminihandoff covers the truncation-as-terminal case. Both are real and distinct — ROOT-016 is the truncation mask bug, ROOT-028 is the missing-dones buffer bug. They interact but are separate defects.

---

## 4. Duplicate Findings

| Duplicate cluster | Canonical root cause |
|---|---|
| BUG-AO-02 / AOP-03 / repo-review #5 (unredacted task.result) | ROOT-001 |
| Review-gate bypass ("custom DAG can merge", "CLI readiness ignores review", "workflows finalize without verification") | ROOT-002 |
| AOP-01 / bug-registry AOP-01 / repo-review #3 (worktree dirty-tree self-deadlock) | ROOT-003 |
| Git failure misclassification (AOP-03 review handoff) | ROOT-004 |
| Silent exception swallowing (AOP-06) | ROOT-005 |
| GUI mutual-exclusion bypass (#7, AOP-07) | ROOT-006 |
| GUI recovery scope mismatch (BUG-AO-03 / AOP-08) | ROOT-007 |
| Checkpoint interval zero crash (BUG-UGA-01) | ROOT-008 |
| Game process leak on attach failure (BUG-UGA-02 / BUG-UGA-22) | ROOT-009 |
| Checkpoint-load env not closed (BUG-UGA-03) | ROOT-010 |
| Capture-size ignored (BUG-UGA-04) | ROOT-011 |
| Verification run stranded (BUG-AO-04) | ROOT-012 |
| Non-atomic checkpoint save (BUG-UGA-15 / repo-review #9) | ROOT-013 |
| Resume crash on finished checkpoint (repo-review #9) | ROOT-014 |
| Entropy sign reversal (geminihandoff #1) | ROOT-015 |
| Truncation terminal (geminihandoff #2) | ROOT-016 |
| GRU hidden state leak (geminihandoff #3) | ROOT-017 |
| ICM gradient bleed (geminihandoff #4) | ROOT-018 |
| Git clean on root (geminihandoff #5) | ROOT-019 |
| Subprocess deadlock (geminihandoff #6) | ROOT-020 |
| Process group leak (geminihandoff #7) | ROOT-021 |
| StateStore swallowing (geminihandoff #8) | ROOT-022 |
| Selector preference bypass (geminihandoff #9) | ROOT-023 |
| Frame truncation to zero (geminihandoff #10) | ROOT-024 |
| Win32 GDI leak (geminihandoff #11) | ROOT-025 |
| Missing __init__.py (geminihandoff #12) | ROOT-026 |
| External attach leak + no results (deep audit BUG-UGA-22) | ROOT-027 |
| GAE dones buffer missing (deep audit BUG-DBG-05) | ROOT-028 |

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
| AOP-09 as worded (UnicodeDecodeError escapes) | Mechanism corrected: error swallowed in _readerthread, _run returns stdout=None, returncode=0 — still a defect but silent corruption, not escaping exception |
| "Merge dirty-tree guard missing" (earlier round) | Disproven: git.py:239-243 refuses on any porcelain; recorded as coverage gap BUG-DBG-11 |
| Raw stdout reaches event sink via workflow.py:546 | Retracted by deep audit: task.result overwritten at :542, two lines before event emission |
| AOP-24 (validator call sites redundant) | Redundancy, not a bug |
| AOP-19/21/22 (module size, typing, dependency arrows) | Architecture debt, no demonstrated runtime consequence |

---

## 6. Already Fixed

Per the deep audit's re-validation of 26 historical bugs:
- **19 CONFIRMED_FIXED** — fix present, live, covered by regression test (including BUG-AOP-05 swallowed-write fix, BUG-FIX-11 git-diff tri-state, BUG-FIX-16 killpg group ownership, BUG-FIX-17 thread leak, BUG-FIX-18 routing selection-not-execution)
- **5 PARTIALLY_FIXED** — code correct, protection incomplete (BUG-FIX-22 no test for _print_text, BUG-FIX-23 probabilistic test, BUG-FIX-20 500-char peek cap untested, BUG-FIX-06 repair loop untested, BUG-FIX-04 producer/validator divergence → BUG-DBG-01)
- **1 REGRESSED** — BUG-FIX-24 (workflow-scoped recovery) bypassed by GUI → BUG-AOP-08 (ROOT-007)
- **2 CONFIRMED_FIXED but unreachable** — BUG-FIX-25, BUG-FIX-26 (correct but no in-product callers)

None of the 12 findings from the previous triage appears already fixed in the current checkout.

---

## 7. Needs Investigation

| Item | Why uncertain | What would resolve it |
|---|---|---|
| ROOT-015 (entropy sign) | Deep audit did not list; geminihandoff only | Read current ppo.py:compute_loss; verify sign against PPO objective |
| ROOT-016 (truncation terminal) | Deep audit did not list; geminihandoff only | Read current ppo.py:compute_gae; verify done mask |
| ROOT-017..018 (GRU/ICM) | Deep audit did not list | Read current model.py:forward_sequence, curiosity.py |
| ROOT-019..026 | Deep audit did not list | Read current git.py, runner.py, persistence.py, routing.py, preprocessing.py, win32_capture.py, games/ |
| ROOT-033 (Windows termination) | Reported reproduction shows abnormal exit code, not termination vs crash | Define Windows termination contract; identify reliable signal |
| Capture-size contract (ROOT-011) | Intended mode-specific meaning undocumented | Ask product owner: should region/window honor out_width/out_height? |
| Contaminated external-Pong results (repo-review #1) | 82-83% of episodes are length-1 zeros | Regenerate results under settle fix before citing numbers |
| Leaked OpenCode key (repo-review #2) | In git history; rotation not recorded | Rotate key; log rotation in decisions.md |
| .gitignore tension (repo-review #7) | decisions.md:219-224 records deliberate removal | Policy call: commit .gitignore or document machine-local paths |
| Test-quality defects (ROOT-034) | Green suite doesn't prove correctness | See deep audit §9 for 10 specific test gaps |

---

## 8. Fix Dependencies

**No hard dependencies** between confirmed fixes. soft dependencies:
- ROOT-003 (self-deadlock) blocks ROOT-004 (git misclassification) only in the sense that both touch git.py; can be done in parallel
- ROOT-011 (capture contract) must be decided before ROOT-011 fix; no other dependency
- ROOT-033 (Windows termination) needs a platform contract before any implementation; blocked on investigation
- ROOT-015/016 (PPO defects) may interact with ROOT-028 (GAE truncation) — fix both, test together
- ROOT-020/021 (subprocess deadlock/group leak) share runner.py; fix together

**Recommended independent batches:**
- Batch A: ROOT-001, ROOT-008, ROOT-009, ROOT-010 (no shared files, directly verifiable)
- Batch B: ROOT-002, ROOT-003, ROOT-004, ROOT-005 (AgentOps persistence/git)
- Batch C: ROOT-006, ROOT-007 (GUI, shared gui_controller.py)
- Batch D: ROOT-013, ROOT-014, ROOT-027, ROOT-028 (UGA training/checkpoint)
- Batch E: ROOT-015..026 (requires source verification first)
- Batch F: ROOT-033 (held for Windows contract)
- Batch G: ROOT-011 (held for contract decision)

---

## 9. Authoritative Fix Queue

| ROOT | Severity | Confidence | Affected files | Minimal fix scope | Regression tests | Dependencies | Risk |
|---|---|---|---|---|---|---|---|
| ROOT-003 | CRITICAL | CONFIRMED | git.py | .gitignore or worktree relocation | Fresh repo → create → merge must not raise | None | Low — worktree relocation changes UX |
| ROOT-002 | CRITICAL | CONFIRMED | cli.py, state.py, execution_model.py | Share assert_workflow_ready | Custom workflow no review → not ready | None | Low — standard path unchanged |
| ROOT-015 | CRITICAL | needs verification | ppo.py | Subtract entropy term | High-entropy dist yields lower loss | None | Training behavior change; verify numerically |
| ROOT-016 | CRITICAL | needs verification | ppo.py | Separate truncated mask in GAE | Mid-rollout truncation bootstraps V | None | Training convergence; test with truncation |
| ROOT-001 | HIGH | CONFIRMED | workflow.py | redact_text() on stdout/stderr | Secret in agent output not in task.result | None | UI may read task.result; preserve diagnostics |
| ROOT-006 | HIGH | CONFIRMED | gui_controller.py | Mint new Event per operation | Concurrent start → _active stays True | None | UI event ordering |
| ROOT-009 | HIGH | CONFIRMED | external_experiment.py | try/except around launch_phase2 | attach fail → proc terminated | None | Process cleanup timing |
| ROOT-013 | HIGH | CONFIRMED | ppo.py | Atomic save + RNG state | Kill mid-save → no truncated file | None | Checkpoint format change |
| ROOT-017 | HIGH | needs verification | model.py | Mask h_t by (1-done) | Episode boundary → no state leak | None | Recurrent update correctness |
| ROOT-018 | HIGH | needs verification | curiosity.py | Detach features | ICM loss doesn't corrupt shared params | None | Curiosity objective change |
| ROOT-019 | HIGH | needs verification | git.py, finalize.py | Mandate worktree_path | abort_merge on root → error | None | Must keep worktree_path always provided |
| ROOT-020 | HIGH | needs verification | runner.py | communicate() or async drain | Large output → no hang | None | Timeout behavior change |
| ROOT-021 | HIGH | needs verification | runner.py | setsid / CREATE_NEW_PROCESS_GROUP | Cancel → children killed | ROOT-020 (shared file) | Signal propagation |
| ROOT-027 | HIGH | CONFIRMED | external_experiment.py | Cleanup + results JSON on attach fail | attach fail → proc dead, results written | None | Results JSON format |
| ROOT-028 | HIGH | CONFIRMED | ppo.py | Pass dones buffer to compute_gae | Mid-rollout truncation → correct V | None | GAE numerical change |
| ROOT-004 | MEDIUM | CONFIRMED | finalize.py, failure.py | Wire FailureClassifier | Each GitError → correct category | ROOT-003 (shared git.py) | Misclassification text |
| ROOT-005 | MEDIUM | CONFIRMED | workflow.py | logger.exception in bare excepts | Unexpected failure → visible log | None | Logging noise |
| ROOT-007 | MEDIUM | CONFIRMED | gui_controller.py | Forward workflow_id to all 3 calls | Scoped recovery → other workflow untouched | None | None — latent, no callers |
| ROOT-008 | MEDIUM | CONFIRMED | ppo.py | Validate checkpoint_every_updates | Zero/negative/positive intervals | None | Zero behavior choice (reject vs disable) |
| ROOT-010 | MEDIUM | CONFIRMED | external_experiment.py | Close temp env in finally | Env closed on success and load fail | None | None |
| ROOT-011 | MEDIUM | LIKELY | external_game.py | Decide + document per-mode contract | Test via factory per mode | None | Resizing may degrade detection |
| ROOT-012 | LOW | CONFIRMED | verification_kernel.py | try/except + finalize FAILED | Check-creation fail → run not RUNNING | None | DB failure may prevent cleanup |
| ROOT-014 | MEDIUM | CONFIRMED | main.py, experiment.py | Guard empty history; warn | Resume finished → no crash | None | None |
| ROOT-022 | MEDIUM | needs verification | persistence.py | Propagate SQLite errors | Write fail → exception, not silent | None | Policy enforcement scope |
| ROOT-023 | MEDIUM | needs verification | routing.py | Preferred-agent sort key | Equal scores → preferred first | None | Routing behavior change |
| ROOT-024 | MEDIUM | needs verification | preprocessing.py | float32 cast before division | Frame range [0,1] not [0,0,1] | None | Preprocessing pipeline |
| ROOT-025 | MEDIUM | needs verification | win32_capture.py | finally DeleteObject/ReleaseDC | Exception → no GDI leak | None | Win32 resource management |
| ROOT-026 | MEDIUM | needs verification | games/__init__.py | Add empty file | Non-editable install → import works | None | Package structure |
| ROOT-032 | MEDIUM | CONFIRMED | git.py | UTF-8 decoding + errors=replace | Non-ASCII branch → no crash | None | Decoding behavior |
| ROOT-033 | MEDIUM | SUSPECTED | runner.py | Define Windows termination contract | Known termination signal → TERMINATED | None | High-bit heuristic unsafe |
| ROOT-034 (cluster) | HIGH/LOW | CONFIRMED | tests/ | 10 test-quality fixes | Each test-quality defect | None | Test suite reliability |

---

## 10. Coverage Gaps

1. **ROOT-015/016/017/018 (geminihandoff PPO defects) not verified against current tree.** The deep audit's 44 bugs do not include these; geminihandoff is the only source. Must read current ppo.py, model.py, curiosity.py before trusting. If real, they are the highest-impact findings in the corpus.

2. **ROOT-019..026 (geminihandoff AgentOps defects) not in deep audit.** Same issue — only source is geminihandoff. Needs source verification.

3. **Contaminated external-Pong results** (repo-review #1) — 82-83% of episodes are length-1 zeros; cited weights may be invalid. Not a code defect per se, but a measurement-integrity gap.

4. **Leaked API key in git history** (repo-review #2) — rotation not recorded. Security action, not a code bug.

5. **Test-quality defects** (deep audit BUG-DBG-06..15) — green suite doesn't prove correctness; 66 UGA tests unreachable under direct execution; 4 tests write 2.2 MB into CWD; no real-object process-group test on Windows.

6. **No CI** — both products Windows-constrained; no automated execution; no secret scanning; "write regression test before fixing" rule has no enforcement.

7. **Instruction drift** — .agents/AGENTS.md and after-task.md stale relative to current tree; agents may read wrong test commands.

8. **Root .gitignore tension** — decisions.md records deliberate removal; current state is machine-local .git/info/exclude; fresh clones lose all ignore rules.

9. **Windows termination semantics** (ROOT-033) — no reliable signal identified; do not implement high-bit heuristic.

10. **Capture-size contract** (ROOT-011) — mode-specific meaning of out_width/out_height undocumented; window native frames may be intentional.

---

*End of synthesis. This document is analysis only — no code changes.*
