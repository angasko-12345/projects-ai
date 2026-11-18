# Project catalog

A verified map of the software projects in `D:\admin\code` and their GitHub homes.
Generated 2026-11-18. Machine-readable form: [`project-index.json`](project-index.json).

Status legend: **Active** (maintained, working), **Research** (measured experiments, not a product),
**Experimental** (prototype or learning project), **Pre-release** (product heading to release),
**Archived** (kept for reference, not maintained), **Local-only** (no version control).

## Main products

### AgentOps

- **Status:** Active
- **Purpose:** Local-first orchestrator for installed coding-agent CLIs. Plans, implements, verifies and reviews work in isolated Git worktrees with a SQLite run history.
- **Stack:** Python 3.11+, SQLite, PySide6 (optional desktop GUI)
- **Directory:** `agentops/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/agentops>
- **Entry points:** `python -m agentops`, `agentops-gui`
- **Docs:** `agentops/README.md`, `agentops/AGENTS.md`
- **Tests:** `python -m unittest discover -s tests` (CI workflow `agentops.yml`)

### QR Generator

- **Status:** Pre-release
- **Purpose:** Android QR code maker for text, URLs, email, phone and Wi-Fi. On-device only, with PNG/SVG export, history, dark mode and an optional Google Play Pro tier.
- **Stack:** Expo, React Native, TypeScript
- **Directory:** `qr-generator/`
- **GitHub:** monorepo copy at <https://github.com/angasko-12345/projects-ai/tree/main/qr-generator>; a separate public repository also exists at <https://github.com/angasko-12345/qr-generator> (see "Divergent copies" below)
- **Entry points:** `npx expo start`, `App.tsx`
- **Docs:** `qr-generator/README.md`, `qr-generator/AGENTS.md`
- **Tests:** `npm test`, `npm run lint`, `npm run typecheck` (CI workflow `qr-generator.yml`)

### AI Token Tracker

- **Status:** Active
- **Purpose:** Desktop dashboard of AI-token usage across local coding tools, backed by a local SQLite database. Read-only against each tool's local data.
- **Stack:** Python 3.10+, SQLite, PySide6
- **Directory:** `ai-token-tracker/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/ai-token-tracker>
- **Entry points:** `python -m token_tracker`
- **Docs:** `ai-token-tracker/README.md`, `ai-token-tracker/AGENTS.md`
- **Tests:** `python -m unittest discover -s tests` (CI workflow `ai-token-tracker.yml`)

### Universal Game Agent

- **Status:** Research
- **Purpose:** Pixel-only reinforcement-learning testbed. A recurrent PPO agent learns a built-in toy Pong from rendered frames, with experiment ledgers and reward diagnostics.
- **Stack:** Python, PyTorch, Gymnasium
- **Directory:** `universal-game-agent/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/universal-game-agent>
- **Entry points:** `python main.py`
- **Docs:** `universal-game-agent/README.md`, `universal-game-agent/AGENTS.md`
- **Tests:** `python -m unittest discover -s tests` (CI workflow `universal-game-agent.yml`)

## Content and research products

### histyt (Crouton Alpha)

- **Status:** Active
- **Purpose:** History-focused YouTube video pipeline: topic → research → script → approval gate → voice → scenes → visuals → assemble → thumbnail → variation → QA → render gate → private upload.
- **Stack:** Python 3.10+, PyYAML, Pillow, requests, FFmpeg
- **Directory:** `history-yt/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/history-yt>
- **Entry points:** `python -m histyt`
- **Docs:** `history-yt/README.md`, `history-yt/histyt/AGENTS.md`
- **Tests:** `python -m pytest tests -q`

### YouTube niche-scan

