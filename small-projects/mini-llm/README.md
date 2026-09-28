# mini-llm

A small GPT-style causal language model built from scratch with PyTorch.
Raw UTF-8 text goes in; next-token predictions come out:

```
raw text → BPE tokenizer → token IDs → batches → Transformer
→ cross-entropy loss → backprop → checkpoints → generated text
```

No Hugging Face Transformers, no pretrained weights. The `tokenizers`
package is used only for BPE training; the model itself is hand-written
`nn.Module`s.

## How the Transformer works

Each position starts as a learned token embedding plus a learned positional
embedding. That vector flows through `n_layers` identical blocks, then a
final LayerNorm and an output projection back to vocabulary size.

One block (pre-norm, with residual skips):

```
x = x + MultiHeadCausalAttention(LayerNorm(x))
x = x + MLP(LayerNorm(x))
```

Causal self-attention per head: `Q = xWq, K = xWk, V = xWv`, then scaled
dot-product `softmax(QKᵀ/√d)V`. A lower-triangular mask sets future scores
to −∞ before softmax, so position *i* only sees positions ≤ *i*.
`n_heads` heads run in parallel and are concatenated through an output
projection. The MLP is `d_model → d_ff → d_model` with GELU. The LM head
is weight-tied to the token embedding.

Architecture defaults: context 512, 6 layers, 6 heads, d_model 384,
d_ff 1536 → **13,982,976 unique parameters (~14M)** at vocab 8192. The
vocabulary is not a design constant: `prepare_data.py` records the size the
tokenizer actually reached in `data/processed/meta.json`, and training adopts
it (or fails) rather than building rows the data can never produce. The
shipped sample corpus yields vocab 308.

## Project structure

```
mini-llm/
├── data/
│   ├── raw/train.txt          # plain UTF-8 continuous text (input)
│   ├── tokenizer.json         # trained BPE tokenizer
│   └── processed/{train,val}.bin  # uint16 token IDs (memmapped)
│   └── processed/meta.json    # real vocab size + split sizes (written by prepare_data.py)
├── checkpoints/               # created by training: step_N.pt + final.pt
├── src/
│   ├── config.py              # all defaults (model + training)
│   ├── tokenizer.py           # BPE train / load / encode / decode
│   ├── model.py               # MiniGPT: attention, MLP, blocks
│   ├── dataset.py             # memmap dataset, x=tokens[:-1] y=tokens[1:]
│   ├── train.py               # AdamW + warmup/cosine, eval, checkpoints
│   └── generate.py            # autoregressive sampling (temperature, top-k)
├── tests/{test_tokenizer,test_model}.py
├── prepare_data.py            # tokenizer training + 99/1 split to .bin
├── requirements.txt
└── README.md
```

## Dataset format

`data/raw/train.txt`: plain UTF-8 continuous text, no labels or formatting.
`prepare_data.py` tokenizes it, splits by tokens (99% train by default, but
never less than one context-length sample for val), wraps each blank-line
passage in `<bos>` … `<eos>`, and writes compact `uint16` arrays plus
`meta.json`. Samples are `context_length + 1`
consecutive tokens, shifted into `(x, y)` next-token pairs. Training windows
slide one token at a time by default (see below), so every next-token
transition is supervised rather than only those inside a non-overlapping
chunk.

The shipped sample corpus is ~2.4 KB / ~920 tokens, which is far below the
512-token context: preparing it needs `--context-length 32`, and training
with it needs the same flag. Both commands refuse to run and say so on a
corpus that is too small for the requested context.

## Installation

```bash
cd mini-llm
pip install -r requirements.txt
```

## Tokenizer / data preparation

```bash
# Train BPE (special tokens <pad> <unk> <bos> <eos>) + build .bin files
python prepare_data.py                    # for a corpus of >= ~1M tokens
python prepare_data.py --context-length 32   # for the tiny shipped sample

# Options
python prepare_data.py --vocab-size 256 --min-frequency 1 --val-frac 0.2
python prepare_data.py --skip-training   # reuse existing tokenizer.json
```

`--context-length` (default 512) sizes the val split: val is
`max(context_length + 2, val_frac * total)` tokens, and the run aborts if that
leaves too few tokens for one train sample. `--vocab-size` may not exceed
65536, the capacity of the `uint16` `.bin` format.

## Training

```bash
python -m src.train                                   # real corpus
python -m src.train --context-length 32                # shipped 2.4 KB sample
# or: python src/train.py --max-steps 2000 --batch-size 8 --context-length 512
```

AdamW (betas 0.9, 0.95, weight decay 0.1 on matrices only — LayerNorm scales
and biases are excluded), linear warmup (500 steps) then
cosine decay to 10% of peak lr, grad clip 1.0, seed 42. Prints device,
parameter count, train/val loss, lr, and step. Checkpoints
(`model_state`, `optimizer_state`, `scheduler_state`, `step`, `config`,
`rng_state`, losses) land in `checkpoints/` every `eval_interval` steps plus
`final.pt`, and load under `weights_only=True` (no pickled objects). If a
`DataLoader` epoch ends mid-run, the iterator is rebuilt and training
continues. `vocab_size` is read from `data/processed/meta.json` unless
`--vocab-size` is given, so the output head matches the tokenizer. Evaluation
never cycles the val loader: it uses at most `eval_batches` batches, or one
full pass if the val set is smaller, and averages **per target token** so a
ragged final batch is not over-weighted.

