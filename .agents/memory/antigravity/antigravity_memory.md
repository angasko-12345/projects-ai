# Antigravity Persistent Agent Memory

Canonical reference for Antigravity sessions working in this repository.
Last verified: 2026-10-05.

---

## 1. Agent Identity & Role

* **Agent**: Antigravity (Google DeepMind agentic coding assistant).
* **Role**: Pair programmer and review collaborator across repository products.
* **Operating Policy**:
  * Adhere strictly to `.agents/AGENTS.md`, `.agents/team.md`, and product-specific `AGENTS.md` files.
  * When tasked as read-only reviewer: never modify the working tree; provide file/line evidence.
  * When tasked as implementer: write regression tests before fixing bugs, preserve backwards compatibility, and keep migrations additive.

---

## 2. Multi-Product Project Structure

This repository is a container for three independent Python products (plus shared automation tooling):

1. **`agentops/`** — Local-first orchestrator for installed coding-agent CLIs (worktree isolation, SQLite persistence, PySide6/Tkinter GUI, PyInstaller packaging).
   * Entry points: `agentops/cli.py`, `agentops/gui.py`, `agentops_gui.py`.
   * Test command: `cd agentops && python -m unittest discover -s tests`.
2. **`universal-game-agent/`** — Reinforcement-learning Pong agent (Gymnasium toy simulation + Windows-only real screen-capture / SendInput path).
   * Entry points: `main.py`, `training/external_smoke.py`.
   * Test command: `cd universal-game-agent && python -m unittest discover -s tests`.
3. **`small-projects/mini-llm/`** — Self-contained causal GPT language model built from scratch with PyTorch.
   * Entry points: `prepare_data.py`, `src/train.py`, `src/generate.py`.
   * Test command: `cd small-projects/mini-llm && python -m unittest discover -s tests`.
4. *(Sibling / External)*: `tiktok-slop-factory/` — Video rendering pipeline (handled in separate sessions; contains uncommitted changes).

> **Critical Rule**: Products are strictly isolated. No cross-product imports. A change in one product is not a change in another. There is no root-level test command.

---

## 3. Mini-LLM Product Deep Dive

### Architecture & Pipeline
* `prepare_data.py`: Trains byte-level BPE tokenizer (via `tokenizers` library) on UTF-8 raw text, chunks streaming text into passages, adds `<bos>`/`<eos>`, and writes memory-mapped `uint16` binary files (`train.bin`, `val.bin`) plus `meta.json` with artifact SHA-256 digests.
* `src/dataset.py`: `TokenDataset` and `TokenDataLoader` slice token streams into next-token `(x, y)` training pairs. Default stride is 1 (supervises every transition).
* `src/model.py`: Hand-written `MiniGPT` (pre-norm LayerNorm, multi-head causal attention with lower-triangular boolean mask, MLP with GELU, tied embeddings `lm_head.weight = wte.weight`). Built strictly via `from_config(cfg)` matching `MODEL_FIELDS = ("vocab_size", "context_length", "n_layers", "n_heads", "d_model", "d_ff", "dropout")`.
* `src/train.py`: AdamW with decoupled weight decay (matrices only), linear warmup + cosine decay, grad clipping, RNG state capture/restore, checkpoint save/load/validate, and resumption logic.
* `src/generate.py`: Autoregressive generation with temperature, top-k filtering, `<eos>` termination, and tokenizer provenance validation.

### Checkpoint Schema Version 2
* Schema version: `CHECKPOINT_SCHEMA_VERSION = 2`.
* Top-level keys: `model_state`, `config`, `step`, `optimizer_state`, `scheduler_state`, `rng_state`, `data_provenance`, `checkpoint_metadata`.
* Self-describing `checkpoint_metadata` block:
  ```json
  {
    "schema_version": 2,
    "model": {"vocab_size": int, "context_length": int, "n_layers": int, "n_heads": int, "d_model": int, "d_ff": int, "dropout": float},
    "tokenizer": {"path": str, "sha256": str | None},
    "progress": {"step": int, "epoch": float | None, "tokens_seen": int | None, "max_steps": int},
    "required_keys": ["model_state", "config", "step"]
  }
  ```
* **Validation contract (`validate_checkpoint()`)**:
  * Rejects non-dict files, unsupported schema versions (`version > 2`), or forged metadata claiming version 1.
  * Rejects missing required keys (`model_state`, `config`, `step`) or missing metadata fields.
  * Cross-validates:
    * `config` vs `meta["model"]` across all `MODEL_FIELDS`.
    * `progress.step` vs `ckpt["step"]`.
    * `tokenizer.path` vs `config["tokenizer_path"]`.
    * `progress.max_steps` vs `config["max_steps"]` when present.
    * `tokenizer.sha256` vs `data_provenance["tokenizer_sha256"]` when present.
    * `model_state` tensor dimensions (`wte.weight`, `wpe.weight`) against `meta["model"]`.
