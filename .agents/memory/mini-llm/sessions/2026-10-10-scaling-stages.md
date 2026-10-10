# 2026-10-10 - mini-llm scaling Stages 1-4 and chunked loss

## Task

Execute `docs/AUDIT-scaling-2026-10-10.md` one stage at a time on branch
`fix/play-readiness`, each stage its own commit and tests, every new flag defaulting to
current behavior. Stage 3's chunked loss was the last piece and was left for a separate
report-and-stop task.

## What changed

- **Stage 1, CLI config (`c112b4a`):** `src/train.py` `CLI_CONFIG_FIELDS` + `apply_cli_config`
  exposing model shape and optimizer/loop knobs; `src/config.py` positivity asserts;
  `tests/test_cli_config.py` (13 tests).
- **Stage 2, atomic write + rotation (`dc98601`):** `_atomic_torch_save`, `save_checkpoint`
  via temp + `os.replace`, `list_step_checkpoints`/`rotate_checkpoints`, `--keep-last`
  (default 3); `tests/test_checkpoint_rotation.py` (17 tests).
- **Stage 4, SDPA attention (`ac8a8ec`):** per-head `scaled_dot_product_attention` in
  `CausalSelfAttention` and `MultiHeadAttention`, same parameter names;
  `tests/test_attention_sdpa.py` (14 tests).
- **Stage 3, gradient accumulation (`6ab4ced`):** `grad_accum_steps` (default 1),
  `--grad-accum-steps`, microbatch loop with loss scaled by `1/N`, one clip and one
  `optimizer.step` per group; `tests/test_grad_accum.py` (16 tests).
- **Stage 3, chunked loss (working tree, not committed):** `MiniGPT.hidden_states()`,
  `MiniGPT.chunked_loss()` using `torch.utils.checkpoint(..., use_reentrant=False)`,
  `Config.loss_chunk_size` (default 0), `_chunked_loss` helper and `--loss-chunk-size` in
  `src/train.py`, `evaluate(..., chunk_size=)`; `tests/test_loss_chunk.py` (18 tests).

## Verification

- From `small-projects/mini-llm/`, `python -m unittest discover -s tests`.
- After gradient accumulation: 206 tests OK, 1 skip. After chunked loss: 224 tests OK, 1 skip.
  The chunked-loss suite covers loss equality at chunk sizes 1/3/7/16/100, gradient equality,
  uneven final chunk, ignored (`-100`) targets, chunked train+eval, chunked-vs-plain weight
  agreement, checkpoint load plus generation after chunked training, and invalid-value errors.
- Chunked-loss memory and time: `docs/PROGRESS-scaling-2026-10-10.md`. At context 256, batch 8,
  vocab 32000, chunk=64 cut peak by about 41 percent for about 15 percent more step time, with
  identical loss.

## Findings

- A full `C:` drive made the suite report 120 errors in seconds; the cause was
  `OSError: [Errno 28]`, not the edit being tested. Reran with `TEMP`/`TMP` on `D:`.
- `.agents/evidence/verification.json` for this product was regenerated after the chunked-loss
  commit; the record now reports 224 tests, 1 skip.

## Limitations

- CPU-only, fp32; no mixed-precision or CUDA comparison is measurable here.
- The benchmark harness is a scratch script outside the repo; the audit's requested test-tree
  harness is a separate task.
- Compiled plus chunked is untested (`--compile` needs MSVC `cl`, absent).
- Trunk activations are still saved; only the output projection plus loss are recomputed.
