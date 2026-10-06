# AI Token Tracker

Local desktop GUI that shows how many AI tokens you are using across your tools, starting with OpenCode. Everything is stored in a local SQLite file; no cloud backend, no accounts.

## Requirements

- Python 3.10+
- PySide6 (`pip install PySide6`)

## Run

```bat
cd ai-token-tracker
python -m token_tracker
```

On launch the app imports OpenCode's usage history (read-only), then shows:

- tokens today / last 7 days / all time (with exact vs estimated split)
- daily usage chart (last 30 days, UTC days)
- provider and model breakdown
- `Import OpenCode` to re-sync (safe to repeat; rows OpenCode updated since the last import are refreshed)
- `Export CSV` to dump every normalized event

## Database

Default location:

- `D:/ai-token-tracker/usage.db` when `D:` exists (Windows preferred location)
- otherwise `%LOCALAPPDATA%/ai-token-tracker/usage.db`

Override with the `AI_TOKEN_TRACKER_DB` environment variable.

## OpenCode data source

Reads OpenCode's own database (`~/.local/share/opencode/opencode.db`, `OPENCODE_DB` overrides), assistant messages only, `data.tokens` per message joined with `session.directory` for the project. OpenCode's message-level token sums reconcile exactly with its own session rollups, so imported events are marked **exact**. Messages without recorded usage are skipped, never guessed.

## Token accounting

`total = input + output + cache_read + cache_write + reasoning` (reasoning is a separate component in OpenCode's own totals, verified against its `tokens.total` on sampled messages).

## Tests

```bat
cd ai-token-tracker
python -m unittest discover -s tests
```
