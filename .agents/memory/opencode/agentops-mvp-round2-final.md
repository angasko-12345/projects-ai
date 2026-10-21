# AgentOps MVP acceptance — round 2, terminal state (2026-10-18)

This is the dated, never-revised-in-place record for the second acceptance round.
Supersedes/archives: `.agents/memory/opencode/agentops-mvp-handoff.md` (marked terminal below).
Pinned alongside: `.agents/memory/opencode/agentops-mvp-final-report.md` (round-1 PARTIAL verdict — none existed at this path at write time; if it exists elsewhere it was never revised in place).

---

## Root cause found — two product-side bugs, both fixed

Round 1's failures were misattributed to the disposable shim/env. Two real product defects were identified and fixed:

**Bug 1 — review status gate unreachable as designed.**
The product's own review prompt mandates *only* the verdict JSON as the agent's final output line, yet `agentops/workflow.py:_structured_agent_status` consulted the runner envelope (`structured_result`, always attached in production), which `parse_agent_result` computes as `UNKNOWN` from a verdict-only payload. A perfectly compliant reviewer (prose + final verdict line, exactly what the prompt demands) therefore failed the status gate every time — live in workflows `b901b8ac` reviews `d8dd91b3/c567aa07/e0d2e455` (round 1), and `60fda4c1`/`257c4996` reviews (blocked downstream of implementation failure, round 2).

Fix: `_structured_agent_status` now treats stdout whose ONLY structured JSON is a verdict-only object as the legacy no-signal path (`None`) instead of forwarding the envelope's `UNKNOWN`. Verdict-only envelopes are excluded too. Behavior preserved for every other case: explicit `{"status":"unknown"}` still coerces to UNKNOWN; status claims still pass/fail as before; verdict-only objects still cannot masquerade as agent success/failure.

**Bug 2 — prompt/gate contract mismatch for non-review roles.**
The architecture/implementation/debugging prompt said *"Optionally end with a JSON AgentResult object … plain-text summaries remain valid"*, while the success gate hard-fails any run whose structured status is `UNKNOWN`. Agents complying with the prompt were guaranteed to fail — exactly what happened in round 1 (`implementation` attempt 1 exit-0 plain text → `UNKNOWN` → FAILED).

Fix: the non-review prompt branch now *requires* the final AgentResult JSON (`"Your FINAL output line MUST be one valid JSON AgentResult object … Do not write anything after that final JSON line."`), aligning the prompt with the gate the code enforces.

## Disposition

**Both fixes committed** — first on branch `agentops/fix-review-status-and-prompt-contract` (`9d6cf6f`), then cherry-picked onto `main` as `3363471` on top of `ebb9560`. Each fix carries a fail-first regression test (fails pre-fix, passes post-fix) in `agentops/tests/test_issue12_gates.py` following the product's existing gate-test pattern:

- `test_real_running_envelope_verdict_only_output_is_not_an_agent_status` — real runner envelope (`_safe_structured_result`) + verdict-only review output → review PASSES when verdict approved.
- `test_nonreview_prompt_requires_structured_agent_result` — implementation prompt requires the final AgentResult JSON.

**Suite results (from `.agents/evidence/verification.json`, not memory):**

| Tree | Config | Result |
|------|--------|--------|
| `main` @ `3363471` (cherry-pick) | shipped, clean tree | 827 tests OK (skipped=4), exit 0 |
| Primary branch `9d6cf6f` | shipped, clean tree | 821 tests OK (skipped=4), exit 0 |
| Pristine `2b647ee` + fix | shipped, clean tree | 827 tests OK (skipped=4), exit 0 |

The live disposable clone's 3 `test_config_registry` failures were entirely environmental: its `agents.yaml` was deliberately rewired for acceptance (`opencode` → shim command, `omp` disabled). The pristine-config run disproves them as product regressions.

**Cancellation/blocked path — exercised truthfully.** Run D (`c46b4292`) was cancelled via the real GUI Cancel button (UIA InvokePattern, no focus needed). `state.sqlite` records architecture agent run `dafb28` as `running → cancelled` (`exit=None`), downstream tasks never started, workflow terminal `failed`, **no merge**, worktree preserved. Screenshot: `…\agentops-mvp\gui-5-cancelled.png`.

## What was NOT achieved — blocked on disposable-env agent execution

Two post-fix end-to-end runs through the real GUI on the fixed clone both failed at **environment-level agent execution**, not at the product fixes:

| Run | Task | Furthest stage | Failure |
|-----|------|----------------|---------|
| E (`60fda4c1`) | `--greeting NAME` | architecture **passed** (new contract honored) | implementation attempt 1 **timed out at 900s** (agent hung after "I'll start by exploring…"); attempt 2 kilo exit 1 |
| F (`257c4996`) | `--count N` | architecture **failed** at attempt 1 (exit 1, no log dir — wrapper/agent failed instantly) | attempt 2 kilo exit 1 |

Diagnosis: opencode agents either hang mid-execution or fail instantly inside `C:\Users\admin\AppData\Local\Temp\opencode\agentops-mvp\`; kilo attempts always exit 1 on Windows (arg-length limit / PowerShell permission rejections / `ls -la` alias failure) — an honest env limitation documented in the prior round. Product-side: architecture passed once under the new contract, and both regression tests pass.

**Acceptance criterion "two consecutive successful E2E workflows" is therefore BLOCKED** on disposable-env agent execution, not on product code. The two product bugs that made the acceptance unreachable as designed are fixed and regression-tested on `main`.

## Handoff

Terminal state — no further work in this tree unless the disposable env is rebuilt or the user restarts the E2E acceptance chase. `.agents/memory/opencode/agentops-mvp-handoff.md` is marked terminal below; its historical content (environment layout, scripts, Run A/B records) is preserved unchanged.

## Boundaries respected (per repo instructions)

- No commits/pushes beyond `agentops/fix-review-status-and-prompt-contract` (local) and cherry-pick `3363471` onto `main`.
- WorkBuddy worktree (`C:\Users\admin\WorkBuddy\Worktrees\projects\issue-12-review-verdict-gates-9f19a4cc`) untouched; its staged Oct-15 snapshot preserved on tmp branch `tmp/workbuddy-snapshot-892bb1f7` within that worktree.
- The four primary untracked items (`.agents/tmp-tests/`, `.github/workflows/ai-token-tracker.yml`, `qr-generator/release-readiness-audit-20261011.md`, `tools/obsidian-sync/`) untouched.
- No Agent Intercom reinstall/reconfiguration.
