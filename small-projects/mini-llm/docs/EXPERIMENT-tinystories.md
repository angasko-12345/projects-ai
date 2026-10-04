# Experiment: TinyStories baseline (first real-corpus run)

Status: completed 2026-09-30 (artifacts on disk, ignored by git); the procedure
below reproduces it from scratch.

## What this run shows, and what it does not

It shows that the prepare → train → checkpoint → generate pipeline works end to
end on a real 13 MB corpus at a vocabulary and step budget the current code
supports without changes. Val loss is recorded and the fixed-seed samples read as
TinyStories-shaped prose.

It shows nothing about general capability. The corpus is short children's
stories, so the model learned that register and nothing else. Nothing here
supports a claim of general intelligence, broad reasoning, or general-purpose
chatbot quality, and the val loss below is a pipeline health signal, not a
quality score.

## Corpus

- `data/raw/tinystories-small.txt` (12,988,293 bytes) is the corpus actually
  prepared.
- `data/raw/TinyStories-train.txt` (1,924,281,556 bytes, the full TinyStories
  set) is staged in the same directory for future scaling and was NOT used for
  this run.
- Both are git-ignored (see `small-projects/mini-llm/.gitignore`); fetch
  TinyStories from its public source if absent.

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

`data/tinystories/meta.json` records: vocab 8192, 3,103,492 train tokens,
31,348 val tokens, dtype uint16. Defaults used: `--val-frac 0.01`,
`--min-frequency 2`.

3,103,492 is a token count, not a distinct-token count. `prepare_data.py`
measures total encoded ids per split and writes them as `train_tokens` /
`val_tokens`; nothing in the codebase counts unique tokens or measures
vocabulary coverage over a corpus. Do not read that figure as "3.1M unique".

`--context-length 256` affected only the split sizing and the recorded
`meta.json` field. It sets the val floor `max(context_length + 2,
val_frac * total)` = 258, well below the 31,348 the 1% split produced anyway,
and nothing validates `meta.json`'s `context_length` against the training flag.
Training below used 512.

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

Recorded outcome (`checkpoints/tinystories/final.pt`, 167,987,879 bytes,
git-ignored): step 10000, train_loss 1.785, val_loss 1.833. Architecture: 6
layers, 6 heads, d_model 384, d_ff 1536, which is 13,982,976 unique parameters
at vocab 8192 (weight-tied LM head). The run processed 40,960,000 tokens
(10,000 steps × 8 × 512), about 13 passes over the 3,103,492-token training set
at stride 1.

## Generation and resume on this artifact (behavior changed 2026-10-10)

This section described a stale-default wart that no longer exists. Superseded
facts, kept for the record:

- Before, `checkpoints/tinystories/final.pt` stored default-valued paths for
  every field (`data/processed/...`, `data/tokenizer.json`) because
  `Config.tokenizer_path` had no CLI flag and `prepare_data.py` recorded no
  tokenizer in `meta.json`. Generation therefore required
  `--tokenizer data/tinystories/tokenizer.json`, and `src/train.py` had no
  `--tokenizer` at all. A bare `--resume` died with
  `vocab_size=8192 but data/processed/train.bin was encoded with a vocabulary of 308`.
- Now `src/train.py` takes `--tokenizer`, `meta.json` records the artifact paths
  and their sha256 digests, and every checkpoint written by the training loop
  records `data_provenance`. A resume verifies the current artifacts against the
  recorded digests and refuses a mismatch.

Consequences for **this** checkpoint specifically: it predates provenance, so
`--resume` now refuses it rather than guessing. Regenerating the data with
`prepare_data.py` (same command as above) rewrites `data/tinystories/meta.json`
with provenance, and a subsequent run produces resumable checkpoints. The
existing 168 MB `final.pt` was deliberately **not** rewritten here, so the run
would have to be redone to make this artifact resumable; it is still usable for
generation with an explicit `--tokenizer`.

## Resume (procedure; smoke-verified, not executed on this artifact)

```bash
python -m src.train --resume checkpoints/tinystories/final.pt \
  --train-bin data/tinystories/train.bin \
  --val-bin data/tinystories/val.bin \
  --checkpoint-dir checkpoints/tinystories \
  --max-steps 12000
```

Resume restores model, AdamW moments, step counter, and the warmup/cosine
schedule from the checkpoint; explicit flags still win. The data flags above are
required for the reason given in the previous section; `--checkpoint-dir` also
keeps the output beside the existing artifact instead of writing
`checkpoints/final.pt`.

The tiny-sample loop (prepare → 6 steps → resume 2 steps → generate) was
verified green on 2026-10-01 with all outputs in `$TEMP/mlsmoke`, proving the
current resume path works. Deliberately NOT re-run on this artifact: it would
rewrite the 168 MB `final.pt`.

## Evaluation

- During training: val loss printed per eval (`val_loss 1.833` at step 10000).
- After training (reproducible; verified 2026-10-01, output re-checked against
  the recorded command):

```bash
python src/generate.py --checkpoint checkpoints/tinystories/final.pt \
  --tokenizer data/tinystories/tokenizer.json \
  --prompt "Once upon a time" --tokens 60 --seed 7
```

Output (seed 7): a coherent short story about a fierce man in the rain
("He roared loudly ... But the rain already ran away and he got wet"),
story-shaped TinyStories text, not general knowledge. Compare checkpoints by
fixed-prompt `--seed` samples plus val loss; there is no validation-based early
stopping, so keep `final.pt` (and any `step_N.pt`) for every run.

## Scale notes

- `data/processed/` currently holds the tiny shipped-sample prep (736 train
  tokens, 184 val tokens, vocab 308, ctx 32) with `data/tokenizer.json` deleted
  from the tree (staged deletion, pre-existing). The tinystories run does not
  depend on either; its bins, tokenizer, and checkpoint are self-contained under
  `data/tinystories/` + `checkpoints/tinystories/`.
- Next scale step is data, not code: prepare the 1.9 GB full file the same way
  (expect ~450M tokens, uint16-safe) and train with a larger step budget on GPU.
  The ~450M token figure is an estimate from corpus size, not a measured
  preparation.