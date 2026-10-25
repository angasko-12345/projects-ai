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

## Headroom (remaining limits, headless)

`python -m token_tracker headroom` reports how much of each provider's usage window is used and when it resets, from data already on the machine. It never estimates a limit from token counts, never reads a stored login or OAuth token, and never stores a key. It is separate from the GUI and the usage database: no PySide6 import, no writes to `usage.db`.

```powershell
cd ai-token-tracker
python -m token_tracker headroom                 # short table, times in local time
python -m token_tracker headroom --json          # versioned JSON (schema below)
python -m token_tracker headroom --json --out "$env:LOCALAPPDATA\ai-token-tracker\headroom.json"
```

Options: `--offline` (skip network sources), `--codex-dir DIR`, `--claude-file FILE`, `--stale-after SECONDS`, `--max-codex-files N` (default 40), `--out FILE` (atomic write, for a consumer that reads a file). Exit code is 0 whenever a document is produced, including when every source is missing; 2 on bad arguments.

### Sources

| Provider | Source (`source`) | What is read | Needs |
|---|---|---|---|
| `codex` | `codex-rollout` | `payload.rate_limits` on `event_msg`/`token_count` lines in the newest Codex rollout files (`sessions/` and `archived_sessions/`, same discovery as the usage collector, `CODEX_SESSIONS` overrides) | nothing; read-only |
| `claude` | `claude-statusline` | the local ledger written by the opt-in status line tee (below) | installing the tee once |
| `openrouter` | `openrouter-key` | `GET https://openrouter.ai/api/v1/key` | `OPENROUTER_API_KEY` already set in the environment; otherwise skipped. The key goes only to OpenRouter and is never written or printed |

Known gaps, reported rather than filled in:
- `codex exec` and API-key Codex sessions write `rate_limits: null`; if those are the only recent rollouts, Codex is `no_data`.
- Claude Code exposes `rate_limits` only to the status line, only for Pro/Max (or a spend-limited gateway), and only after the first response. Headless `claude -p` runs no status line, so it never feeds the ledger.
- One Codex and one Claude account per machine are assumed; per-account keys are future work.

### Claude status line tee (opt-in)

`token_tracker/statusline_tee.py` is stdlib-only and runs as a plain script from any directory. It reads the status line JSON from stdin, appends `{observed_at, session_id, claude_version, rate_limits}` to the ledger when `rate_limits` is present, then runs your existing status line command with the same stdin and passes its stdout and exit code through unchanged. A failure to write the ledger never breaks the status line. The ledger is capped at about 1 MB (rewritten with the newest 500 records).

Ledger location: `TOKEN_TRACKER_CLAUDE_STATUSLINE`, else `TOKEN_TRACKER_LIMITS_DIR/claude-statusline.jsonl`, else `%LOCALAPPDATA%\ai-token-tracker\limits\claude-statusline.jsonl` on Windows (`$XDG_STATE_HOME`, default `~/.local/state`, elsewhere).

In `~/.claude/settings.json` (forward slashes; Claude Code runs the command through Git Bash or PowerShell):

```json
{
  "statusLine": {
    "type": "command",
    "command": "python C:/path/to/projects-ai/ai-token-tracker/token_tracker/statusline_tee.py --command \"powershell -NoProfile -File C:/Users/you/.claude/statusline.ps1\""
  }
}
```

Without `--command` (or `-- argv...`) the tee prints a short `5h 23% | 7d 41%` line itself. To uninstall, put your original command back.

### JSON schema (`schema_version` 1)

Stable contract for consumers such as an AgentOps routing gate. Fields are only ever added within version 1; a rename, removal or meaning change bumps `schema_version`. Consumers should reject an unknown `schema` or a `schema_version` they do not know, and treat that like a missing file.

