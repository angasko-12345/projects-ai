# mini-llm product instructions

Local instructions for the `small-projects/mini-llm/` package. The repository-wide
contract lives in `.agents/AGENTS.md` and outranks this file on process, roles, and
collaboration. Where this file and `.agents/AGENTS.md` disagree on this product's
facts, this file is correct.

## Identity

- A small GPT-style causal language model built from scratch with PyTorch: BPE
  tokenizer (`tokenizers` package used only for BPE training), hand-written
  Transformer (`nn.Module`s, no Hugging Face), AdamW + warmup/cosine training loop.
- The repository root is a container. This package is one of four products here; see
  `agentops/AGENTS.md`, `universal-game-agent/AGENTS.md`, and `ai-token-tracker/AGENTS.md`
  for the others. No product is a subproject of another, and a change in one is not a change in another.
- Runtime dependencies: `torch`, `tokenizers`, `numpy` (see `requirements.txt`,
  unpinned). Training is CPU-capable; the expensive real-corpus runs target a cloud
  GPU via the existing device-agnostic loop (`--device cuda`, fail-fast without CUDA).

## Tests

Run from `small-projects/mini-llm/`, never from the repository root:

```bat
cd small-projects/mini-llm
python -m unittest discover -s tests
```

- CPU only. Covers tokenizer load/encode/decode, model shape/loss/causal masking,
  generation, weight tying, checkpoint round-trip, the data pipeline
  (`prepare_data.py` framing/splits/`meta.json`), streaming-prepare parity,
  window stride, device selection, CPU throughput, and the training loop end to
  end including resume.
- The current measured result for this product lives in
  `.agents/evidence/verification.json` (`product: "small-projects/mini-llm"`), not in
  this file. Regenerate it with `python tools/evidence/generate.py
  small-projects/mini-llm` from the repository root and check it with `python
  tools/evidence/check.py`. Run the suite for current truth.
- `prepare_data.py` **streams**: it reads text in 1 MiB chunks and spills encoded
  IDs to a temp file, so a corpus much larger than RAM never sits in memory
  whole. `tests/test_prepare_streaming.py` pins byte-identical output against the
  small-corpus reference encoder.
- `data/tokenizer.json` is a **tracked artifact** and must stay in the working tree. It
  was deleted locally on 2026-10-04 and restored from git; nothing in history ever
  deleted it. Without it `generate.py` cannot load a tokenizer, which surfaces as three
  `TestGenerationSeed` errors. `TestShippedData.test_shipped_tokenizer_matches_the_committed_vocab`
  now guards it, and the committed `data/tokenizer.json` reproduces the committed
  `data/processed/*` byte for byte. It is present in the tree as of 2026-10-10
  (18,261 bytes); an earlier note in `docs/EXPERIMENT-tinystories.md` saying it is
  deleted is superseded.
- There is no configured lint, formatter, type-check, or coverage command. Do not
  invent one.
- The shipped sample corpus (`data/raw/train.txt`, ~2.4 KB) needs
  `--context-length 32` for prepare/train commands; the default context 512 refuses
  to run on it and says so.

## Conventions

- Do not claim the model is a useful general-purpose chatbot merely because it
  completes a training run. The sample corpus teaches shape, not the world.
- `data/processed/meta.json` records the vocab size the tokenizer actually reached;
  training adopts it (or fails) rather than building rows the data cannot produce.
  It also records the train/val/tokenizer paths and their sha256 digests; see
  "Checkpoint provenance and resume safety" below.
- Checkpoints load under `weights_only=True`; stride is a data-pipeline choice not
  stored in checkpoints, so pass the same `--stride` when resuming.
- `src/train.py` has `--tokenizer` (added 2026-10-10). `src/generate.py` also has
  `--tokenizer`; both verify it against the digest the checkpoint recorded.
- Default data paths are `data/processed/{train,val}.bin` and
  `data/tokenizer.json`. Any other prepared set needs `--train-bin`,
  `--val-bin`, and `--tokenizer` together; `config_for_data()` fills the latter two
  from `meta.json`, so `--train-bin` alone is enough when the set was prepared by
  the current `prepare_data.py`.
- The shipped `data/processed/` prep is the tiny sample: vocab 308, 736 train
  tokens, 184 val tokens, `context_length` 32. It needs `--context-length 32`
  for both prepare and train.

## Checkpoint provenance and resume safety (added 2026-10-10)

`meta.json` records which artifacts produced the `.bin` files, by content:

```json
"train_bin": "data/processed/train.bin",
"val_bin": "data/processed/val.bin",
"tokenizer_path": "data/tokenizer.json",
"train_sha256": "...", "val_sha256": "...", "tokenizer_sha256": "..."
```

- The paths are what was typed on the command line, for humans and for messages.
  The sha256 digests are the contract: an artifact tree that was moved or copied
  still verifies, and a file replaced at the same path does not.
- `src/train.py` takes `--tokenizer` and records it in every checkpoint it writes
  (as `config.tokenizer_path` plus a `data_provenance` block). `--train-bin` and
  `--val-bin` behave the same way.
- On resume, `src/train.py` compares the checkpoint's recorded digests against the
  current artifacts and refuses with a `SystemExit` naming both sides when they
  differ. A checkpoint written before provenance existed is refused rather than
  resumed on trust. A bare `--resume` needs no data flags, because the checkpoint
  carries verified paths.
- `src/generate.py` applies the same check to `--tokenizer`, so a foreign
  tokenizer cannot decode a checkpoint silently.
- `config_for_data()` adopts `tokenizer_path` and `val_bin` from `meta.json` when
  they are not passed, so pointing `--train-bin` at a prepared data set is enough.
- A `meta.json` without provenance keys is rejected with a message asking for a
  re-run of `prepare_data.py`. Re-running it on the shipped corpus reproduces
  `data/processed/*.bin` and `data/tokenizer.json` byte for byte; only
  `meta.json` gained fields.
- Checkpoints still load with `weights_only=True`; `data_provenance` is a plain
  dict of strings and ints.

## Checkpoint metadata schema (added after 2026-10-10)

Each checkpoint now carries a top-level `checkpoint_metadata` block
(`weights_only=True`-safe plain containers). Schema version 2 is current;
version 1 is the original layout (no block, facts stored only in `config`).

```json
"checkpoint_metadata": {
    "schema_version": 2,
    "model": {"vocab_size": ..., "context_length": ..., "n_layers": ...,
              "n_heads": ..., "d_model": ..., "d_ff": ..., "dropout": ...},
    "tokenizer": {"path": "...", "sha256": "..."},
    "progress": {"step": ..., "epoch": ..., "tokens_seen": ..., "max_steps": ...},
    "required_keys": ["model_state", "config", "step"]
}
```

- The tokenizer path and digest survive save/load; the digest (not the path)
  is what identifies the tokenizer's contents, so a renamed copy is still
  verifiable, and a replaced file is detected.
- `validate_checkpoint(ckpt, path)` is the single gate: missing or
  contradictory metadata raises `ValueError` naming the problem instead of
  filling in defaults (a defaulted vocab_size/context_length/tokenizer builds
  a different model). A legacy v1 checkpoint with no block is accepted when its
  `config` still carries the facts it was trained with, and is reported as
  schema version 1 in memory only.
- A metadata block with a schema version newer than the running code is
  refused with a message naming both versions.
- `tokens_seen` / `epoch` are counted across a resume: the continuation
  carries the count the earlier run wrote, so `epoch` never restarts.
- `save_checkpoint` writes the block; `read_checkpoint` validates and
  returns it; `load_model` and `load_checkpoint` are unchanged callers.
