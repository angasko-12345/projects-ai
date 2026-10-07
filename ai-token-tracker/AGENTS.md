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
    __main__.py   -> launches the PySide6 GUI
    gui.py        -> MainWindow, DailyChart, theme toggle, import/export actions
    db.py         -> SQLite storage, insert_events upsert/dedup, totals, breakdown, export/import
    model.py      -> UsageEvent dataclass, normalize_event, make_event_id, make_dedup_key
    collectors.py -> REGISTRY, find_*/collect_* functions, sync_all
    opencode.py   -> OpenCode-specific collector
    format.py     -> human_tokens, human_count, human_cost
  tests/
    test_opencode.py   -> OpenCode collector and idempotency
    test_collectors.py -> all other collectors, cross-source dedup, sync_all, legacy import
    test_db.py         -> storage, totals, breakdown, export/import round-trips
    test_format.py     -> human formatters
```

Data flow: collectors read local agent stores read-only -> `normalize_event` validates and produces `UsageEvent` -> `db.insert_events` upserts with idempotency -> GUI queries `db.totals`, `db.breakdown`, `db.daily_series`, `db.all_events`.

Database default location: `D:/ai-token-tracker/usage.db` when `D:` exists, otherwise `%LOCALAPPDATA%/ai-token-tracker/usage.db`. Override with `AI_TOKEN_TRACKER_DB`.

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

## Verification

The canonical test result for this product is recorded in `.agents/evidence/verification.json`. Do not restate a test count in instruction files; point readers at that file instead.
