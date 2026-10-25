# tiktok-slop-factory product instructions

Local instructions for the `tiktok-slop-factory/` package. The repository-wide
contract lives in `.agents/AGENTS.md` and outranks this file on process, roles,
and collaboration. Where this file and `.agents/AGENTS.md` disagree on this
product's facts, this file is correct.

## Identity

- A procedural generator that turns short fictional scripts into vertical
  (1080x1920, H.264/AAC) TikTok-style MP4s. One CLI entry point,
  `make_videos.py` (`--count`, `--backend`, `--dry-run`; exit codes 0/1/2 as
  documented in the README). All content produced is explicitly fictional.
- Two backends, deliberately behind one seam (`app/providers.py`, the
  five-call contract `generate_ideas`/`generate_script`/`script_to_text`/
  `generate_tts`/`generate_caption`):
  - `gemini` (default) — ideas, script, caption, and narration TTS from the
    Gemini REST API; needs `GEMINI_API_KEY`. This is the only part of any
    pipeline run that uses an API key.
  - `local` — keyless: text from `local_text.json` (override with
    `TEXT_PROVIDER_JSON`), narration from Microsoft Edge TTS. **Edge TTS is a
    network service**: "keyless" never means "offline".
  - Adding a backend means implementing the same contract and registering it
    in `app/providers.py`; nothing may be monkeypatched onto `app.gemini`.
- Visuals are generated locally with FFmpeg (`app/visuals.py`, `app/renderer.py`);
  no stock-media API. Deliberately excluded scope (README): database, dashboard,
  browser automation, TikTok uploader, Docker, agent orchestration. Do not add
  any of these here.
- Runtime dependencies: `python-dotenv`, `edge-tts` (`requirements.txt`).
  The test runner is **pytest**, not unittest — this product is the exception
  to the unittest commands the other products use.

## Tests

Run from `tiktok-slop-factory/`, never from the repository root (ideally with
the project's `.venv` interpreter, which has `requirements.txt` installed):

```bat
cd tiktok-slop-factory
python -m pytest tests -q
```

The full suite shells out to real FFmpeg and takes on the order of ten minutes,
dominated by real 1080x1920 renders. Know the two tiers before running:

1. **Deterministic offline unit tests** — config, backend selection, Gemini
   parse/retry logic, caption timing, narration coherence logic, filtergraph
   construction, output-validation logic. No key, no network, no FFmpeg
   needed. HTTP behavior is exercised behind faked transports at the
   `_json_request`/`urlopen` seams, so no test requires `GEMINI_API_KEY` or
   reaches a live service.
2. **FFmpeg render tests** — marked `@ffmpeg_required` (`tests/conftest.py`),
   which builds skip conditions from `FFMPEG_PATH`/`FFPROBE_PATH` as resolved
   by `app.config` (`.env` included), **not** from `PATH`. They produce real
   MP4/WAV fixtures in `tmp_path` and are skipped explicitly when FFmpeg is
   unavailable.

There is no third, credential-gated tier: the last live-provider test
(`test_gemini_parsing.py::test_generate_ideas_rejects_duplicate_padding`) was
made hermetic at the `_json_request` seam in 2026-10-22 and every run of the
suite is now keyless. Never add a test that calls the live Gemini or Edge
services; if real provider behavior ever needs coverage, add it as an
explicitly named integration test that skips with a stated reason when
credentials are absent, and never as a silently skipped or deselected unit
test. A whole-suite deselect is no longer part of this product's workflow.

Write regression tests before fixing defects, following the existing file
conventions (`tests/test_*.py`, small docstrings recording the bug the test
pins). There is no configured formatter, type-check, or coverage command here; the
repository's only lint is repo-wide `ruff check .` (root `pyproject.toml`, CI workflow
`.github/workflows/ruff.yml`). Do not invent an additional one.

Verification evidence: this product is deliberately **excluded** from
`.agents/evidence/verification.json`; the `excluded` block in that file and the
`EXCLUSIONS` constant in `tools/evidence/evidence.py` record why. Do not add a
count for this product into any instruction file; run the suite for current
truth.

## Secrets

- The only credential is `GEMINI_API_KEY` (**user environment variable or
  `.env`**; on this machine `.env` holds only `FFMPEG_PATH`/`FFPROBE_PATH`).
- `.env` is git-ignored; `.env.example` is the tracked template. Never commit a
  real key, and never copy one into a test, fixture, log, or memory file.
  Tests set fake values through the `fake_env`/monkeypatch fixtures only.
- The API key travels in the `x-goog-api-key` header, never a URL query string
  (`app/gemini.py`), and error bodies pass through `_redact()` — keep those
  properties true when touching transport code. `test_api_key_sent_in_header_not_url`
  and `test_error_body_is_redacted` pin them.

## Environment variables

All configuration is environment-borne (see `app/config.py` and the README
table): `TEXT_BACKEND`, `GEMINI_API_KEY`, `GEMINI_TEXT_MODEL`,
`GEMINI_TTS_MODEL`, `TTS_VOICE` (Gemini voice), `TEXT_PROVIDER_JSON`,
`EDGE_TTS_VOICE`, `EDGE_TTS_RATE` (local backend), `FFMPEG_PATH`,
`FFPROBE_PATH`, `VISUAL_FONT`. `.env` is loaded once from the project root —
never the CWD. Every getter re-reads its variable per call, so tests may change
them via monkeypatch; changing `get_project_root`'s return value affects the
`.env` search path that way. Settings are never persisted back to disk, and no
secret-bearing value is ever written to `output/`.

## Subprocess and render side effects

- Production code shells out to FFmpeg/ffprobe (`app/pipeline.py` probes
  duration and volume with `volumedetect`; `app/visuals.py` renders the visual
  track; `app/renderer.py` muxes and validates). On Windows these use
  `subprocess.run(..., creationflags=CREATE_NO_WINDOW)`; FFmpeg never runs
  hidden-but-interactive.
- Real runs write real files into `<project root>/output/`
  (`videos/`, `audio/`, `captions/`, `metadata/`, `visuals/`) — git-ignored,
  but a careless manual `make_videos.py` invocation renders on the order of
  minutes and hundreds of MB per video into the repository tree. Redirect the
  output by running through a caller that owns it, not by editing `config.py`.
  Tests monkeypatch `pipeline.get_output_dir` to `tmp_path` before touching the
  pipeline.
- Renderer subtleties pinned by tests, do not regress: timestamps render with
  FFmpeg run with `cwd` set to the caption directory (absolute Windows paths
  break the `subtitles=` filter parser); the final MP4 is 1080x1920, `yuv420p`,
  audio mapped explicitly (never `-shortest`-truncated), and validated with
  ffprobe after render.
- Tests also spawn subprocesses: FFmpeg `lavfi` fixtures, and the CLI itself
  via `sys.executable make_videos.py` (dry-run variants only — no test
  performs a real Edge TTS synthesis). Never add a test that renders into the
  repository output directory or calls the live Gemini/Edge services.