* **Legacy Checkpoint Compatibility**:
  * Checkpoints lacking `checkpoint_metadata` are accepted if `config` has `MODEL_FIELDS` and `tokenizer_path`.
  * `_legacy_metadata()` synthesizes an in-memory metadata block with `schema_version: 1`.
  * Legacy checkpoints without `data_provenance` load for evaluation/generation, but are intentionally refused for `--resume` to prevent training against unverified data.
* **Resume Semantics**:
  * Programmatic: `train(cfg, resume_from=...)` defaults `start_step` to `int(ckpt["step"]) + 1`. Earlier steps are never rerun.
  * Progress accounting: `tokens_seen` accumulates `tokens_before` + `total_tokens` of the current segment. `epoch` is continuous: `(tokens_before + total_tokens) / n_train_tokens`.
  * Provenance: Resumes verify `train_sha256`, `val_sha256`, and `tokenizer_sha256` by content. Relocated directories with matching digests pass; modified artifacts fail fast.
* **Safe Loading**: All checkpoint fields are tensors and primitive containers; loads cleanly under `torch.load(..., weights_only=True)`.

---

## 4. Verified Test Baselines

All baselines independently executed and confirmed as of 2026-10-05:

| Product | Test Command | Verified Baseline (2026-10-05) | Notes |
|---|---|---|---|
| `small-projects/mini-llm/` | `cd small-projects/mini-llm && python -m unittest discover -s tests` | **142 tests, 1 skip, OK** | +27 tests over original 115 baseline (+21 in commit `b189c34`, +6 in Antigravity session). |
| `universal-game-agent/` | `cd universal-game-agent && python -m unittest discover -s tests` | **366 tests, 1 skip, OK** | Re-verified 2026-10-05 following cadence experiment work. |
| `agentops/` | `cd agentops && python -m unittest discover -s tests` | **501 tests, 4 skips, OK** | Re-verified 2026-10-04 following control center implementation. |

---

## 5. Non-Negotiable Conventions & Operating Constraints

1. **Dirty Tree Isolation**:
   * Either Pi or Oh-My-Pi is the sole writer during concurrent sessions. Reviewers work in read-only snapshots.
   * Do NOT stage unrelated dirty files (`git status` contains unstaged edits in `.agents/memory/`, `universal-game-agent/`, `tiktok-slop-factory/`). Always stage explicit paths (`git add <specific-file>`), never `git add -A` or `git commit -a`.
2. **Secrets & Hygiene**:
   * Never commit or persist raw prompts, API keys, tokens, credentials, or environment dumps.
3. **SQLite Migrations**:
   * Additive only (`ALTER TABLE ADD COLUMN`, `CREATE TABLE IF NOT EXISTS`, `INSERT OR IGNORE`).
   * Explicit column names on every insert; positional inserts fail on legacy schemas.
4. **Platform Compatibility (Windows)**:
   * Python 3.11 is the supported floor. Avoid backslashes inside f-string expressions (`{'\n '.join(...)}` is a `SyntaxError` on <= 3.11).
   * Use `CREATE_NO_WINDOW` and process-group helpers for child processes.
   * Handle legacy console encoding gracefully; avoid bare unicode terminal writes.

---

## 6. Previous Pitfalls to Avoid

* **Masked Resume Defaults**: Unit tests passing explicit arguments (e.g. `start_step=4`) masked a bug where `train(cfg, resume_from=...)` defaulted to `start_step=1`. Always test the default API contract without optional overrides.
* **Superficial Metadata Cross-Checks**: Checking `MODEL_FIELDS` between config and metadata was insufficient; non-model fields (`step`, `tokenizer_path`, `max_steps`, `sha256`) and tensor shapes were unvalidated and could silently contradict each other.
* **Duplicate Disk I/O**: `train()` and `generate.py` were reading large checkpoints twice in sequence (once for provenance, once for state dicts). Functions accepting `str | dict` allow reusing in-memory checkpoints.
* **Stale Documentation Baselines**: Test count numbers in markdown docs quickly drift out of date after feature additions. Trust the live test suite output over documentation claims.
* **Corpus LF/CRLF Sensitivity**: BPE tokenizers produce different vocabulary sizes if line endings differ (LF = 308, CRLF = 310 on sample corpus). `.gitattributes` pins `-text` on `data/`.

---

## 7. Useful Workflows & Commands

* **Run focused Mini-LLM checkpoint tests**:
  ```pwsh
  python -m unittest tests.test_pipeline.TestCheckpointMetadata
  ```
* **Run full Mini-LLM test suite**:
  ```pwsh
  cd small-projects/mini-llm
  python -m unittest discover -s tests
  ```
* **Train and resume Mini-LLM via CLI**:
  ```pwsh
  python -m src.train --context-length 32 --max-steps 10
  python -m src.train --resume checkpoints/step_6.pt --max-steps 20
  ```
* **Text Generation**:
  ```pwsh
  python src/generate.py --checkpoint checkpoints/final.pt --prompt "The tiny"
  ```
