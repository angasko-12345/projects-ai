# Plan — PR #21 sync, review, and merge (2026-10-20)

Status: complete. Merged as `4d1c933`. Output: `.agents/outputs/pr21-codex-least-privilege.md`.

Single writer: this session, in a dedicated worktree at `D:/admin/code/pr21-worktree`
so the primary tree's unrelated changes were never staged or moved.

| # | Subtask | Status |
|---|---|---|
| 1 | Inspect PR #21: base/head SHAs, diff, reviews, checks | done |
| 2 | Isolate the branch in its own worktree (primary tree left alone) | done |
| 3 | Merge `origin/main` (`c825a1b`) into the branch without discarding changes | done — `ac7cae9`; the agentops patch is byte-identical before and after |
| 4 | Resolve the sole conflict (`.agents/evidence/verification.json`) by regenerating, never by choosing a side | done |
| 5 | Confirm least-privilege Codex args survive (`--sandbox workspace-write`, no `--approve-for-me`) | done — verified in source, in tests, and against `codex-cli 0.163.0 --help` |
| 6 | Run the full AgentOps suite on Windows at the final head | done — 831 tests, OK, 4 skips, exit 0 at `c34f4d7` |
| 7 | Regenerate evidence with `tools/evidence/generate.py` | done — clean clones of `ac7cae9`, all four products `pass` |
| 8 | Re-run checks, inspect the final diff | done — 4/4 checks pass, 6 files, 116+/20− |
| 9 | Mark ready for review, then review and merge on explicit user instruction | done — ready, then merged `4d1c933` |
