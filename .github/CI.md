# What CI validates

Three independent products, three workflows, plus one workflow that checks the
recorded verification evidence. Each product workflow triggers only on its own
paths and runs that product's supported test command from that product's
directory. There is no repository-wide test command.

| Workflow | Directory | Command | Extra setup |
|---|---|---|---|
| `agentops.yml` | `agentops/` | `python -m unittest discover -s tests` | none (stdlib only) |
| `universal-game-agent.yml` | `universal-game-agent/` | `python -m unittest discover -s tests` | `pip install -r requirements.txt` |
| `mini-llm.yml` | `small-projects/mini-llm/` | `python -m unittest discover -s tests` | `pip install -r requirements.txt` |
| `evidence.yml` | repository root | `python tools/evidence/check.py` | none (stdlib only); needs full history |

All jobs run on `ubuntu-latest` with Python 3.11 (the lowest supported
version) on CPU.

## Headless GUI policy (agentops)

The AgentOps job runs headless, so it has no `$DISPLAY`. AgentOps GUI tests
that construct a real Tk root window are skipped rather than faked: they are
marked `@requires_display` (`agentops/tests/tk_display.py`), which detects
display availability and skips with an explicit reason.

This is a testing-only policy. Production GUI code is unchanged and still
fails loudly when no display exists — AgentOps never fabricates a GUI
success condition. Controller and workflow logic that the GUI depends on is
covered by non-Tk test classes, which run in CI.

## Verification evidence

`.agents/evidence/verification.json` is the single machine-readable record of
each product's measured result: product, command, test count, skipped count,
failures, errors, pass or fail, the UTC time of the run, the commit that was
measured, the interpreter and platform, and whether the working tree was clean.

- Regenerate: `python tools/evidence/generate.py`. By default each suite runs in
  a throwaway clone of the current commit, so the recorded result describes
  committed code rather than whatever is lying in the working tree. `--in-place`
  measures the working tree and records the dirty paths.
- Verify: `python tools/evidence/check.py`. It fails when the file is malformed,
  when a file under a measured product changed after the recorded commit, when
  the run was made in a dirty tree, or when an instruction file restates a test
  count instead of pointing at the evidence file.
- The checker is tested by `python -m unittest discover -s tools/evidence -t
  tools/evidence -p "test_*.py"`, which includes corruption tests that break a
  recorded value and assert the checker notices.

`evidence.yml` runs the checker and its tests, but not a fourth copy of the
three suites: those numbers would be a fifth place a count could live.

## What CI does NOT validate

- No lint, formatter, type-check, or coverage gate: no product configures
  one, so CI invents none.
- No tiktok-slop-factory suite: it has no product `AGENTS.md`, so there is no
  agreed command, and it runs real FFmpeg renders. The exclusion and its reason
  are recorded in the evidence file under `excluded`.
- No GPU training (mini-llm real-corpus runs, UGA training): CPU only.
- No live external-game runs (UGA Windows path with real window capture and
  keyboard input). External-game tests in CI use fakes and the `synthetic`
  capture backend; they need no display.
- No Windows packaging (`agentops` PyInstaller executable): that remains a
  documented manual procedure in `agentops/AGENTS.md` and
  `.github/copilot-instructions.md`.
- Torch-gated UGA test files skip cleanly when torch is absent, so an
  environment that fails to install torch would report a pass with skips;
  the install step above exists to prevent that silent weakening.
