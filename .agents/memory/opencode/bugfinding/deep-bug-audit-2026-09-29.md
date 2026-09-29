# Deep Bug-Forensics Audit — `angasko-12345/projects-ai`

**Date:** 2026-09-29
**Audited revision:** `a64ac96e8afde76a5145de8912f698068ef8e57b` (branch `main`)
**Registry:** [`.agents/memory/bug-registry.md`](bug-registry.md) — every finding below is keyed to a `BUG-*` ID there.
**Constraints honoured:** no production code modified; no git write commands; all probes in
`C:\Users\admin\AppData\Local\Temp\opencode\*`; no real game input; no display required.

---

## 1. Executive summary

The prior audit corpus is large — roughly **401 recovered records** across ChatGPT, Claude/fcc-claude,
OpenCode, Copilot, Pi, and Gemini sources. After normalization and deduplication, that corpus resolves to
**44 confirmed current bugs**, of which most had *already* been found and correctly reported by earlier
reviewers. The genuine novelty of this round is small and specific: **3 new AgentOps defects, 1 new
numeric-correctness defect in UGA, 10 test-quality defects, 1 explicitly retracted claim, and 1 mechanism
correction to a prior finding.**

Three things dominate the result.

**1. The corpus was mostly right, and mostly already known.** Of 60 fully-detailed historical claims
adjudicated (`AOP-01..25`, `UGA-01..35`), **exactly one was a false positive** (`UGA-13`), and only
`UGA-12`/`UGA-13` were stale. Reviewer disagreement was rare and mostly about *severity* or *mechanism*,
not about whether a bug existed. That is an unusually healthy record and it is worth saying plainly.

**2. The single most severe finding is a self-inflicted deadlock** — `BUG-AOP-01` (CRITICAL).
AgentOps writes its worktrees into `<target-repo>/.agentops/worktrees/…` (`git.py:100-101`), installs no
ignore rule anywhere in the package, and then `merge()` refuses to merge whenever porcelain output is
non-empty (`git.py:239-243`). On any repository AgentOps has not previously touched, **its own state
makes the repository dirty and therefore blocks its own merge.** The primary happy path fails on every
fresh target repo. No reviewer had flagged this as the top risk.

**3. The two green suites do not support the products' central safety claims.** The prior round
mutation-tested the review gate and found that replacing `cli.py:394` with `ready = True` leaves the
suite at 382 OK — **zero tests detect the removal of the review gate.** A separate audit found
**66 of 132 UGA tests are unreachable** when a file is run directly, and **4 tests write a 2.2 MB
checkpoint into the caller's working directory**. The gates that matter — review before ship, secrets
before disk, process death before return — have the weakest assertions and the fewest real-object tests.

A necessary correction: this round **retracts** a claim from its own previous round. The earlier report
stated that raw agent stdout reaches the unredacted event sink via `workflow.py:546`. Reading the source
disproves it — `task.result` is overwritten at `:542`, two lines before the event is emitted, so that
call site passes a generated string. The real, *confirmed* unredacted sink is `workflow.py:563` →
`task.result` persistence, recorded as `BUG-AOP-03`.

---

## 2. Current repository state

```
HEAD:   a64ac96e8afde76a5145de8912f698068ef8e57b
branch: main
```

**Mid-audit revision drift.** The audit began at `567f367` and the tree advanced to `a64ac96` while
lanes were running. The tip commit (`a64ac96 feat(mini-llm): cloud GPU portability`) touches only
`small-projects/mini-llm/`, `tasks/task.md`, and `.agents/memory/decisions.md`. Verified:

```
git diff --stat 567f367 HEAD -- agentops universal-game-agent   →   (empty)
```

**Both products are byte-identical across the drift**, so every finding established at `567f367` — the
entire prior round included — remains valid at `a64ac96`. This is worth stating explicitly because the
ledger's own prior history (`lessons.md:362-367`) records a session ruined by exactly this hazard.

`git status` at audit start (before any probe):

```
 D .agents/memory/opencode/repo-review-2026-09-26.md
 M small-projects/mini-llm/data/raw/train.txt
?? .agents/memory/opencode/temp-readit/
```

Two of these are external to the audit: the deletion is a prior-session file move (the review now lives
only under `temp-readit/`), and the `mini-llm` modification belongs to a concurrent writer, not to this
audit. This audit never invoked `mini-llm`.

**Working tree at completion** is reported in §15.

---

## 3. Validation performed

