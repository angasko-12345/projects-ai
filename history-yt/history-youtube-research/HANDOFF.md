# HANDOFF: history-yt workflow refinement (deep research -> pipeline)

**Created:** 2026-11-05
**Next session task:** Implement the workflow refinement in `D:\admin\code\projects\history-yt` (code edits), run tests and doctor/CLI verification, re-read changed files, then report completion. Then (separate session task only if asked) run a full end-to-end (voice -> visuals -> assemble -> QA -> upload dry-run).

---

## 1. What the user wants (verbatim intent)

User history:
1. Asked for deep research on History YouTube channels (full spec: Phases 1-10, 20+ channels, 60+ videos, scriptwriting system, AI visual production, audio, thumbnails/titles, workflow/automation, QA framework, 10 deliverables) under `history-youtube-research/`, with "Use playwright mcp".
2. Chose antislop applied "during the work" for writing.
3. Asked to "refine the workflow of D:\admin\code\projects\history-yt based on that, make sure it can run safely in my pc. if you have to add anything to the pc, make sure it is inside D:\admin\code\projects\history-yt (or if absolutely necessary, D:/)".
4. Said "begin. do not stop until you have implemented everything."

The research is DONE and verified. The pipeline code implementation is NOT done. The next session must do the code work. Directives from the user that must be obeyed:
- Run everything safely on their PC. Anything installed must live inside `D:\admin\code\projects\history-yt` or, if absolutely necessary, `D:/`. Nothing outside D: (no C: installs, no globals).
- Do not stop until everything is implemented, tested, and verified.
- The existing project rules in `histyt/AGENTS.md` (see section 5) must be obeyed. Never modify existing application code lightly; the refinement IS the user's requested modification, so proceed but keep the gates and defaults.

---

## 2. Research deliverables — location and status