- **Status:** Active
- **Purpose:** Read-only YouTube Data API v3 tool that ranks niche keywords by RPM tier × small-channel win rate. No OAuth, uploads or scraping.
- **Stack:** Python 3.10+ standard library
- **Directory:** `youtube-niche-scan/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/youtube-niche-scan>
- **Entry points:** `python scan.py`, `python analyze.py`
- **Docs:** `youtube-niche-scan/README.md`
- **Tests:** `python -m pytest tests -q`

### TikTok Slop Factory

- **Status:** Experimental
- **Purpose:** One-command generator that turns short fictional scripts into vertical (1080×1920) videos with narration and burned-in captions.
- **Stack:** Python 3.10+, FFmpeg, Gemini API or local text backend
- **Directory:** `tiktok-slop-factory/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/tiktok-slop-factory>
- **Entry points:** `python make_videos.py`
- **Docs:** `tiktok-slop-factory/README.md`, `tiktok-slop-factory/AGENTS.md`
- **Tests:** `python -m pytest tests -q` (CI workflow `tiktok-slop-factory.yml`)

## AI and machine learning

### Small Model Fleet

- **Status:** Research
- **Purpose:** Fleet of small specialised language models for planning and PC-agent work. Holds the frozen benchmark suites, scoring harness, Kaggle experiment kernels, model registry and experiment log. Milestone 1 is done; baselines are queued, no benchmark numbers exist yet.
- **Stack:** Python 3.12+, PyTorch, Hugging Face, Kaggle GPU kernels
- **Directory:** `small-model-fleet/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/small-model-fleet>
- **Entry points:** `src/smf_bench.py`, `src/smf_runner.py`, `experiments/*/kernel/run.py`
- **Docs:** `small-model-fleet/README.md`, `PROJECT_STATUS.md`, `MODEL_REGISTRY.md`, `EXPERIMENT_LOG.md`
- **Tests:** `python -m pytest tests -q` (offline harness tests)

### mini-llm

- **Status:** Active
- **Purpose:** Small GPT-style causal language model written from scratch in PyTorch: BPE tokenizer, transformer blocks, training loop, sampling and optional GGUF export.
- **Stack:** Python 3.10+, PyTorch, tokenizers
- **Directory:** `small-projects/mini-llm/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/small-projects/mini-llm>
- **Entry points:** `python prepare_data.py`, `python -m src.train`
- **Docs:** `small-projects/mini-llm/README.md`, `AGENTS.md`
- **Tests:** `python -m pytest tests -q` (CI workflow `mini-llm.yml`)

### verytinyllm

- **Status:** Experimental
- **Purpose:** Minimal character-level GPT-style model (`tiny_llm.py`) with a small sample corpus. A learning project, not a packaged product.
- **Stack:** Python, PyTorch
- **Directory:** `small-projects/verytinyllm/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/small-projects/verytinyllm>
- **Entry points:** `python tiny_llm.py`
- **Docs:** none (source docstring only)
- **Tests:** none

### tinyllm (script)

- **Status:** Experimental
- **Purpose:** Standalone single-file version of the same from-scratch PyTorch language-model experiment. Kept next to `verytinyllm/` because it is the earlier, self-contained form.
- **Stack:** Python, PyTorch
- **Directory:** `small-projects/tinyllm.py`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/blob/main/small-projects/tinyllm.py>
- **Entry points:** `python tinyllm.py`
- **Docs:** source docstring
- **Tests:** none

## Developer infrastructure and tools

### Privacy Audit Tool

- **Status:** Active
- **Purpose:** Read-only local Windows tool that finds forgotten online accounts from Google Takeout mail exports, browser history and usernames. Deletes nothing; offline by default.
- **Stack:** Python 3.10+, standard library
- **Directory:** `small-projects/privacy_audit_tool/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/small-projects/privacy_audit_tool>
- **Entry points:** `run_audit.ps1`, `privacy-audit` console script
- **Docs:** `small-projects/privacy_audit_tool/README.md`, `AGENTS.md`
- **Tests:** `python -m pytest tests -q` (CI workflow `privacy-audit.yml`)

### Evidence tooling

- **Status:** Active (internal)
- **Purpose:** Generates and checks `.agents/evidence/verification.json` so documented test results are measured, not remembered.
- **Stack:** Python 3.10+
- **Directory:** `tools/evidence/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/tools/evidence>
- **Entry points:** `python tools/evidence/check.py`, `python tools/evidence/generate.py`
- **Docs:** `tools/evidence/` docstrings; root `README.md`
- **Tests:** `python -m pytest tools/evidence -q`

### Obsidian sync

- **Status:** Active (internal)
- **Purpose:** One-way mirror of canonical agent memory into an Obsidian vault. The repository stays authoritative.
- **Stack:** PowerShell, Python (tests)
- **Directory:** `tools/obsidian-sync/`
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/tools/obsidian-sync>
- **Entry points:** `sync-agent-memory.ps1`, `install-hook.ps1`
- **Docs:** `tools/obsidian-sync/README.md`
- **Tests:** `python -m pytest tools/obsidian-sync -q`

## Experiments and archived utilities

### ride-a-noob (standalone repository)

- **Status:** Experimental (early foundation)
- **Purpose:** Rojo project skeleton for a Roblox game. Code-container-only mapping with server/client/shared trees. This is a **separate Git repository** nested at `rojo/ride-a-noob/`, with its own history and a private GitHub remote.
- **Stack:** Lua/Luau, Rojo 7.7.1, Rokit
- **Directory:** `rojo/ride-a-noob/` (nested directory, not part of the monorepo history)
- **GitHub:** <https://github.com/angasko-12345/ride-a-noob> (private)
- **Entry points:** `default.project.json`
- **Docs:** `rojo/ride-a-noob/README.md`, `rojo/ride-a-noob/AGENTS.md`, `.agents/memory/ride-a-noob.md`
- **Tests:** `python tests/roundtrip/verify.py`

### Archived utilities

- **quickscripts** — one-off HTML showreels under `archive/quickscripts/vids ai/`.
- **Cube Timer** — single-page Rubik's cube timer at `archive/Cube Timer.html`.
- **Status:** Archived. Not maintained, tested or measured.
- **GitHub:** <https://github.com/angasko-12345/projects-ai/tree/main/archive>

## Standalone repositories and local-only experiments

### outreach

- **Status:** Active
- **Purpose:** Manual Facebook outreach toolkit: OpenStreetMap lead finder, message writer, image sampler and a daily driver. Sending is manual only.
- **Stack:** Python 3, Pillow, optional LiteLLM
- **Directory:** `D:\admin\code\outreach`
- **GitHub:** <https://github.com/angasko-12345/outreach> (private)
- **Entry points:** `python find_leads.py`, `python write_messages.py`, `python make_samples.py`, `python today.py`
- **Docs:** `README.md`, `README.txt`, `AGENTS.md`
- **Important:** `leads.csv` and `samples/` contain real personal data. They are git-ignored and must never be committed; the repository contains code and documentation only.

### kagglegpt

- **Status:** Experimental (local-only)
- **Purpose:** A single GPT-2 30M inference/resume experiment. The directory is one Python script plus a 1.79 GB `gpt2-30m-resume-bundle.zip`.
- **Stack:** Python, PyTorch
- **Directory:** `D:\admin\code\kagglegpt`
- **GitHub:** none
- **Entry points:** `gpt2-30m-infer.py`
- **Docs:** none
- **Important:** The 1.79 GB archive is a local build/resume bundle, not source. It should not be uploaded; keep download/rebuild instructions instead.

## Divergent copies

### qr-generator (standalone copy)

- **Status:** Pre-release
- **Purpose:** Standalone public repository for the QR app, with `docs/`, `marketing/` and `PRIVACY_POLICY_CORRECTIONS.md` that the monorepo copy does not have. The monorepo copy has newer `src/util/feedback*.ts` and `review*.ts` that the standalone copy does not.
- **Directory:** `D:\admin\code\qr-generator-public`
- **GitHub:** <https://github.com/angasko-12345/qr-generator>
- **Note:** The two copies have diverged. They were **not** merged or overwritten. Reconciliation needs an explicit decision about which copy is canonical.

## GitHub repositories without a local working copy

These repositories exist on GitHub but no corresponding directory was found under `D:\admin\code` during this audit. They are recorded so the index is complete; no local action was taken.

| Repository | Visibility | Note |
|---|---|---|
| [OpenFrontHOI4](https://github.com/angasko-12345/OpenFrontHOI4) | Public | No local checkout found in scope. |
| [qr-poster-service](https://github.com/angasko-12345/qr-poster-service) | Private | No local checkout found in scope. |
| [Worlds-Divided](https://github.com/angasko-12345/Worlds-Divided) | Private | No local checkout found in scope. |