# Pending Tasks

> Rewritten 2026-10-02 to be the **live task queue**. It contains ONLY unfinished work.
> **Reconciled 2026-10-20 against `main` at `c825a1b`** (same commit as `origin/main` at
> reconciliation time; the evidence file was measured at `ad73f30` and `check.py` is green).
> The live bug ledger is `.agents/memory/opencode/bugfinding/master-bug-synthesis.md`
> section 0; verify a status there before starting anything on this page.
>
> Completed history stays in `.agents/memory/project.md`. The 2026-09-29 audit fix queue
> (master-bug-synthesis.md section 9) is superseded and must not be used as a queue.
>
> Test counts are **not** stated here. The only current measured result per product is
> `.agents/evidence/verification.json` (regenerate with `python tools/evidence/generate.py`,
> verify with `python tools/evidence/check.py`).
>
> **2026-10-21 (scoped update, OpenCode/GLM):** root README truth fixes were applied and
> recorded (completion record in `.agents/memory/project.md`, decision in `decisions.md`,
> lesson in `lessons.md`). Rows on this page were **not** re-verified in that pass — the
> 2026-10-20 reconciliation above still stands as the last full pass. One new open row was
> added under "Repository tooling" below.

## AgentOps -- open

| ID | Item | Evidence |
|---|---|---|
| ✅ 2026-10-25 | ~~ROOT-034 (AgentOps share): `DBG-12` residual test debt~~ — resolved: `tests/test_failure_kernel.py::test_cancellation_bounded` was a stub (asserted `should_retry_attempt` plus `is_set()` on an event it set itself, so it could not fail for the reason its name claims). It is replaced by two engine-level tests pinning the real cancellation consumption path: a set cancel event stops the retry chain before any backoff sleep and before a second agent invocation despite remaining budget (task terminal FAILED, one failure row, exactly one runner call), with a contrast test proving the same budget retries when no cancel event is set. Mutation-proven: disabling the post-run cancellation check in `workflow.py` fails the test on the sleep guard (asleep awaited once instead of zero). With this, every named DBG-06..15 defect is closed and ROOT-034 is FIXED in the ledger §0 | `2a57b0d` on `main`; agentops suite run locally: 903 tests, 4 skipped, OK; result lives in `.agents/evidence/verification.json` |
| -- | `SpawnFactory` Protocol typing (deferred from the A3 review) | still deferred; `runtime.py:49` is still the bare alias `SpawnFactory = Callable[..., Awaitable[Any]]` |
| ✅ 2026-10-23 | ~~Repository characteristics for routing are inert~~ — resolved: `workflow._select_agent` derives bounded deterministic characteristics through a new `agentops/repo_inspection.py` leaf (names-only scan, capped manifests, per-path cache; never opens `.env`/credential files) and passes real flags into `AgentRouter.route` as desired-capability hints; a test-runner signal provably flips one tie-break while explicit preferences and the static fallback still work. Covered by `tests/test_repo_inspection.py` plus new classes in `tests/test_routing.py`. | `.agents/memory/decisions.md` 2026-10-23 entry; agentops suite 876 tests / 4 skipped, OK, exit code 0; **uncommitted** (no authorization), so evidence re-measure is owed at the next commit |
| -- | Control center never observed against a live running workflow | Automated coverage is fixture/offscreen only (`tests/test_control_center.py`: `ControlCenterProjectionTests`, `ControlCenterViewTests`). The real-GUI acceptance rounds did drive live workflows through the real PySide6 window and did press the real Cancel button (Run D `c46b4292`, `running → cancelled`, no merge), but neither record documents the **control-center surface** — the Workflows live stage flow, per-stage durations, live card — being observed while a workflow ran. Owner: the MVP acceptance session; its records are untracked in this tree |
| -- | Issue #4 end-to-end acceptance stays BLOCKED on the disposable environment | Issue #4 is still OPEN with **0 comments**, last updated 2026-10-05. Round 1 (2026-10-17) was PARTIAL: Runs A and B were submitted through the real GUI and both ended terminal-honest-`failed` (two further rows of undetermined provenance in that `state.sqlite` also failed, so the session counted four). Round 2 (2026-10-18) fixed and regression-tested the **two product bugs** the acceptance exposed (`3363471` on `main`, fail-first tests in `tests/test_issue12_gates.py`) and exercised cancellation truthfully, but both post-fix runs (E `60fda4c1`, F `257c4996`) died at **environment-level agent execution** (opencode hung past the 900 s timeout; kilo exits 1 on Windows). The "two consecutive successful E2E workflows" criterion is therefore unproven, not disproven. **2026-10-25 stdin root cause fixed** (`e2f24bb`): `ProcessRuntime.spawn_process` now spawns children with `stdin=asyncio.subprocess.DEVNULL` by default (explicitly overridable), so a non-TTY agent that reads stdin to EOF (`opencode run "<prompt>"`, anomalyco/opencode#52938) can no longer block on the orchestrator's inherited stdin — fail-first regression tests in `tests/test_stdin_spawn_policy.py`. Issue #4 is **not** claimed solved: Runs E/F hung at environment-level execution and that root cause was never confirmed against a live run; the likely mechanism is removed, but the E2E acceptance remains unproven until a real GUI run passes. Unblocks only when the disposable env is rebuilt or the user restarts the chase — no further work in that tree until then |
| -- | Issue #12 security reviewer pass still owed | The copilot read-only snapshot review ran 2026-10-15 and drove 4 fixes (`2415b25`), 2 findings rejected with file/line evidence. The fcc-claude **security** pass never ran (`fcc-server` down, nothing on `127.0.0.1:8082`; opencode probe produced no output). Rerun it from a machine where `fcc-server` is up before treating `security.py`, the secret prescan, and the gate fail-closed paths as reviewed. Adjudication: `.agents/memory/decisions.md` 2026-10-15. Roadmap D8 is the standing policy for this |
| -- | `.git/hooks/post-commit` never runs (UTF-8 BOM, and not executable) | Re-verified 2026-10-20: the file starts `ef bb bf 23 21 2f` and is mode `0644` (`-rw-r--r--`). Git therefore prints `cannot spawn ... post-commit: Exec format error` for some commits and `cannot spawn .git/hooks/post-commit: No such file or directory` for others, and the auto-mirror silently never fires. The manual path works and was used today: `powershell -NoProfile -ExecutionPolicy Bypass -File tools/obsidian-sync/sync-agent-memory.ps1` (121 files, exit 0). To repair: strip the BOM, make it executable, or reinstall via the untracked `tools/obsidian-sync/install-hook.ps1`. Nothing in the repo is damaged by this — the mirror just never fires on its own. Observed again on both 2026-10-22 commits (`6de4c56`, `469305e`) — still unfixed |
| -- | `dist/AgentOps.exe` predates the current AgentOps source | `agentops/dist/AgentOps.exe` is the **Oct 12 18:54** build (git-ignored). No packaging input changed after it (`AgentOps.spec`, `agentops_gui.py`, `scripts/build_windows_exe.py` have no commit since), so D6 owes no rebuild on packaging grounds alone — but agentops source moved a lot afterwards (`2e80160` strict review gates on Oct 13, `2415b25`, `abe0d73`, `3363471`), so the shipped binary does **not** contain the review-verdict gate work. Rebuild when the user takes the D6 decision |