Root: `D:\admin\code\projects\history-yt\history-youtube-research\` (note: it sits inside the project dir, NOT at repo root).

| File | Status |
|---|---|
| `README.md` | complete (all 10 deliverables marked complete) |
| `SCOPE.md` | scope/constraints/tooling transparency |
| `reports/master-research-report.md` | complete (Phase 1-10 summary, evidence labels) |
| `reports/tool-directory.md` | complete (verified pricing: Runway, ElevenLabs, fal/BFL) |
| `reports/recommended-production-blueprint.md` | complete (format, stack, budgets, 10 title formulas, 10 thumbnail concepts, 5 pairings, 8-week plan) |
| `handbooks/history-scriptwriting-handbook.md` | complete (11-step process, templates, hooks, Teutoburg example) |
| `handbooks/ai-visual-production-handbook.md` | complete (scene record, style lock, prompt template, period direction for Rome/Greece/Egypt/Medieval/Japan/WWI/WWII/battles/cities/biographies, consistency, i2v, failure table) |
| `workflows/production-workflow.md` | complete (18 stages, gates, automation vs human) |
| `workflows/agent-architecture.md` | complete (12 specialist agents, inputs/outputs/gates) |
| `workflows/quality-assurance-checklist.md` | complete (11-category rubric 1-10, minimums, 8 auto-rejections, checkpoints G1-G4, release checklist) |
| `data/channel_benchmark.csv` | 21 channels, VERIFIED browser-extracted 2026-11-05 |
| `data/video_analysis.csv` / `.json` | 60 unique videos (see section 3), metadata + proxy analysis |
| `data/video_selection.md`, `data/video_candidates.json` | selection record (63 rows reconciled) |
| `data/video_meta/*.json` | 60 per-video metadata files |
| `evidence/` | raw YouTube JSON/txt, about-full captures, youtube-ai-disclosure-2026-11-05.md |
| `screenshots/` | 21 channel grid screenshots + 2 frames |
| `scripts/` | browser extraction + normalization scripts |

## 3. Key research numbers (so the next session does not re-derive them)

- 21 channels benchmarked. Subs leaders: The Infographics Show 15.5M (7.02B total views), OverSimplified 9.68M (33 videos only), Timeline 5.91M, Simple History 5.12M, Extra History 4.63M, Kings and Generals 4.22M, Epic History TV 3.2M. Smallest: TimeGhost 337K, Captivating History 506K, Stefan Milo 655K.
- Views/subscriber: History Dose 89 (lowest) up to The Infographics Show 453. Recent median views separate the field more than lifetime stats: Fall of Civilizations 4.15M on recent uploads vs Captivating History 2,700.
- 60-video analysis: 63 candidate rows -> 59 unique candidate IDs + 1 extra verified capture (MvwAWgiERBM, Alt Shift X, The Future of Dunk and Egg) = 60 unique. Duplicates removed: d2lJUOv0hLA, fD-R35DSSZY, _uk_6vfqwTA, o1cyBZ8M7dE. 51/60 have chapters; median duration 1,272s; median chapter density 0.32/min; 12/60 titles end with '?'.
- Video EMY3CAxTB_c is unavailable ("This video isn't available anymore") and was removed; the removal is recorded in data/video_selection.md.
- Evidence labels used throughout: VERIFIED / UNKNOWN / HYPOTHESIS. Retention is HYPOTHESIS only (public data does not show it). Visuals/AI use per video is UNKNOWN from metadata.
- Verified tool pricing (2026-11-05): fal FLUX.1 schnell $0.003/MP, FLUX 2 $0.012/MP, FLUX.1 dev $0.025/MP, Seedream 4.5 $0.04/image; FLUX 3 Max video $0.085/s, Kling v2.6 $0.07/s, Kling v3 $0.14/s, MiniMax H3 Max $0.05/s; Kokoro $0.02/1000 chars via fal. ElevenLabs: Free 10k credits, Starter $6/mo 30k credits (commercial license), Creator $22 121k, Pro $99 600k; TTS 1 credit/char, music 900/min, SFX 200/gen. Runway: Free 125 one-time credits; Standard $15/mo ($12 annual) 625 credits/mo (= 52s Gen-4.5 / 78 images); Pro $35/$28 2250; Max $95/$76 9500.
- YouTube AI disclosure (VERIFIED, evidence/youtube-ai-disclosure-2026-11-05.md): realistic AI scenes require disclosure; script/thumbnail help and cloning your own voice do not; undisclosed content risks manual labels/removal/YPP suspension.

---

## 4. Current pipeline state (what exists, not yet refined)

Project: `D:\admin\code\projects\history-yt`, package `histyt`, Python 3.14.7, hatch-style setuptools, deps: pyyaml, pillow, requests; test extra: pytest.

Entry: `python -m histyt <command>` (`histyt/__main__.py` -> `cli.py`). Exit codes 0 ok, 1 error, 2 gate. Global flags `--config PATH`, `--runs DIR`.

Flow: topic-add -> research (add sources, >=2 required) -> script (template rotation + LLM prompt + claim/source validation, each claim needs >=2 source citations) -> **approve-script (GATE 1, script hash pinned)** -> voice (chunk per claim sentence) -> visuals (license gate + local title/timeline cards; AI image disabled by default) -> assemble (segments -> concat -> captions sidecar + burn attempt -> ffprobe validation) -> thumbnail (seeded 1280x720, 8 layouts, dhash) -> variation-check (title ngram similarity vs history window, thumbnail phash distance, template repeat window) -> **approve-render (GATE 2, final.mp4 hash pinned)** -> upload (always `privacyStatus: "private"`; mock client for dry-runs). Shorts: shorts-plan (deterministic heuristic over durations.json chunks) + shorts-extract (needs render approval + hash pin).

`python -m histyt doctor` output (verified last check): python 3.14.7 OK, ffmpeg/ffprobe found (Gyan 9.0.2), pillow/pyyaml/requests installed, config OK, runs writable, tts provider `fake`, OPENROUTER_API_KEY present + usable (HTTP 200), GOOGLE_API_KEY absent (optional), FAIL youtube client json / token (not set - expected, use --mock for dry-runs).

`config/channel.yaml` (current): niche history, target_length_minutes 12, 6 script_templates, thresholds as in defaults, `tts.provider: fake` (pacing_min 0.9, pacing_max 1.1), `llm.provider: fake`, youtube tokens unset, `ai_image.provider: disabled`, shorts max 3 clips / 15-60s, videos 1920x1080@30.

Tests: `python -m pytest tests -q`. Test files: conftest.py (cfg fixture forces llm=fake, tts=fake; make_video helper), test_claims, test_cli, test_end_to_end (full dry-run topic->research->script->approve->run->approve-render->upload mock; asserts 1920x1080, audio stream, thumbnail, captions, private), test_gates, test_licenses, test_refinements (resumability, render pin, voice gates, fetch_url https/HTML/size guards), test_shorts, test_upload, test_variation.

---

## 5. Project rules that MUST be preserved (`histyt/AGENTS.md`)

- Fake providers are the default and the ONLY ones tests use (`llm.provider: fake`, `tts.provider: fake`). No real LLM/YouTube calls in tests.
- Gates: any `script.md` change after approval blocks voice+ (hash mismatch). Upload is blocked until `render_approved`.
- Never upload publicly; privacyStatus is always the literal "private".
- Synthetic/AI disclosure: `youtube.contains_synthetic_media` config-driven only; if unset, print ACTION REQUIRED to set toggle manually in YouTube Studio.
- No silent fallbacks: missing sources, bad licenses, unknown providers raise PipelineError with clear message.
- Caches on D:: `PIP_CACHE_DIR=D:\tmp\pip-cache`, `TEMP/TMP/TMPDIR=D:\tmp`. Do not push to git; do not change git config.
- Identity: deployment/presentation name Crouton Alpha. Code/comments in English. Never print secrets (doctor shows present/absent only).
- Existing state machine (STAGE_ORDER) must stay resumable; completed stages are skipped on re-run.

---

## 6. What the refinement should add (derived directly from the research)

Implement these in `histyt/` with tests. Keep all gates and fake-provider defaults.

1. **Scene planner stage (`scenes`)**. After script approval, generate `scene_manifest.json` from script claims: one scene per claim/chunk with narration, period/location fields, style lock, camera, duration (words/2.6s rounded to 0.5s), transition, continuity refs. Gate: scene manifest must exist before visuals/assemble, or visuals must still work without it (backward compat for tests). Add CLI `scenes <id>`.
2. **QA stage (`qa`)**. Score a finished `final.mp4` against the 11-category rubric (structural checks automated: streams present, dimensions match, loudness target -14 LUFS via ffmpeg/ffprobe where available, captions present, no placeholder text; historical/script categories scored from script_meta claims and flagged for human review). Output `qa_report.json` with pass/revise/reject. Auto-rejections (fabricated quote patterns, missing sources, wrong dimensions, unlicensed asset) fail hard. Wire QA into the pipeline before `approve-render` (render gate is the natural insertion point). Add CLI `qa <id>`.
3. **Visual direction upgrade in `visuals.py`**. Add style-lock metadata + per-chunk visual variety: generate 3-4 card variants per video (not just title/timeline), vary palettes deterministically per chunk, and record style lock in assets.json. Keep license gate unchanged. AI image provider stays disabled by default; if enabled, require explicit config and a license field (generated content cannot fake a license).
4. **Voice robustness**. Default stays `fake`; add a `pipers`/`kokoro` path only if the executable/model is inside D: (paths validated D:-only). Keep pacing bounds config. Narration must read from the approved script hash (already gated).
5. **Path confinement**. Add a helper `ensure_d_confined(path)` that raises PipelineError unless every written path (runs, venv, caches, models, renders) is under `D:\` (or the project dir). doctor() must report this. PIP_CACHE_DIR/TEMP must be D:-based in README/docs.
6. **Research source quality**. Research stage currently just adds sources. Add a lightweight quality pass: source tier (primary/secondary/discovery) recorded in sources.json, and a script-stage warning (not a hard fail) if a claim cites only discovery-tier sources. Keep fake-provider dry-run working.
7. **Config additions** (safe defaults, no breaking changes): `target_length_minutes` already exists; add optional `scene:` style lock, `qa:` minimums, `ai_image:` settings (default disabled). Keep `config/channel.yaml` defaults as-is unless a change is required.
8. **README + AGENTS.md update**. Document new commands, D:-confined paths, how to run safely (venv in project dir, caches on D:\tmp), and that nothing installs outside D:.

Suggested implementation order: (5) path confinement + doctor, (1) scenes, (2) QA wired after assemble before render gate, (3) visuals, (4) voice, (6) research tiers, (7) config, (8) docs. After each stage: run `python -m pytest tests -q`, `python -m histyt doctor`, and one dry-run (`topic-add`, `research` x2, `script`, `approve-script`, `run`, `approve-render`, `upload --mock`).

---

## 7. Environment facts (so the next session does not re-discover them)

- OS: Windows 11. PowerShell 5.1 shell.
- Working dir: `D:\admin\code\projects\history-yt`. Git repo at `D:\admin\code\projects` (parent). Do NOT touch git config or push.
- Python: 3.14.7 via `py -3.14`. Venv at `D:\admin\code\projects\history-yt\.venv` (git-ignored, recreate per README if missing). `pip install -e .[test]` with `PIP_CACHE_DIR=D:\tmp\pip-cache`, `TEMP/TMP/TMPDIR=D:\tmp`.
- ffmpeg/ffprobe: Gyan 9.0.2 via WinGet links. Already on PATH.
- OpenRouter key IS present and usable (HTTP 200). Google key absent. YouTube OAuth NOT configured (expected).
- No Playwright MCP in this environment (verified: `opencode.list_mcp_resources` empty). Browser automation was done via agent-browser 0.38.2 CLI (`agent-browser eval`, activesession `hist-research-main`). `websearch` returned HTTP 402 (quota exhausted) - avoid it; use `webfetch` and `agent-browser read`.
- `websearch` quota is exhausted. `webfetch` works but caches; for BFL docs it returns the full llms.txt mirror (noise) - go direct to `https://bfl.ai/pricing`, `https://runway.com/pricing`, `https://elevenlabs.io/pricing`, `https://fal.ai/pricing`.
- Antislop skill: user chose "during the work". Core skill is loaded in the parent context; its hard rules (no em dash, no invented numbers, evidence over claims) apply to writing.
- The PowerShell `shell` tool in this harness needs small commands; heredocs do not work on PS 5.1 - write `.py` files with the `write` tool instead and run `py -3 scripts/...`.
