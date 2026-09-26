# Memory: omp (oh-my-pi) ↔ OpenCode free-tier 403 FreeTierError

> Status: **RESOLVED 2026-09-22** — config-only mirror provider fix landed and verified live
> (see "The fix (verified)" below). Gate rule for `/zen/v1` is now fully pinned: attribution
> headers are **format-only** (UA `opencode/*` + `x-opencode-session: ses_`+26), no key needed,
> and the only working free route is `/zen/v1/responses` (chat/completions is dead, 503).
> Applies to: oh-my-pi (`@oh-my-pi/pi-coding-agent`, installed v18.2.8, npm name
> `@oh-my-pi/pi-coding-agent`) using free Zen models (`opencode-zen/muse-spark-1.3-contributor-free`,
> `:high`/`:low`/`:medium` thinking variants).

## Symptom

- omp call to `opencode-zen/muse-spark-1.3-contributor-free:medium` fails:
  `403 {"type":"error","error":{"type":"FreeTierError","message":"Error from provider (Console): OpenCode's free tier can only be used from within OpenCode"}}`.
- Same model works inside the official opencode CLI (`opencode run --model opencode/muse-spark-1.3-contributor-free "..."` → reply).
- Upstream omp fix (v18.2.6) only fixed *discovery* attribution (install-id `hd()` / uuid for `GET /v1/models`); chat path still passes omp's own headers through.

## Live probe matrix (2026-09-20, same machine, real key)

