# histyt - history YouTube pipeline

Topic -> research -> script -> **script approval gate** -> voice -> scenes -> visuals ->
assemble -> thumbnail -> variation -> **QA** -> **render approval gate** -> private upload.

## Quickstart (PowerShell, Windows)

```powershell
Set-Location D:\admin\code\projects\history-yt
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
$env:PIP_CACHE_DIR="D:\tmp\pip-cache"; $env:TEMP="D:\tmp"; $env:TMP="D:\tmp"; $env:TMPDIR="D:\tmp"
pip install -e .[test]
python -m histyt doctor
python -m histyt topic-add "The Fall of Rome" --angle rise_fall
python -m histyt research <id> --fetch-wikipedia "Fall of the Western Roman Empire" --tier secondary
python -m histyt research <id> --url https://example.com/b --title "Source B" --tier secondary
python -m histyt script <id>
python -m histyt approve-script <id>   # GATE 1: pins the script hash
python -m histyt run <id>              # voice..QA, stops at the render gate (exit 2)
python -m histyt qa <id>               # print the last QA report
python -m histyt approve-render <id>   # GATE 2: refuses on a QA reject
python -m histyt upload <id> --mock    # no network, no credentials
python -m histyt visuals-fetch <id> --all           # one licensed image per scene
python -m histyt visuals-fetch <id> --query "Roman legionary relief"  # one extra image
python -m histyt shorts-plan <id> [--max-clips N]
python -m histyt shorts-extract <id>
python -m histyt status
python -m histyt topic-list
python -m pytest tests -q
```

Reproducible venv: `.venv/` is git-ignored, recreate it as above after `git clone`.
Keep `$env:TMPDIR="D:\tmp"` set so pytest temp directories land on D:.

## State layout

`runs/topics.jsonl` (registry) and per video:

```
runs/<video_id>/
  state.json  script.md  script_meta.json  sources.json  assets.json
  scene_manifest.json  qa_report.json
  audio/chunk_NNN.wav  durations.json  captions.srt
  images/  images/scenes/  segments/seg_NNN.mp4  video.mp4  final.mp4
  thumbnail.png  thumbnail_meta.json  variation_report.json  upload.json
  shorts/shorts_plan.json  shorts/short_NN.mp4  shorts/shorts_manifest.json
```

## Config (config/channel.yaml)

| Key | Purpose |
|---|---|
| `niche`, `angle`, `language`, `voice` | channel settings (niche is config, never hardcoded) |
| `target_length_minutes` | target narration length |
| `words_per_minute` | words per minute used to turn minutes into a word budget (default 150) |
| `script_templates` | >=4 named templates with section lists |
| `thresholds` | title-ngram similarity, phash distance, template window, history window |
| `tts` | `fake` (default), `piper`, or `elevenlabs`; pacing bounds and provider settings |
| `llm` | `fake` (default), `google`, `openrouter`, `local`, `openai-compatible`; `temperature`, `max_tokens` |
| `youtube` | oauth client json, token path, `contains_synthetic_media` |
| `scene` | `style_lock`, optional `period` and `location` copied into every scene record |
| `visuals` | archive fetch thresholds: `min_width`, `min_height`, `min_hash_distance`, `max_downloads_per_scene`, `history_window` |
| `qa` | `profile` (`structural` or `release`), loudness target and tolerance, true peak max |
| `audio` | `normalize` and `loudness_lufs` for the assemble pass |
| `ai_image` | provider, disabled by default |
| `shorts` | max clips, min/max seconds |
| `videos` | width, height, fps (1920x1080@30) |

`config/real.yaml` is the opt-in production config (OpenRouter + ElevenLabs). The
default config stays on fake providers so tests and dry-runs never touch the network.

## Script stage: length contract and source grounding

`script` now enforces the contract the rest of the pipeline relies on:

- The prompt carries a word budget (`target_length_minutes * words_per_minute`),
  a hook requirement, one-idea-per-sentence rules, and the exact section shape.
- Claims must sit under `## <section>` headers. A script with no sections is rejected.
- Claim ids are renumbered across the whole script, so models that restart at `C1`
  in every section cannot collide with the scene and audio mapping.
- Below 60% of the word target is a hard error. Between 60% and 85% is a warning
  shown at the script gate.
- Each claim is checked against the stored text of the sources it cites. The check
  is lexical (distinctive words plus numbers) and labeled as a heuristic:
  `supported`, `weak`, `unverified`, or `unchecked` when no source text exists.
  Claims citing only `discovery`-tier sources are flagged.
