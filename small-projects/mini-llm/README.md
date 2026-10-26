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
shipped sample corpus yields vocab 515: the full 256-symbol byte alphabet,
4 special tokens, and 255 merges.

## Project structure

```
mini-llm/
├── data/
│   ├── raw/train.txt          # plain UTF-8 continuous text (input)
│   ├── tokenizer.json         # trained BPE tokenizer
│   └── processed/{train,val}.bin  # uint16 token IDs (memmapped)
│       processed/meta.json    # vocab size, split sizes, artifact paths + sha256
├── checkpoints/               # created by training: step_N.pt + final.pt
├── src/
│   ├── config.py              # all defaults (model + training)
│   ├── tokenizer.py           # BPE train / load / encode / decode
│   ├── model.py               # MiniGPT: attention, MLP, blocks
│   ├── dataset.py             # memmap dataset, x=tokens[:-1] y=tokens[1:]
│   ├── train.py               # AdamW + warmup/cosine, eval, checkpoints
│   ├── generate.py            # autoregressive sampling (temperature, top-k)
│   ├── evaluate.py            # val bits-per-byte + fixed seeded sample sheet
│   └── export_gguf.py         # checkpoint -> GGUF for llama.cpp (optional `gguf` dep)
├── scripts/                   # gguf_demo.{ps1,sh}: train -> export -> llama-server;
│                              # llama_parity.py: compare against a real llama-server
├── tests/                     # test_model, test_pipeline, test_prepare_streaming,
│                              # test_throughput, test_tokenizer
├── prepare_data.py            # tokenizer training + streaming encode/split to .bin
├── kaggle/                    # train_tinystories.ipynb + run_recipe.py (T4 baseline)
├── docs/                      # experiment records (EXPERIMENT-tinystories.md, RESULTS.md)
├── requirements.txt
├── requirements-gguf.txt      # optional: `gguf`, only for export_gguf.py
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
python prepare_data.py --context-length 32 --val-frac 0.2   # reproduces the shipped sample

# Options (--vocab-size must be at least 260: 256 byte symbols + 4 specials)
python prepare_data.py --vocab-size 512 --min-frequency 1 --val-frac 0.2
python prepare_data.py --skip-training   # reuse existing tokenizer.json
```

`--context-length` (default 512) sizes the val split: val is
`max(context_length + 2, val_frac * total)` tokens, and the run aborts if that
leaves too few tokens for one train sample. `--vocab-size` may not exceed
65536, the capacity of the `uint16` `.bin` format.

**Preparation streams.** The encoder reads the input in 1 MiB text chunks and
spills encoded IDs to a temp file in bounded buffers, so a corpus far larger
than RAM is prepared without ever holding it whole — peak usage is one text
chunk plus one write buffer plus a batch of passages. The split is then a
bounded copy out of that temp file. This matters for the multi-GB TinyStories
runs; `tests/test_prepare_streaming.py` pins the output byte-for-byte against a
small-corpus reference encoder so streaming and reference cannot drift.

