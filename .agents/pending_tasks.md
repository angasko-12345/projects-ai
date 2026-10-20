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

## AgentOps -- open

| ID | Item | Evidence |
|---|---|---|
| ROOT-034 (AgentOps share) | `DBG-12` residual test debt; `DBG-15` residual migration-test debt | Re-verified 2026-10-20 at `c825a1b`: still open, still test debt only, no production bug. `DBG-12`: `tests/test_failure_kernel.py::test_cancellation_bounded` asserts only `policy.should_retry_attempt(1, cancelled)` plus `threading.Event.is_set()` on an event it sets itself, so it cannot fail for the reason its name claims. `DBG-15`: `tests/test_events.py:276` and `tests/test_failure_kernel.py:588` still assert `versions == [1, 2, 3, 4, 5, 6, 7]` while `state.py` migrates to version 7. **Ledger header is stale:** §0 says "last verified 2026-10-02 against `849e015`" (`849e015` is 2026-10-01), so re-dating it belongs with closing these two. `DBG-08` remains CLOSED (`tests/test_runtime.py::test_real_parent_terminate_process_kills_descendant_tree`) |
| -- | `SpawnFactory` Protocol typing (deferred from the A3 review) | still deferred; `runtime.py:49` is still the bare alias `SpawnFactory = Callable[..., Awaitable[Any]]` |
| -- | Repository characteristics for routing are inert | `agentops/agentops/workflow.py:832` still passes `repository_characteristics={}` into the router, so every repository-derived routing signal is dead input (`routing.py:431` reads it as a real mapping) |
| -- | Control center never observed against a live running workflow | Automated coverage is fixture/offscreen only (`tests/test_control_center.py`: `ControlCenterProjectionTests`, `ControlCenterViewTests`). The real-GUI acceptance rounds did drive live workflows through the real PySide6 window and did press the real Cancel button (Run D `c46b4292`, `running → cancelled`, no merge), but neither record documents the **control-center surface** — the Workflows live stage flow, per-stage durations, live card — being observed while a workflow ran. Owner: the MVP acceptance session; its records are untracked in this tree |
| -- | Issue #4 end-to-end acceptance stays BLOCKED on the disposable environment | Issue #4 is still OPEN with **0 comments**, last updated 2026-10-05. Round 1 (2026-10-17) was PARTIAL: Runs A and B were submitted through the real GUI and both ended terminal-honest-`failed` (two further rows of undetermined provenance in that `state.sqlite` also failed, so the session counted four). Round 2 (2026-10-18) fixed and regression-tested the **two product bugs** the acceptance exposed (`3363471` on `main`, fail-first tests in `tests/test_issue12_gates.py`) and exercised cancellation truthfully, but both post-fix runs (E `60fda4c1`, F `257c4996`) died at **environment-level agent execution** (opencode hung past the 900 s timeout; kilo exits 1 on Windows). The "two consecutive successful E2E workflows" criterion is therefore unproven, not disproven. Unblocks only when the disposable env is rebuilt or the user restarts the chase — no further work in that tree until then |
| -- | Issue #12 security reviewer pass still owed | The copilot read-only snapshot review ran 2026-10-15 and drove 4 fixes (`2415b25`), 2 findings rejected with file/line evidence. The fcc-claude **security** pass never ran (`fcc-server` down, nothing on `127.0.0.1:8082`; opencode probe produced no output). Rerun it from a machine where `fcc-server` is up before treating `security.py`, the secret prescan, and the gate fail-closed paths as reviewed. Adjudication: `.agents/memory/decisions.md` 2026-10-15. Roadmap D8 is the standing policy for this |
| -- | `.git/hooks/post-commit` never runs (UTF-8 BOM, and not executable) | Re-verified 2026-10-20: the file starts `ef bb bf 23 21 2f` and is mode `0644` (`-rw-r--r--`). Git therefore prints `cannot spawn ... post-commit: Exec format error` for some commits and `cannot spawn .git/hooks/post-commit: No such file or directory` for others, and the auto-mirror silently never fires. The manual path works and was used today: `powershell -NoProfile -ExecutionPolicy Bypass -File tools/obsidian-sync/sync-agent-memory.ps1` (121 files, exit 0). To repair: strip the BOM, make it executable, or reinstall via the untracked `tools/obsidian-sync/install-hook.ps1`. Nothing in the repo is damaged by this — the mirror just never fires on its own |
| -- | `dist/AgentOps.exe` predates the current AgentOps source | `agentops/dist/AgentOps.exe` is the **Oct 12 18:54** build (git-ignored). No packaging input changed after it (`AgentOps.spec`, `agentops_gui.py`, `scripts/build_windows_exe.py` have no commit since), so D6 owes no rebuild on packaging grounds alone — but agentops source moved a lot afterwards (`2e80160` strict review gates on Oct 13, `2415b25`, `abe0d73`, `3363471`), so the shipped binary does **not** contain the review-verdict gate work. Rebuild when the user takes the D6 decision |
| -- | PR #21 review follow-ups (non-blocking) | Two gaps recorded when reviewing the merged config-shadowing fix. (1) No test pins the **positive** preference path — a real, parseable file at `agents/agents.yaml` must still win over the packaged copy; today that is only covered indirectly by every test that calls `load_config()`. (2) `_is_bundled_config` (`paths.py`) validates with `json.loads` only, while `_load_data` accepts JSON-valid YAML *or* conventional YAML when PyYAML is installed — a conventional-YAML bundle at the preferred path falls through to `importlib.resources` (harmless in a source checkout, but the fast path and the loader disagree about "valid"). Context: `.agents/outputs/pr21-codex-least-privilege.md` |

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
| ROOT-034 (UGA share) | `DBG-06`/`DBG-07` CLOSED, `DBG-13` fixed for routing, `DBG-14` DISPROVEN; `DBG-12` residual shared test debt only | `DBG-06` and `DBG-07` closed 2026-10-03 in `59f5a1b`; `tests/test_cwd_isolation.py` is the standing DBG-07 guard and is present. Nothing on the UGA side is an open production bug; the row exists only because the shared DBG-12/DBG-15 test debt is not closed. See master-bug-synthesis section 0 |
| -- | STEP 3 exp02 re-run (post-fix validation run) | The pre-flight ran 2026-10-02 and the detector bands hold (0 steps in the ambiguous 200..300 gap, MISS band exact, no window-chrome red above the hit band), so the re-run is unblocked. It has **not** happened: cancelled because the machine was in use, and the exp02 verdict stays suspended. Ask before starting a long GUI run — it sends real `SendInput` keystrokes and holds a real window for three phases. Read `universal-game-agent/AGENTS.md` § "Cadence / reward investigation" first: the synthetic matrix (2026-10-05) showed the reward discriminates (lookahead oracle +0.1 / never dies vs random -1.0 / dies at 5.6 steps), so the live run is the remaining evidence for real-window learnability |
| -- | Live confirmation of per-decision paddle displacement | Source math over the 60 fps loop gives 15-20 px per decision (60 ms hold = 3-4 ticks x 5 px); the synthetic cell verifies 15-20 px against the mirrored tick model, but the real window has never been measured. One short `training.external_smoke` run with position sampling settles it — ask first (real SendInput) |
| -- | Larger-budget cadence comparison | The 16384-step synthetic pilot left every cell at final rolling reward -0.47..-0.52 vs the oracle's +0.1, so no convergence comparison exists yet. Repeat `python -m training.cadence_experiment` with a real budget (e.g. `total_timesteps: 50000`) across the same four cells before concluding anything about relative learnability — synthetic only, no approval needed |

