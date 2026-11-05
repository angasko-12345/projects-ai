# Changelog

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
- `shorts.plan_shorts` Phase-2 stub contract test