- `approve-script` prints the review summary before it pins the hash. The human
  gate decides; the check never silently approves or rejects a claim.

Source tiers are `primary`, `secondary`, and `discovery`. Fetch helpers store the
page text in `sources.json`; the script prompt includes an excerpt of each source
so the model can actually use it.

## Scenes and visuals

`scene_manifest.json` has one scene per claim: narration, section, period,
location, camera, lighting, transition, duration, style lock and continuity ref.
Durations come from `durations.json` after voice, or from a words-per-second
estimate when you run `scenes` before voice. The same pass stamps a `scene_id` on
every audio chunk so assemble can pick the image for that segment.

`visuals` generates one deterministic card per scene, keyed by the style lock and
scene id, and records the style lock in `assets.json`. If you provide real assets
(`visuals-fetch` or a hand-written `assets.json`), those are used and cycled across
scenes instead. The license gate is unchanged: only public domain, CC0, US federal
work, and CC BY are accepted.

`visuals-fetch` searches Wikimedia Commons, drops anything outside the allowed
licenses, checks content type and size, and records attribution and source page.
It does not judge historical authenticity; that stays a human review item.

`visuals-fetch --all` resolves one image per scene from the scene manifest. Each
scene carries three queries built by `visual_spec` from its claim, plus the
configured period and location; the narration is never searched as a phrase.
Candidates are filtered to at least `visuals.min_width` × `min_height`, must be
landscape, and are rejected when their average hash sits within
`visuals.min_hash_distance` of an image already used in this video or the last
`visuals.history_window` runs. Selection is deterministic: the same search picks
the same file. A scene with no usable candidate is marked `missing` in the
manifest and can be reviewed with `histyt scenes`; it is never filled with an
image from the wrong period. Quotes are a limit on candidates *searched*, not
images kept, because Commons filters by license after retrieval — a low limit
can look empty while the same query finds public-domain results further down.
Re-running the command only fills scenes that are still empty.

## QA stage and the render gate

`qa` writes `qa_report.json` and runs after variation, before the render gate.
It has two profiles:

- `structural` (tests and dry-runs): files, streams, export specs, render length
  against the narration track, captions, licenses, placeholder text, and claim
  numbers. Release-only checks are marked `not_available`.
- `release`: adds loudness (-14 LUFS, true peak <= -1 dBTP), silence gaps over 2s,
  black frames over 2s, and a hard target-length check.

The verdict is `pass`, `revise`, or `reject`. Any failed check is an auto-rejection
and `approve-render` refuses with the failing check ids. The 11 human rubric
categories are emitted as `null` in `human_scorecard`: this module never invents a
score it cannot prove. Score them by hand against
`history-youtube-research/workflows/quality-assurance-checklist.md`.

## Real providers

ElevenLabs (free tier available):

```powershell
$env:ELEVENLABS_API_KEY="..."   # current session only
python -m histyt --config config/real.yaml run <id>
```

`config/real.yaml` uses `eleven_multilingual_v2` and the premade Rachel voice by
default. Change `tts.elevenlabs_voice_id` to use another voice. Pacing is sent as
`voice_settings.speed` (clamped 0.7 to 1.2); Piper uses `--length_scale`. The fake
provider ignores pace by design.

OpenRouter and Google are OpenAI-compatible endpoints. **Test with a small request
first and report the actual quota/region** returned by the provider. There is **no
silent fallback**: if a provider call fails, histyt raises `PipelineError`.

## D: confinement

New installs, models and caches stay on D:. `doctor` reports whether the runs
directory is inside `D:\` or the project directory, and `config.ensure_d_confined`
is the shared policy helper. Keep `PIP_CACHE_DIR`, `TEMP`, `TMP`, and `TMPDIR` on
`D:\tmp`. Nothing in this project needs a C: install.

## Synthetic media disclosure

The YouTube API field `containsSyntheticMedia` is only sent when you explicitly
verify it against the official docs and set `youtube.contains_synthetic_media`
in the config. Otherwise histyt prints *ACTION REQUIRED* and you must set the
synthetic/AI disclosure toggle manually in YouTube Studio.

`upload_stage` always sends `"privacyStatus": "private"`; any attempt to upload
public raises immediately.

## Shorts (Phase 2)

`shorts-plan` ranks consecutive-chunk windows from `durations.json` by a
deterministic offline heuristic and writes `shorts/shorts_plan.json`.
`shorts-extract` cuts the planned windows from `final.mp4` and is gated on render
approval and the pinned hash. Vertical reframe, caption re-render and Shorts
upload remain out of scope.