`meta.json` also records the train/val/tokenizer paths and their sha256 digests,
which is what lets a later run prove it is reading the same artifacts (see
[Resuming](#resuming)).

## Training

```bash
python -m src.train                                   # real corpus
python -m src.train --context-length 32                # shipped 2.4 KB sample
python -m src.train --device cuda                      # require the GPU (see below)
# or: python src/train.py --max-steps 2000 --batch-size 8 --context-length 512
```

Training against a non-default prepared data set names its artifacts:

```bash
python -m src.train \
  --train-bin data/tinystories/train.bin \
  --val-bin data/tinystories/val.bin \
  --tokenizer data/tinystories/tokenizer.json \
  --context-length 512
```

`--tokenizer` says which tokenizer produced the `.bin` files. It is recorded in
every checkpoint, and `--train-bin` is usually enough on its own because
`config_for_data()` reads the tokenizer and val paths out of `meta.json`; pass
them explicitly when the set was prepared by an older `prepare_data.py`, or to
override what the metadata says.

`--device {auto,cpu,cuda}` picks the training device. `auto` (default) keeps
the historical behavior: CUDA when available, else CPU. `cpu` forces CPU even
on a GPU machine. `cuda` never falls back: it exits with an error if this
PyTorch build/runtime has no CUDA, so a misconfigured cloud runtime fails
before spending a training budget on CPU.

AdamW (betas 0.9, 0.95, weight decay 0.1 on matrices only — LayerNorm scales
and biases are excluded), linear warmup (500 steps) then
cosine decay to 10% of peak lr, grad clip 1.0, seed 42. Prints device,
parameter count, train/val loss, lr, and step. Checkpoints
(`model_state`, `optimizer_state`, `scheduler_state`, `step`, `config`,
`rng_state`, losses, `checkpoint_metadata`) land in `checkpoints/` every
`eval_interval` steps plus `final.pt`, and load under `weights_only=True`
(no pickled objects). `checkpoint_metadata` is the self-describing block
that states the checkpoint schema version, the model shape (vocab_size,
context_length, n_layers, n_heads, d_model, d_ff, dropout), the tokenizer
by path and sha256 digest, and the training progress (step, epoch,
tokens_seen). If a DataLoader epoch ends mid-run, the iterator is rebuilt
and training continues. `vocab_size` is read from `data/processed/meta.json`
unless `--vocab-size` is given, so the output head matches the tokenizer.
Evaluation never cycles the val loader: it uses at most `eval_batches`
batches, or one full pass if the val set is smaller, and averages **per
target token** so a ragged final batch is not over-weighted.

### Cloud GPU (Kaggle, Colab, ...)

The training loop is device-agnostic: batches are moved to the selected
device, and checkpoints are read with `map_location="cpu"`, so a checkpoint
saved on a GPU loads on a CPU-only machine or a different GPU (optimizer
state is cast to each parameter's device when it loads). To move the
expensive run to a free cloud GPU:

```bash
pip install -r requirements.txt
python prepare_data.py                    # your corpus, same as locally
python -m src.train --device cuda --amp fp16 --max-steps 10000   # T4: fp16, not bf16
```

Run everything from the project root: `train_bin`, `val_bin`,
`tokenizer_path` and `checkpoint_dir` are stored as relative paths in
checkpoints, so a checkpoint resumed on another machine expects the same
working-directory layout. Paths are only part of the story: each checkpoint
also records `data_provenance`, the sha256 digests of the train, val and
tokenizer files it was trained on. When training runs on CUDA, the per-GPU RNG
state is saved under `rng_state["cuda"]` and restored on resume; checkpoints
without that key (all older ones) load unchanged.

### Mixed precision (`--amp`)

`--amp {off,fp16,bf16}` (default `off`) runs the forward pass and loss under
`torch.autocast`. Weights, optimizer state and checkpoints stay fp32 in every
mode.

- `off`: plain fp32, the exact code path from before the flag existed (a test
  pins bit-identical weights against a hand-written fp32 loop).
- `fp16`: CUDA only. Adds a `GradScaler`; gradients are unscaled before
  clipping, so `--grad-clip` means the same thing, and updates with inf/nan
  gradients are skipped (the count is printed at the end). The scaler state
  is saved in every checkpoint as `scaler_state` and restored on `--resume`.
  **A Kaggle/Colab T4 has no bf16, so use `--device cuda --amp fp16` there.**
- `bf16`: no scaler needed. Runs on CPU, or on a GPU with native bf16
  (Ampere and newer); a GPU without it is refused instead of being emulated.

`--amp fp16` without CUDA fails before any work. A resume keeps the precision
the checkpoint was trained in: omit `--amp` (or pass the same value) to
continue; a different value is refused, so a loss scale is never silently
dropped or invented. Checkpoints written before `--amp` existed read as `off`.
`--amp` works with `--grad-accum-steps` and `--loss-chunk-size`.

### Kaggle T4 baseline (`kaggle/train_tinystories.ipynb`)

A fixed, reproducible recipe on a free Kaggle GPU: full TinyStories, vocab
8192, d384/L6, context 512, `--amp fp16`, 196,608,000 tokens, two seeds,
validation bits per byte as the headline number. The notebook only calls
`kaggle/run_recipe.py`, which also runs from a shell
(`python kaggle/run_recipe.py --stage smoke|full|export|summary|all`). Results
go in [`docs/RESULTS.md`](docs/RESULTS.md); its fields stay blank until a run
fills them.

How to run it on Kaggle:

1. kaggle.com → **Create → New Notebook → File → Import Notebook**, and import
   `small-projects/mini-llm/kaggle/train_tinystories.ipynb` from this repo
   (GitHub URL or uploaded file).
2. Right panel → **Session options**: **Accelerator: GPU T4 x2** (the recipe
   uses one GPU) and **Internet: On** (it downloads the 1.9 GB corpus and the
   code). Phone verification on the Kaggle account is needed for both.
3. In the first code cell, set `REPO_REF` (`main` once the PRs are merged,
   otherwise the branch name). Optional first check: set `RUN_FULL = False`
   and **Run All** interactively; the smoke cell prints tokens/sec and the
   projected hours for the full run.
4. For the full run: `RUN_FULL = True`, then **Save Version → Save & Run All
   (Commit)**. It keeps running with the browser closed.
5. When it finishes, open the version's **Output** tab and download
   `minillm-t4/results.md`, `run.json` and (optionally) `gguf/model-q8_0.gguf`.

Time and quota, honestly stated:

- Not yet measured on a T4. The smoke cell measures it; the 2026-10-25 plan's
  rough estimate was 1–2 h per epoch-scale run at 14M params, and this budget is
  ~0.4 epoch per seed. Data download + tokenizer training + encoding is extra
  and also unmeasured.
- Kaggle's GPU quota is about 30 h/week ("or sometimes higher depending on
  demand", Kaggle docs) and a session runs at most 12 h. If the smoke cell says
  both seeds will not fit one session, run one seed per saved version
  (`ONLY_SEED = 1337`, then `ONLY_SEED = 4242`); each version re-prepares the
  data, and the tokenizer and `.bin` digests in `run.json` show it was the same
  data (prep was byte-identical across two runs on a 2 MB slice; not checked at
  full scale). Within one session, re-running a cell skips finished work and an
  unfinished seed resumes from its newest checkpoint; a new session starts
  clean.
- A forum post claims the P100 option was retired on 2026-09-15, leaving T4 x2.
  That is **unverified**; check the Accelerator menu.

The GGUF export cell needs `src/export_gguf.py` (the GGUF export PR). On a
checkout without it the cell prints why it skipped and everything else is
unaffected; the Q8_0 step downloads the pinned llama.cpp `b11874` CPU build.

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
states are all restored (plus the per-GPU CUDA RNG when the run is on CUDA).
Any flag you pass explicitly still wins (including `--max-steps`). Checkpoints
are read with `weights_only=True`; a file that is not a mini-llm checkpoint is
rejected rather than unpickled.

**A resume only continues the same data.** The checkpoint records the sha256 of
the train, val and tokenizer files it was trained on, and the current artifacts
are hashed and compared before the first step. Digests, not paths, decide, so a
copied or relocated artifact tree resumes fine while a *different* file at the
same path does not. Any difference is a hard error naming both sides:

```
refusing to resume checkpoints/step_500.pt: its data does not match the current artifacts.
  train_sha256: checkpoint=ee3b5453a1a1... current=b1b36e6890a4...
  checkpoint data: train=data/tinystories/train.bin tokenizer=data/tinystories/tokenizer.json
  current data:   train=data/processed/train.bin tokenizer=data/tokenizer.json
```

A checkpoint written before provenance was recorded is refused too,
rather than resumed on trust. A checkpoint written before the
metadata block existed (schema 1) is loaded using its `config` — its
model shape and tokenizer are still known — and every later run
carries `checkpoint_metadata`. Because the paths are verified, a bare
`--resume` needs no data flags. Generation applies the same check to
`--tokenizer`.

## Evaluation: bits per byte

Validation loss per token depends on the tokenizer (a bigger vocabulary packs
more bytes into each token), so it cannot compare runs with different vocab
sizes. Every eval during training therefore also logs `val_bpb`, and every
checkpoint stores it next to `val_loss`:

```
bpb = total target NLL (nats) / (ln 2 * UTF-8 bytes of those targets)
```

The bytes are counted from the exact target ids that were scored, using a
per-id byte-length table built from the byte-level vocabulary. Special tokens
(`<bos>`, `<eos>`, ...) count 0 bytes but their NLL is still counted, so the
model is charged for predicting story boundaries. (nanochat also drops 0-byte
targets from the NLL, so its bpb is close to, not identical with, this one.)

To score a checkpoint on its whole val set and print a fixed, seeded sample
sheet (5 prompts, greedy plus T=0.8 / top-k 40), so two runs can be compared:

```bash
python -m src.evaluate --checkpoint checkpoints/final.pt --samples --json-out eval.json
```

By default it scores non-overlapping windows (`--stride` = context length), so
each val token counts once; training-time evals use stride 1 over
`--eval-batches` batches, and `--stride 1 --batches <eval_batches>` reproduces
the number a checkpoint recorded exactly. The val `.bin` and tokenizer default
to the ones the checkpoint recorded, and the tokenizer is checked against its
digest.

## Generation

```bash
python src/generate.py --checkpoint checkpoints/final.pt --prompt "The fox" \
    --tokens 100 --temperature 0.8 --top-k 40
```

Conditioning is truncated to `context_length`. `--temperature 0` = greedy;
temperatures are clamped at 1e-3, and negative values are rejected. `--top-k`
must be at least 1. Sampling stops at `<eos>` unless `--no-eos-stop` is given.
The tokenizer defaults to the path stored in the checkpoint config, and is
checked against the digest the checkpoint recorded: a `--tokenizer` that is not
the one the run used is refused instead of producing garbage. Pass `--seed N`
for reproducible sampling (same checkpoint + prompt + seed + settings ⇒ same
text); without it, each run samples differently.

## Run it in llama.cpp (GGUF export)

`src/export_gguf.py` writes a checkpoint as a GGUF file that llama.cpp loads as
its `gpt2` architecture, so your model runs in `llama-server`, `llama-cli` or LM
Studio. It needs the optional `gguf` package (`pip install -r requirements-gguf.txt`).

```bash
python -m src.export_gguf --checkpoint checkpoints/final.pt --out model-f32.gguf   # --dtype f16 for half size
llama-server -m model-f32.gguf -c 512 --port 9931
curl -s http://127.0.0.1:9931/v1/completions -H "Content-Type: application/json" \
  -d '{"prompt": "Once upon a time", "max_tokens": 40}'
```

`scripts/gguf_demo.ps1` (or `.sh`) does the whole train → export → serve → query
loop on the shipped sample, and `scripts/llama_parity.py` checks that llama.cpp
gives the same next-token probabilities as mini-llm. The tokenizer is verified
against the checkpoint's recorded digest, as in `generate.py`. Details, the tensor
mapping, quantization notes and the tokenizer caveat for pre-fix tokenizers:
[docs/GGUF-llama-server.md](docs/GGUF-llama-server.md).

## Testing

```bash
python -m unittest discover -s tests
```

CPU only. The current measured result lives in `.agents/evidence/verification.json`
(product `small-projects/mini-llm`); run `python tools/evidence/check.py` from the
repository root to confirm it. Test counts are not quoted here.

Covers tokenizer load/encode/decode, output shape, finite scalar
loss, causal masking (future-token swap leaves past logits bit-identical),
generation ID validity, weight tying, and checkpoint round-trip, plus the
data pipeline: `prepare_data.py` framing and split sizing, `meta.json`,
streaming-prepare parity against the reference encoder, `TokenDataset`
window coverage, empty/undersized-loader diagnostics, LR warmup,
token-weighted evaluation with a ragged final batch, the training loop end to
end, resume (step/optimizer/schedule/RNG), device selection
(`--device` auto/cpu/cuda fail-fast), AdamW decay groups, safe checkpoint
loading, seeded vs unseeded generation, window-stride sample counts, the
agreement between the shipped corpus, tokenizer and `meta.json`, and
`TestDataProvenance` — non-default tokenizer/train/val paths, matching resume
(including a relocated-but-identical artifact tree), and refusal on
tokenizer/data/val mismatch.

`tests/test_export_gguf.py` covers the GGUF export: tensor names, shapes,
dtypes and values read back with gguf-py, tokenizer metadata, and a numpy
re-implementation of llama.cpp's gpt2 graph run on the GGUF tensors alone,
compared with MiniGPT's logits. Its gguf-dependent tests skip when `gguf` is not
installed (CI installs it and sets `MINI_LLM_REQUIRE_GGUF=1`, so there they
fail instead). `TestLlamaCppParity` runs against a real `llama-tokenize` and
`llama-server` only when `LLAMA_CPP_DIR` points at a llama.cpp build.

## Limitations

- Tiny by design: the sample corpus teaches shape, not the world. Real
  training needs megabytes of text and a GPU.
- Fixed context window (512); longer prompts are truncated, not summarized.
- Byte-level BPE with no whole-word guarantees; small corpora underfill the
  requested vocabulary, so the model is built for the size the tokenizer
  actually reached (515 on the shipped sample), not the 8192 target.
- The tokenizer is trained with the full 256-symbol byte alphabet
  (`initial_alphabet=ByteLevel.alphabet()`), so any text, including characters
  the corpus never contained (accents, emoji, tabs), encodes without `<unk>` and
  decodes back exactly. Tokenizers prepared before this change only hold the
  bytes their corpus contained; re-run `prepare_data.py` to get the fix. That
  changes the tokenizer and `.bin` digests, so checkpoints trained on the old
  artifacts are refused on `--resume` and by `generate.py --tokenizer`; this is
  the provenance check working as intended. Retrain on the new artifacts.
- Training does not stop on its own: there is no validation-based early
  stopping. An interrupted run continues correctly via `--resume`, but a run
  that is restarted from scratch begins a new schedule at step 1.
- Byte-level BPE is sensitive to the corpus's line endings: the shipped
  sample yields vocab 515 with LF and 516 with CRLF. `.gitattributes` marks
  `data/` as `-text` so no checkout can rewrite it, and
  `tests/test_pipeline.py::TestShippedData` fails if the corpus and
  `meta.json` ever disagree.
