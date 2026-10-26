# ai-token-tracker AGENTS.md

## Supported Python versions

- CPython 3.10+
- CPython 3.14+ preferred (stdlib `compression.zstd` removes the `zstandard` backport dependency)

## Test command

```bat
cd ai-token-tracker
python -m unittest discover -s tests -v
```

All tests must pass. The test command is deterministic: fixtures are built in temporary directories, no test touches real local agent data, and no network is required.

## Architecture

```
ai-token-tracker/
  token_tracker/
    __main__.py   -> launches the PySide6 GUI; `headroom` subcommand goes to cli.py (no Qt import)
    cli.py        -> headless CLI: `headroom [--json]`
    headroom.py   -> headroom ledger: Codex rollout rate_limits, Claude status line ledger, OpenRouter /api/v1/key
    statusline_tee.py -> opt-in, stdlib-only Claude Code status line tee (records rate_limits locally)
    gui.py        -> MainWindow, DailyChart, theme toggle, import/export actions
    db.py         -> SQLite storage, insert_events upsert/dedup, totals, breakdown, export/import
    model.py      -> UsageEvent dataclass, normalize_event, make_event_id, make_dedup_key
    collectors.py -> REGISTRY, find_*/collect_* functions, sync_all
    config.py     -> layered data-directory resolution (env var, config file, home default)
    opencode.py   -> OpenCode-specific collector
    format.py     -> human_tokens, human_count, human_cost
  tests/
    test_opencode.py   -> OpenCode collector and idempotency
    test_collectors.py -> all other collectors, cross-source dedup, sync_all, legacy import
    test_codex_collector.py -> Codex record-type selection (token_count-only, dual-write) and archived_sessions
    test_db.py         -> storage, totals, breakdown, export/import round-trips
    test_format.py     -> human formatters
    test_headroom.py   -> headroom sources, schema shape, statusline tee, headless CLI
```

Data flow: collectors read local agent stores read-only -> `normalize_event` validates and produces `UsageEvent` -> `db.insert_events` upserts with idempotency -> GUI queries `db.totals`, `db.breakdown`, `db.daily_series`, `db.all_events`.

Database default location: `D:/ai-token-tracker/usage.db` when `D:` exists, otherwise `%LOCALAPPDATA%/ai-token-tracker/usage.db`. Override with `AI_TOKEN_TRACKER_DB`.

Collector data directory: the optional collected-agent tree root is resolved by `config.data_dir()` — `TOKEN_TRACKER_DATA_DIR`, then `data_dir` in `%APPDATA%/ai-token-tracker/config.json` (or `$XDG_CONFIG_HOME`/`~/.config`), then the user's home directory. No absolute machine-specific paths belong in collector code; a configured directory that is missing raises `config.ConfigError` and surfaces as a sync error.

## Collector rules

- A collector is added only when the source exposes usable per-request or per-message token usage locally.
- Collectors must read official local data read-only.
- Collectors scan full history on every sync; `db.insert_events` deduplication makes repeated syncs idempotent.
- Messages or sessions without recorded usage are skipped, never estimated.
- Registry order is attribution order: agent-side collectors run before provider-side ones.
- The registry also holds known-unavailable sources so the coverage indicator reports them honestly instead of silently omitting them.

## No-estimation policy

- `exact=True` means numbers came from provider-reported usage.
- `exact=False` means they are estimates.
- Estimates only ever enter the database through manual import, never from a collector.
- Unavailable sources produce explicit `capability="unavailable"` and an `error` reason; they never fabricate zero-valued fake usage.
- If a source denies access, only exposes aggregate totals, or lacks per-request token fields, it is marked unavailable with the reason recorded.
- A sync whose collection itself fails (locked/missing store, malformed data pulled a record apart) is recorded as `capability="error"`, distinct from a designed `unavailable`: the GUI shows a red error dot and does not count it as audited-and-unusable.
- Missing cost data is stored as NULL (shown as `-`), never as $0.00; a real source-reported $0.00 stays 0.0.

## Headroom rules

- Limit readings come only from provider-reported data already on the machine (or OpenRouter's key endpoint when the user has already set `OPENROUTER_API_KEY`). Never estimate a limit from token sums, never read stored OAuth tokens or logins, never write a key anywhere.
- A missing window is absent, never 0%. A reading past its `resets_at` is `expired`/`unknown`, never carried forward as current.
- The JSON schema in README is a contract: add fields freely, but any rename, removal or meaning change bumps `schema_version`.
- The headless path must not import PySide6 (a test enforces it).

## Verification

The canonical test result for this product is recorded in `.agents/evidence/verification.json`. Do not restate a test count in instruction files; point readers at that file instead.
