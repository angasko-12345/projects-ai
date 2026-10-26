# What CI validates

Seven product workflows, each scoped to one product's paths, plus one workflow
that checks the recorded verification evidence. Six products run Python
suites; four of them are measured in `.agents/evidence/verification.json`
(`privacy-audit` is not measured there), and `qr-generator/` is an
Expo/React Native product whose suite is Node/React Native, also not
measured in that evidence file. Each product workflow
triggers only on its own paths and runs that product's supported commands
from that product's directory. There is no repository-wide test command.

| Workflow | Directory | Command | Extra setup |
|---|---|---|---|
| `agentops.yml` | `agentops/` | `python -m unittest discover -s tests`, then `python acceptance_test.py` from the repository root (the Issue #4 end-to-end scenarios) | none (stdlib only) |
| `universal-game-agent.yml` | `universal-game-agent/` | `python -m unittest discover -s tests` (two jobs: `tests` without ale-py, where every ALE test skips; `tests-atari` with the optional `requirements-atari.txt`, where they run) | `pip install -r requirements.txt` (+ `-r requirements-atari.txt` in `tests-atari`); no Pillow in either |
| `mini-llm.yml` | `small-projects/mini-llm/` | `python -m unittest discover -s tests` (with `MINI_LLM_REQUIRE_GGUF=1`) | `pip install -r requirements.txt`, then `pip install -r requirements-gguf.txt` (the optional `gguf` package, so the GGUF export tests run instead of skipping) |
| `ai-token-tracker.yml` | `ai-token-tracker/` | `python -m unittest discover -s tests -v` | none (stdlib only) |
| `privacy-audit.yml` | `small-projects/privacy_audit_tool/` | `python -m unittest discover -s tests` | none (stdlib only; `maigret` is optional, online-scan-only, and deliberately not installed) |
| `qr-generator.yml` | `qr-generator/` | `npm run typecheck`, `npm run lint`, `npm test` | `npm ci` (installs from the lockfile); Node 24 |
| `tiktok-slop-factory.yml` | `tiktok-slop-factory/` | `python -m pytest tests -q` (pytest, not unittest) | `pip install -r requirements.txt` and the optional `requirements-qc.txt` (numpy, so clipcheck's logo tests run instead of skipping); `ffmpeg` apt-installed; ~10-minute suite; no key, see below |
| `evidence.yml` | repository root | `python tools/evidence/check.py` | none (stdlib only); needs full history |

All jobs run on `ubuntu-latest` on CPU. The four measured Python products use
Python 3.11 (the lowest supported version). The `privacy-audit` job also uses
Python 3.11, matching the other Python product jobs; the product itself
declares `requires-python = ">=3.10"`, so that declared floor is not the
version CI runs. The `qr-generator` job uses Node 24,
one of the ranges `react-native@0.86`'s engines field supports
(`^20.19.4 || ^22.13.0 || ^24.3.0 || >= 25.0.0`) — npm pins the dependency
tree from `package-lock.json`, so the suite runs the versions the developer
runs, with the cache keyed on `qr-generator/package-lock.json`.

## Headless GUI policy (agentops)

The AgentOps desktop client is PySide6, an optional dependency (the
`desktop` extra). Widget-level tests are marked `@requires_qt`
(`agentops/tests/qt_display.py`) and always run on Qt's `offscreen`
platform, so no `$DISPLAY` is needed.

`agentops.yml` runs two jobs:

- `tests`: the stdlib-only suite with no PySide6, so every `@requires_qt`
  test is skipped with an explicit reason. This is the configuration the
  evidence file measures.
- `gui-tests`: installs PySide6 (plus the Qt runtime libraries it needs on
  Ubuntu), checks that a `QApplication` starts offscreen, and runs the whole
  suite with the GUI tests enabled. A broken Qt install fails that check
  instead of silently skipping the GUI tests again.

This is a testing-only policy. Production GUI code is unchanged and still
fails loudly when it cannot start; AgentOps never fabricates a GUI success
condition.

## FFmpeg requirements and runtime (tiktok-slop-factory)

The tiktok-slop-factory suite is the repository's only pytest product and its
only suite that shells out to real FFmpeg:

- **Requirement:** `ffmpeg`/`ffprobe` binaries. The render tier's skip
  condition resolves `FFMPEG_PATH`/`FFPROBE_PATH` through `app.config`
  (project-root `.env` included, falling back to `PATH` when unset), so it
  never runs hidden-but-interactive. The CI workflow installs FFmpeg with apt
  explicitly instead of trusting the runner image, so a missing binary fails
  the install step rather than silently skipping the render tier.
- **Runtime:** the full suite takes on the order of ten minutes, dominated by
  real 1080x1920 H.264/AAC renders; the job carries a 30-minute timeout.
- **Credentials:** none. No `GEMINI_API_KEY` is set in CI and no test needs
  one: HTTP behavior is exercised behind faked transports, and CLI tests are
  dry-run only, so no Edge TTS synthesis happens either. The former
  credential-gated test (`test_generate_ideas_rejects_duplicate_padding`,
  which failed offline with `ConfigError` and made a real Gemini API call
  with a key set) was made hermetic at the `_json_request` seam.

## Verification evidence

`.agents/evidence/verification.json` is the single machine-readable record of
each product's measured result: product, command, test count, skipped count,
failures, errors, pass or fail, the UTC time of the run, the commit that was
measured, the interpreter and platform, and whether the working tree was clean.
Each record carries its own commit, so re-measuring one product cannot re-date
the others.

- Regenerate: `python tools/evidence/generate.py`. By default each suite runs in
  a throwaway clone of the current commit, so the recorded result describes
  committed code rather than whatever is lying in the working tree. `--in-place`
  measures the working tree and records the dirty paths.
- Verify: `python tools/evidence/check.py`. It fails when the file is malformed,
  when a file under a measured product changed after that product's recorded
  commit, when the run was made in a dirty tree, or when an instruction file
  restates a test count instead of pointing at the evidence file.
- The checker is tested by `python -m unittest discover -s tools/evidence -t
  tools/evidence -p "test_*.py"`, which includes corruption tests that break a
  recorded value and assert the checker notices.

### What this does not defend against

A commit that changes product code *and* edits `verification.json` to match --
with the counts and the recorded summary lines changed together -- passes. Nothing
inside a file can distinguish that from a measurement; closing it would need the
suites re-run in this workflow or a signature, and this job deliberately does
neither. What does hold is that the forgery expires at the next product commit.

`evidence.yml` triggers on the measured product directories as well as on the
evidence and instruction files: a product commit is what makes evidence stale, so
it is what must run the checker.

`evidence.yml` runs the checker and its tests, but not a fourth copy of the
three suites: those numbers would be a fifth place a count could live.

## What CI does NOT validate

- No lint, formatter, type-check, or coverage gate for the five Python
  products: none of them configures one, so CI invents none. (`qr-generator`
  does configure eslint and tsc, and `qr-generator.yml` runs them.)
- No Maigret / online username scan for `privacy-audit`: CI installs no optional
  dependency, so the `--online-usernames` path (and the Maigret command builder
  it uses) can only be exercised through the tests' stubs, never against real
  external sites.
- No live Gemini or Edge TTS call for `tiktok-slop-factory`: no key is set in
  CI and no test needs one. The workflow row above documents the FFmpeg
  requirement and the ~10-minute runtime. The exclusion of this product from
  `.agents/evidence/verification.json` remains about the render cost, no
  longer about credentials: the former credential-gated test is hermetic at
  the `_json_request` seam (2026-10-22), so a full keyless run is measured by
  `tiktok-slop-factory.yml` itself.
- No GPU training (mini-llm real-corpus runs, UGA training): CPU only.
- No llama.cpp runtime for mini-llm: `TestLlamaCppParity` (real `llama-tokenize`
  and `llama-server` against the exported GGUF) runs only when `LLAMA_CPP_DIR`
  is set, so it is skipped in CI. CI does run the export, the GGUF read-back
  and the numpy re-implementation of llama.cpp's gpt2 graph from the GGUF.
- No live external-game runs (UGA Windows path with real window capture and
  keyboard input). External-game tests in CI use fakes and the `synthetic`
  capture backend; they need no display.
- No Windows packaging (`agentops` PyInstaller executable): that remains a
  documented manual procedure in `agentops/AGENTS.md` and
  `.github/copilot-instructions.md`.
- Torch-gated UGA test files skip cleanly when torch is absent, so an
  environment that fails to install torch would report a pass with skips;
  the install step above exists to prevent that silent weakening.
