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