| Check | Result |
|---|---|
| `agentops/`: `python -m unittest discover -s tests` | **382 tests, OK (4 skipped)** |
| `universal-game-agent/`: `python -m unittest discover -s tests` (CWD = scratch) | **278 tests, OK** |
| `python -m agentops agents` | 6 ONLINE, `claude` DISABLED, `antigravity` OFFLINE |
| `python -m agentops status` | `No persisted workflows.` |
| `python -m agentops logs` | no output, exit 0 |
| `python main.py smoke-test` | `smoke-test OK`; `obs=(4,84,84)`; `ppo: pg=-0.0000 vf=0.0005 ent=1.0986` |
| `python -m environment.toy_pong --episodes 5 --seed 0` | 5 × `steps=500 total_reward=12.0 truncated` — bit-stable |
| `python -m environment.preprocessing --episodes 3 --seed 0` | 3 × `decisions=250 range=[0.00,1.00]` — bit-stable |
| `python -m agent.model --batch-size 4 --steps 3` | `(4,4,84,84) → logits (4,3) value (4,) hidden (1,4,128)`, 2.9–7.4 ms/step |
| Real throwaway Git repos (non-ASCII path/branch, dirty, detached, conflict, retry-merge) | `BUG-AOP-01` reproduced |
| Real temp SQLite DBs (persistence failure injection, legacy schema) | `BUG-AOP-09`, `BUG-UGA-20/21/16/17` reproduced |
| Fake-backed process probes (attach timeout, give-up) | `BUG-UGA-22` reproduced |
| Mutation test (review gate removed) | 382 OK — review gate untested |
| `git check-ignore` A/B probes | `BUG-UGA-33`, `BUG-UGA-35` confirmed |

All 4 AgentOps skips are environmental (3 POSIX-permission/symlink, 1 POSIX process-group). **The
process-group skip means `BUG-DBG-08`'s coverage hole is widest precisely on the product's own target.**

---

## 4. Confirmed current bugs

44 current. Full entries in the registry.

### CRITICAL (1)

- **`BUG-AOP-01`** — AgentOps' own `.agentops/` makes a fresh target repo dirty and trips its own merge guard. Confirmed by probe: fresh repo → porcelain `''` → after `create()` → `'?? .agentops/'` → `merge()` raises. No `.gitignore`/`info/exclude` write exists in the package. Self-deadlock on the primary happy path.

### HIGH (9)

- **`BUG-AOP-02`** — Custom-DAG CLI reports READY and merges with **no review task** (`cli.py:394`). Mutation-proven invisible to the suite.
- **`BUG-AOP-03`** — Raw agent stdout/stderr persisted to SQLite unredacted (`workflow.py:563`). Reproduced: `token=sk-…` read back verbatim.
- **`BUG-AOP-08`** — GUI `recover_interrupted` recovers runs across **all** workflows (`gui_controller.py:579-583`) while only `recover_tasks` is scoped. A GUI regression against an already-fixed engine defect.
- **`BUG-UGA-15`** — `save_checkpoint` is a non-atomic `torch.save` onto the only copy of external weights (`ppo.py:290-303`).
- **`BUG-UGA-22`** — External attach failure leaks the game process *and* its log handle and writes **no results JSON** (`external_experiment.py:267-271`).
- **`BUG-DBG-05`** *(new)* — GAE bootstraps a **mid-rollout truncation** from the next episode's reset observation (`ppo.py:78` + `:225-226`). See §8.
- **`BUG-DBG-06`** *(new)* — 66 of 132 UGA tests unreachable under direct execution.
- **`BUG-DBG-07`** *(new)* — 4 UGA tests write 2.2 MB into the caller's CWD.
- **`BUG-DBG-08`** *(new)* — Process-tree termination has no real-object test on any platform.

### MEDIUM (18)

`BUG-AOP-07`, `BUG-AOP-09`, `BUG-AOP-13`, `BUG-DBG-01`, `BUG-DBG-02`, `BUG-DBG-03`, `BUG-DBG-09`,
`BUG-DBG-10`, `BUG-DBG-11`, `BUG-DBG-15`, `BUG-UGA-01`, `BUG-UGA-02`, `BUG-UGA-03`, `BUG-UGA-14`,
`BUG-UGA-17`, `BUG-UGA-18`, `BUG-UGA-20`, `BUG-UGA-21`.

### LOW (16)

`BUG-AOP-10`, `BUG-AOP-11`, `BUG-AOP-17`, `BUG-AOP-23`, `BUG-DBG-12`, `BUG-DBG-13`, `BUG-DBG-14`,
`BUG-UGA-06`, `BUG-UGA-09`, `BUG-UGA-16`, `BUG-UGA-19`, `BUG-UGA-23`, `BUG-UGA-25`, `BUG-UGA-26`,
`BUG-UGA-33`, `BUG-UGA-35`.

### Adjudicated non-bugs

