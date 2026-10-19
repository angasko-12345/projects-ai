# ai-token-tracker — data-pipeline audit, fixes, validation (2026-10-19)

Status: complete. Commit `ad73f30` on `main` (pushed). Evidence: `.agents/evidence/verification.json`
(clean clone at `ad73f309938c`, `python tools/evidence/check.py` green).

## Source-by-source coverage

| # | Source | Mode | Found | Exact? | Notes |
|---|--------|------|-------|--------|-------|
| 1 | opencode | collector (JSONL store) | 6,721 events | yes | provider names normalized |
| 2 | OpenCode session (omp) | collector | 7,497 events | yes | |
| 3 | pi/coding-agent | collector (JSONL) | 3,015 events | yes | |
| 4 | codex | collector (JSONL) | 128 events | yes | |
| 5 | claude | collector (JSONL) | 109 events | usage yes; cost no | real store has `content_block_delta` usage but **no costUSD** (0/449 assistant lines) |
| 6 | copilot | collector (JSONL) | 21 events | yes | `tokenDetails.input.tokenCount` is the *exclusive* non-cached input; `usage.outputTokens` includes reasoning |
| 7 | cline | collector (JSONL) | 3,879 events | yes | |
| 8 | hermes | collector (JSONL) | 36 events | usage yes; cost no | cost stays NULL when no pricing result (store cost_status: None×25, 'estimated'×7, 'unknown'×4, all `0.0 actual+estimated`) |
| 9 | dsh | collector | 270 events | yes | |
| 10 | kilo | collector (SQLite) | 5 events | yes | real DB actually resolves now (`~/.local/share/kilo/kilo.db`; previously missing → silent no-op) |
| 11 | cursor | unavailable | — | — | no on-disk usage store found; documented skip |
| 12 | windsurf | unavailable | — | — | documented skip |
| 13 | gemini-cli | unavailable | — | — | usage in `.json` transcripts not parsed; documented skip |
| 14 | qwen-code | unavailable | — | — | documented skip |
| 15 | crush | unavailable | — | — | documented skip |
| 16 | aider | unavailable | — | — | requires explicit usage log; documented skip |
| 17 | zed | unavailable | — | — | documented skip |
| 18 | openrouter (remote API) | unavailable | — | — | would require `OPENROUTER_API_KEY`; identified as next most valuable integration |

Validation totals (real stores, 2026-10-19): **21,681 events / 3,449,207,906 tokens / $0.38 reported cost / 0 estimated**; per-event invariant (input+cache+output vs total) holds; duplicate-request dedup and synthetic-row filtering verified; re-collection is idempotent (`unchanged` on resync); zero cross-source dedup collisions.

## Changes (all regression-test-first; tests confirmed failing before implementation)

1. **Copilot semantics** — `tokenDetails.input.tokenCount` treated as the exclusive non-cached input (verified against real store sessions `0642f84c`/`c87d49c1` where 33 + 436,512 + 75,452 = 511,997); `usage.outputTokens` already includes reasoning, so it is not double-counted.
2. **Kilo path** — `find_kilo()` now checks `~/.local/share/kilo/kilo.db` (real DB has 5 usage messages over 9 sessions); without this the source was silently uncollectable.
3. **Claude semantics** — documents `<synthetic>` placeholder rows are skipped (some carry zero-usage blocks); provider alias `open_router` → `openrouter` (real store shows `anthropic/open_router/...`); the earliest usage-bearing record is preferred for duplicate `requestId`s (real store: 108/111 IDs duplicated); cost stays `None` because the real store has no `costUSD` field.
4. **Hermes cost policy** — cost is NULL on `cost_status` `None`/`'unknown'` with `0.0 actual+estimated`, instead of fabricated `$0.00`; provenance (`exact=True` vs estimated) preserved everywhere — missing data never becomes zero usage or zero cost.
5. **Error capability** — sync failures record `capability="error"` (red GUI dot) distinct from the designed `unavailable` (gray dot), previously silent.
6. **Timestamp robustness** — removed the second `_canonical()` parse path in `collectors.py`; timestamps go raw into `normalize_event`/`parse_timestamp` (a single robust parser that no longer crashes on `None`).
7. **Cost readout** — `db.totals` adds `cost_events`, `format.cost_summary()` formats it, the all-time GUI stat card shows reported cost (`cost $0.25 across 1 event` in the offscreen smoke test).

## Verification

- Baseline: **46 tests OK** (working tree); after fixes: **52 tests OK (3.0 s)**, `python -m unittest discover -s tests` from `ai-token-tracker/`.
- Clean-clone evidence: regenerated at the pushed tip, 52 pass / 0 skipped; `check.py` green for all four products.
- Real-store validation: all 10 sources collect; resync idempotent; per-event invariant holds; provider aliasing resolved; no dedup collisions.
- GUI smoke (offscreen): life card `cost $0.25 across 1 event`; `● Error` label on error capability.

## Known inaccuracies / limitations

- Claude: 437 of 449 assistant lines use `anthropic/open_router/...`; provider is the *routed* provider, not OpenRouter's internal model mapping. There is no cost data at all for Claude — the suite reports Claude cost as missing, not zero.
- Hermes has 4 partial rows with `cost_status='unknown'` and 25 with none; no pricing fallback, so cost stays NULL.
- Reported cost across all sources comes only from sources that provide it today (`$0.38` over 3.4B tokens); it is a floor, not an estimate.
- The 7 unavailable sources were audited for stores not for API keys; only verified-absent facts are recorded in README.
- A shared `C:\Users\admin\AppData\Local\Temp\opencode\inspect.py` polluted stdlib imports while probing — scripts moved to a subdirectory; unrelated to the code.

## Next most valuable integration

**OpenRouter provider-side activity import** (`/api/v1/activity`, `OPENROUTER_API_KEY`): OpenRouter is already the single biggest route in the Claude Code store (437/449 records) and exposes provider-side per-model generation/cost records that the local JSONL stores never contain. It is additive (a new collector behind one env var), idempotent (page through activity ordered by created time), and fills the exact gap the local stores leave: real dollars billed rather than absent cost fields.
