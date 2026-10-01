# Experiment: TinyStories baseline (first real-corpus run)

Status: completed 2026-09-30 (artifacts on disk, ignored by git); procedure below
reproduces it from scratch. This is a training-pipeline baseline, not a claim
that the model is a useful general chatbot.

## Corpus

- `data/raw/tinystories-small.txt` (13 MB) — the corpus actually prepared.
  `data/raw/TinyStories-train.txt` (1.9 GB full TinyStories) is staged in the
  same directory for future scaling but was NOT used for this run.
- Both files are git-ignored; fetch TinyStories from its public source if absent.

## Preparation (verified 2026-10-01 on current code)

```bash
cd small-projects/mini-llm
python prepare_data.py \
  --input data/raw/tinystories-small.txt \
  --tokenizer-out data/tinystories/tokenizer.json \
  --train-out data/tinystories/train.bin \
  --val-out data/tinystories/val.bin \
  --vocab-size 8192 --context-length 256
```

Result (`data/tinystories/meta.json`): vocab 8192, 3,103,492 train tokens,
31,348 val tokens, uint16. (`--context-length 256` only sizes the val split;
training below used 512.) Defaults used: `--val-frac 0.01`, `--min-frequency 2`.

## Training

```bash
python -m src.train \
  --train-bin data/tinystories/train.bin \
  --val-bin data/tinystories/val.bin \
  --checkpoint-dir checkpoints/tinystories \
  --context-length 512 --batch-size 8 --max-steps 10000
# on a cloud GPU add: --device cuda  (fail-fast if CUDA is missing; never
# silently falls back to CPU)
```

Recorded outcome (`checkpoints/tinystories/final.pt`, 168 MB, git-ignored):
step 10000, train_loss 1.785, val_loss 1.833. Architecture: 6 layers, 6 heads,
d_model 384, d_ff 1536 (~14M params at vocab 8192). ~41M tokens processed over
3.1M unique (~13 epochs at stride 1).

Known wart: the checkpoint config stores the default
`tokenizer_path: data/tokenizer.json`, not the tinystories tokenizer that was
actually used. Generation and any `--resume` must pass the tokenizer
explicitly (see below); a resumed run otherwise inherits the wrong default
tokenizer path. Future runs should record the real path.

## Resume (procedure; smoke-verified, not executed on this artifact)

```bash
python -m src.train --resume checkpoints/tinystories/final.pt --max-steps 12000
```

Resume restores model, AdamW moments, step counter, and the warmup/cosine
schedule from the checkpoint; explicit flags still win. The tiny-sample loop
(prepare -> 6 steps -> resume 2 steps -> generate) was verified green on
2026-10-01 with all outputs in `$TEMP/mlsmoke`, proving the current resume path
works. Deliberately NOT re-run here: it would rewrite the 168 MB `final.pt`.

## Evaluation

- During training: val loss printed per eval (`val_loss 1.833` at step 10000).
- After training (reproducible; verified 2026-10-01):

```bash
python src/generate.py --checkpoint checkpoints/tinystories/final.pt \
  --tokenizer data/tinystories/tokenizer.json \
  --prompt "Once upon a time" --tokens 60 --seed 7
```

Output (seed 7): a coherent short story about a fierce man in the rain
("He roared loudly ... But the rain already ran away and he got wet") —
story-shaped TinyStories text, not general knowledge. Compare checkpoints by
fixed-prompt `--seed` samples plus val loss; there is no validation-based
early stopping, so keep `final.pt` (and any `step_N.pt`) for every run.

## Scale notes

- `data/processed/` currently holds the tiny shipped-sample prep (736 train
  tokens, vocab 308, ctx 32) with `data/tokenizer.json` deleted from the tree
  (staged deletion, pre-existing). The tinystories run does not depend on
  either; its bins, tokenizer, and checkpoint are self-contained under
  `data/tinystories/` + `checkpoints/tinystories/`.
- Next scale step is data, not code: prepare the 1.9 GB full file the same way
  (expect ~450M tokens, uint16-safe) and train with a larger step budget on GPU.
