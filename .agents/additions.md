# Additions Log

> Track new files, patches, and artifacts created during this session.

## 2026-09-14 — Agent Intercom Windows EPERM fsync Fix Validation ✅ COMPLETE

### New Files Created

| Path | Description |
|------|-------------|
| `agent-intercom-fix/` | Root directory for all fix artifacts |
| `agent-intercom-fix/0001-fix-windows-eperms-fsync-on-durable-json-temp-file.md` | Technical background: root cause, impact, why `r+` |
| `agent-intercom-fix/durable-json.windows-eperm.pi.patch` | 1-line git diff for `@ctliz/agent-intercom-pi` |
| `agent-intercom-fix/durable-json.windows-eperm.opencode.patch` | 3-line git diff for `@ctliz/agent-intercom-opencode` |
| `agent-intercom-fix/durable-json.windows-eperm.patch` | ~~Combined patch (superseded by split patches)~~ |
| `agent-intercom-fix/durable-json.test.ts` | 4-test Windows EPERM regression suite |
| `agent-intercom-fix/BUG_REPORT.md` | Upstream PR description |
| `agent-intercom-fix/HOW_TO_REAPPLY.md` | PowerShell + git apply + sed instructions |
| `pending_tasks.md` | Task tracking file |
| `additions.md` | This file — artifact log |

### Patch Validation Results

- **agent-intercom-pi**: `git apply --check` ✅ | 1 line changed | `openSync(temporaryPath, "r")` → `"r+"`
- **agent-intercom-opencode**: `git apply --check` ✅ | 3 lines changed | interface + impl + call site
- Both: only `durable-json.ts` modified, no other files touched
- Real-FS roundtrip verified: `writeDurableJson` succeeds with patched code

### Upstream Repos

| Repo | Version | Fix Present? |
|------|---------|-------------|
| `ctliz/agent-intercom-pi` | 0.12.2 | ❌ No |
| `ctliz/agent-intercom-opencode` | 0.12.1 | ❌ No |

### Test Suite

`durable-json.test.ts` contains 4 tests:
1. `writeDurableJson calls open with r+ flag for fsync (Windows EPERM regression)` — verifies op ordering and flag
2. `writeDurableJson with r+ flag preserves data through full fsync+rename cycle` — real-FS roundtrip
3. `writeDurableJson open flag type accepts r and r+` — TypeScript type compatibility
4. `writeDurableJson does not swallow EPERM on win32 for directory fsync` — platform guard intact

---

## 2026-09-14 — AgentOps Packaging & Release

### New Files Created

| Path | Description |
|------|-------------|
| `agentops/agent_run.py` | Persistent AgentRun domain object |
| `agentops/verification_model.py` | Verification profile domain model |
| `agentops/verification_kernel.py` | Deterministic verification executor |
| Various test files | AgentRun + Verification Kernel tests (27 new tests) |

### Milestone

- AgentOps v0.12.2 with AgentRun lifecycle + Verification Kernel
- 122-test suite passing (1 Windows-platform skip)
- `dist/AgentOps.exe` rebuilt (~14.7 MB)