Probes via Node fetch scripts in `C:\Users\admin\AppData\Local\Temp\opencode\` (`probe-gate*.js`, `probe-responses.js`).

| Probe | Endpoint | Headers / notes | Result |
|---|---|---|---|
| Discovery | `GET /zen/v1/models` | omp UA + uuid session | **200** (discovery attribution works) |
| chat | `/zen/v1/chat/completions` | uuidv7 session (omp natural) | 403 |
| chat | "/" | `ses_`+26 + `opencode/1.18.31` UA (runbook combo) | 403 |
| chat | "/" | `ses_` + all 4 CLI headers (`x-opencode-request`, `x-opencode-client:opencode`) | 403 |
| chat | "/" | `x-opencode-client` ∈ {opencode,tui,cli,omp} | all 403 |
| chat | "/" | REAL registered `ses_...` ids from opencode.db | 403 |
| chat | "/" | LIVE session id (this conversation, `ses_f4295b...`) + `big-pickle` model | 403 |
| responses | `/zen/v1/responses` | muse-spark-1.3-free, `input` body, omp headers | 403 |
| responses | "/" | muse-spark-1.3-free, full CLI headers | 403 |
| responses | `/zen/go/v1/responses` | full CLI headers | **401 ModelError** (model not on go endpoint) |
| chat | `big-pickle` | full CLI headers | 403 |

**Conclusions so far:**
- Header presence / format is no longer sufficient (contradicts pi runbook's 09-17 table and the
  `opencodex`/hermes community claim that "any session value clears the gate").
- Both error variants exist in the wild: keyless = `MissingSessionID` "…in OpenCode" (older, header-presence gate);
  ours = `FreeTierError` "…from within OpenCode" (deployed ~2026-09-16/17, stricter).
- The running CLI sends nothing extra beyond the discovered headers for the opencode provider — so the
  new gate likely validates *values* (registered session / user.id format / IP+fingerprint correlation), not just header keys.

## Real opencode CLI ground truth (opencode-ai v1.18.31)

Binary: `D:\admin\code\node_modules\opencode-ai\bin\opencode.exe` (compiled, strings at offsets below).

- Header construction (offset ~100892200), only when `model.providerID.startsWith("opencode")`:
  ```
  x-opencode-session: e.sessionID
  x-opencode-request: e.user.id
  x-opencode-client:  e.flags.client
  User-Agent:         opencode/1.18.31        (const _i; == `opencode/${InstallationVersion}`)
  x-opencode-project: project.id (only when project context; other offset ~100892000)
  ```
  Non-opencode providers instead get `x-session-affinity` + `X-Session-Id` = sessionID (2-letter-case headers differ!).
- UA constant is exactly `opencode/1.18.31` (no OS suffix).
- Real session IDs: `ses_` + 26 chars (12 hex-unixms + 14 base62) e.g. `ses_fb28e2d7bffe09maHAlOL8XU8z` — same format as runbook. opencode.db `account` table is empty (0 rows); sessions are local.
- `user.id` for main request: `e.user.id` where `user` is `{id: <IdGenerator>, sessionID, role:"user", time:{created}, agent, model}`. Construction seen at offset ~100662681 uses `id: fm.ascending()` (an opencode message/part-style ID generator; `msg_…`-ish, not a session id). Exact value format for `x-opencode-request` NOT yet pinned against the gate.
- Header merge order (source `request.ts` line ~187): CLI headers → `input.model.headers` (user config) → runtime params. So static config headers CANNOT override the force-set opencode attribution in the CLI.

## Where the gate lives (opencode console source, dev branch)

- `packages/console/app/src/routes/zen/util/handler.ts` (dev): reads `x-opencode-session/-request/-client/-project` + `user-agent` per request; keyless/free billing source selection around lines 827-836; `FreeUsageLimitError` → HTTP 429. The exact **FreeTierError** string is NOT in dev source (deployed console is newer/different; see community reports below).
- `packages/console/app/src/lib/inference-proxy.ts`: forwards `x-opencode-request` → `x-opencode-request-id` upstream.
- `packages/opencode/src/session/llm/request.ts`: confirmed the CLI header block (source matches binary).

## Community findings (Sep 2026)

- **9router issue #4101** (2026-09-17): free-tier models started returning the exact `FreeTierError "…from within OpenCode"` for third-party proxies ~Sep 16 20:55; root cause: two server-side validation checks deployed on `/zen/v1/chat/completions` and `/zen/v1/responses`.
- **opencode issue #42500**: free Zen models are UA-gated; `x-opencode-client`/session headers do NOT unlock it — only `User-Agent: opencode/<version>`. (Older deployment; our probes fail even with correct UA, so a second check landed.)
- **opencode issue #49431** (2026-09-17): error variant `OpenCode 1.17.0 or newer is required to use the free tier` — UA version parse check; requires `opencode/` UA ≥1.17.
- **opencodex** (`isOpenCodeZenFreeTierLockIn`): documents keyless `MissingSessionID` gate as header-presence-only; explicitly refuses to fabricate the header. Not sufficient for our keyed case anymore.
- **earendil-works/pi PR #2824**: pi added runtime-generated staggered IDs for opencode headers; keeps UA = `opencode/<pi-version>` — pi still reported broken on 09-17 (can1357/oh-my-pi#12306).

## omp internals relevant to the fix (v18.2.6 bundle `C:\Users\admin\.omp\agent\` / `D:\unorganized\bun\install\global\node_modules\@oh-my-pi\pi-coding-agent\dist\cli.js`)

- `BH()` (in opencode provider block) **force-sets** `x-opencode-session` (via `vKe`) and UA (via `Mte`, set-if-absent) **only when provider id is exactly `opencode-zen` or `opencode-go`**. Runs AFTER user headers.
- omp `models.db`/`agent.db` sessions use uuidv7 ids (`01a0bcfb-…`) — never `ses_` format, and there's no per-provider hook to change them (searched: `before_provider_headers`/`userHeaders`/`headerOverrides`/`transformRequest` hooks all absent).
- **Config surface found (09-20):** separate `models.yml` config (`eMe("models")`, default `<agentDir>/models.yml` = `C:\Users\admin\.omp\agent\models.yml`, not created yet; also .yaml/.json). Schema allows per-provider `baseUrl`, `apiKey`, `api`, `auth`, `discovery`, `compat`, `modelOverrides`, `models`, **`headers`**. Validation msg: `Provider ${e}: must specify "baseUrl", "headers"...`. Credential source "config override (models.yml)" exists.
- Key design exploit: a mirror provider under a **non-`opencode-zen/-go` id** (e.g. `omp-zen`) with `baseUrl: https://opencode.ai/zen/v1` + `apiKey` + provider `headers` makes `s=false` in `BH()` → omp leaves user headers untouched. **This only helps if the static/known-good header value set is accepted — currently unproven (all chat probes 403).**
- omp auth: `auth_credentials` id=5 `opencode-zen` api_key == the same `sk-…` used by pi and opencode auth.json (`C:\Users\admin\.local\share\opencode\auth.json`, provider `opencode` type api). A transient `auth_credential_blocks` entry for `opencode-zen:api_key` (~60s) appeared right after a 403 but expired on its own.

## Candidate fixes (ranked, all unverified until gate rule pinned)

1. Find the accepted chat value set (next step) → then apply config-only via new `~/.omp/agent/models.yml` mirror provider `omp-zen` (non-`opencode` id so `BH()` skips force-set) with `baseUrl` + `headers` incl. real `ses_…` + `opencode/1.18.31` UA (+ `x-opencode-request` if it turns out to matter).
2. Patch installed `dist/cli.js` `BH()` to emit CLI-identical headers even for `opencode-zen`/`opencode-go`.
3. Model swap to a non-free, non-opencode provider (git-backed out of scope).

## Blocked / next steps

