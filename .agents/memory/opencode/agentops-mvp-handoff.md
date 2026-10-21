# AgentOps MVP handoff — TERMINAL (2026-10-18)

> **STATUS:** TERMINAL — no further work in this tree unless the disposable environment is rebuilt or E2E acceptance is restarted. All subsequent outcomes are recorded in the dated addendum `.agents/memory/opencode/agentops-mvp-round2-final.md`, which is the authoritative source for round 2. Historical content below is preserved unchanged.

**Date:** 2026-10-17 (~11:10 local) — original preparation; terminal marker added 2026-10-18
**Prepared by:** the acceptance session for the AgentOps MVP Live E2E Acceptance task
**Goal:** prove AgentOps can autonomously complete real coding tasks through its GUI (live end-to-end acceptance). Fixture repos live outside `projects-ai`; nothing is merged into any primary branch; WorkBuddy and the four primary untracked items are untouched.

---

## Environment (disposable — NOT the primary repo)

**Fresh clone:** `C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\code`
- Clone of `origin/main` at `2b647ee9e966de5692fa3449667309d4dcd3a6e9` (PR #18 merged 2026-10-17T06:52:46Z, PR `kilo/peach-owl-p3k` force-updated to `4b95aa1`).
- Baseline suite: `cd agentops && python -m unittest discover -s tests` → **825 tests OK (skipped=4)** in 196.7s. Log: `…\agentops-mvp\mvp-baseline-suite.log`.
- GUI launched as **PID 16208** (`python run_gui.py` cwd `…\agentops-mvp\code`; `run_gui.py` inserts `…\code\agentops` at sys.path[0] so the editable-install shadow of `D:\admin\code\projects\agentops` does not shadow the clone's package). Window title `AgentOps`. Logs: `gui-out3.log`, `gui-err3.log` in `…\agentops-mvp\`.
- GUI interaction: UI Automation via `uia-drive.ps1` / `uia-newtask.ps1` / `uia-read.ps1` (ValuePattern on the repo edit + description edit, InvokePattern on Start Task); screenshots via PrintWindow of PID 16208's MainWindowHandle (`gui-shot.ps1`); no full-screen captures, no focus-stealing.

**Fixture repo (target of both runs):** `C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\fixtures\hello-cli`
- Baseline commit `3f09e91 Initial fixture: CLI with tests`: `hello.py`, `tests/test_hello.py`, `README.md` (CLI prints `Hello, World!`, default `--name World`, unittest suite). Nothing merged into it yet; git status clean at `3f09e91`.
- State DB: `…\fixtures\hello-cli\.agentops\state.sqlite` — truthful source of workflows/tasks/agent_runs/failures (also `typed_events`, `artifacts`, `verification_*`, `schema_migrations`, `worktree_refs`).

**Disposable test shim (test-only, confined to the disposable env — never in the primary repo):**
`C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\shims\oc_contract_shim.py`
- Wraps `D:\admin\code\opencode.CMD run <prompt>`, streams stdout/stderr verbatim, appends exactly one status JSON derived solely from the real exit code (`success` exit 0, `failure` otherwise; summary = last 1800 chars of stdout or stderr).
- Validated with a READY test (exit 0 → appended `{"status": "success", "summary": "READY", …}`).

**Clone's bundled config (edited ONLY in the clone, never in `projects-ai`):**
`…\agentops-mvp\code\agentops\agentops\agents\agents.yaml`
- `opencode` command → `python C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\shims\oc_contract_shim.py run {prompt}` (roles architecture/implementation/debugging/review).
- `omp` → `enabled: false` (its repair-attempt failure mode: `400 The requested model is not supported`, AGENT_ERROR).

---

## Read scripts (all in `…\agentops-mvp\`)

- `readstate2.py` — prints tasks/agent_runs with role, status, attempts, agent, exit_code, structured_result summary.
- `readfail.py` — prints workflows, tasks (results), agent_runs (error/failure_classification/structured_result), and failures rows incl. `structured_evidence` and `recommended_action`.
- `readart.py` — artifacts table dump.
- `gui-shot.ps1` — PrintWindow capture of the GUI main window (PID passed as arg).
- `uia-drive.ps1` — steps: set-repo, set-desc, focus-repo/focus-desc, click-start, rawclick, raise-dialog, dialog-shot.
- `uia-newtask.ps1` — opens the New Task dialog via InvokePattern on the New Task button.
- `uia-read.ps1` — reads back repo/description field values and Start Task enabled state.
- `gui-drive.ps1` — legacy cursor/dispatch helper (superseded by UIA; kept for reference).

---

## Run A — COMPLETE (terminal, truthful FAILED)

**Task text:** “Add a `--greeting NAME` option to this Python CLI. Default to `World`, print `Hello, NAME!`, add tests in tests/, and update the README. Preserve existing behavior, add no dependencies, and avoid unrelated changes.”

**Submitted:** via the real GUI at 10:22 UTC — New Task dialog: repository prefilled from settings, description typed via ValuePattern, Automatic routing selected, Default verification profile, Start Task clicked.

**Workflow:** `5b55fbd3-c0f4-4726-b206-7672c9ce4c71`

**Stage-by-stage truth (from `state.sqlite`):**
1. **architecture** — agent `opencode`, attempt 1, status `passed`, exit_code 0, structured result `{"status": "success", "summary": "Plan: add --greeting NAME (default World, overrides --name) to hello.py; tests; README.", "tests_run": 0, "tests_passed": 0, "tests_failed": 0, "verification_results": [], "review_findings": []}` — real structured plan emitted (log `…\logs\fc3a6409-...\2026-10-17T10-32-44.*`).
2. **implementation** — agent `opencode`, attempt 1, status `completed`, exit_code 0, files_changed `[README.md, hello.py, tests/test_hello.py, __pycache__/*.pyc]`, BUT structured result `{"status": "unknown", …}` — agent exit-0 success but plain-text prose ("I'll dispatch @explorer…") instead of schema-conforming JSON → classified DIRTY_WORKTREE, `failure_classification: null`, `recommended_action: retry_same_agent`.
3. **implementation repair** — agent `omp`, attempt 2, status `failed`, exit_code 1, error `400 The requested model is not supported` (type=invalid_request_error, param=model), `AGENT_ERROR`, `recommended_action: retry_same_agent` — model-config failure, bounded repair exhausted (attempts 2/2).
4. **verification** — status `blocked`, reason `Blocked by an unsuccessful dependency.` (attempts 0/1).
5. **review** — status `blocked`, reason `Blocked by an unsuccessful dependency.` (attempts 0/2).

**Terminal state:** workflow `failed`; architecture=passed (1/1), implementation=failed (2/2), verification=blocked, review=blocked. Fixture repo untouched (`3f09e91`). Evidence: state.sqlite workflows/tasks/agent_runs/failures rows plus three meta/stderr/stdout log sets under `…\.agentops\logs\fc3a6409-*`.

**What Run A demonstrates (accurately):** real agent plumbing through the GUI, honest failure classification, bounded repair attempted with a *different* agent, exhaustion, verification/review correctly blocked, **no merge of unverified changes** — a correct terminal failure, not a fabricated success.

---

## Run B — IN PROGRESS (submitted, awaiting completion)

**Task text:** “Add a tested `--uppercase` option to this Python CLI. When set, print the greeting uppercased. Add tests in tests/, update the README. Preserve existing behavior, add no dependencies, avoid unrelated changes.”

**Submitted:** via real GUI at ~11:07 UTC — NEW-TASK-OPENED → DESC-SET (206 chars) → START-INVOKED. Architecture agent spawn confirmed running: `gui PID 16208 → shim 16452 → opencode.CMD 7892`.

**IMPORTANT — re-verify before acting:** the last `readstate2.py` snapshot *before* submission showed workflows `5b55fbd3`, `4e645835`, `533cdeac` all `failed`; those were **attempt-history rows from Run A repairs**, not Run B's workflow. The fresh Run B workflow row was being created as START-INVOKED fired. **On continuation, first run `readstate2.py` and readfail.py to identify Run B's workflow ID and its current task statuses** (architecture/implementation/verification/review → passed/running/failed/blocked).

**GUI PID 16208 still RUNNING.** If it has exited, relaunch `run_gui.py` from the clone cwd and re-attach UI Automation to the new window handle.

---

## Next steps on continuation

1. `python readstate2.py` → identify Run B's workflow ID and task statuses; poll until architecture→implementation→verification→review all reach terminal states (or honest failure).
2. Capture GUI screenshots via `gui-shot.ps1` at each terminal state for the report (PrintWindow only).
3. **If Run B passes end-to-end:** confirm fixture `git status --porcelain` shows only `hello.py`, `tests/test_hello.py`, `README.md` modified (no `__pycache__` committed); run `python -m unittest discover -s tests -v` in the fixture; confirm GUI reports truthful final READY status; confirm merge into fixture main happened via `finalize.py` provenance path (worktree_refs row valid).
4. **If Run B fails:** record exact blocker (file, command, failure, affected product = hello-cli fixture) — do NOT fabricate a pass.
5. **Remaining acceptance items not yet exercised:** cancellation/blocked path (start a long task, cancel via the GUI Cancel button, confirm truthful terminal state + preserved worktree + no unverified merge). The repair path was already demonstrated honestly in Run A.
6. Do NOT merge anything into `projects-ai`'s `main`; do not touch the WorkBuddy worktree (`C:\Users\admin\WorkBuddy\Worktrees\projects\issue-12-review-verdict-gates-9f19a4cc`); do not touch the four primary untracked items (`.agents/tmp-tests/`, `.github/workflows/ai-token-tracker.yml`, `qr-generator/release-readiness-audit-20261011.md`, `tools/obsidian-sync/`).

---

## Key files

- Handoff: this file.
- State DB (truth): `C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\fixtures\hello-cli\.agentops\state.sqlite`
- Logs: `C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\fixtures\hello-cli\.agentops\logs\`
- GUI logs: `C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\gui-out3.log`, `gui-err3.log`
- Scripts: all in `C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\`