## AgentOps -- held / requires decision

| Item | Why held |
|---|---|
| ROOT-033 -- Windows termination classification | No Windows termination contract exists. Do not implement the high-bit heuristic. Needs an explicit platform decision first |
| D3 -- diff viewer implementation (`git diff` shell-out vs bundled parser) | Gates the C1 diff viewer. Roadmap recommends shell-out first, but no decision is recorded in `decisions.md` |
| D5 follow-up -- GitHub Actions under the local-only repo policy | CI itself shipped at `2cb2413` (5 path-filtered workflows); what remains undecided is whether it should **gate** PRs, which needs a user decision on secrets/auth |
| D6 -- exe rebuild cadence after each Track A milestone vs batched | standing constraint, unresolved; see the stale-exe row above for its current consequence |

## Universal-game-agent -- open

| ID | Item | Evidence |
|---|---|---|
| ROOT-034 (UGA share) | CLOSED — every named defect resolved (DBG-06/07 2026-10-03, DBG-14 DISPROVEN, DBG-12 shared debt closed 2026-10-25) | `DBG-06` and `DBG-07` closed 2026-10-03 in `59f5a1b`; `tests/test_cwd_isolation.py` is the standing DBG-07 guard and is present. The shared DBG-12 test debt was closed 2026-10-25 (`2a57b0d`, mutation-proven cancellation-path tests in `tests/test_failure_kernel.py`), so the last reason this row existed is gone; ledger §0 now records ROOT-034 as FIXED. Nothing is open on the UGA side. See master-bug-synthesis section 0 |
| -- | Stage 1 (ALE) user-PC runs: 10M-step Pong anchor x3 seeds and the Atari pilot x3 seeds (branch `upgrade/uga-stage1-ale`, stacked on PR #31) | Only smoke tests, 10-episode baselines and a 150k-step Pong sanity run (flat: -20.35 rolling, greedy -21.0) fit the 2-vCPU sandbox (PPO ~60-80 steps/s there). Exact PowerShell commands, resume steps and wall-clock estimates: `universal-game-agent/experiments/stage1/RESULTS.md`. No window or SendInput involved |
| -- | STEP 3 exp02 re-run (post-fix validation run) | The pre-flight ran 2026-10-02 and the detector bands hold (0 steps in the ambiguous 200..300 gap, MISS band exact, no window-chrome red above the hit band), so the re-run is unblocked. It has **not** happened: cancelled because the machine was in use, and the exp02 verdict stays suspended. Ask before starting a long GUI run — it sends real `SendInput` keystrokes and holds a real window for three phases. Read `universal-game-agent/AGENTS.md` § "Cadence / reward investigation" first: the synthetic matrix (2026-10-05) showed the reward discriminates (lookahead oracle +0.1 / never dies vs random -1.0 / dies at 5.6 steps), so the live run is the remaining evidence for real-window learnability |
| -- | Live confirmation of per-decision paddle displacement | Source math over the 60 fps loop gives 15-20 px per decision (60 ms hold = 3-4 ticks x 5 px); the synthetic cell verifies 15-20 px against the mirrored tick model, but the real window has never been measured. One short `training.external_smoke` run with position sampling settles it — ask first (real SendInput) |
| -- | Larger-budget cadence comparison | The 16384-step synthetic pilot left every cell at final rolling reward -0.47..-0.52 vs the oracle's +0.1, so no convergence comparison exists yet. Repeat `python -m training.cadence_experiment` with a real budget (e.g. `total_timesteps: 50000`) across the same four cells before concluding anything about relative learnability — synthetic only, no approval needed |

## mini-llm -- open

| ID | Item | Evidence |
|---|---|---|
| -- | Full 1.9 GB TinyStories prep + GPU run | needs a cloud GPU, not this box; procedure is in `docs/EXPERIMENT-tinystories.md` |

## ai-token-tracker -- open

The 2026-10-19 data-pipeline audit is **complete** and merged (`ad73f30`, records in
`.agents/memory/archive/2026-09-15-agent-registry-routing-task-record.md` and
`.agents/outputs/token-tracker-data-audit.md`); only this follow-on is
open.

| ID | Item | Evidence |
|---|---|---|
| -- | OpenRouter provider-side activity import | `GET /api/v1/activity` with `OPENROUTER_API_KEY`; additive collector, idempotent page-through. Fills the real-cost gap: the audited Claude store carries no `costUSD` at all (0/449 usage-bearing lines), so provider-side activity is the only way to recover real cost for that route. Scope and caveats: `.agents/outputs/token-tracker-data-audit.md` |

## tiktok-slop-factory -- open

| ID | Item | Evidence |
|---|---|---|
| -- | Evidence-record follow-ups from granting the product discoverability | (1) The `excluded` block recorded in `.agents/evidence/verification.json` (and `.agents/tmp-tests/*` stale copies) still carries pre-2026-10-22 text; `tools/evidence/evidence.py` `EXCLUSIONS` now records hermeticity (runtime-only exclusion, no credential reason), and the JSON refreshes at the next real `generate.py` run (do not hand-edit measured sections). (2) `check_documents`' `BASELINE_FILES` (7 files) does not audit `tiktok-slop-factory/AGENTS.md` or the root `README.md`; extending it needs its own regression test first, same shape as the "Add the root README" row under Repository tooling. |



| ID | Item | Evidence |
|---|---|---|
| -- | Add the root `README.md` to the evidence checker's document audit | `check_documents` in `tools/evidence/evidence.py` audits exactly the seven files listed in `BASELINE_FILES`; the root README is not one of them, which is how its stale "700+ tests" chip, closed-issue #12 roadmap bullet, and "CI per project" line survived green helpers and green CI until the manual 2026-10-21 truth fix (see `.agents/memory/lessons.md` 2026-10-21). The README now names `.agents/evidence/verification.json`, so adding it to `BASELINE_FILES` would pass today's `check.py` immediately. Needs its own regression test first (assert a count claim in the README fails `check.py`), per the repo regression-test-first rule — do not ship as a doctrine change without it |

## Frozen

Frozen by deliberate decision. **Re-entry requires an explicit user decision.** It is not a
backlog item and must not be picked up as ordinary work.

| ID | Item | Freeze note |
|---|---|---|
| A5 | WorkflowEngine decomposition | FROZEN 2026-10-01. The stated entry condition (close the correctness queue) HAS been satisfied, but the freeze remains by deliberate decision. Rationale + criteria in `.agents/plans/architecture-freeze-a5-a6-a7.md` |
| A6 | First-class `ReviewRun` / `MergeRun` | FROZEN 2026-10-01, same condition and same deliberate decision. No `ReviewRun`/`MergeRun` classes exist; this is a design task first, not a split |
| A7 | StateStore repository split | FROZEN 2026-10-01, same condition and same deliberate decision |

## Not started

| ID | Item | Note |
|---|---|---|
| A9 | Artifact lifecycle + orphan recovery | `artifacts.py` has only the two helpers from the copilot review (`scan_files`, `find_orphans`); no state transitions, no cleanup policy |
| B6 | Policy gates | no `Policy` type or enforcement hook exists |
| B8 | Approvals (human gate, first-class) | no approval table, operation, or event exists |
| B9 | Project memory (per-repo conventions) | not started |
| B10 | REST API + evals | not started; depends on B8 |
| C1 | Remaining C1 items: approval screen, diff viewer (blocked on D3), one-click retry/repair | dashboard, New Task, run timeline, live feed, failure view, and the control center already shipped (`5b80d9b`, `2159af6`, `7a5b472`) |
| C2 | Remaining C2 items: `agentops init`, AgentOps Doctor, task templates | command palette, agent profiles, and verification-profile selection already shipped |
| C3 | Whole phase (visual builder, replay, global search, cost tracking, budgets, analytics, notifications) | not started; needs A5/B10 |

## QR Generator -- open (user action required)

The app work is done and `QR Generator Beta 1.0.0` is published. The product code needs
nothing further. See `.agents/memory/project.md` (2026-10-09, "QR Generator public
repository + zero-budget launch"). The public repo is `angasko-12345/qr-generator`
(working copy `D:/admin/code/qr-generator-public`, local `5ab0655` and **1 commit behind**
`origin/main` at `6ce9619`).

| ID | Item | Evidence |
|---|---|---|
| -- | `LICENSE` carries the Expo/650 Industries boilerplate copyright line | Identical first lines in the parent (`qr-generator/LICENSE`) and public (`qr-generator-public/LICENSE`) copies: `Copyright (c) 2015-present 650 Industries, Inc. (aka Expo)`. Decide the correct copyright line before publishing widely — it is the only license file in the public repository |
| -- | Released APK predates the current code | Beta `beta-1.0.0` was published 2026-10-09 (`gh release list` in the public repo: `QR Generator Beta 1.0.0 (Android)`, Latest, 2026-10-09T12:59:20Z). The SVG export (`e2bbe33`) was ported into the public repo on 2026-10-16 (`be52ea7`), so the shipped APK does not contain it. Rebuild + re-release when the user wants the current code shipped |
| -- | Nothing has been posted; first 10 genuine testers not recruited | `marketing/marketing-checklist.md` in the public repo is **24 unchecked / 0 checked**. Count a tester only when they report an actual scan result. Posting requires reading each community's rules first. Machine-fillable placeholders are gone: the only remaining tokens are `[YOUR_NAME_OR_HANDLE]` and a legend line naming `[LINK_TO_REPO]` (both intentional, `small-business-outreach.md:46,59,60`). Repo has 0 issues |
| -- | 2 open Dependabot advisories for the packed QR app deps (both HIGH) | Re-checked 2026-10-25 via `gh api repos/angasko-12345/projects-ai/dependabot/alerts?state=open`, both in `qr-generator/package-lock.json` (parent repo): `zod` **high** alert #5 (vulnerable `< 4.6.6` — upstream fix exists, so an npm lock refresh in the QR product closes it) and `node-forge` **high** alert #2 (`<= 1.4.0`, still without an upstream fix — monitor only). Since the 2026-10-20 pass: `braces` and `sprintf-js` no longer appear open, and `uuid` has moved to `fixed`. These also fire as the GitHub "2 high" banner on every push of this repo. Fixes ship when the user takes the QR rebuild/re-release decision — the QR product is user-action-gated in this queue. Prior tracking: `.agents/memory/qr-generator-npm-security-2026-10-17.md` |

## Superseded / historical pointer

- `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` section 9 "Authoritative
  Fix Queue" is **SUPERSEDED 2026-10-02**. Read section 0 of that file instead.
  Sections 1-11 remain as historical audit evidence.
- The other reports in `.agents/memory/opencode/bugfinding/` are dated audit evidence
  (`bug-registry.md`, `deep-bug-audit-2026-09-29.md`, `geminihandoff.md`,
  `projects-ai-bug-handoff.md`, `projects-ai-review-handoff.md`,
  `repo-review-2026-09-26.md`). They are not instructions and are never revised in place.
- ROOT-015..ROOT-025 and ROOT-028 are DISPROVEN and are not queue items.
- **B7 routing is done, and its task record now lives in the archive.** The plan section "Agent
  registry capability resolver and deterministic router — 2026-09-15" has all five subtasks
  at `Status: done` and an Output section; roadmap B7 is `DONE 2026-09-16`. As of 2026-10-21
  that record (plus every other completed Plan/Output previously appended to `tasks/task.md`)
  is preserved verbatim at `.agents/memory/archive/2026-09-15-agent-registry-routing-task-record.md`,
  and `tasks/task.md` is the active-task slot pointing at this queue. Completed records are
  never live work — do not reopen them as queue items.
- **A8's reviewer pass was run.** The opencode architecture review of A8 is adjudicated in
  `.agents/memory/opencode/repo-review-2026-09-26.md` § "A8 reviewer pass adjudication" (three
  observations, all declined with code evidence); the earlier pass's findings were fixed in
  `aa830ef`. The line "A8's reviewer pass was never run" still standing in
  `.agents/memory/roadmap.md` (A10 note) and the old row in this file were both stale. What
  is *not* recorded is a copilot A8 snapshot review; `team.md` does not require one.
- The Agent Intercom Windows EPERM fix, the AgentOps packaging/release work, the roadmap
  Phase 1+3 delivery, A4, A3, B7, A1, A2, A8, A10, the temp-archive and restore operations,
  the PR #5 autonomous-workflow merge, the 2026-10-15 Issue #12 review gates, and the
  2026-10-19 ai-token-tracker data audit are all complete. Their history is in
  `.agents/memory/project.md`.
- **PR #21 merged 2026-10-20 (`4d1c933`, merge commit, not squash).** `fix/codex-args-paths-shadowing`
  was synced onto `main` at `c825a1b` by merge `ac7cae9`, re-measured (`tools/evidence/generate.py`
  in a clean clone: agentops 831 pass / 4 skipped, UGA 498 / 1, mini-llm 252 / 1,
  ai-token-tracker 52 / 0), and the regenerated evidence was committed as `c34f4d7`. Landed:
  Codex runs `codex exec --sandbox workspace-write` with no `--approve-for-me` in the bundled
  args, and `resource_path` no longer lets a directory, empty file, or unparseable file shadow
  the bundled `agents.yaml`. `main` at `4d1c933` has the same tree as the tested head `c34f4d7`,
  and `check.py` passes there against the evidence recorded at `ac7cae9`.
  Its two non-blocking review follow-ups are open rows above.
