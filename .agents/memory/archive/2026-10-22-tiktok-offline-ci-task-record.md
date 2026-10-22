# tiktok-slop-factory offline tests + CI workflow -- task record 2026-10-22

> Completed record. Preserved verbatim after completion per
> `tasks/after-task.md`; do not re-execute. Owner: GLM (OpenCode session), one
> bounded task from the user prompt, standing on the open rows
> "Make test_gemini_parsing.py::... hermetic" and "Evidence-record follow-ups"
> in `.agents/pending_tasks.md`.

## Task

Inspect the known GEMINI_API_KEY-dependent TikTok Slop Factory failures,
reproduce and classify them, convert deterministic behavior to offline
fixtures/mocks where reasonable, mark any remaining real-provider tests as
integration tests with a stated skip reason, add a path-filtered pytest
workflow, document FFmpeg requirements/runtime in `.github/CI.md`, verify the
workflow discovers the expected tests, and keep secret values out of output.

## Findings

- `tests/test_gemini_parsing.py::test_generate_ideas_rejects_duplicate_padding`
  reproduced offline (key removed from the process env): fails raising
  `app.config.ConfigError`, not `GeminiError`, because only `_parse_json_ideas`
  was mocked while `generate_ideas()` travels
  `_call_with_retries` -> `_json_request` -> live `urllib`. Classification:
  **test missing mocks**. With `GEMINI_API_KEY` set (it exists as a user env
  var on this machine, never printed) it would perform a real Gemini API call,
  so a live-integration test marker was the wrong tool: the repository rule
  forbids tests that call the live Gemini/Edge services. Fixed by faking the
  transport at the `_json_request` seam (the same pattern
  `test_api_key_sent_in_header_not_url` uses) and adding a companion
  dedup-success test. No product code changed; the dedup logic being pinned is
  and was correct.
- `tests/test_config.py::test_loads_dotenv_from_project_root` reproduced:
  **passes** (8/8 in its file, offline). It never touches the credential path
  (it stubs `get_project_root` and writes its own tmp `.env`). This matches
  the 2026-10-21 re-verification already noted in the queue: the historical
  "two GEMINI_API_KEY-dependent failures" claim was stale.

## Changes

- `tiktok-slop-factory/tests/test_gemini_parsing.py` -- the duplicate-padding
  test now fakes `_json_request` (offline, no key) and pins the
  repeated-ideas -> `GeminiError` path; new
  `test_generate_ideas_deduplicates_and_keeps_first_occurrences` pins the
  in-order dedup success path. Test-only.
- `tiktok-slop-factory/AGENTS.md` -- Tests section now documents two tiers
  (deterministic offline; `@ffmpeg_required` renders) instead of three; the
  deselect command is gone; a rule states never to add live-service tests and
  that any future provider test must be an explicitly named integration test
  skipping with a stated reason when credentials are absent.
- `.github/workflows/tiktok-slop-factory.yml` -- new path-filtered pytest
  workflow (push + pull_request scoped to `tiktok-slop-factory/**`, the
  workflow file, and `.github/CI.md`), Job: ubuntu-latest, Python 3.11,
  `pip install -r requirements.txt` (pytest included), FFmpeg apt-installed
  explicitly so a missing binary fails the suite rather than silently skipping
  the render tier, `python -m pytest tests -q`, 30-minute timeout,
  `contents: read`, `persist-credentials: false`.
- `.github/CI.md` -- table row plus a dedicated "FFmpeg requirements and
  runtime" section; the "No tiktok-slop-factory workflow" exclusion replaced
  with the no-live-service-call statement; the evidence exclusion now cites
  runtime, not credentials.
- `tools/evidence/evidence.py` -- `EXCLUSIONS` text updated for the same
  reason (the JSON refreshes at the next real `generate.py` run; measured
  sections were not hand-edited).
- `.agents/pending_tasks.md` -- hermetic-test row removed (done); the
  evidence-record row rewritten to what remains (JSON refresh +
  `BASELINE_FILES` extension).

## Verification

- Reproduction runs with `GEMINI_API_KEY` removed from the process
  environment (the real key was never printed or echoed):
  - Before fix: the target test failed with `ConfigError` (0.22s).
  - After fix: `python -m pytest tests/test_gemini_parsing.py -q` -> 20
    passed, 0.09s.
  - Full offline suite: `python -m pytest tests -q` -> **190 passed, 0
    failed, 0 skipped** in 322.71s (0:05:22), real FFmpeg renders included.
    This is the workflow-discovery check: the same command the workflow runs
    discovers and passes the whole suite with no key and no network.
- `python tools/evidence/check.py` -> OK (evidence recorded at `525d397`, all
  four measured products still pass; checker's own suite
  `python -m unittest discover -s tools/evidence -t tools/evidence -p
  "test_*.py"` -> 39 OK after the `EXCLUSIONS` edit).
- Product instruction table in `.agents/AGENTS.md` updated: tiktok command
  no longer carries a deselect clause nor a "one credential-gated test" caveat.

## Not verified / risks

- The new workflow has not executed on a real GitHub runner; the ubuntu
  FFmpeg install step, 30-minute timeout, and YAML validity are verified only
  by inspection against the existing six workflows and the local full-suite
  run. First run on `origin` should be watched once a commit lands (no commit
  was authorized for this task).
- `GEMINI_API_KEY` remains a user environment variable on this machine; runs
  that forget to strip it historically made real API calls. After this change
  no test reads it at all, so the exposure window closes with the suite.
- `verification.json`'s `excluded` block still carries the old text until the
  next real `generate.py` run (tracked in `.agents/pending_tasks.md`).
