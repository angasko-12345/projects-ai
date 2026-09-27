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
consecutive tokens, shifted into `(x, y)` next-token pairs.

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

AdamW (betas 0.9, 0.95, weight decay 0.1), linear warmup (500 steps) then
cosine decay to 10% of peak lr, grad clip 1.0, seed 42. Prints device,
parameter count, train/val loss, lr, and step. Checkpoints
(`model_state`, `optimizer_state`, `scheduler_state`, `step`, `config`,
losses) land in `checkpoints/` every `eval_interval` steps plus `final.pt`.
If a `DataLoader` epoch ends mid-run, the iterator is rebuilt and training
continues. `vocab_size` is read from `data/processed/meta.json` unless
`--vocab-size` is given, so the output head matches the tokenizer. Evaluation
never cycles the val loader: it uses at most `eval_batches` batches, or one
full pass if the val set is smaller.

## Generation

```bash
python src/generate.py --checkpoint checkpoints/final.pt --prompt "The fox" \
    --tokens 100 --temperature 0.8 --top-k 40
```

Conditioning is truncated to `context_length`. `--temperature 0` = greedy;
temperatures are clamped at 1e-3, and negative values are rejected. `--top-k`
must be at least 1. Sampling stops at `<eos>` unless `--no-eos-stop` is given.
The tokenizer defaults to the path stored in the checkpoint config.

## Testing

```bash
python -m unittest discover -s tests
```

CPU only. Covers tokenizer load/encode/decode, output shape, finite scalar
loss, causal masking (future-token swap leaves past logits bit-identical),
generation ID validity, weight tying, and checkpoint round-trip, plus the
data pipeline: `prepare_data.py` framing and split sizing, `meta.json`,
`TokenDataset` sampling, empty/undersized-loader diagnostics, LR warmup, the
training loop end to end, and generation argument validation.

## Limitations

- Tiny by design: the sample corpus teaches shape, not the world. Real
  training needs megabytes of text and a GPU.
- Fixed context window (512); longer prompts are truncated, not summarized.
- Byte-level BPE with no whole-word guarantees; small corpora underfill the
  requested vocabulary, so the model is built for the size the tokenizer
  actually reached (308 on the shipped sample), not the 8192 target.
- No validation-based early stopping or checkpoint resumption of the lr
  step counter (restart begins at step 1 with a fresh schedule).
