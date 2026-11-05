# histyt — history YouTube pipeline (Phase 1)

Topic -> research -> script -> **script approval gate** -> voice -> visuals -> assemble -> thumbnail -> variation -> **render approval gate** -> private upload.

## Quickstart (PowerShell, Windows)

```powershell
Set-Location D:\admin\code\projects\history-yt
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
$env:PIP_CACHE_DIR="D:\tmp\pip-cache"; $env:TEMP="D:\tmp"; $env:TMP="D:\tmp"
pip install -e .[test]
python -m histyt doctor
python -m histyt topic-add "The Fall of Rome" --angle rise_fall
python -m histyt research <id> --url https://example.com/a --title "Source A"
python -m histyt research <id> --url https://example.com/b --title "Source B"
python -m histyt script <id>
python -m histyt approve script <id>
python -m histyt run <id>          # stops at render gate (exit 2)
python -m histyt approve render <id>
python -m histyt upload <id>       # private only
python -m pytest tests -q
```

## State layout

`runs/topics.jsonl` (registry) and per video:

```
runs/<video_id>/
  state.json  script.md  script_meta.json  sources.json  assets.json
  audio/chunk_NNN.wav  durations.json  captions.srt
  segments/seg_NNN.mp4  video.mp4  final.mp4
  thumbnail.png  thumbnail_meta.json  variation_report.json  upload.json
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
| `videos` | width/height/fps (1920x1080@30) |

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
