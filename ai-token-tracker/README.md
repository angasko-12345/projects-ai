# AI Token Tracker

Local desktop GUI that shows how many AI tokens you are using across your tools, starting with OpenCode. Everything is stored in a local SQLite file; no cloud backend, no accounts.

## Requirements

- Python 3.10+
- PySide6 (`pip install PySide6`)

## Run

Double-click `AI-Token-Tracker.bat`, or from a terminal:

```bat
cd ai-token-tracker
python -m token_tracker
```

On launch the app imports OpenCode's usage history (read-only) and shows two tabs:

**Dashboard**
- tokens today / last 7 days / all time (with exact vs estimated split)
- daily usage chart (last 30 days, UTC days)
- two breakdown tables with a group-by selector (provider, model, project, agent), sortable by any column

**Recent events**
- every stored event, newest first, sortable by any column
- filter by free text (provider, model, agent, project), by provider, and by exact vs estimated
- estimated rows are marked `estimated` in amber; exact rows say `exact`

Header actions:
- `Import OpenCode` re-syncs (safe to repeat; rows OpenCode updated since the last import are refreshed; last sync result stays visible in the header)
- `Import CSV/JSON` imports a previously exported file (validated before writing, idempotent; a bad row aborts the whole import)
- `Export CSV` / `Export JSON` dump every normalized event (refuses to overwrite the tracker database itself)

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
