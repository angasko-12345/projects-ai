# mini-llm results

Measured results only. Every field below is filled from a run's own
`results.md` / `run.json` (written by `kaggle/run_recipe.py`); a blank field
means the run has not happened yet. Do not fill a field from an estimate, a
memory file or another session's report.

The older TinyStories number (val loss 1.833, 2026-09-30, 13 MB subset, no
bpb) is historical and is kept in `docs/EXPERIMENT-tinystories.md`; it is not
comparable with this table (different corpus size, token budget and recipe).

## Baseline: `tinystories-t4-v1` (Kaggle T4, fp16)

Recipe (fixed in `kaggle/run_recipe.py` `RECIPE`; change it and you have a new
recipe with a new name):

| setting | value |
|---|---|
| data | full TinyStories V1 `TinyStories-train.txt` (roneneldan/TinyStories on Hugging Face), one passage per story |
| tokenizer | byte-level BPE, vocab 8192, min frequency 2, full 256-byte alphabet |
| split | `prepare_data.py --val-frac 0.01` (last 1% of tokens is val) |
| model | GPT-2 style, d_model 384, 6 layers, 6 heads, d_ff 1536, context 512, tied head |
| optimizer | AdamW (0.9, 0.95), lr 6e-4 (untuned), warmup 500, cosine to 10%, wd 0.1, clip 1.0 |
| batch | 32 x 512 tokens, no accumulation |
| budget | 12,000 steps = 196,608,000 tokens (~0.4 epoch) |
| precision | `--amp fp16` (autocast + GradScaler) |
| seeds | 1337, 4242 |
| headline metric | val bits per byte over the whole val split, non-overlapping windows, fp32 eval (`python -m src.evaluate`) |

### Run record

- Run date (UTC):
- Kaggle accelerator / GPU name:
- torch / CUDA / Python:
- Repo commit (and dirty flag):
- Raw corpus bytes / sha256:
- Stories / train tokens / val tokens / vocab:
- Data prep wall time (s):
- Smoke test checks (finite loss, resume, GradScaler restored):
- Smoke tokens/sec and projected hours per seed:

### Per seed

| seed | val_loss | val_bpb (full val) | tokens/sec | train time (s) | fp16 skipped updates | final loss scale |
|---|---|---|---|---|---|---|
| 1337 | | | | | | |
| 4242 | | | | | | |

- val_bpb mean ± half-range:
- val_loss mean ± half-range:

### Export (first seed)

- GGUF f16 bytes:
- GGUF Q8_0 bytes (llama.cpp b11874 `llama-quantize`):
- llama.cpp parity check on this checkpoint (`scripts/llama_parity.py`, run on a PC):

### Sample sheet (first seed, fixed prompts, greedy and T=0.8 / top-k 40, seeded)

(paste from `results.md`)

## How to fill this in

1. Run `kaggle/train_tinystories.ipynb` on Kaggle (see the README section
   "Kaggle T4 baseline").
2. Download `/kaggle/working/minillm-t4/results.md` and `run.json` from the
   notebook's Output tab.
3. Copy the values across. If `results.md` starts with **NOT the baseline
   recipe**, the run was overridden and does not belong in this table.
