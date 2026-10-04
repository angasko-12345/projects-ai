# mini-llm product instructions

Local instructions for the `small-projects/mini-llm/` package. The repository-wide
contract lives in `.agents/AGENTS.md` and outranks this file on process, roles, and
collaboration. Where this file and `.agents/AGENTS.md` disagree on this product's
facts, this file is correct.

## Identity

- A small GPT-style causal language model built from scratch with PyTorch: BPE
  tokenizer (`tokenizers` package used only for BPE training), hand-written
  Transformer (`nn.Module`s, no Hugging Face), AdamW + warmup/cosine training loop.
- The repository root is a container. This package is one of three products here; see
  `agentops/AGENTS.md` and `universal-game-agent/AGENTS.md` for the others. No product
  is a subproject of another, and a change in one is not a change in another.
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
  (`prepare_data.py` framing/splits/`meta.json`), window stride, device selection,
  and the training loop end to end including resume.
- Current baseline (re-run 2026-10-04): **98 run, 1 skip, OK**.
- `data/tokenizer.json` is a **tracked artifact** and must stay in the working tree. It
  was deleted locally on 2026-10-04 and restored from git; nothing in history ever
  deleted it. Without it `generate.py` cannot load a tokenizer, which surfaces as three
  `TestGenerationSeed` errors. `TestShippedData.test_shipped_tokenizer_matches_the_committed_vocab`
  now guards it, and the committed `data/tokenizer.json` reproduces the committed
  `data/processed/*` byte for byte.
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
- Checkpoints load under `weights_only=True`; stride is a data-pipeline choice not
  stored in checkpoints, so pass the same `--stride` when resuming.

## Known wart — checkpoints store default paths (unfixed as of 2026-10-02)

- `Config.tokenizer_path` has **no CLI flag**, and `prepare_data.py` does **not** record it
  in `meta.json`. Every checkpoint therefore stores the default `data/tokenizer.json`
  regardless of which tokenizer was actually used. Only `src/generate.py --tokenizer` can
  override it at generation time; `src/train.py` has no `--tokenizer` flag at all and the
  training loop never loads a tokenizer.
- `Config.train_bin` / `val_bin` / `checkpoint_dir` *are* settable through `src.train`
  flags, but a resumed run takes its config from the checkpoint, so those stored strings
  are authoritative unless re-passed. A checkpoint trained against non-default paths
  therefore resumes against the defaults.
- This is load-bearing because `Config.validate_against_data()` reads `meta.json` next to
  `train_bin` and raises on a `vocab_size` mismatch. Reproduced read-only on the
  TinyStories artifact (`checkpoints/tinystories/final.pt`, vocab 8192) resuming against
  the vocab-308 `data/processed/` prep: `ValueError: vocab_size=8192 but
  data/processed/train.bin was encoded with a vocabulary of 308`.
- When documenting or reproducing an experiment, pass the data paths explicitly rather than
  relying on what a checkpoint stores. Fixing the underlying gap is a source change and is
  tracked in `.agents/pending_tasks.md` under mini-llm.
