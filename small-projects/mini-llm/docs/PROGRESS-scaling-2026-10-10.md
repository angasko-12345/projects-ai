# Progress: mini-llm scaling stages

Record of what the scaling roadmap in `AUDIT-scaling-2026-10-10.md` has produced so far.
Numbers here are dated observations from this machine, not a current baseline; the current
measured result for the product lives in `.agents/evidence/verification.json`.

Branch: `fix/play-readiness`. All work CPU-only, fp32 (no CUDA on this box).

## Status

| Stage | Item | Commit | State |
|---|---|---|---|
| 1 | CLI flags for model and training hyperparameters | `c112b4a` | committed |
| 2 | Atomic checkpoint write + `--keep-last` rotation | `dc98601` | committed |
| 4 | Per-head scaled dot-product attention (SDPA) | `ac8a8ec` | committed |
| 3 | Gradient accumulation (`--grad-accum-steps`) | `6ab4ced` | committed |
| 3 | Chunked loss (`--loss-chunk-size`) | none | in working tree, tested, benchmarked, not committed |
| doc | Scaling audit | `0d8360f` | committed |

Every new flag defaults to the previous behavior, so an unmodified invocation is unchanged.
No attention/init/optimizer/checkpoint-format change rode along except SDPA, which keeps the
per-head parameter names (`wq`/`wk`/`wv`/`proj`) so old checkpoints still load.

## Stage details

### Stage 1: CLI configuration (`c112b4a`)
`src/train.py` grew a `CLI_CONFIG_FIELDS` table and `apply_cli_config`, exposing model shape
(`--n-layers`, `--n-heads`, `--d-model`, `--d-ff`), optimizer and loop knobs (`--learning-rate`,
`--dropout`, `--grad-clip`, `--warmup-steps`, `--eval-interval`, `--eval-batches`, `--seed`),
and `--tokenizer`. `src/config.py` gained positivity assertions for the numeric fields.

### Stage 2: atomic writes and rotation (`dc98601`)
`save_checkpoint` writes to a temp file and `os.replace`s it into place, so a crash mid-write
leaves the previous checkpoint intact. `keep_last` (default 3) caps retained step checkpoints
via `list_step_checkpoints` / `rotate_checkpoints`.

### Stage 4: SDPA attention (`ac8a8ec`)
`CausalSelfAttention` and `MultiHeadAttention` use `torch.nn.functional.scaled_dot_product_attention`
per head. The `q/k/v` projections are unchanged, so the state dict is identical to the manual
implementation. Measured on one attention op (B=2, T=2048, d=1024, h=16): manual 2396 ms vs
SDPA 1575 ms forward+backward, about 1.5x, with the `T x T` weight tensor gone (peak delta
769.5 MB to 104.1 MB).

### Stage 3: gradient accumulation (`6ab4ced`)
`grad_accum_steps` (default 1) splits one optimizer update into N microbatches. Loss is the
mean over tokens, so each microbatch loss is scaled by `1/N` before backward; there is one
`zero_grad`, one gradient clip, and one `optimizer.step` per group. `max_steps`,
`eval_interval`, checkpoints, and `tokens_seen` all count optimizer updates, not microbatches.
With dropout off, the accumulated gradient matches a single large batch exactly; the test
suite pins that.

### Stage 3: chunked loss (working tree, uncommitted)
`--loss-chunk-size N` (0 default, off) computes the training and evaluation loss one chunk of
`N` sequence positions at a time. `MiniGPT.hidden_states()` returns the post-`ln_f` hidden
states; `MiniGPT.chunked_loss()` runs `lm_head` plus `cross_entropy(reduction="sum")` per chunk
inside `torch.utils.checkpoint.checkpoint(..., use_reentrant=False)`. Only one
`(B, N, vocab)` logits tensor is live at a time and the full `(B, T, vocab)` logits and its
gradient are never materialized; each chunk forward is recomputed in backward. The chunked sum
is divided by the count of non-`-100` targets, the same count the plain loss uses, so ignored
targets cancel and the result matches the plain `cross_entropy`. `forward()` and generation are
unchanged when the flag is off. Non-positive `loss_chunk_size` is rejected in `Config` and at
the CLI.

## Measured: chunked vs plain loss

Same model and batch both runs: 6 layers, d_model 384, n_heads 6, vocab 32000, batch 8, 3
AdamW steps, fp32, 6 CPU threads. Peak is the process `WorkingSetSize` delta over the idle
base, sampled around the steps; time is wall clock per optimizer step. Batch size, context,
vocabulary, and step count are identical between the two rows of each pair, so the memory and
time deltas are attributable to the loss path.

| context | path | peak delta | ms/step | loss |
|---|---|---|---|---|
| 256 | plain | ~1602 MB (1600 to 1604) | ~2050 (1919 to 2080) | 9.9961 |
| 256 | chunk=64 | ~941 MB (928 to 961) | ~2400 (2343 to 2696) | 9.9961 |
| 256 | chunk=32 | 781 MB | 3141 | 9.9961 |
| 512 | plain | crashes `0xC0000005` (3 of 3 recent runs); one earlier run 3389 MB | n/a | n/a |
| 512 | chunk=64 | ~1308 MB (1306 to 1310) | ~4500 | 10.0924 |

Reading:

- At context 256, chunk=64 cuts peak by about 660 MB, 41 percent, at about 15 percent more
  step time. Chunk=32 cuts about 820 MB, 51 percent, at about 53 percent more time.
- At context 512 the plain path overflows this machine and dies with an access violation,
  while chunk=64 completes at about 1.3 GB. The one earlier plain run that got through peaked
  at 3389 MB, so chunking is about 61 percent lower there and removes the crash.
- Loss is identical between paths at each size, which is the equivalence check on a real
  forward, not only in a unit test.

The measured reduction is larger than the raw logits size (a `B*T*V` fp32 tensor is 262 MB at
T=256 and 524 MB at T=512) because keeping only one chunk live also avoids holding the logits
gradient and the next op's input at full size at the same time.

## Reproduce

Loss equivalence and gradient equivalence (in the repo):

```bat
cd small-projects/mini-llm
python -m unittest tests.test_loss_chunk -v
```

Training with chunked loss (uses the shipped tiny sample, which needs context 32):

```bat
python src/train.py --loss-chunk-size 64 --context-length 32 --max-steps 20
```

The memory and time table above came from a scratch script outside the repo (a step loop plus a
background thread reading `WorkingSetSize` through `K32GetProcessMemoryInfo`). That harness is
not committed; the audit's section 11 asks for one under the test tree, which is a separate
task.

## Limitations and open items

- CPU-only, fp32. No mixed-precision, TF32, or tensor-core comparison is measurable here.
- Working-set sampling can miss very short transients, so peaks are approximate.
- Chunked loss adds recomputation cost: 15 to 50 percent more step time for the memory saved.
- Compiled plus chunked is untested (`--compile` needs MSVC `cl`, absent here); `_chunked_loss`
  unwraps `_orig_mod` as the mitigation.
- Only the output projection plus loss are recomputed. The trunk activations are still saved;
  gradient checkpointing of the blocks is a later stage (Stage 5).
- `.agents/evidence/verification.json` for this product still names an older commit and needs
  regeneration by the session that commits the chunked-loss work.