Not filed as bugs because the evidence says otherwise: `AOP-04`, `AOP-14`, `AOP-15`, `AOP-16`,
`AOP-18`, `AOP-19`, `AOP-21`, `AOP-22`, `AOP-24`, `AOP-12`, `AOP-20`, `UGA-04`, `UGA-05`, `UGA-07`,
`UGA-08`, `UGA-10`, `UGA-11`, `UGA-13`, `UGA-27`–`UGA-32`, `UGA-34`. Rationale per finding in the registry.

---

## 5. Historical confirmed/fixed bugs

**26 historical bugs were re-validated, not assumed.** Results:

- **19 CONFIRMED_FIXED** — fix present, live, and covered by a test that would fail on regression.
  Includes `BUG-AOP-05` (swallowed write certifying a `passed` report — the highest-value historical
  fix in the ledger), `BUG-FIX-11` (git-diff tri-state), `BUG-FIX-16` (`killpg` group ownership),
  `BUG-FIX-17` (thread leak), `BUG-FIX-18` (routing recorded selection not execution).
- **5 PARTIALLY_FIXED** — code is correct, protection is not:
  - `BUG-FIX-22` (CLI `UnicodeEncodeError`) — **no test references `_print_text` at all.**
  - `BUG-FIX-23` (WAL `SQLITE_LOCKED`) — test is probabilistic (~25 % flake rate), no direct assertion.
  - `BUG-FIX-20` (evidence secrets) — executable-only and scrubbing tested; the 500-char peek cap never asserted.
  - `BUG-FIX-06` (migration repair loop) — counting tested, the repair loop itself untested.
  - `BUG-FIX-04` (optional failures) — validator fixed, **producer still disagrees** → `BUG-DBG-01`.
- **1 REGRESSED** — `BUG-FIX-24` (workflow-scoped recovery): fixed in the engine, **bypassed by the GUI
  entry point** → `BUG-AOP-08`.
- **2 CONFIRMED_FIXED but unreachable** — `BUG-FIX-25` (recovery idempotency) and `BUG-FIX-26`
  (cancellation repair budget) are correctly fixed and correctly tested, but
  `WorkflowEngine.recover_incomplete` and `FailureClassifier.plan_repair` have **no in-product callers**
  (the CLI `recover` command calls `state.recover_all()` directly). Correct, tested, and dead.

Fixed bugs are preserved in the registry, not erased, per §11 of the audit brief.

---

## 6. Rejected / false-positive findings

This is the section that matters most for trusting the ledger. **Eight claims did not survive.**

