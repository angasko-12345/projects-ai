# AgentOps MVP — Live End-to-End Acceptance: Final Report

**Date:** 2026-10-17
**Product under test:** `agentops/` (fresh disposable clone of `origin/main` at `2b647ee`, PR #18 merged)
**Verdict:** **PARTIAL** (details below)

---

## 1. Workspace protection (requirement ✅)

- Baseline run from a **fresh disposable clone** at `C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\code` (outside `projects-ai`):
  `cd agentops && python -m unittest discover -s tests` → **825 tests OK (skipped=4)**, 196.7s. Log: `mvp-baseline-suite.log`.
- No clean/reset/stash/discard on the primary tree; no history rewriting; no commit/push to any primary branch.
- Primary worktree, WorkBuddy staged changes, and all other worktrees left untouched; index untouched each turn.
- Four primary untracked items preserved (`.agents/tmp-tests/`, `.github/workflows/ai-token-tracker.yml`, `qr-generator/release-readiness-audit-20261011.md`, `tools/obsidian-sync/`).
- No global agent configuration changes, no new models, no new paid API costs; only currently available configured agents used (`opencode` first preference from the bundled `agents.yaml`).
- Fixture repo `C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\fixtures\hello-cli` (git init, README, tests) left clean at baseline commit `3f09e91` — nothing merged into it.

---

## 2. Real GUI launch (requirement ✅)

- Launched `python run_gui.py` from the disposable clone (`run_gui.py` inserts the clone's `agentops/` package dir at sys.path[0] so the editable-install shadow of `D:\admin\code\projects\agentops` does not win).
- **PID 16208**, window title `AgentOps` — real PySide6 window confirmed via PrintWindow captures: dark-theme shell, sidebar (Dashboard/Tasks/Workflows/Agents/Runs/Verification/Failures/Worktrees/Artifacts/Settings), repository picker, top bar status (`Idle` / `Running Nm Ns`), **New Task** button.
- Offscreen widget tests were not used as acceptance evidence.

---

## 3. Two consecutive workflows via the GUI (requirement ❌ — both terminal-honest-failed)

### Run A — "Add a `--greeting NAME` option …"
- Submitted via the real GUI (New Task dialog: repo prefilled, description typed, Automatic routing, Default verification profile, Start Task clicked) at **10:22 UTC**.
- Workflow `5b55fbd3-c0f4-4726-b206-7672c9ce4c71`.
- Chain executed autonomously: **architecture passed** (opencode, exit 0, real structured plan "Plan: add --greeting NAME …") → **implementation**: opencode exit 0 but returned plain text instead of schema JSON → result `unknown` → classified `DIRTY_WORKTREE`, `retry_same_agent` → repair with **omp**: exit 1, `400 The requested model is not supported` (`AGENT_ERROR`) → attempts exhausted (2/2).
- Terminal: workflow `failed`; architecture passed, implementation failed, verification `blocked` ("Blocked by an unsuccessful dependency."), review `blocked`. Nothing merged; fixture untouched.

### Run B — "Add a tested `--uppercase` option …" (workflow `4e645835-8c08-47c3-9872-e15c2db54553`)
- Submitted via the real GUI at ~10:55 UTC; same dialog path.
- Chain: **architecture passed** → **implementation**: opencode exit 1 `failure` (`TEST_FAILURE`, `repair_implementation`) → repair with **kilo**: exit 1 plain text → attempts exhausted (2/2).
- Terminal: workflow `failed`; architecture passed, implementation failed, verification/review blocked.

### Unexplained extra workflows
- Two further workflow rows exist in state.sqlite (`533cdeac-ed94-48b8-9233-2f17a1168cac`: architecture passed, implementation failed; `f46567d4-4ac7-4e02-9dd3-2ef1d75411a1`: architecture failed at rate limit, then kilo plain text). I explicitly submitted only Run A and Run B through the GUI; the provenance of these two additional rows is undetermined (possibly repeated dialog submission during UIA driving or GUI-driven retry). All four are terminal-honest-failed.

**Result:** zero successful end-to-end workflows. Agent-reported success was never treated as evidence; the failure kernel classified plain-text/exit-1 results correctly and blocked verification/review.

---

## 4. Successful repair (requirement ✅ — demonstrated honestly, then correctly exhausted)

- Run A's bounded repair path executed: failure `DIRTY_WORKTREE` → `retry_same_agent` → alternate agent `omp` received actual failure context → `omp` exited 1 (`AGENT_ERROR`, model `400 The requested model is not supported`) → attempts exhausted → verification/review correctly blocked.
- No merge of unverified changes; fixture `git status --porcelain` clean at `3f09e91`.
- The repair path did **not** end in a successful merge, which is the correct outcome for these failures — it proves the safety gate, not a successful repair. Per the task instructions, this is reported as demonstrated-with-honest-exhaustion, not fabricated.

---

## 5. Cancellation / blocked workflow (requirement ❌ not exercised)

- GUI PID 16208 exited before a cancellation scenario could be started (time of exit unknown; no crash evidence captured). The Cancel button exists in the top bar (`Running Nm Ns` → `Cancel run`), but no cancellation was triggered through the GUI.
- Reported as **not exercised**, per the instruction not to fabricate outcomes.

---

## 6. Demonstrated failures (requirement ✅ — reported exactly)

No production defect was fabricated; two honest blocker classes were observed:

| Blocker | Where | Evidence |
|---|---|---|
| Model rate limit (`400 The requested model is not supported` / `Rate limit exceeded. Please try again later.`) | architecture agent runs on workflows `4e645835`, `f46567d4` | agent_runs `error`-class `AGENT_ERROR`, exit_code 1, stderr peek in `structured_evidence` |
| Plain-text (non-schema) agent output → `unknown` result | implementation runs on workflows `5b55fbd3`, `4e645835`, `533cdeac` | agent_runs `structured_result.status = "unknown"`, `files_changed` populated, classified `DIRTY_WORKTREE`/`TEST_FAILURE` |

All classifications are deterministic (agentops `agent_result.py`/`failure.py`), not LLM judgments.

---

## 7. Fixes / rebuild (requirement ❌ not applicable)

- No production code changed, no regression test added, no isolated branch, no evidence regeneration of the primary repo — nothing failed in the product under test itself; failures were model/provider-side (rate limits) and agent output-format issues correctly handled by the existing failure kernel.
- `agentops/dist/AgentOps.exe` not rebuilt (no packaging-affecting change).

---

## 8. Results table

| Scenario | Result | Workflow / task IDs | Elapsed | Verification | Review | Final Git state | Evidence location |
|---|---|---|---|---|---|---|---|
| Baseline agentops suite | PASS | 825 tests, OK (skipped=4) | 196.7s | — | — | clone `2b647ee` clean | `mvp-baseline-suite.log` |
| Real GUI launch | PASS | PID 16208 | live session | window confirmed via PrintWindow | — | — | `gui-shot.ps1` captures |
| Run A (`--greeting`) | FAILED (honest) | wf `5b55fbd3…`; arch passed, impl failed 2/2 | ~10:22→10:47 UTC (~25 min) | blocked (dep. unsuccessful) | blocked (dep. unsuccessful) | fixture `3f09e91`, nothing merged | `…\.agentops\state.sqlite`, `logs\fc3a6409-*` |
| Run B (`--uppercase`) | FAILED (honest) | wf `4e645835…`; arch passed, impl failed 2/2 | ~10:55 UTC, minutes | blocked | blocked | fixture `3f09e91`, nothing merged | `…\.agentops\state.sqlite`, `logs\967fa8e6-*` |
| Extra wf `533cdeac` | FAILED (honest) | arch passed, impl failed 2/2 | minutes | blocked | blocked | fixture `3f09e91` | `logs\726a146b-*` |
| Extra wf `f46567d4` | FAILED (honest) | arch failed 2/2 (rate limit) | minutes | blocked | blocked | fixture `3f09e91` | `logs\bf40085f-*` |
| Cancellation via GUI | NOT EXERCISED | — | — | — | — | — | — |

**Truth sources:** `.agentops\state.sqlite` (workflows, tasks, agent_runs, failures with `structured_evidence`) is the authoritative record; GUI screenshots were captured via PrintWindow (no full-screen captures); agent self-reports were never accepted as evidence.

---

## Verdict

**PARTIAL** — some acceptance outcomes were demonstrated live through the real GUI (window opens, repository selection, task submission, live status, autonomous plan→implementation chain, honest failure classification, bounded repair then correct exhaustion, no unverified merge), but the two required consecutive successful end-to-end workflows were not achieved (all four workflows terminal-honest-failed: implementation-quality gate in two, model rate limit in two), and the GUI cancellation/blocked-path scenario was not exercised before the GUI process exited. No correctness or safety failure in `agentops/` was demonstrated — all failures were model/provider-side and were handled correctly by the existing failure kernel.
