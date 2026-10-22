# tiktok-slop-factory: offline test tier + path-filtered CI

Date: 2026-10-22. Owner: GLM (OpenCode). Full record:
`.agents/memory/archive/2026-10-22-tiktok-offline-ci-task-record.md`.

## What shipped

- The last credential/network-dependent test in the product was made
  hermetic: `test_gemini_parsing.py::test_generate_ideas_rejects_duplicate_padding`
  now fakes the transport at the `_json_request` seam instead of mocking only
  `_parse_json_ideas` and reaching live `urllib`. A companion test pins the
  dedup success path. Classification of the two known failures: the first was
  a test-mock gap (repro: offline `ConfigError`; with a key, a real API
  call), the second (`test_config.py::test_loads_dotenv_from_project_root`)
  does not reproduce offline and never depended on the credential.
- No integration test remains in the product, and the product rule says none
  may call live services; AGENTS.md now states that any future provider test
  must skip with a stated reason without credentials.
- `.github/workflows/tiktok-slop-factory.yml`: path-filtered pytest workflow,
  full suite, no key in CI, FFmpeg installed explicitly.
- `.github/CI.md`: row + "FFmpeg requirements and runtime" section; the old
  "No tiktok-slop-factory workflow" exclusion replaced.
- `tools/evidence/evidence.py` `EXCLUSIONS` text: runtime-only exclusion.

## Verification

- `cd tiktok-slop-factory && python -m pytest tests -q` (offline, no
  `GEMINI_API_KEY` in env): 190 passed, 0 failed, 0 skipped, 322.71s.
- `python tools/evidence/check.py`: OK. Evidence-checker suite: 39 OK.
- Workflow YAML itself: inspected, not executed on a real runner.

## Files

- `tiktok-slop-factory/tests/test_gemini_parsing.py`
- `tiktok-slop-factory/AGENTS.md`
- `.github/workflows/tiktok-slop-factory.yml` (new)
- `.github/CI.md`
- `tools/evidence/evidence.py`
- `.agents/pending_tasks.md`, `.agents/AGENTS.md`
- memory: `decisions.md`, `lessons.md`, `project.md`, archive record above.

## Follow-ups

- Watch the workflow's first run on `origin` (no commit authorized).
- JSON `excluded` block refreshes at the next real `generate.py` run.
- `BASELINE_FILES` extension (root README + product AGENTS.md) still open,
  regression-test-first.