### Window stride

`--stride N` sets how far the training window advances between samples
(default `1`). Samples always contain `context_length + 1` tokens; the stride
only decides how many windows the corpus yields:

```
samples = (n_tokens - context_length - 1) // stride + 1
```

Measured on the shipped corpus (736 train tokens, `context_length` 32,
`batch_size` 8):

| stride | samples | batches/epoch | tokens processed/epoch |
| --- | --- | --- | --- |
| `1` (default) | 704 | 88 | 23,232 |
| `context_length` (32) | 22 | 2 | 726 |
| `context_length + 1` (33) | 22 | 2 | 726 |

**The default is `1`.** Two reasons. It supervises every transition — the
non-overlapping layout silently discards the ones that straddle a seam (11 of
99 at `context_length` 8), and no window is seen twice in an epoch. And
because this project trains for a fixed step budget rather than to exhaustion,
stride 1 means every step sees a different window; at stride 32 the 22
available windows repeat ~7× per epoch, which on a corpus this small just
memorises them.

**The cost is compute per epoch, not per step:** an epoch over the same unique
tokens costs `stride×` more (33× here, 512× at `context_length` 512). On a real
corpus that you do iterate to exhaustion, `--stride <context_length>` buys the
same unique tokens for a fraction of the compute. The windowing is otherwise
identical.
Stride is a data-pipeline choice and is *not* stored in the checkpoint, so pass
the same value when you `--resume` a run.

### CPU throughput

Batching converts `uint16 → int64` once per batch (in the DataLoader collate
step) instead of once per sample, over the same memory-mapped `.bin` files.
Optional flags (all default off, all preserved across `--resume`):

```bash
python -m src.train --torch-threads 6 --torch-interop-threads 2
python -m src.train --compile   # torch.compile; benchmark first, not known-good on Windows CPU
```

Thread counts are only applied when passed; the active
`intraop/interop` values print at startup either way. `--compile` fails fast
with a clear error if `torch.compile` is unavailable, and never leaks into
checkpoints: `step_N.pt`/`final.pt` and generation always use the uncompiled
module. Every run ends with a one-line summary reporting total training time,
steps/sec, and tokens/sec (plus the total token count, so ragged tails count
for what they are).

Batch order differs from the pre-throughput loader by one RNG draw:
`TokenDataLoader` overrides `__iter__`, so it never consumes the internal seed
draw that `DataLoader.__iter__` performs before shuffling. Same batch
distribution, deterministic within this version, but a run started on the old
loader and resumed on the new one will not be bit-identical.

### Resuming

```bash
python -m src.train --resume checkpoints/step_500.pt                  # finish the run
python -m src.train --resume checkpoints/step_500.pt --max-steps 20000  # extend it
```

A resumed run takes its config from the checkpoint, so the original warmup and
cosine schedule continue from the stored step instead of restarting at step 1.
Model weights, AdamW moments, the step counter and the torch/python/numpy RNG
states are all restored. Any flag you pass explicitly still wins (including
`--max-steps`). Checkpoints are read with `weights_only=True`; a file that is
not a mini-llm checkpoint is rejected rather than unpickled.

## Generation

```bash
python src/generate.py --checkpoint checkpoints/final.pt --prompt "The fox" \
    --tokens 100 --temperature 0.8 --top-k 40
```

Conditioning is truncated to `context_length`. `--temperature 0` = greedy;
temperatures are clamped at 1e-3, and negative values are rejected. `--top-k`
must be at least 1. Sampling stops at `<eos>` unless `--no-eos-stop` is given.
The tokenizer defaults to the path stored in the checkpoint config. Pass
`--seed N` for reproducible sampling (same checkpoint + prompt + seed +
settings ⇒ same text); without it, each run samples differently.

## Testing

```bash
python -m unittest discover -s tests
```

CPU only. Covers tokenizer load/encode/decode, output shape, finite scalar
loss, causal masking (future-token swap leaves past logits bit-identical),
generation ID validity, weight tying, and checkpoint round-trip, plus the
data pipeline: `prepare_data.py` framing and split sizing, `meta.json`,
`TokenDataset` window coverage, empty/undersized-loader diagnostics, LR warmup,
token-weighted evaluation with a ragged final batch, the training loop end to
end, resume (step/optimizer/schedule/RNG), AdamW decay groups, safe checkpoint
loading, seeded vs unseeded generation, window-stride sample counts, and the
agreement between the shipped corpus, tokenizer and `meta.json`.

## Limitations

- Tiny by design: the sample corpus teaches shape, not the world. Real
  training needs megabytes of text and a GPU.
- Fixed context window (512); longer prompts are truncated, not summarized.
- Byte-level BPE with no whole-word guarantees; small corpora underfill the
  requested vocabulary, so the model is built for the size the tokenizer
  actually reached (308 on the shipped sample), not the 8192 target.
- Training does not stop on its own: there is no validation-based early
  stopping. An interrupted run continues correctly via `--resume`, but a run
  that is restarted from scratch begins a new schedule at step 1.
- Byte-level BPE is sensitive to the corpus's line endings: the shipped
  sample yields vocab 308 with LF and 310 with CRLF. `.gitattributes` marks
  `data/` as `-text` so no checkout can rewrite it, and
  `tests/test_pipeline.py::TestShippedData` fails if the corpus and
  `meta.json` ever disagree.