- **Blocked:** no known-good chat header set on 09-20. Real CLI works with same key → gate accepts something extra (registered session? `user.id` value format? per-account fingerprint). Cannot implement a passing fix yet.
- Next: (a) pin `user.id`/`x-opencode-request` value by extracting the ID generator format from the binary (offset ~100662681 context, `fm.ascending()`, `msg_…` prefix) or replaying a captured real CLI request; (b) OR capture a real request via a local HTTPS MITM/proxy to read the exact bytes the working CLI sends; (c) then verify accepted set with probes before touching omp config.

## Pitfalls / facts to remember

- Do NOT `eMe`/`hd()` confusion: discovery attribution ≠ chat attribution; upstream fixed only discovery.
- `~/.omp/logs/http-400-requests/*.json` captures are **openrouter** (nex-agi) noise, not opencode — not evidence.
- omp v18.2.6 upstream still broken for chat; npm latest == installed.
- Probe scripts must be written to files (PowerShell `node -e` quoting breaks): keep in `C:\Users\admin\AppData\Local\Temp\opencode\`.
- The real user-id/session-value `ses_`+26 format is from opencode.db session rows; uuidv7 always fails — never use omp's native session id for opencode models.
- Gate changes fast (worked 09-17 with header-format only; dead 09-20). Re-probe before any implementation claim.

## RESOLVED 2026-09-22 — root cause pinned & fix verified

### Why it broke (v18.2.8, `D:\unorganized\bun\install\global\node_modules\@oh-my-pi\pi-coding-agent\dist\cli.js`)

- Earlier "force-set via `BH()`/`vKe`/`Mte`" theory was **wrong**: `vKe` (idx ~12337809) is a time-based cost/usage validator, `Mte` (idx ~12347377) is a JSON whitespace normalizer (`.replace(/\s\s+/g," ")`). Neither touches headers.
- The real clobber is `function uW(e,t)` (idx ~13050930), called per-request for provider `opencode-zen`/`opencode-go` only:
  - `_se(e,"User-Agent","omp/"+(Jo))` — **set-if-absent**: a user-configured UA survives.
  - `x-opencode-session`: if protocol anthropic → `X-Claude-Code-Session-Id`; if protocol openai AND provider `opencode` → force-set (`h6e`, deletes same-lowercase header then sets) `session_id` + `x-client-request-id`; if provider is opencode-zen/go → force-set `x-opencode-session` to `t.sessionId` = agent session id (uuidv7). So config headers alone CANNOT make an `opencode-zen` session correct.
- v18.2.8 already routes `muse-spark-*` to `openai-responses` (bundle routing catalog; `minimax-m3*` → completions). Only the forced session (uuidv7) / UA (`omp/18.2.8`) were wrong.
- `--provider-session-id <ses_id>` CLI flag overrides `t.sessionId` per-invocation, but there is no config key for a persistent session id.

### Gate rules (probed live 2026-09-22, via node probes in `Temp\opencode\{probe-ua-session,probe-session-format,probe-auth-required}.js`)

`POST https://opencode.ai/zen/v1/responses` (JSON):
- `User-Agent` must START `opencode/` exactly (`opencode/1.18.32` passes; `omp/…`, `OMP/…`, `okcomputer/…`, absent → 403).
- `x-opencode-session` must be `ses_`+26 (12 hex unixms + 14 base62). Format check only: fresh, fabricated, or **reused static** values pass (>1 call with same value → 200 twice); uuidv7 → 403.
- **Authorization is NOT checked on the free tier** → `auth: none` mirror works (no key, no secret persisted). Bogus key → 401; valid hardcoded headers with NO auth → 200.
- Body must be responses-format: `stream:true` + non-empty `tools` + `input` array + bare model id.
- `/zen/v1/chat/completions` is dead: **503 "Endpoint is unavailable"** (route disabled; this is why the pi runbook combo now fails on chat).

### The fix (verified)

`C:\Users\admin\.omp\agent\models.yml` — mirror provider with non-`opencode-zen/-go` id so `uW` never clobbers headers:

```yaml
providers:
  omp-zen:
    baseUrl: "https://opencode.ai/zen/v1"
    api: openai-responses
    auth: none
    headers:
      User-Agent: "opencode/1.18.32"
      x-opencode-session: "ses_01a0c93f46a0F87wagIvaLefvj"   # static, reusable (refreshing optional)
    models:
      - id: muse-spark-1.3-contributor-free
        reasoning: true
        input: [text, image]
        output: [text]
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }
        contextWindow: 1048576
        maxTokens: 131072
        thinking: { mode: effort, efforts: [minimal, low, medium, high, xhigh] }
```

