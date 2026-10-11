# AI Token Tracker

Local desktop GUI that shows how many AI tokens every AI agent and tool on your computer has consumed. Everything is stored in a local SQLite file; no cloud backend, no accounts.

## Requirements

- Python 3.10+
- PySide6 (`pip install PySide6`)

## Run

Double-click `AI-Token-Tracker.bat`, or from a terminal:

```bat
cd ai-token-tracker
python -m token_tracker
```

On launch the app syncs every registered collector (read-only against their local data) and shows three tabs:

**Dashboard**
- tokens today / last 7 days / last 30 days / all time, each with an exact vs estimated split
- daily usage chart (last 30 days, UTC days)
- three breakdown tables (by tool, by provider, by model), each with a group-by selector (tool, provider, model, agent), sortable by any column

**Sources**
- coverage indicator: `Tracked agents 10/10 · exact 10/10 · unavailable 7`
- rollup tables aggregating independently by agent, provider, and model
- collector status table: colored status dot (green exact / yellow estimated / gray unavailable), source, tool, last successful sync, events imported, and the error message when a source is unavailable

**Recent events**
- every stored event, newest first, sortable by any column
- filter by free text (provider, model, agent, project), by provider, and by exact vs estimated
- estimated rows are marked `estimated` in amber; exact rows say `exact`

Header actions:
- `Sync sources` runs every collector (safe to repeat; idempotent, see deduplication below; the last sync result stays visible in the header)
- `Import CSV/JSON` imports a previously exported file (validated before writing, idempotent; a bad row aborts the whole import)
- `Export CSV` / `Export JSON` dump every normalized event (refuses to overwrite the tracker database itself)
- `Dark` / `Light` toggles the theme; the choice persists across restarts (QSettings)

## Collectors

All collectors read official local data read-only and import full history on every sync, so repeated syncs never duplicate events.

| Source | Data | Capability | Historical |
|---|---|---|---|
| OpenCode | `~/.local/share/opencode/opencode.db` (`OPENCODE_DB` overrides) | exact | full history |
| Kilo | Kilo Code session SQLite (`KILO_DB` overrides) | exact | full history |
| Hermes | Hermes session/usage SQLite (`HERMES_STATE_DB` overrides) | exact | full history |
| Codex | Codex rollout session files (`CODEX_SESSIONS` overrides) | exact | full history |
| Pi | Pi session JSONL dirs (`PI_SESSIONS` overrides) | exact | full history |
| Oh My Pi | Oh My Pi session JSONL dirs (`OMP_SESSIONS` overrides) | exact | full history |
| GitHub Copilot | Copilot CLI `events.jsonl` (AppData) | exact | full history |
| **Claude Code** | `~/.claude/projects/*.jsonl` (`CLAUDE_PROJECTS` overrides) | exact | full history |
| **Cline** | `~/.cline/data/sessions/*.messages.json` (`CLINE_SESSIONS` overrides) | exact | full history |
| **DeepSeek Harness** | `~/dsh/sessions/**/session.v4.jsonl.zstd` (`DSH_SESSIONS` overrides) | exact | full history |
| OpenRouter | official usage API | unavailable (HTTP 403) | — |
| Google Gemini | aggregate-only exports (double-count risk) | unavailable | — |
| OpenAI | official usage API, no key configured | unavailable | — |
| **Antigravity** | local protobuf conversations (no usage fields) | unavailable | — |
| **FreeBuff/Codebuff** | context-size telemetry only | unavailable | — |
| **free-claude-code** | empty local DB; delegates to other CLIs | unavailable | — |
| **Gemini CLI** | config only; no session data | unavailable | — |

OpenCode's message-level token sums reconcile exactly with its own session rollups, so imported events are marked **exact**. Messages or sessions without recorded usage are skipped, never guessed. Unavailable sources are shown with a gray dot and their reason in the status table; estimates only ever enter the database through manual import, never from a collector.

## Agent vs provider, and deduplication

An agent and a provider are tracked independently: OpenCode -> OpenRouter -> model X and OMP -> OpenRouter -> model X are one agent attribution each, one shared provider. Content identity of a canonical usage event is `request_id` when a source provides one, otherwise a fallback key of provider + model + token counts + timestamp bucketed to 5 seconds. Content dedup applies only across different sources (the same underlying request seen from two sources collapses into one row, keeping the first-seen attribution); two events from the same source with different ids are never content-merged, and zero-token rows stay distinct.

## Database

Default location:

- `D:/ai-token-tracker/usage.db` when `D:` exists (Windows preferred location)
- otherwise `%LOCALAPPDATA%/ai-token-tracker/usage.db`

Override with the `AI_TOKEN_TRACKER_DB` environment variable.
If it names a directory, the tracker stores `usage.db` inside it.

## OpenCode data source

Reads OpenCode's own database (`~/.local/share/opencode/opencode.db`, `OPENCODE_DB` overrides), assistant messages only, `data.tokens` per message joined with `session.directory` for the project. OpenCode's message-level token sums reconcile exactly with its own session rollups, so imported events are marked **exact**. Messages without recorded usage are skipped, never guessed.

## Token accounting

`total = input + output + cache_read + cache_write + reasoning` (reasoning is a separate component in OpenCode's own totals, verified against its `tokens.total` on sampled messages).

## Tests

```bat
cd ai-token-tracker
python -m unittest discover -s tests
```
