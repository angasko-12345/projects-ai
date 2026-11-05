# Changelog

## Unreleased - workflow refinement (2026-11-05)
- Script stage enforces the length contract: word budget from
  `target_length_minutes * words_per_minute`, hard error below 60%, warning band
  at 60-85% and above 140%, hook and one-idea-per-sentence prompt rules
- Claims are written under their template section headers; empty template
  headers are no longer emitted; claim ids are renumbered across the script so
  models that restart at C1 cannot collide with audio/scene mapping
- Source grounding: `sources.json` stores an evidence tier (`primary`,
  `secondary`, `discovery`) and the fetched page text; the script prompt
  includes source excerpts; each claim gets a lexical support status
  (`supported`, `weak`, `unverified`, `unchecked`) and missing-number list;
  `approve-script` prints the review before pinning the hash
- New `scenes` stage: `scene_manifest.json` with one scene per claim, style
  lock, camera, lighting, transition, continuity reference and real durations;
  `durations.json` gains `scene_id` so assemble uses the right image per segment
- New `qa` stage: `qa_report.json` with provable structural/release checks,
  auto-rejections, verdict and a null-only 11-category human scorecard;
  `approve-render` requires a report and refuses on `reject`
- Per-scene deterministic visual cards keyed by style lock; real assets from
  `assets.json` are reused; Windows font fallback chain; style lock recorded in
  `assets.json`
- ElevenLabs TTS provider (REST + ffmpeg WAV conversion, key redaction, speed
  from pace); Piper now applies `--length_scale`; `doctor` reports the
  ElevenLabs key and D: confinement
- `visuals-fetch` pulls license-filtered public-domain/CC images from Wikimedia
  Commons with attribution, content-type and size checks
- Assemble fixes: looped image input now uses the configured frame rate (every
  segment was 0.5s instead of 0.6s), first segment uses the first asset, output
  size follows `videos`, and a loudnorm pass targets -14 LUFS / -1 dBTP
- Assemble fixes found by the first real render: segments are frame-exact (AAC
  priming made a 28-chunk concat average 29.907 fps), the subtitle pass forces
  CFR at `videos.fps`, and loudness uses two-pass loudnorm plus a measured
  gain/limiter correction (one-pass landed at -15.3 LUFS / -0.7 dBTP). Measured
  output is -14.2 LUFS / -1.6 dBTP; silent input falls back to one pass
- Description gains chapters (only when they can be valid), a disclosure line
  when the config sets it, and a correction invite
- New `research --tier`; `config/real.yaml` for opt-in OpenRouter + ElevenLabs
  runs; `qa` and `scene` config sections; canonical stage list shared by
  `state` and `pipeline`
- Tests: 72 fake-provider tests, including script length, grounding, QA,
  scenes, ElevenLabs, Commons, chapters, D: policy, and the export specs the
  first real render failed (frame rate, loudness target, true peak)

## 0.1.0 — Phase 1 initial
- Topic → research → script → **[GATE 1: script approval]** → voice → visuals →
  assemble → thumbnail → variation → **[GATE 2: render approval]** → upload (private)
- `config/channel.yaml`: niche as config value, 4+ script templates, thresholds
- LLM adapters (OpenAI-compatible, Google AI Studio, OpenRouter, local llama.cpp, fake)
- TTS adapters (Piper first, Kokoro stub, fake); license-checked visuals; ffmpeg assemble
- Seeded thumbnails; variation checks (title n-gram, thumbnail pHash, template repeat)
- `doctor` command; 17 fake-provider tests

## Unreleased — Refinement pass
- `run_until_gate` is resumable: skips stages already in `completed_stages`,
  records `state.error` on failure and keeps the last-good stage
- Render gate pins `final.mp4` SHA-256 at approval; upload re-verifies the hash
- `voice_stage` enforces script approval internally (defense in depth)
- `pick_template` no longer takes a dead `history_ids` argument
- `fetch_url` validates HTTP status and content-type, rejects redirect loops,
  caps response size
- `doctor` v2: Piper model/D: check, YouTube OAuth path hints, ffmpeg Gyan
  build detection, optional API-key usability probe (presence only, no values)
- `status` pretty table with `--json` flag, `topic list`, `variation-check` PASS/FAIL table
- Captions synced to cumulative TTS durations; 8 seeded thumbnail layouts
- Duration warning when estimated narration deviates from `target_length_minutes`
- Pinned dependencies (`pyyaml>=6.0`, `pillow>=10`, `requests>=2.31`), `[tool.ruff]` config

## Unreleased — Phase 2 shorts (plan + landscape extract)
- `shorts-plan <id> [--max-clips N]`: deterministic offline ranking of
  consecutive-chunk windows (caption timing) by hook/entity/digits/sweet-spot
  scoring; non-overlapping greedy selection, `shorts/shorts_plan.json` with
  windows, scores, slugs and deterministic title suggestions
- `shorts-extract <id>`: cuts planned windows from `final.mp4` into
  `shorts/short_NN.mp4` (h264/yuv420p + aac, source/landscape aspect) with a
  `shorts/shorts_manifest.json` (durations + SHA-256); gated on render
  approval and the pinned `final.mp4` hash (same contract as upload)
- New `shorts:` config section (`max_clips`, `min_seconds`, `max_seconds`)
- Replaced the Phase-2 stub contract test with real coverage
  (`tests/test_shorts.py`); upload gained opt-in `--mock` CLI flag
