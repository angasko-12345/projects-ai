# histyt — history YouTube pipeline (Phase 1)

Topic -> research -> script -> **script approval gate** -> voice -> visuals -> assemble -> thumbnail -> variation -> **render approval gate** -> private upload.

## Quickstart (PowerShell, Windows)

```powershell
Set-Location D:\admin\code\projects\history-yt
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
$env:PIP_CACHE_DIR="D:\tmp\pip-cache"; $env:TEMP="D:\tmp"; $env:TMP="D:\tmp"; $env:TMPDIR="D:\tmp"
pip install -e .[test]
python -m histyt doctor
python -m histyt topic-add "The Fall of Rome" --angle rise_fall
python -m histyt research <id> --url https://example.com/a --title "Source A"
python -m histyt research <id> --url https://example.com/b --title "Source B"
python -m histyt script <id>
python -m histyt approve-script <id> # GATE 1
python -m histyt run <id>              # stops at render gate (exit 2)
python -m histyt approve-render <id>   # GATE 2
python -m histyt upload <id>           # private only (real upload needs HISTYT_YT_TOKEN)
python -m histyt upload <id> --mock    # no network, no credentials (mock client)
python -m histyt shorts-plan <id> [--max-clips N]  # Phase 2: plan <=60s landscape clips
python -m histyt shorts-extract <id>               # cut shorts/short_NN.mp4 from final.mp4
python -m histyt status
python -m histyt topic-list
python -m pytest tests -q
```

Reproducible venv: since `.venv/` is git-ignored, recreate it as above
after `git clone`. Keep `$env:TMPDIR="D:\tmp"` set so pytest's
`pytest-of-*` dirs land on D: (never in the working tree).

## State layout

`runs/topics.jsonl` (registry) and per video:

```
runs/<video_id>/
  state.json  script.md  script_meta.json  sources.json  assets.json
  audio/chunk_NNN.wav  durations.json  captions.srt
  segments/seg_NNN.mp4  video.mp4  final.mp4
  thumbnail.png  thumbnail_meta.json  variation_report.json  upload.json
  shorts/shorts_plan.json  shorts/short_NN.mp4  shorts/shorts_manifest.json
```

## Config (config/channel.yaml)

| Key | Purpose |
|---|---|
| `niche`, `angle`, `language`, `voice` | channel settings (niche is config, never hardcoded) |
| `target_length_minutes` | target length |
| `script_templates` | >=4 named templates with section lists |
| `thresholds` | title-ngram similarity, phash distance, template window, history window |
| `tts` | provider (piper/fake), pacing bounds, piper_exe, model |
| `llm` | provider (fake/google/openrouter/local/openai-compatible), base_url, model |
| `youtube` | oauth_client_json (env `HISTYT_YT_CLIENT_JSON`), token_path (env `HISTYT_YT_TOKEN`), `contains_synthetic_media` |
| `ai_image` | provider disabled by default |
| `shorts` | max_clips (3), min_seconds (15), max_seconds (60) — Shorts planning (Phase 2) |
| `videos` | width/height/fps (1920x1080@30) |

## Phase 2: shorts (plan + landscape extract)

`shorts-plan` ranks consecutive-chunk windows (from `durations.json`, the same
timing captions use) by a deterministic, offline heuristic — hook questions,
named-entity density, digits, sweet-spot length — and picks non-overlapping
windows of at most `shorts.max_seconds` (default 60). It writes
`shorts/shorts_plan.json` with per-clip windows, scores, slugs, and title
suggestions (deterministic scaffolding — review before publishing). Requires
script approval.

`shorts-extract` cuts the planned windows from `final.mp4` into
`shorts/short_NN.mp4` (h264/yuv420p + aac, source aspect ratio, landscape) and
writes `shorts/shorts_manifest.json`. It is gated on render approval and the
pinned `final.mp4` hash — the same contract as upload. Vertical reframe,
caption re-render and any Shorts upload are out of scope by design; Shorts
publishing is a separate, private-only decision.

## Free providers

Google (`GOOGLE_API_KEY`) and OpenRouter (`OPENROUTER_API_KEY`) are free-tier
OpenAI-compatible endpoints. **Test with a tiny request first and report the
actual quota/region** returned by the provider. There is **no silent fallback**:
if a provider call fails, histyt raises `PipelineError` — you will see the error.

## Synthetic media disclosure

The YouTube API field `containsSyntheticMedia` is only sent when you explicitly
verify it against the official docs and set `youtube.contains_synthetic_media`
in the config. Otherwise histyt prints *ACTION REQUIRED* and you must set the
synthetic/AI disclosure toggle manually in YouTube Studio. The CURRENT build
checks the docs on `check_synthetic_field()`: if the field name appears in the
docs page, it is safe to set `contains_synthetic_media: true` in the config.

`upload_stage` always sends `"privacyStatus": "private"`; any attempt to upload
public raises immediately.