## mini-llm -- open

| ID | Item | Evidence |
|---|---|---|
| -- | Full 1.9 GB TinyStories prep + GPU run | needs a cloud GPU, not this box; procedure is in `docs/EXPERIMENT-tinystories.md` |

## ai-token-tracker -- open

The 2026-10-19 data-pipeline audit is **complete** and merged (`ad73f30`, records in
`tasks/task.md` and `.agents/outputs/token-tracker-data-audit.md`); only this follow-on is
open.

| ID | Item | Evidence |
|---|---|---|
| -- | OpenRouter provider-side activity import | `GET /api/v1/activity` with `OPENROUTER_API_KEY`; additive collector, idempotent page-through. Fills the real-cost gap: the audited Claude store carries no `costUSD` at all (0/449 usage-bearing lines), so provider-side activity is the only way to recover real cost for that route. Scope and caveats: `.agents/outputs/token-tracker-data-audit.md` |

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
| -- | 3 open Dependabot advisories with no upstream fix | Re-checked 2026-10-20 via `gh api repos/angasko-12345/projects-ai/dependabot/alerts`: `node-forge` **high**, `braces` **high**, `sprintf-js` **medium** all still `open`; `uuid` `fixed`. These are on the **parent** repo's default branch (the QR app's `package.json` lives in `qr-generator/`); Dependabot is disabled on the public repo, so query the parent. Monitor only until upstream ships: `.agents/memory/qr-generator-npm-security-2026-10-17.md` |

## Superseded / historical pointer

- `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` section 9 "Authoritative
  Fix Queue" is **SUPERSEDED 2026-10-02**. Read section 0 of that file instead.
  Sections 1-11 remain as historical audit evidence.
- The other reports in `.agents/memory/opencode/bugfinding/` are dated audit evidence
  (`bug-registry.md`, `deep-bug-audit-2026-09-29.md`, `geminihandoff.md`,
  `projects-ai-bug-handoff.md`, `projects-ai-review-handoff.md`,
  `repo-review-2026-09-26.md`). They are not instructions and are never revised in place.
- ROOT-015..ROOT-025 and ROOT-028 are DISPROVEN and are not queue items.
- **B7 routing is done, and `tasks/task.md` records it as done.** The plan section "Agent
  registry capability resolver and deterministic router — 2026-09-15" has all five subtasks
  at `Status: done` and an Output section; roadmap B7 is `DONE 2026-09-16`. That section is a
  completed task record, not live work, and `tasks/task.md` is owned by the task procedure —
  do not reopen it as a queue item.
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