```json
{
  "schema": "ai-token-tracker/headroom",
  "schema_version": 1,
  "generated_at": "2027-01-15T08:00:00Z",
  "thresholds": {"warn_percent": 80.0, "critical_percent": 95.0},
  "providers": [
    {
      "provider": "codex",
      "source": "codex-rollout",
      "status": "ok",
      "detail": "newest snapshot per window from 12 newest rollout file(s)",
      "location": "C:\\Users\\you\\.codex\\sessions",
      "warnings": [],
      "windows": [
        {
          "source": "codex-rollout",
          "window": "5h",
          "limit_id": "codex",
          "window_minutes": 300,
          "unit": "percent",
          "used": 62.0,
          "limit": 100.0,
          "remaining": 38.0,
          "used_percent": 62.0,
          "resets_at": "2027-01-15T09:00:00Z",
          "resets_at_basis": "reported",
          "reset_interval": null,
          "as_of": "2027-01-15T07:50:00Z",
          "age_seconds": 600,
          "freshness": "fresh",
          "state": "ok",
          "reached": false,
          "elapsed_percent": 76.667,
          "pace": 0.809,
          "plan_type": "plus"
        }
      ]
    }
  ]
}
```

All timestamps are UTC ISO-8601 (`Z`). `providers` always lists `codex`, `claude`, `openrouter`, in that order.

Provider fields:

| Field | Meaning |
|---|---|
| `provider` | `codex`, `claude` or `openrouter` |
| `source` | where the readings come from (table above) |
| `status` | `ok` (has windows), `no_data` (source present, no limit readings), `not_configured` (source absent or opt-in not set up), `skipped` (`--offline`), `error` (source could not be read; `detail` says why) |
| `detail`, `location` | human explanation; the file/dir/URL read (never a key) |
| `warnings` | skipped malformed lines or windows, counted |
| `windows` | one entry per window, empty unless `status` is `ok` |

Window fields:

| Field | Meaning |
|---|---|
| `window` | label by length, never by slot: `5h`, `weekly`, other `Nh`/`Nd`; `spend` (Claude gateway spend limit); OpenRouter `key_credits`, `budget`, `free_daily_requests` |
| `limit_id` | provider's quota id: Codex `codex`/`premium`/...; Claude `five_hour`/`seven_day`/`spend_limit`; OpenRouter `key_limit`, the budget `scope`, `free_model_daily_requests` |
| `window_minutes` | window length, or `null` when the provider does not state one |
| `unit` | `percent` (used/limit out of 100), `usd`, or `requests` |
| `used`, `limit`, `remaining` | in `unit`; `null` when not reported |
| `used_percent` | 0-100 (can exceed 100 for spend), `null` when not derivable |
| `resets_at` | when the window resets; `null` when not reported (never guessed) |
| `resets_at_basis` | `reported`, or `documented_utc_day` for OpenRouter free requests (resets at 00:00 UTC per OpenRouter's docs) |
| `reset_interval` | provider-stated period (`daily`/`weekly`/`monthly`) when given |
| `as_of`, `age_seconds` | when the reading was observed, and how old it is now |
| `freshness` | `fresh`; `stale` (older than a quarter of the window, capped at 6 h; 1 h when the length is unknown; `--stale-after` overrides); `expired` (`resets_at` has passed, so the reading describes a window that is over); `unknown` (no observation time) |
| `state` | `ok` < 80% <= `warn` < 95% <= `critical`; `exhausted` at >= 100% or when Codex reports `rate_limit_reached_type` (applied to that snapshot's fullest window); `unknown` when expired or no percentage. A `stale` reading keeps its state: usage inside a window only rises until the reset, so a stale value is a lower bound |
| `reached` | the provider said this limit was hit |
| `elapsed_percent`, `pace` | how far through the window `as_of` was, and `used_percent / elapsed_percent` (> 1 means ahead of a sustainable rate); `null` when the window length or reset is unknown, or the window expired |
| `plan_type` | Codex plan (`plus`, `pro`, ...) when reported |

Suggested gate reading (not built here): route away from a window in `exhausted`/`critical`; treat `unknown`, `stale`, `no_data`, `not_configured` and `error` as "no trustworthy signal" under the gate's own policy; never read a missing number as 0%.

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
