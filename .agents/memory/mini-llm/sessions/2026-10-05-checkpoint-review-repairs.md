# 2026-10-05 — checkpoint review, then three targeted repairs

Follow-on to `2026-10-05-checkpoint-metadata.md` (schema v2, commit `b189c34`).
Two sessions: a read-only review of the consistency repairs, then a scoped fix
for the three issues that review raised. No redesign of the checkpoint system.

## Session 1 — review (read-only, no files modified)

Verified the four previously-reported issues were genuinely fixed rather than
assumed, by reverting each one in a scratch copy of the package and re-running
`TestCheckpointMetadata`:

```
M1_resume_start_step -> CAUGHT      M5_no_tokpath_check  -> CAUGHT
M2_no_wte_shape      -> CAUGHT      M6_no_maxsteps_check -> CAUGHT
M3_no_wpe_shape      -> SURVIVED    M7_no_toksha_check   -> CAUGHT
M4_no_step_check     -> CAUGHT
```

Six of seven held. `M3` was the finding: the `wpe.weight` / `context_length`
shape check existed and worked, but no test exercised it.

Probed what the two-tensor shape check does *not* cover. `n_layers`,
`n_heads`, `d_head`, `d_ff` can all contradict the recorded metadata without
`validate_checkpoint` noticing:

```
n_layers-contradiction       validate: PASSES   load_model: RuntimeError
n_heads-contradiction        validate: PASSES   load_model: RuntimeError
d_ff-contradiction           validate: PASSES   load_model: RuntimeError
d_head-contradiction         validate: PASSES   load_model: RuntimeError
lm_head vocab-contradiction  validate: PASSES   load_model: RuntimeError
wpe context-contradiction    validate: REFUSED  load_model: RuntimeError
wte vocab-contradiction      validate: REFUSED  load_model: RuntimeError
```

Judged not worth fixing under the "no redesign" constraint: `strict=True`
loading catches all of them, so the cost is a less precise error rather than a
silent wrong-shaped model. `lm_head` needs no separate check — it is tied to
`wte.weight`.

Second finding: `load_model(dict)` / `load_checkpoint(dict)` accepted any raw
dict, skipping validation entirely. Demonstrated by loading a `torch.load`
result whose metadata claimed `d_ff=99999` and getting a `d_ff=32` model back.
Not reachable in-tree, but the `str | dict` union made the gate opt-out by
construction.

## Session 2 — the three repairs

1. `tests/test_pipeline.py`: added
   `test_position_embedding_shape_contradicting_context_length_is_refused` and
   `test_embedding_shape_contradicting_d_model_is_refused`.
2. `src/train.py`: added `VALIDATED_KEY` and `validated_checkpoint()`;
   `load_model()` and `load_checkpoint()` both route through the gate.
3. `small-projects/mini-llm/AGENTS.md`: baseline `115 → 146`.

Also added `test_raw_unvalidated_dict_cannot_bypass_validation` and
`test_a_validated_dict_still_loads_through_both_loaders`, pinning the gate from
both directions. Class went 26 → 30 tests.

### Why a string key and not a `dict` subclass

The natural marker is `class ValidatedCheckpoint(dict)`. Probed it directly:
`torch.load(..., weights_only=True)` raises `UnpicklingError: Unsupported
global: GLOBAL __main__.ValidatedCheckpoint`. The subclass would have broken the
safe-loading property mini-llm is built on. Plain string key used instead.

### Single-read preserved (verified, not assumed)

Instrumented `torch.load` and counted calls:

| Flow | Disk reads |
|---|---|
| `read_checkpoint` + `load_model(dict)` (generate.py) | 1 |
| `load_model(path)` | 1 |
| `load_checkpoint(dict)` (train.py) | 0 extra |

Validation is a pure function over the dict and reads no files, so marking is a
re-validation skip, never a re-read.

### Re-mutation of the new protections

```
F1_remove_wpe_context_check -> CAUGHT
F2_remove_dmodel_checks      -> CAUGHT
F3_reopen_bypass             -> CAUGHT
```

## Verification

- `python -m unittest tests.test_pipeline.TestCheckpointMetadata` → 30 OK (28s).
- `python -m unittest discover -s tests` → 146 OK, 1 skip (89s).
- Scratch mutation copies deleted; `git status` unchanged outside the intended
  files.

## Notes for the next session

- An external process committed this work as `923b874` mid-task. Content is
  correct, but the authorship is not ours — re-author if that matters.
- Still open, deliberately: shape validation covers 2 of 7 architecture fields
  (see "What is cross-checked" in `../checkpoint-schema.md`).
- The DataLoader shuffle reproducibility issue was explicitly out of scope and
  remains unaddressed.