# Memory: qr-generator npm dependency security triage (2026-10-17)

> Applies to: `qr-generator/package.json` / `package-lock.json` at `main` >= `074182d`.
> Source: 4 Dependabot alerts on `angasko-12345/projects-ai` default branch (read with
> `gh api repos/angasko-12345/projects-ai/dependabot/alerts`, 2026-10-17).
> Note: `npm audit` counts (51) use npm's own graph and deliberately differ from these 4
> Dependabot alerts; do not treat audit totals as the alert list.

## Resolution record

1. **uuid (alert #4→#1 numbering per GH: alert 1) — FIXED at `074182d`.**
   CVE-2026-41907 / GHSA-w5hq-g745-h8pq (medium), path
   `expo`/`expo-sharing`/`expo-splash-screen` → `@expo/config-plugins` → `xcode@3.0.1`.
   Locked 7.0.3; fixed via **scoped override** `"overrides": { "xcode": { "uuid": "11.1.1" } }`
   (scoped so only xcode's subtree is pinned; NOT a global uuid override). Validated in a
   disposable worktree first: `npm ci`, 164/164 jest, `tsc --noEmit`, eslint, expo config,
   Android prebuild, and a direct xcode/uuid CJS smoke test (xcode uses only `uuid.v4()`).
   uuid@11.1.1 is the advisory's first patched version and still ships `dist/cjs/index.js`.
   Known future task: uuid v11 line is expected to be deprecated ~2028 — migrate to v12+/ESM
   or re-evaluate then.

## Remaining advisories (3) — monitor, no upstream fix published as of 2026-10-17

2. **node-forge 1.4.0 — HIGH** — CVE-2026-85393 / GHSA-86w9-cpqp-85rv
   "RSA PKCS#1 v1.5 signature verification accepts extra nested DigestAlgorithm elements".
   Vulnerable range `<= 1.4.0` (1.4.0 is the latest published release; no fix exists).
   Path: `expo` → `@expo/cli` → node-forge `^1.3.3`; also `@expo/code-signing-certificates`.
   Practical exposure: EAS/CLI build tooling only; the app has no PKCS#1 verification flow.
   Action when a patched release appears: `npm update node-forge` (in-range bump, low risk).

3. **braces 3.0.3 — HIGH** — CVE-2026-93687 / GHSA-vfj7-8cjw-p6xm
   "stack-exhaustion denial of service through deeply nested patterns".
   Vulnerable range `<= 3.0.3` (3.0.3 is latest published; no fix exists).
   Path: `micromatch@4.0.8` (`^3.0.3`) ← jest/jest-expo (dev) and `@expo/metro`/`metro-file-map`
   (build-time). Shipped app bundle is not affected by dev-server/build tooling.
   Action: track upstream; patch release → in-range `npm update braces` (low risk).

4. **sprintf-js 1.0.3 — MODERATE** — CVE-2026-97058 / GHSA-hp3w-g68c-fv3c
   "denial of service through unbounded precision specifiers".
   Vulnerable range `<= 1.1.3` — even the latest release (1.1.3) is inside the vulnerable
   range, and the parent chain pins `~1.0.2` (`js-yaml@3` → `argparse@1`). Requires an
   upstream double-release before any lockfile remedy exists. GitHub scope: development
   (jest/istanbul + eslint tooling). Action: monitor only.

## Coordination warning (WorkBuddy worktree)

The WorkBuddy worktree (`Worktrees/projects/issue-12-review-verdict-gates-9f19a4cc`)
carries a large STAGED (uncommitted) rollback set, including removing `expo-dev-client`
from `qr-generator/package.json` + `package-lock.json`. Those staged snapshots were taken
BEFORE `074182d` and do **not** contain the uuid override. If its owner commits those
staged versions of the two dependency files unchanged, the override (and CVE-2026-41907
fix) would be silently dropped from `main`. The owner must re-derive their staged diff
against current `main` before committing the qr-generator dep files. Their staged set has
not been touched by OpenCode (verified byte-identical before/after, 2026-10-17).

## Verification status for the commit

- Commit `074182d` (`fix(qr-generator): pin uuid 11.1.1 for xcode via scoped override
  (CVE-2026-41907)`) changes exactly 2 files: `qr-generator/package.json` (+5) and
  `qr-generator/package-lock.json` (8+/5−, blob `0ff5a62..63524b3`).
- Checks at the commit: `npm ci` clean; `npm ls uuid xcode` = `xcode@3.0.1 → uuid@11.1.1
  (overridden)`; jest 164/164; `tsc --noEmit` clean; eslint clean; `expo config --type
  public` clean; `npm audit` no longer reports uuid.
- Pre-existing (NOT introduced by this change): `expo-doctor` reports 5 SDK-57 patch
  versions behind current recommendations (expo 57.0.27 vs ~57.0.30 etc.), 20/21 checks.
- iOS `expo prebuild` native generation is skipped on Windows (requires macOS/Linux);
  the pbxproj structural A/B comparison was not executable on this host. Android prebuild
  generation and the xcode/uuid smoke test passed.
- qr-generator remains EXCLUDED from the canonical evidence contract
  (`.agents/evidence/verification.json`); this note is the record of that product's state.