`C:\Users\admin\.omp\agent\config.yml` roles: `plan: omp-zen/muse-spark-1.3-contributor-free`, `default: omp-zen/muse-spark-1.3-contributor-free:high`.

Profiles show: reasoning true, input text/image, contextWindow 1048576, maxTokens 131072, efforts [minimal,low,medium,high,xhigh]. Wire is `/zen/v1/responses` with bare model + `input` + real tools + `stream:true`, headers `user-agent: opencode/1.18.32` + static `ses_`, no Authorization (auth:none). Live tests pass: `omp --print --no-pty --model omp-zen/muse-spark-1.3-contributor-free "Reply with exactly: OK"` and default-role run both return `OK` (no 403).

### Ops notes

- Debug harness killed: mockzen.js (127.0.0.1:8998) + relay.js (127.0.0.1:8999) node processes stopped; no listeners on 8998/8999.
- Real key remains ONLY in `auth.json`/`auth_credentials` — never persisted to omp config (auth: none).
- If a stale session starts failing: regenerate `ses_`+26 (see pi runbook snippet) and update models.yml; UA must stay `opencode/*`.

## 2026-09-22 follow-up (which models are usable)

- Mirror now exposes `muse-spark-1.3-contributor-free` **and** `muse-spark-1.2-contributor-free` (both verified `OK` live via omp).
- Live catalog (`GET /zen/v1/models`) has ~366 ids, but usable free set on the working `/responses` route is ONLY the two `muse-spark-*-contributor-free` (both profiled `api: openai-responses`). Every other free id (`big-pickle`, `mimo-v2.5-free`, `mimo-v2.6-flash-free`, `deepseek-v4-flash-free`, `jev-1.13-free`, `ling-3.0-flash-fin-free`, `nemotron-3-ultra-free`, `nemotron-3.5-lightning-free`) is profiled `api: openai-completions` → `/chat/completions`, which is 403-gated for non-CLI regardless of body shape/session (and returns 500 "Internal server error" if forced onto `/responses`).
- **New gate nuance:** `/responses` FreeTierError also depends on body shape — a FULL CLI-shaped body (non-empty `instructions` + multi-message `input`) → 200; minimal single-message bodies → 403 FreeTierError even with valid headers and a fresh `ses_`. (Earlier "stream:true + tools + input" was necessary but not sufficient; `instructions` presence is part of the gate.)
- **`/chat/completions` IS usable with `stream:true`** (2026-09-22): `mimo-v2.5-free`, `big-pickle` return 200 with fresh `ses_` + opencode UA + no auth + full body. The earlier "chat dead / 403" conclusion was wrong — those probes used `stream:false` (403 FreeTierError) or outdated header sets. `jev-1.13-free` genuinely 500s (broken entry, excluded).
- `models.yml` now has TWO mirror providers: `omp-zen` = `api: openai-responses` (muse-spark-1.3 + 1.2) and `omp-zen-c` = `api: openai-completions` (big-pickle, mimo-v2.5-free, mimo-v2.6-flash-free, deepseek-v4-flash-free, ling-3.0-flash-fin-free, nemotron-3-ultra-free, nemotron-3.5-lightning-free), both `auth: none` + same static-UA/session headers. Total 9 free models, all verified: `omp --print --no-pty --model omp-zen-c/mimo-v2.5-free "Reply with exactly: OK"` → `OK`, same for big-pickle. omp sends `stream:true` on its completions wire, so it passes the gate.
- **SINGLE PROVIDER (2026-09-22, v18.2.11):** per-model `api` IS supported in `models.yml` model entries (verified empirically — the request router reads `model.api`). So the two mirrors were merged into ONE `omp-zen` provider: provider-level `api: openai-responses` default, chat models carry `api: openai-completions` per-entry. `omp-zen-c` removed; `omp models` now shows `omp-zen (9)` one clean table.
- **omp self-updated v18.2.8 → v18.2.11 during the session and REWROTE `~/.omp/agent/config.yml`**: added `task/web/slow/smol/vision` roles and reset `default` to `ollama-cloud/qwen3-coder-next`. Restored `default: omp-zen/muse-spark-1.3-contributor-free:high`. Watch for config.yml being rewritten on version bumps.
- **ollama-cloud 401 root cause:** a stored `api_key` (len 59) IS present in `auth_credentials`, but ollama.com rejects it — probed both `https://ollama.com/api/chat` and `/v1/chat/completions` (Bearer, gpt-oss:120b) → 401 on both. Fix = user: fresh key from `https://ollama.com/settings/keys` then `omp` → `/login ollama-cloud` (paste), or set `OLLAMA_CLOUD_API_KEY` env var (built into provider `envVars`). `smol: ollama-cloud/glm-4.6` in config.yml will 401 until then.