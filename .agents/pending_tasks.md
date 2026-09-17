# Pending Tasks

> Created 2026-09-14. Track work items that need validation, implementation, or follow-up.

## Agent Intercom Windows EPERM fsync Fix — Validation ✅ COMPLETE

**Status:** Validated and reproducible

**Context:** The `writeDurableJson()` function in `@ctliz/agent-intercom-pi` and `@ctliz/agent-intercom-opencode` throws `EPERM` on Windows because `openSync(temporaryPath, "r")` creates a read-only handle, and Windows `FlushFileBuffers` requires `GENERIC_WRITE`. Fix: change to `"r+"`.

**Artifacts created:**
- `agent-intercom-fix/durable-json.windows-eperm.pi.patch` — 1-line patch for agent-intercom-pi
- `agent-intercom-fix/durable-json.windows-eperm.opencode.patch` — 3-line patch for agent-intercom-opencode (interface + impl + call site)
- `agent-intercom-fix/durable-json.test.ts` — Windows EPERM regression test suite
- `agent-intercom-fix/BUG_REPORT.md` — upstream PR description with all details
- `agent-intercom-fix/HOW_TO_REAPPLY.md` — PowerShell + git apply + sed instructions
- `agent-intercom-fix/0001-fix-windows-eperms-fsync-on-durable-json-temp-file.md` — technical background

**Validation results:**
- ✅ `git apply --check` passes cleanly on both repos
- ✅ Only `durable-json.ts` changed in both repos (pi: 1 line, opencode: 3 lines)
- ✅ `writeDurableJson` roundtrip confirmed working with patched code
- ✅ Existing test suite runs without new failures caused by patch
- ✅ Upstream repos do NOT contain this fix (verified via GitHub API)

**Remaining work:**
- Submit upstream PRs to `ctliz/agent-intercom-pi` and `ctliz/agent-intercom-opencode`
- Add `durable-json.test.ts` to each repo's test suite
- Monitor for upstream merge (expected versions: pi@0.12.3+, opencode@0.12.2+)

---

## AgentOps Packaging & Release — ✅ RESOLVED 2026-09-15

**Status:** Done. Commit strategy executed (`935f4dc` + subtree fold `a1c95d3` + branch unification `fd9abfb`); Roadmap Phase 1 + Phase 3 delivered (`1047121`); fresh `dist/AgentOps.exe` (14,807,079 bytes) published as GitHub Release `v0.1.3`. Exe stays out of git per standing decision.

**See:** `memory/project.md` release record.

## Roadmap Phase 1 + Phase 3 — ✅ COMPLETE 2026-09-15

**Status:** Done, suite 224 OK. Plan: `plans/phase-1-storage-dtos-phase-3-worktree-refs-plan.md`. Output: `outputs/phase-1-storage-dtos-phase-3-worktree-refs.md`.

## A4 Structured Failure Evidence — ✅ COMPLETE 2026-09-16

**Status:** Done, suite 356 OK. Copilot and OpenCode reviews closed. Plan: `plans/a4-evidence-plan.md`. Output: `outputs/a4-evidence.md`.

## A3 Shared ProcessRuntime — ✅ COMPLETE 2026-09-16

**Status:** Done, suite 336 OK. Copilot and OpenCode reviews closed. Plan: `plans/a3-runtime-plan.md`. Output: `outputs/a3-runtime.md`.

## B7 Capability Resolver and Deterministic Router — ✅ COMPLETE 2026-09-16

**Status:** Done, suite 336 OK. Copilot and OpenCode reviews closed. Plan: `plans/b7-router-plan.md`. Output: `outputs/b7-router.md`.

---

## AgentOps / Agent Intercom Temp Artifact Archive — ✅ COMPLETE 2026-09-17

**Status:** Complete and verified.

- Moved 27 name-matched Temp entries into `.local-temp-archive/` under `agent-intercom/` and `agentops/`.
- Archive contains 30,476 files totaling 314,882,683 bytes; no matching entries remain in `C:\Users\admin\AppData\Local\Temp`.
- Staged copies were hash-verified before source deletion; final files were reverified against `.local-temp-archive/move-manifest.json`.
- At archive time, no name-matched Temp entries remained. The two Agent Intercom trees were later restored; the 25 AgentOps entries remain archived.
- The machine-local archive is excluded through `.git/info/exclude`; no product code or packaging files changed.
- Full suite from `agentops/` at archive time: 357 passing, 4 environment skips.

---

## Agent Intercom Temp Restore — ✅ COMPLETE 2026-09-17

**Status:** Complete and verified.

- Restored `C:\Users\admin\AppData\Local\Temp\agent-intercom-opencode-test` and `C:\Users\admin\AppData\Local\Temp\agent-intercom-pi-test` from the machine-local archive.
- Restore totals: 30,264 files, 310,090,625 bytes; final Temp trees hash-verified.
- The AgentOps archive was not modified: 212 files, 4,792,058 bytes, tree digest unchanged.
- Restore manifest: `.local-temp-archive/agent-intercom-restore-manifest.json`.
- Full suite from `agentops/` after restore: 357 passing, 4 environment skips.
