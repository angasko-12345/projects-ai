# 2026-10-05 — checkpoint metadata schema v2

## Task

Make checkpoints self-describing and safer: add a metadata schema recording
checkpoint version, model architecture, tokenizer identifier/digest, context
length, step/epoch, plus other metadata the loader depends on. Preserve legacy
checkpoint loading; refuse incomplete checkpoints instead of guessing.

## What changed (committed b189c34)

- `src/train.py`: `CHECKPOINT_SCHEMA_VERSION = 2`, `build_checkpoint_metadata`,
  `validate_checkpoint`, `_legacy_metadata`, `_metadata_gaps`. `save_checkpoint`
  writes `checkpoint_metadata`; `read_checkpoint` validates and attaches it
  (legacy v1 gets synthesized block in memory only). `train()` counts
  `tokens_seen`/`epoch` across a resume via `progress_snapshot`.
- `tests/test_pipeline.py`: +20 tests, `TestCheckpointMetadata`.
- `README.md`, `AGENTS.md`: checkpoint format docs.

## Verification

- Focused: `python -m unittest tests.test_pipeline.TestCheckpointMetadata -v`
  → 20 OK.
- Full suite: `python -m unittest discover -s tests` → 136 OK, 1 skip.
- Legacy on-disk checkpoints (`checkpoints/`, `checkpoints_test/`) still load
  and report schema 1; generation end-to-end works.

## Compatibility limitation discovered

- Legacy v1 checkpoints load only when `config` carries model shape + tokenizer
  path; otherwise refused. `tokens_seen`/`epoch` for a legacy resume start at 0
  (no history recorded). `stride` still not stored.

## Key decisions

- Version 2 chosen so the number encodes history (v1 = pre-metadata format).
- Metadata block repeats facts already in `config`/`data_provenance` so one
  block names the format and fields; config remains what loaders rebuild from,
  keeping the existing round-trip test unchanged.
- Config↔metadata cross-check catches hand-edited checkpoints.