| Claim | Adjudication |
|---|---|
| `UGA-13` — "smoke test never exercises the configured env" | **FALSE_POSITIVE.** `main.py:113,133,136` all use the configured env; the second `ToyPongEnv` at `:122` feeds only a print block. An external config cannot yield a green smoke test. |
| `UGA-11` — "VK mappings may mismatch" | **Not a bug.** YAML `37/39` ≡ `0x25/0x27` in all three definitions. Drift risk only. |
| `AOP-09` as worded — "`UnicodeDecodeError` escapes" | **Mechanism corrected.** The error is raised in `subprocess._readerthread` and *swallowed*; `_run` returns `stdout=None, returncode=0`. Still a defect, but silent corruption, not an escaping exception. |
| "Merge dirty-tree guard is missing" (this session's own earlier round) | **DISPROVEN.** `git.py:239-243` refuses on any porcelain output; `:244-249` also guards branch and base-commit. Recorded as `BUG-DBG-11`, a coverage gap, not a live bug. |
| "Raw agent stdout reaches the unredacted event sink via `workflow.py:546`" (this session's own earlier round) | **RETRACTED.** `:542` overwrites `task.result` two lines before the event. The confirmed unredacted sink is `workflow.py:563` (`BUG-AOP-03`). |
| `AOP-24` — "validator call sites are redundant" | **Redundancy, not a bug.** Re-checking booleans is harmless. |
| `AOP-19/21/22` — module size, typing, dependency arrows | **Architecture debt.** No demonstrated runtime consequence. |
| Copilot snapshot packaging claims; regex sweep; `_operation_lock` claim | **False positives**, correctly rejected previously and still invalid (harness artifact; self-matching replacement text; the lock belongs to `AgentOpsController`). |

**Disagreement pattern.** Reviewers did not really disagree about bugs. They disagreed about *severity*
and *mechanism* — e.g. on `AOP-01` (one reviewer P0-as-infrastructure, this audit CRITICAL-as-deadlock) and
`AOP-07` (one reviewer "policy is merely descriptive", this audit MEDIUM with a bounded, proven impact).
Code evidence resolved each case; §6 of the registry records the losing claim and why.

---

## 7. Duplicate analysis

Ten clusters collapse to single underlying defects. The most important for future reviewers:

- **Review-gate bypass** — "custom DAG can merge without review", "CLI readiness ignores review",
  "user workflows can finalize without verification", `AOP-02`. **One** bug: `cli.py:394` omits the
  review term. Three phrasings, one defect.
- **Recovery scoping** — `AOP-08` and `BUG-FIX-24` are one defect: the GUI passes no `workflow_id`.
- **Copilot Phase-3 batch** — `BUG-FIX-24/25/26` are one review batch over one dead seam; 24 regressed
  via the GUI, 25 and 26 are fixed but unreachable.
- **Unredacted persistence** — `AOP-03` (redaction boundary absent) and `AOP-07` (fail-closed policy
  unenforced) are **two distinct defects** that reviewers repeatedly merged into one. Keeping them split
  matters: only one is a live secret exposure.
- **Config parsed but not applied** — `UGA-06` and `UGA-23` are the same shape in two places.
- **Resume reproducibility** — `UGA-16/17/18` are one cluster: resume can neither detect nor reproduce
  a changed run.

---

## 8. New findings

Genuinely new this round, after the historical corpus was reconciled.

1. **`BUG-DBG-05` (HIGH, UGA) — GAE mid-rollout truncation bleed.** `compute_gae` computes
   `next_v = values[t+1] if t+1 < len(values) else next_value` (`ppo.py:78`) and receives **only**
   `buf["terminated"]` (`:225-226`). The buffer *does* record truncations in `buf["dones"]` (`:150`) but
   that is never passed in, so a truncation at `t < T-1` bootstraps from `V(post-reset obs)` — the next
   episode's value. The buffer-**final** case is handled correctly (`:204-211`), the critic loss correctly
   excludes the reset frame (`:171`), and curiosity correctly uses `~dones` (`:282`); the bleed is
   confined to GAE. **Why it matters here:** the toy environment truncates *every* episode at the step
   budget, so truncation is the common case, and mid-episode boundary truncation is not rare. No test
   constructs a mid-rollout truncation, and none compares GAE against a reference. Confirmed by
   source-level proof; numeric magnitude not recomputed this round (§12).
2. **`BUG-DBG-01` (MEDIUM, AgentOps) — producer/validator divergence.** The optional-failure fix landed
   in the validator (`execution_model.py:146-152`) but not the producer: in `FAIL_FAST` with an *optional*
   check failing first, the kernel emits `PASSED` with `passed_checks == 0`, which
   `assert_report_consistent` then rejects → `StateTransitionError` at `workflow.py:569-573`. Fails safe
   (abort, not fabrication) but aborts a healthy workflow — the same landmine class the ledger's A1R entry
   was written to eliminate.
3. **`BUG-DBG-02` (MEDIUM, AgentOps) — unclassified evidence write.** `verification_kernel.py:585-586`
   wraps `LogManager.write_run_artifacts` in `except Exception: stdout_path = stderr_path = None` with no
   policy row, no `Degradation`, and no test. Cannot fabricate a `PASSED` claim (terminal state is still
   persisted at `:529-546`), but it silently destroys the durable evidence pointer and contradicts the
   declared A8 policy at `persistence.py:58-64`.
4. **`BUG-DBG-03` (MEDIUM, AgentOps) — provenance-write bypass.** A second unguarded worktree-provenance
   write survives at `gui_controller.py:728-730`, outside the centralized
   `finalize.record_worktree_provenance` whose A6 fix claimed a single call site. Medium confidence: the
   line is reported by the revalidation lane, not independently re-read by the orchestrator.
5. **`BUG-DBG-06` … `BUG-DBG-15`** — ten test-quality defects, detailed in §9.

**Mechanism correction to a prior finding** (not new, but materially changed): `BUG-AOP-09` — see §6.

---

## 9. Test-quality findings

A green suite is not evidence of quality. For each: *what production bug could exist while this test still
passes?*

| ID | Sev | Question answered |
|---|---|---|
| `BUG-DBG-06` | HIGH | A whole PPO/external test class can be deleted and `python tests/test_ppo.py` still shows 8/20 green. 66 tests are unreachable outside `discover`. |
| `BUG-DBG-07` | HIGH | The suite can pollute the developer's working tree and stay clean in Git, because the artifact is gitignored. |
| `BUG-DBG-08` | HIGH | The child process can be orphaned on Windows and every test still passes — the only real-process-group test is POSIX-only and skipped on the target platform. |
| `BUG-DBG-09` | MEDIUM | Secrets can flow straight into the event timeline and the test still passes, because its fixture contains no secret. |
| `BUG-DBG-10` | MEDIUM | A game process can leak and a results file can go missing and every test still passes — the driver is never called. |
| `BUG-DBG-11` | MEDIUM | A merge can silently do the wrong thing and every test still passes — no test produces a real conflict, so all four refusal causes are indistinguishable. |
| `BUG-DBG-15` | MEDIUM | Migration 8 can break two suites with a confusing assertion instead of a clear failure. |
| `BUG-DBG-12` | LOW | `should_retry_attempt` could return `True` unconditionally and the "cancellation budget" test would still pass. |
| `BUG-DBG-13` | LOW | The wrong agent could execute and every routing test would still pass, because all roles map to one `"fallback"` profile. |
| `BUG-DBG-14` | LOW | The test only passes from the documented CWD, so it encodes an accident of invocation. |

**Independently confirmed strong coverage** (so the ledger is not one-sided): `redact_text` is genuinely
well protected — 5 tests kill an identity mutant. Recovery scoping *and* idempotence are covered
(`test_failure_kernel.py:388-419`). GUI generation tokens are covered by a real `tk.Tk()` test
(`test_gui.py:301-309`). UGA input isolation is genuinely headless-safe: `RecordingBackend` /
`SyntheticBackend` / `FakeInterface` throughout, and the one real-Tk test skips on `returncode == 2 and
b"no display"`. Both suites are disciplined about *not* doing the wrong thing; the failure mode is that
the safety-critical assertions are the weakest ones.

**Baseline drift.** Observed 382 / 278. `agentops/AGENTS.md` records 357; `.agents/AGENTS.md` records 357
and 261; `tasks/after-task.md` records 224. `lessons.md:355-360` documents these as volatile observations
that rotted within hours. `GOV-03` tracks this.

---

## 10. Security / reliability findings

**Security**
- `BUG-AOP-03` (HIGH) is the only **confirmed** secret-exposure defect: agent stdout/stderr reaches
  `state.sqlite` unredacted. Agents routinely echo config and environment values. The database is durable
  and unencrypted, and `agentops/AGENTS.md` asserts the opposite.
- `BUG-DBG-09` (MEDIUM) is a **latent** boundary gap: the event sink has no redaction, but no live
  untrusted path to it is confirmed (see the retraction in §6).
- `BUG-DBG-02`/`BUG-DBG-03` (MEDIUM) are unclassified *write* losses, not disclosures.
- Held up well: allowlisted commands only; explicit argv, never a shell; reduced child environment;
  shared `CREATE_NO_WINDOW` rather than ad-hoc flags; prompt hashing; `ARTIFACT_SCHEMA_VERSION` and
  schema-tolerant event decoding; FK-free crash-evidence tables so recovery can record what never
  persisted. Out of scope but flagged: the prior corpus records a plaintext API key in git history
  requiring rotation, and possible secrets in historical `failure.evidence` rows (`copilot-26`).
- `BUG-AOP-09` is availability, not disclosure: a non-ASCII branch or path corrupts merge/cleanup
  decisions or raises a misleading `AttributeError`.

**Reliability**
- **Process lifecycle is the weakest link.** `BUG-UGA-22` is a confirmed process + handle leak with a
  cascade: a leaked window poisons later runs via the stale-title guard at `external_experiment.py:244`.
  `BUG-DBG-08` is zero real-object coverage on the target platform. `runtime.py:198` guards tree
  termination on `returncode` and does not verify `taskkill`'s own exit status.
- **Checkpointing.** `BUG-UGA-15` (non-atomic) + `BUG-UGA-20` (empty-history `IndexError`) +
  `BUG-UGA-21` (late `ZeroDivisionError`) + `BUG-UGA-16/17/18` (resume is neither versioned, reproducible,
  nor config-checked) form a five-part reliability cluster around the single most important artefact in
  the product.
- **Cross-workflow blast radius.** `BUG-AOP-08` is the only confirmed instance of the "action is wider
  than the user believes" class, and it is latent (`recover_interrupted` has zero callers).
- Performance is a non-issue at documented scales (2.9–7.4 ms/step; toy runs bit-stable across seeds).

---

## 11. Cross-component failure paths

Where a *single* local failure becomes a system-level one.

1. **State inside the guarded repository → self-deadlock.** `BUG-AOP-01`: worktree creation
   (`git.py:100-101`) writes into the target repo; `merge()` (`git.py:239-243`) reads that same repo's
   porcelain; the two components contradict each other. No test spans them.
2. **Unclassified write + declaration of safety → latent forgery.** `BUG-DBG-02` +
   `BUG-DBG-03` + `BUG-AOP-07`: a policy table asserts fail-closed enforcement at three sites that do not
   enforce it. The system is currently safe only because the uncovered paths happen to raise instead of
   swallow. That is an accident, not a control.
3. **Untested orchestrator + failure path = silent resource loss.** `BUG-DBG-10` × `BUG-UGA-22`: the
   leak lives precisely in the state machine with zero coverage, and a leaked window then cascades into
   every subsequent run through the title guard.
4. **Resume across a changed world.** `BUG-UGA-18` (env config not compared) × `BUG-UGA-17` (no RNG) ×
   `BUG-UGA-19` (inflated FPS) × `BUG-UGA-01` (contaminated tracked artifacts): a resumed run is compared
   against a baseline produced by different code, with different randomness, under a reported throughput
   number that is wrong. Individually LOW/MEDIUM; together they make every resume-based comparison in this
   product uninterpretable.
5. **GUI and engine diverging on the same fix.** `BUG-AOP-08`: a defect fixed in the engine seam is live
   again in the GUI entry point. The engine has a regression test; the GUI has none. Two code paths, one
   invariant, no shared test.
6. **Test execution as a write.** `BUG-DBG-07` + `BUG-DBG-06`: the documented validation command mutates
   the working tree *and* silently under-tests. A developer following the documented procedure gets both a
   dirty tree and false confidence.

---

## 12. Unverified hypotheses

Explicitly **not** promoted to confirmed.

| Hypothesis | Missing evidence |
|---|---|
| Numeric magnitude of `BUG-DBG-05` | Control flow proven by source read; the delta versus a correct GAE was not recomputed this round. |
| Residual impact bound of `BUG-DBG-02` | Impact *reasoned* to be bounded because `_finish_check_persisted` still writes terminal state; not measured end-to-end under a forced `OSError`. |
| `BUG-DBG-03` at `gui_controller.py:728-730` | Reported by the revalidation lane with a file:line; not independently re-read by the orchestrator. |
| Windows grandchild process leak in `runtime.py` | No grandchild-spawn probe was run. The *code path* is confirmed (`BUG-DBG-08` coverage hole); an actual leak is not demonstrated. |
| Extern-Pong detector signal margin (`UGA-32`) | Requires a live window and production-frame distributions — prohibited by audit rules. |
| Live `reset(seed=)` propagation (`BUG-UGA-02`) | Proven by source (the seed is never read); not proven against a spawned process. |
| API key in git history / secrets in historical `failure.evidence` | Historical, out of scope for a read-only code audit. Flagged, not investigated. |
| Attribution of the ≈106-record Claude handoff | Inferred, not proven. Treat author identity as a hypothesis. |

---

## 13. Recommended verification work

Cheapest highest-value next steps, in order.

1. **Quantify `BUG-DBG-05`.** Write a reference GAE and diff it against `compute_gae` on a rollout with a
   mid-episode truncation. One function, one test, settles HIGH-vs-LOW and proves or refutes the single
   most consequential numeric finding.
2. **Span-test `BUG-AOP-01`.** One real throwaway repo: `create()` → assert porcelain still clean →
   `merge()`. Fails today; becomes the regression test.
3. **Test the gates, not the functions.** For each of `BUG-AOP-02` and `BUG-DBG-11`, mutate production
   code in a scratch copy and require the suite to fail. A test that cannot fail is not coverage.
4. **Prove or refute a Windows process-tree leak.** Spawn a real grandchild, cancel, check `tasklist`
   before/after. This converts `BUG-DBG-08` from a coverage claim into a defect claim or a clean bill.
5. **Close the durability cluster.** `BUG-UGA-15` (atomic write) is a one-function change with the
   largest blast radius in the product.
6. **Re-validate the 6 unprotected historical fixes** (`BUG-FIX-04` producer, `-06` repair loop,
   `-20` peek cap, `-22` `_print_text` entirely, `-23` probabilistically, `-08` retry validation).
   Correct code plus a missing test is one refactor away from a silent regression.
7. **Un-truncate the UGA test files.** Move `unittest.main()` to the end of the 7 files; add a guard.
   Purely mechanical, and it is what keeps `BUG-DBG-05`-class defects invisible.
8. **Reconcile the recorded test baselines** (`GOV-03`) or delete the numbers and point at the command.

---

## 14. Highest-risk invariant failures

Ranked by consequence × likelihood × invisibility.

1. **"Work must not ship without review" — broken on the custom-DAG path.** `BUG-AOP-02`. The single
   most important product promise, defeated on a user-driven path, and **mutation-invisible**: removing
   the gate entirely leaves the suite green.
2. **"AgentOps state must not corrupt the repository it manages" — broken.** `BUG-AOP-01`. The primary
   workflow deadlocks on every fresh target repo.
3. **"Cancelled work must release every resource" — broken.** `BUG-UGA-22`, with a cascade through the
   stale-title guard and **zero** test coverage of the driver (`BUG-DBG-10`).
4. **"Secrets must not cross the persistence boundary" — broken for task results.** `BUG-AOP-03`.
5. **"Training data must be trustworthy" — broken.** `BUG-DBG-05` (biased advantages at every truncation
   boundary), `BUG-UGA-01` (contaminated tracked artifacts), `BUG-UGA-18` (resume ignores a changed env
   config), `BUG-UGA-17` (resume not reproducible).
6. **"Recovery must be scoped" — broken in the GUI.** `BUG-AOP-08`. One workflow's recovery mutates all.
7. **"Verification state must agree with persisted evidence" — mostly upheld, with one soft spot.**
   `BUG-DBG-01` (producer/validator divergence, fails safe) and `BUG-DBG-02` (unclassified evidence
   write, bounded). The core invariant held up well — this is the ledger's strongest result.

---

## 15. Final assessment — what remains genuinely broken

**Both products are in materially better shape than their own memory files suggest, and their green
suites are real.** 382 + 278 tests pass, all seven documented smoke and CLI checks pass, and toy runs are
bit-stable across seeds. **19 historical bugs are genuinely, currently, testably fixed** — including the
subtle ones (`killpg` group ownership, the thread leak, git-diff tri-state, routing-recorded-selection).
The redaction helper is well protected. Recovery scoping and idempotence are covered. GUI generation
tokens are covered by a real Tk test. UGA input isolation is genuinely headless-safe.

**The dangerous pattern is that the green result is weakest exactly where the central promises live.**
One CRITICAL and eight HIGH findings are all invisible to a full passing run. The custom-DAG path will
merge and delete a worktree for a workflow that never had a review task, and replacing the readiness
expression with `True` changes nothing observable in the suite. AgentOps blocks its own merge on every
fresh repository. The external orchestrator leaks a game window and a file handle, writes no results, and
poisons later runs — with the entire driver untested. Two thirds of the UGA external/PPO/eval test surface
does not execute when a developer runs the file directly, and the suite writes into whatever directory it
is invoked from. GAE bootstraps a mid-rollout truncation from the next episode's reset observation, in
the one code path whose numbers matter most, with no test that compares against a reference.

**The prior review corpus deserves credit and one caution.** Of 60 fully-detailed historical claims,
exactly one was a false positive and only two were stale. That is a strong record. The caution is
mechanism: reviewers were right that a bug existed and wrong about *how* in at least two cases
(`AOP-09`, and this audit's own retracted event-sink claim), and a "fixed" label concealed six fixes with
no real regression test and two correct-but-unreachable code paths. Code evidence, not the ledger's own
confidence, is what separated them.

**What I would not claim.** That the process-tree guarantee is broken — the coverage hole is proven, the
leak is not. That `BUG-DBG-05` is a large numerical error — the control flow is proven, the magnitude is
not. That the event-timeline redaction gap is exploitable today — no live untrusted path is confirmed. And
that the ≈106-record Claude handoff is authored by Claude — that attribution is inferred.

**Net:** the ledger is trustworthy enough to hand to the next reviewer without starting from zero. Fix
order is §13: one real-repo merge test, one reference-GAE test, one atomic-write fix, and one grandchild
leak probe would retire or bound most of the HIGH set. No fix was applied during this audit, and none
should be applied without the regression tests named in the registry — the repository's own rule, and
the one the current test suite most needs to obey.

---

---

## Addendum — round 2 (external review verified, 2026-09-29b)

An external reviewer produced a second, independent bug report. It was adjudicated on this machine
(Windows) by reproduction, not by reading. Outcome: **16 confirmed, 1 rejected, 2 false positives, and
2 corrections to round-1 entries.** Full entries in `bug-registry.md` §1.10 and §2.

**File relocation.** The user moved the prior-audit corpus out of
`.agents/memory/opencode/temp-readit/` into `.agents/memory/opencode/bugfinding/`. The two occurrences of
`temp-readit/` in §2 above are a quoted `git status` and a description of the audit-time state; they are
left unedited as an accurate record. Current layout:

```
.agents/memory/opencode/bugfinding/
  bug-registry.md                     ← the ledger (moved here from .agents/memory/)
  deep-bug-audit-2026-09-29.md        ← this report
  repo-review-2026-09-26.md, projects-ai-bug-handoff.md,
  projects-ai-review-handoff.md, geminihandoff.md
```

`bug-registry.md` was moved into the same directory so this report's relative link resolves.

### Corrections to round 1

| Entry | Change |
|---|---|
| `BUG-DBG-05` (GAE bleed) | **Severity HIGH → MEDIUM.** An independent reference implementation reproduced the cross-episode coupling, so the defect is confirmed with numbers — but the reviewer's "fires in ~25% of rollouts" claim is **refuted**: 0/120 recorded toy eval episodes reach `max_steps` (max 145), 0/400 for a random policy, and both external runs report `train_truncated_episodes = 0`. The bug has never fired in this repository. It arms itself as training succeeds. The fix also needs an extra model forward pass per boundary — the correct bootstrap value is absent from the buffer, so passing `dones` alone is insufficient. |
| `BUG-AOP-09` (git decode) | Mechanism **re-confirmed** as `stdout=None, returncode=0`, not an escaping exception. Two worse instances added in `agent_run.py:219-227,240-248` → `BUG-DBG-25`. |

### New HIGH findings

- **`BUG-DBG-16`** — every line of agent output is discarded on timeout or cancel. Reproduced: control
  50/50 lines, timeout **0/50**, cancel **0/50**. `asyncio`'s `StreamReader.read(-1)` accumulates into a
  local list that is discarded when the `communicate()` task is cancelled, and the follow-up
  `communicate()` recovers nothing. Timeouts are the most common agent failure, so diagnostics are lost
  exactly when they matter. Survived because `tests/test_runtime.py:95-106` asserts against a fake that
  hardcodes the value under assertion (`BUG-DBG-32`).
- **`BUG-DBG-17`** — the reviewer marked this *probable* for lack of Windows; this machine settled it.
  `shutil.which` returns `opencode.CMD`, `codex.CMD`, `pi.CMD`, `copilot.CMD` — **4 of the 5 installed,
  enabled agents**. `cmd.exe` does not honor Python's `\"` escaping and ends the command line at the first
  newline, so the agent receives only `"You are the implementation agent for AgentOps task 1f0c."` — no
  request, no workspace path, no instructions — **and the shim still exits 0, so the run is recorded as a
  success.** This is a false-success fabrication on the default configuration of this machine, and it is
  the most consequential finding of round 2.

### Round-2 adjusted priorities

1. **`BUG-DBG-17`** — agent prompts are silently truncated and graded as successful. 4 of 5 agents affected.
2. **`BUG-DBG-16`** — diagnostics destroyed on the most common failure path.
3. **`BUG-AOP-01` + `BUG-DBG-18`** — merge blocked in every fresh target repo, then misdiagnosed as a
   conflict. Two independent defects; fixing either alone leaves the repo broken.
4. **`BUG-DBG-20`** — one hook failure wedges the repository for every later workflow.
5. **`BUG-DBG-19`** — a merged success persists as `failed`.
6. `BUG-DBG-05` and the UGA cluster below that.
7. `BUG-DBG-21`, `BUG-DBG-22`, `BUG-DBG-23`, `BUG-DBG-24`.
8. LOW cluster: `BUG-DBG-25` … `BUG-DBG-32`.

**Note on the reviewer's own test count.** Their suite ran 315 tests on Linux with 3 modules failing to
import (no `tkinter`) and 1 failure. That is not comparable to the Windows 382. One of the failures is
`test_posix_terminate_uses_process_group`, which `BUG-DBG-32` shows is genuinely broken rather than
merely skipped — the mocked `killpg` never releases `HangingProcess`. It will fail the first time anyone
runs this suite on Linux.

### What still did not survive

Three claims were rejected outright, and they are recorded rather than quietly dropped: the
`UnicodeDecodeError`-escapes mechanism (wrong — swallowed on a reader thread), `SendInput` with
`wScan=0` (correct per Win32 — `wScan=0` is *required* when `KEYEVENTF_SCANCODE` is unset, and numpad
arrows have distinct VKeys so the `wVk`-only path is unambiguous), and the missing termination-provider
reset (no termination provider holds per-episode state). The `SendInput` and termination-reset items
would both have been filed as real bugs on a plausible-looking reading.

**Standing caveat.** Two of the reviewer's mechanism claims about UGA frequency were wrong in the same
direction — plausible arithmetic, unstated premises. Round 1 caught one of my own claims in exactly that
way (the retracted `workflow.py:546` event-sink claim). Every severity in this ledger is stated with the
measurement behind it for that reason.

---

### Audit footprint

Two files written, both audit artifacts:
- `.agents/memory/bug-registry.md` (new)
- `.agents/memory/opencode/deep-bug-audit-2026-09-29.md` (this file)

No production source, test, config, or memory file was modified. No git write command was issued. All
probes ran in `C:\Users\admin\AppData\Local\Temp\opencode\*`. Final `git status` is reported in the audit
output accompanying this report.
