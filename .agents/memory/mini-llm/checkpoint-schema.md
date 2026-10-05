# mini-llm checkpoint metadata schema (verified 2026-10-05)

## Current format: schema version 2

Each checkpoint carries a top-level `checkpoint_metadata` block
(plain containers, still `weights_only=True`-safe):

```json
{
  "schema_version": 2,
  "model": {"vocab_size": ..., "context_length": ..., "n_layers": ...,
            "n_heads": ..., "d_model": ..., "d_ff": ..., "dropout": ...},
  "tokenizer": {"path": "...", "sha256": "..."},
  "progress": {"step": ..., "epoch": ..., "tokens_seen": ..., "max_steps": ...},
  "required_keys": ["model_state", "config", "step"]
}
```

Defined in `src/train.py`: `CHECKPOINT_SCHEMA_VERSION = 2`,
`MODEL_FIELDS`, `REQUIRED_CHECKPOINT_KEYS`,
`build_checkpoint_metadata()`, `validate_checkpoint()`.

## Gate behavior

- `validate_checkpoint(ckpt, path)` is the single gate: missing or
  contradictory metadata raises `ValueError` naming the problem instead of
  filling in defaults (a defaulted vocab_size/context_length/tokenizer builds
  a different model).
- `read_checkpoint()` validates and attaches the metadata back into the dict
  so callers always see `ckpt["checkpoint_metadata"]`.
- A schema-1 checkpoint (no block) is accepted when its `config` still carries
  the model shape and tokenizer path; reported as `schema_version: 1` in
  memory only. A v1 checkpoint whose config lacks those fields is refused.
- A metadata block with a schema version newer than the running code is
  refused with both versions named.

## Progress across resume

`tokens_seen` / `epoch` accumulate across a resume (read from the checkpoint's
progress block, not restarted). A v1 legacy checkpoint has no history, so
resume starts from 0 — honest, not an error.

## Limitations (pre-existing, unchanged)

- `stride` is not stored in checkpoints; pass the same `--stride` when
  resuming.
- `tokenizer.sha256` is `None` when the run had no `meta.json` beside the
  `.bin` files (path is still recorded).
- Epoch is `tokens_seen / train_tokens`; stride-independent.

## Tests

`tests/test_pipeline.py::TestCheckpointMetadata` — 20 tests, all five
required cases: save writes metadata, load restores it, tokenizer identity
survives, schema/version handling, invalid/incomplete checkpoints refused.

## Verified baselines

- 2026-10-05: `python -m unittest discover -s tests` → **136 tests, OK,
  1 skip** (from 116 before this work).
