# What CI validates

Three independent products, three workflows. Each triggers only on its own
paths and runs that product's supported test command from that product's
directory. There is no repository-wide test command.

| Workflow | Directory | Command | Extra setup |
|---|---|---|---|
| `agentops.yml` | `agentops/` | `python -m unittest discover -s tests` | none (stdlib only) |
| `universal-game-agent.yml` | `universal-game-agent/` | `python -m unittest discover -s tests` | `pip install -r requirements.txt` |
| `mini-llm.yml` | `small-projects/mini-llm/` | `python -m unittest discover -s tests` | `pip install -r requirements.txt` |

All jobs run on `ubuntu-latest` with Python 3.11 (the lowest supported
version) on CPU.

## What CI does NOT validate

- No lint, formatter, type-check, or coverage gate: no product configures
  one, so CI invents none.
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
