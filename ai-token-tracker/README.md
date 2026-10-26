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
- the all-time card appends reported cost (`cost $X across N events`) when sources report cost; missing cost data never shows as $0.00
- daily usage chart (last 30 days, UTC days)
- three breakdown tables (by tool, by provider, by model), each with a group-by selector (tool, provider, model, agent), sortable by any column

**Sources**
- coverage indicator: `Tracked agents 10/10 · exact 10/10 · unavailable 7`
- rollup tables aggregating independently by agent, provider, and model
- collector status table: colored status dot (green exact / yellow estimated / gray unavailable / red error), source, tool, last successful sync, events imported, and the error message when the source is unavailable or a sync fails

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
| Kilo | `~/.kilo/share/kilo.db` or `~/.local/share/kilo/kilo.db` (`KILO_DB` overrides) | exact | full history |
| Hermes | Hermes session/usage SQLite (`HERMES_STATE_DB` overrides) | exact | full history |
| Codex | Codex rollout session files, live `sessions/` and `archived_sessions/` (`CODEX_SESSIONS` overrides) | exact | full history |
| Pi | Pi session JSONL dirs (`PI_SESSIONS` overrides) | exact | full history |
| Oh My Pi | Oh My Pi session JSONL dirs (`OMP_SESSIONS` overrides) | exact | full history |
| **GitHub Copilot** | Copilot CLI `events.jsonl` (AppData) | exact | full history |
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

OpenCode's message-level token sums reconcile exactly with its own session rollups, so imported events are marked **exact**. Messages or sessions without recorded usage are skipped, never guessed. Claude Code sessions carry no cost field, `<synthetic>` client placeholders (including zero-usage copies) are skipped rather than stored as zero-token requests, and Hermes rows whose pricing engine produced nothing store cost as missing (shown as `-`), never as a fabricated $0.00. Unavailable sources are shown with a gray dot and their reason in the status table; a sync that fails (locked file, missing store) shows a red error dot instead, so a transient failure is never reported as "audited and found unavailable"; estimates only ever enter the database through manual import, never from a collector.

## Agent vs provider, and deduplication

An agent and a provider are tracked independently: OpenCode -> OpenRouter -> model X and OMP -> OpenRouter -> model X are one agent attribution each, one shared provider. Content identity of a canonical usage event is `request_id` when a source provides one, otherwise a fallback key of provider + model + token counts + timestamp bucketed to 5 seconds. Content dedup applies only across different sources (the same underlying request seen from two sources collapses into one row, keeping the first-seen attribution); two events from the same source with different ids are never content-merged, and zero-token rows stay distinct.

## Configuration

Each collector looks for its source in layers, most specific first:

1. **Per-source environment variable** — `OPENCODE_DB`, `KILO_DB`, `HERMES_STATE_DB`, `CODEX_SESSIONS`, `PI_SESSIONS`, `OMP_SESSIONS`, `CLAUDE_PROJECTS`, `CLINE_SESSIONS`, `DSH_SESSIONS`. Point one at the source's own store and that source reads nothing else.
2. **The source's standard local path** — e.g. `~/.claude/projects`, `~/.codex/sessions`, Cline's `~/.cline/data/sessions`, Copilot's `%APPDATA%/Code/User/globalStorage/github.copilot-chat/session-state`.
3. **The per-agent data directory** — an optional directory holding collected agent trees laid out as `<data_dir>/<agent>/home/...`: `kilo/home/share/kilo.db`, `claude/home/projects`, `cline/home/data/sessions`, `dsh/sessions`, `pi/home/agent/sessions`, `oh-my-pi/home/agent/sessions`, `codex/home/sessions`, `hermes/state.db`, `copilot/home/session-state`.

Layer 3 is configured by, in order:

- the `TOKEN_TRACKER_DATA_DIR` environment variable, or
- `data_dir` in the config file — `%APPDATA%/ai-token-tracker/config.json` on Windows, `$XDG_CONFIG_HOME/ai-token-tracker/config.json` (else `~/.config/...`) everywhere else:

```json
{"data_dir": "~/.ai-token-tracker/cli_files"}
```

- otherwise it defaults to your home directory, so `~/kilo/home/share/kilo.db` and friends are checked before the collector reports the source as missing.

A configured location that does not exist is an error, not a silent fallback to the default: the source's status shows a red error dot naming the variable or config file and the path, so a typo never looks like "that agent is not installed". `~` is expanded in configured values.

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

Collector-verified inclusive/exclusive semantics:
- **GitHub Copilot**: `usage.inputTokens` is the total including cache reads/writes; `tokenDetails.input.tokenCount` is the exclusive non-cached input (`33 + 436512 + 75452 == 511997` in the live store) and is used directly. `usage.outputTokens` includes reasoning.
- **Claude Code**: Anthropic semantics, `input_tokens` excludes cache, `output_tokens` includes thinking (split out).
- **Codex**: `usage.input_tokens` includes cached input; `output_tokens` includes reasoning; both split. Per rollout file one record type is read: `token_usage_record` when present (newer CLIs also write `event_msg`/`token_count` for the same responses, which is then ignored so nothing counts twice), otherwise `token_count` (`info.last_token_usage`, or the delta of `info.total_token_usage`; repeated snapshots skipped). The sibling of the found sessions dir (`sessions` <-> `archived_sessions`) is scanned too; a rollout present in both is read once, live copy first.
- **Cline**: `inputTokens` includes cache reads/writes; split.
- **Hermes**: API-counted rollups where `input + output + cache_read + cache_write = api-reported total`.

## Tests

```bat
cd ai-token-tracker
python -m unittest discover -s tests
```
