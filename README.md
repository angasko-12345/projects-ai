# projects-ai

Local-first AI tools and experiments by [@angasko-12345](https://github.com/angasko-12345).
Each folder is a self-contained project with its own README, dependencies and tests.

AgentOps, Universal Game Agent, mini-llm and AI Token Tracker are the four Python products measured in `.agents/evidence/verification.json`. Every product here has a CI test suite; TikTok Slop Factory is deliberately left out of the evidence file (its real FFmpeg renders make re-measuring expensive), and QR Generator is a Node app outside that file.

Everything here runs on your own machine: no accounts, no hosted backend, and no telemetry unless a project says otherwise.

## Main projects

| Project | What it is | Status | Stack |
|---|---|---|---|
| [**AgentOps**](agentops/) | An orchestrator for the coding-agent CLIs you already have installed (Claude Code, Codex, Copilot, OpenCode, Kilo, Pi and more). It plans, implements, verifies and reviews in isolated Git worktrees, with a SQLite run history and a desktop GUI. | Active · CI | Python, SQLite, Qt |
| [**QR Generator**](qr-generator/) | An Android QR code maker for text, URLs, email, phone and Wi-Fi. Live preview, PNG export and share, history, dark mode, and an optional Pro tier through Google Play. Runs fully on-device, with no ads and no tracking. | Pre-release, heading to Play closed testing | Expo, React Native, TypeScript |
| [**AI Token Tracker**](ai-token-tracker/) | A desktop dashboard of the tokens used by every AI coding tool on your PC (Claude Code, Codex, Cline, Kilo, Copilot, OpenCode and more). It reads local session files only. | Usable | Python, SQLite, Qt |
| [**Universal Game Agent**](universal-game-agent/) | A pixel-only reinforcement-learning testbed: a recurrent PPO agent that learns games from rendered frames, with experiment ledgers and reward diagnostics. | Research · CI | Python, PyTorch |

## Smaller projects

| Project | What it is |
|---|---|
| [mini-llm](small-projects/mini-llm/) | A small GPT-style language model written from scratch in PyTorch: tokenizer, training loop, sampling. |
| [Privacy Audit Tool](small-projects/privacy_audit_tool/) | A read-only Windows tool that finds your forgotten online accounts from mail exports, browser history and usernames. It deletes nothing and runs offline by default; its optional username checks contact external sites. |
| [TikTok Slop Factory](tiktok-slop-factory/) | An experiment that turns short fictional scripts into vertical videos with one command. |
| [quickscripts](small-projects/quickscripts/), [Cube Timer](small-projects/Cube%20Timer.html) | Small utilities. |

## Quick start

Each project installs separately. Two examples:

```bash
git clone https://github.com/angasko-12345/projects-ai.git
cd projects-ai

# AgentOps (Python 3.11+)
cd agentops
pip install -e .
python -m agentops agents      # detect installed agent CLIs
python -m agentops gui

# QR Generator (Node.js LTS)
cd ../qr-generator
npm install
npx expo start
```

See each project's README for full setup, configuration and tests.

## Dev commands

`tasks.py` at the repo root is one stdlib-only entrypoint that runs the same commands CI and the product docs run. It works the same in PowerShell and bash, and installs nothing (install each product's dependencies first).

```powershell
python tasks.py list                    # products and the exact commands behind them
python tasks.py test agentops           # unit suite + acceptance_test.py
python tasks.py test all                # every Python product's suite
python tasks.py lint                    # ruff check . with the repo-wide config
python tasks.py format-check mini-llm   # ruff format --check on one product
python tasks.py evidence ai-token-tracker   # regenerate + check verification evidence
```

Product names: `agentops`, `ai-token-tracker` (`tracker`), `mini-llm`, `universal-game-agent` (`uga`), `privacy-audit`, `tiktok-slop-factory`.

## Repository layout

```
agentops/               AgentOps orchestrator (Python package + GUI)
qr-generator/           Android QR app (Expo)
ai-token-tracker/       Token usage dashboard
universal-game-agent/   Pixel-based RL testbed
tiktok-slop-factory/    Video generation experiment
small-projects/         mini-llm, privacy audit tool, scripts
tools/evidence/         Verification-evidence tooling used by CI
.agents/                Agent instructions, plans and verification records
tasks/                  Active-task slot (or pointer) + after-task procedure; live queue: .agents/pending_tasks.md
.agents/memory/archive/ Completed task records, preserved verbatim and searchable
AGENTS.md, pi_AGENTS.md Root instruction shims (canonical copies live in .agents/)
acceptance_test.py      AgentOps acceptance tests from Issue #4
tasks.py                Dev entrypoint: test / lint / evidence per product
.github/workflows/      Nine CI workflows: a test suite for every product, plus repo-wide ruff and the evidence checker
```

## How work gets verified

Changes are developed with coding agents, then checked before they're trusted. Test suites for every product run in CI — AgentOps, Universal Game Agent, mini-llm and AI Token Tracker on Python 3.11, plus TikTok Slop Factory, QR Generator and the Privacy Audit Tool in their own jobs — and `tools/evidence/` records which commands ran and whether they passed in `.agents/evidence/verification.json` for the four measured products. A change isn't done until its tests pass and the evidence is regenerated. Test counts are not quoted in this README — they live in the evidence file.

## Roadmap

Planned work is tracked in [Issues](https://github.com/angasko-12345/projects-ai/issues). Highlights:

- AgentOps: finishing the autonomous coding-loop MVP ([#4](https://github.com/angasko-12345/projects-ai/issues/4))
- AI Token Tracker: per-account limit warnings and session handoff notes ([#13](https://github.com/angasko-12345/projects-ai/issues/13))
- Universal Game Agent: PPO on a live browser game ([#15](https://github.com/angasko-12345/projects-ai/issues/15))
- Privacy Audit Tool: a recurring-charge scan from bank statements ([#16](https://github.com/angasko-12345/projects-ai/issues/16))

## Contributing

Bug reports and ideas are welcome. Open an issue and name the project in the title, for example `[agentops] ...`. For code, keep each PR to one project and include tests.

## License

[GPL-3.0](LICENSE)
