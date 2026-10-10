# AUDIT: scaling mini-llm

Date: 2026-10-10
Auditor: opencode (CPU-only local session)
Scope baseline commit: `817ca9cac97ad39de749312880a540f19b1e77f5` (the commit in `.agents/evidence/verification.json`)
HEAD at audit time: `e3dc0c5` (does not touch `small-projects/mini-llm`)
Working tree for `small-projects/mini-llm`: clean

This is a read-only audit. No source file in `small-projects/mini-llm` was changed. No report was added to the library code. All benchmark scripts were written to a scratch directory outside the repository.

## 1. Questions this audit answers

1. What is the current state of the mini-llm training stack, and what already works?
2. Which parts become bottlenecks when the model grows from 14M parameters to 20M, 50M, 100M, 300M, and 1B?
3. What must be added to make larger from-scratch runs feasible, in what order, and with what dependencies?
4. What is the single biggest blocker right now?
5. What is the smallest useful first task?

## 2. Method

Every claim below is tied to one of: a command run on this machine, a line in the source, or a computation whose inputs are printed alongside it. Nothing is asserted from the README alone. The full test suite was run, the end-to-end pipeline was run, the config class parameter counts were cross-checked against a closed-form formula at six shapes, and fourteen benchmark scripts were run for throughput and peak memory.

The machine has no CUDA device, so GPU-only effects (TF32, fp16 tensor cores, flash attention, `torch.compile` codegen) could not be measured here. They are marked as such and excluded from the measured claims.

## 3. Measured environment

| Property | Value | How measured |
|---|---|---|
| Python | 3.14.7 | `python --version` |
| PyTorch | 2.14.0+cpu | `torch.__version__` |
| CUDA available | False | `torch.cuda.is_available()` |
| CPU | AMD Ryzen 5 4600G, 6 cores / 12 logical | `platform.processor()`, `os.cpu_count()` |
| Torch threads | 6 | `torch.get_num_threads()` |
| Installed RAM | 28.9 GB | `Get-CimInstance Win32_ComputerSystem` |
| Free physical RAM | 13.4 GB | `Get-CimInstance Win32_OperatingSystem` at audit time |
| Disk D: free | 308.3 GB of 476.9 GB | `Get-PSDrive` |
| GPU | integrated AMD Radeon only, no CUDA | `torch.cuda.is_available()` |

The 13.4 GB free figure is what any local run actually has to work with. It is well below the 28.9 GB installed, because the desktop session and other processes hold the remainder. Local fit decisions below use 13.4 GB, not 28.9 GB.

Note: `rtk`, `head`, and `tail` are not on this shell's PATH, so all counts and scans used `python` and PowerShell cmdlets instead. The test suite was run with the exact command from `small-projects/mini-llm/AGENTS.md`.

## 4. What already works (verified by running it)

| Capability | Evidence |
|---|---|
| Test suite green | `python -m unittest discover -s tests` -> 146 tests, 1 skip, OK, 30.2s. Matches `.agents/evidence/verification.json` (product `small-projects/mini-llm`, total 146, pass). |
| Data preparation (streaming) | `prepare_data.py` on the 2432-byte `data/raw/train.txt` produced 716 tokens, vocab 512 at `--min-frequency 1`, and wrote `meta.json` and `train.bin`/`val.bin`. |
| Training runs end to end | `python -m src.train` ran 40 steps at 10.85M params, 6.59 steps/s, 843 tok/s, and wrote `final.pt`. |
| Generation runs end to end | `python src/generate.py` loaded the checkpoint and produced text. Output is repetitive, which is expected for a 40-step run. |
| `--compile` fails cleanly when unusable | On this box `--compile` exits 1 with `InductorError: InvalidCxxCompiler: Compiler: cl is not found`, prefixed by `torch.compile dry run failed`. Fail-fast, actionable. `torch.compile` is not usable here (no MSVC `cl`). |
| Resume from checkpoint | Covered by the green test suite (`test_pipeline.py`). |
| Self-describing checkpoints | Checkpoint carries config, model_config, provenance, and identity keys (see `src/config.py`). |

The end-to-end path is real. This audit is about what breaks as the model grows, not about whether the current code works.

## 5. Findings, prioritized

Severity key: P0 blocks a credible larger run or risks lost work; P1 costs money or time at scale; P2 is a performance limit that only matters after P1 is fixed; P3 is a design limit to plan around.

### P0

**P0-1. There is no way to select a model size from the command line.**
`make_parser` (`src/train.py:727`) exposes 10 flags: `batch_size, checkpoint_dir, compile, context_length, max_steps, torch_interop_threads, torch_threads, train_bin, val_bin, vocab_size`. The `Config` dataclass (`src/config.py`) has 25 fields. Sixteen fields have no CLI flag, including every model-shape field: `n_layers`, `n_heads`, `d_model`, `d_ff`, and also `learning_rate`, `dropout`, `grad_clip`, `eval_interval`, `eval_batches`, `warmup_steps`, `seed`, `weight_decay`, `min_lr_ratio`, `beta1`, `beta2`, `tokenizer_path`. Consequence: a user cannot run anything other than the default 14M shape without editing Python source. Every other scaling item in this report is gated on this one.

**P0-2. Checkpoint writes are non-atomic.**
`save_checkpoint` (`src/train.py:405`) calls `torch.save(ckpt, path)` directly at `src/train.py:450`. A crash or power loss mid-write corrupts `step_N.pt` in place, with no previous valid copy unless an earlier interval survives. Fix: write to a temp path in the same directory, then `os.replace` (atomic on the same filesystem).

**P0-3. Checkpoints are never rotated.**
The training loop writes `step_N.pt` every `eval_interval` and keeps all of them (`src/train.py:707`, `src/train.py:713`). Measured checkpoint size is 12.0 bytes per parameter: 38.8M params -> 466 MB, 109.7M -> 1316 MB. At 25 checkpoints that is 12.9 GB for a 110M model and 33 GB for a 1B model. On a 308 GB disk a single 1B run can fill the drive. Fix: `--keep-last N` plus delete the oldest.

**P0-4. No mixed precision and no gradient accumulation.**
Training is fp32 everywhere. Weights (4 B/param), grads (4 B/param), and AdamW moments (8 B/param) total 16 B/param live, and the measured checkpoint ratio of 12.0 B/param confirms the tensors are real and not somehow packed. There is no `torch.autocast`, no `GradScaler`, and no accumulation loop. This is the direct cause of the memory wall in section 7.

**P0-5. Default initialization is depth-unsafe.**
`_init_weights` (`src/model.py:20`) sets `nn.init.normal_(std=0.02)` on every `nn.Linear` and `nn.Embedding`, with no residual scaling of the attention projection or MLP output (the GPT-2 style `1/sqrt(2*n_layers)` factor) and no scaling by `d_model`. At 6 layers this is fine. The test suite has no coverage for 12, 24, or 48 layers, so nothing here would catch a residual blow-up at `n_layers>=24`. This must be validated before the 300M and 1B configs are trusted.

### P1

**P1-1. Logits are fully materialized every step.**
The head is computed as `logits = self.lm_head(x)` (`src/model.py:126`), a full `B x T x V` fp32 tensor, and cross-entropy then reduces it. At `B=8, T=512, V=32000` that logits-plus-grad pair alone is 1049 MB, which was the measured driver of the 6813 MB peak for the 110M model at batch size 8. A chunked loss that computes cross-entropy on a slice of the time axis would cap this term.

**P1-2. Attention materializes a per-head `T x T` score and weight tensor.**
`src/model.py:39-43` computes `scores = q @ k^T`, applies the mask, applies softmax, and keeps the weights for backward, once per head per layer. Bounded by `B*H*T*T*4` bytes. At `T=512` this is small; at `T=2048` with `H=16` it is the term that pushed the `L=8, d=2048, T=2048` config into an access violation on this box.

### P2

**P2-1. Attention is a Python loop over heads with three separate projections per head.**
`src/model.py:30-32` builds `wq`, `wk`, `wv` per head, and `src/model.py:62` concatenates with `torch.cat`. This is 3 times `n_heads` small GEMMs plus a copy, instead of one fused QKV GEMM. Measured (forward plus backward, CPU): at `B=8, T=512, d=384, h=6`, the current loop is 180.6 ms, per-head SDPA is 131.8 ms (1.37x), fused QKV plus SDPA is 109.8 ms (1.64x). At `B=4, T=2048, d=1024, h=16`, the same three are 3058.7 / 1604.6 / 1129.7 ms, so 1.91x and 2.71x. Parameter counts are identical across all three variants, so this is a pure speed change.

**P2-2. Explicit masking and softmax instead of `scaled_dot_product_attention`.**
Same lines as P1-2. SDPA also has a fused and memory-efficient backend that never materializes the full score matrix, which is what removes P1-2's memory ceiling and lets `T` grow.

### P3 (design limits, plan around, do not fix now)

- Learned absolute positions only: `self.wpe` (`src/model.py:106`). Context length is frozen at construction and extending it later means interpolating or replacing the table. RoPE would remove this coupling but would break checkpoint compatibility.
- No KV cache: `generate_tokens` (`src/generate.py:40`) runs a full forward over the whole prompt for every generated token. Fine for short outputs, wasteful for long ones.
- Weight tying (`src/model.py:114`) couples `vocab_size` to `d_model`; enlarging the vocabulary means enlarging the embedding.
- Token IDs are `np.uint16` (`src/dataset.py:11`), capping vocabulary at 65536; `prepare_data.py:25` enforces the same cap. Fine for the 32000 configs below.
- Train/val split is sequential, the last `val_frac` of the stream (`prepare_data.py`), not shuffled. `evaluate` reads only `eval_batches` (default 50) batches, so validation is a small fixed sample.
- `stride=1` default (`src/dataset.py:42`) is 512 times redundant at context 512 on a large corpus; it multiplies the effective corpus and the data loader work.
- `prepare_data.py` accepts a single `--input` file, no multi-file manifest.

### Doc drift (low, but fix while touching these files)

- `README.md` (near line 310) says "115 tests, 1 skip". The skip is right; the count is not. The suite is 146 tests, 1 skip today, per the green run and the evidence file.
- `docs/EXPERIMENT-tinystories.md` (near lines 172-176) states the 13 MB and 1.9 GB TinyStories corpora are still on this machine. They are not. `data/raw/` contains only `train.txt` at 2432 bytes. Anything that depends on those corpora must re-download them.

### Non-findings (measured, look like bugs, are not; do not spend time here)

- The per-layer causal mask rebuild (`src/model.py:61`) is only 0.4-0.7 percent of block time (measured at `B=8/T=512/h=6` and `B=8/T=1024/h=12`). It disappears for free when SDPA lands. Do not optimize it on its own.
- `evaluate` calls `model.eval()` on the uncompiled module while forward passes go through the `torch.compile` wrapper. This propagates correctly to the wrapped module (verified with `backend="eager"`), so eval-mode dropout is off as intended.
- Weight tying does not double-store the tied tensor in the checkpoint (`torch.save` deduplicates shared storage; confirmed by `data_ptr()` equality after load). A fresh tiny model checkpoint is 1.59 MB with empty optimizer state.
- The empty-prompt `IndexError` in `generate_tokens` (`src/generate.py:40-42`, `index -1 is out of bounds ... size 0`) is not reachable from the CLI: the ByteLevel pre-tokenizer emits at least one token for any non-empty text, and an empty `--prompt` is falsy and falls back to `<bos>`. Whitespace-only prompts work. Low severity.
- Data loading is not a bottleneck. Single-process `num_workers=0` memmap throughput on a 4M-token bin was 248,865 tok/s at `B=8/T=512`, up to 1,036,775 tok/s at `B=32/T=512`. Compute is 72-1321 tok/s. The loader is 100-1000x ahead of compute; adding workers now would help nothing.

## 6. Component inventory

Present, verified in source and by running the suite:

- pre-norm residual blocks, causal multi-head attention, GELU MLP
- learned absolute positions, weight tying
- AdamW with separate weight-decay groups
- warmup plus cosine schedule, gradient clipping, configurable dropout
- streaming tokenizer/data prep, memmap dataset, token-weighted eval without val-set cycling
- device-agnostic with a fail-fast CUDA path
- self-describing checkpoints with config, provenance, and identity keys
- resumable optimizer, RNG, and LR schedule state
- opt-in `torch.compile`, CPU thread knobs, `weights_only=True` loads on the safe path

Absent (this is the scaling work):

- mixed precision / autocast / GradScaler
- gradient accumulation
- gradient checkpointing
- fused QKV projection
- `scaled_dot_product_attention` / flash attention
- RoPE
- KV cache
- data loader `num_workers` and `pin_memory`
- TF32
- 8-bit / quantization for inference
- DDP
- atomic checkpoint write
- checkpoint rotation
- chunked / memory-bounded loss
- early stopping
- CLI flags for model size and optimizer hyperparameters

## 7. Proposed model configurations and memory

Parameter counts were produced by the closed form

```
params = V*d + T*d + L*(4*d*d + 2*d*d_ff + 6*d + d_ff) + 2*d
```

and confirmed equal to `MiniGPT.count_parameters()` at six shapes including all five below (exact match). `n_heads` does not enter the formula because the per-head projections sum to the same total as a fused one.

### 7.1 Configuration table

| name | vocab | ctx | layers | heads | d_model | d_ff | params | non-embedding | head dim |
|---|---|---|---|---|---|---|---|---|---|
| S-20M | 32000 | 1024 | 6 | 5 | 320 | 1280 | 17,960,320 | 7.4M | 64 |
| S-50M | 32000 | 1024 | 8 | 8 | 512 | 2048 | 42,116,096 | 25.2M | 64 |
| S-100M | 32000 | 1024 | 12 | 12 | 768 | 3072 | 110,390,784 | 85.0M | 64 |
| S-300M | 32000 | 1024 | 24 | 16 | 1024 | 4096 | 336,054,272 | 302.2M | 64 |
| S-1B | 32000 | 1024 | 24 | 28 | 1792 | 7168 | 984,456,704 | 925.3M | 64 |

All head dimensions are 64. The 1B target lands at 984M parameters (925M non-embedding), which is the usual convention for a "1B" label.

### 7.2 Memory model

The analytic floor used below is

```
train_peak(B) = 12*params                  # fp32 weights (4 B) + fp32 AdamW moments (8 B)
              + 2*B*T*V                     # logits and its gradient, fp32
              + B*(6*d + 2*d_ff)*T*4*L      # per-layer norm and MLP intermediates kept for backward
              + B*H*T*T*4*L                 # per-head softmax weights kept for backward
```

The `12*params` coefficient is the empirically fitted term, equal to fp32 weights plus the two fp32 AdamW moment buffers. The transient gradient buffer and the interpreter baseline are absorbed into the slack described below. Some references count the gradient separately and use 16 bytes per parameter; with that convention the floor rises and would over-predict the measurements. For bf16 the weights and activations are halved while AdamW moments stay fp32. This floor was validated against five fresh-process peak-memory measurements:

| validation config | batch | measured MB | floor MB | floor/measured |
|---|---|---|---|---|
| 110M / L12 / d768 / T512 / V32k | 1 | 2441 | 1768 | 0.72 |
| 110M / L12 / d768 / T512 / V32k | 4 | 4003 | 3112 | 0.78 |
| 110M / L12 / d768 / T512 / V32k | 8 | 6813 | 4904 | 0.72 |
| 335M / L24 / d1024 / T512 / V32k | 1 | 6231 | 5166 | 0.83 |
| 335M / L24 / d1024 / T512 / V32k | 4 | 8217 | 8587 | 1.04 |

The floor runs 22-28 percent below measurement at low batch, because it omits a roughly 0.9-1.1 GB interpreter and CPU-allocator baseline and because PyTorch keeps freed blocks. Treat the floor as a lower bound and add roughly 1 GB for a real run.

### 7.3 Per-configuration memory

| name | wt fp32 | wt bf16 | AdamW m+v | train fp32 B=1 (floor) | train bf16 B=8 (floor) | inference fp32 | inference bf16 + fp16 KV | KV / token fp16 |
|---|---|---|---|---|---|---|---|---|
| S-20M | 72M | 36M | 144M | 0.5G | 1.4G | 88M | 44M | 7.5 KB |
| S-50M | 168M | 84M | 337M | 1.1G | 2.7G | 202M | 101M | 16.0 KB |
| S-100M | 442M | 221M | 883M | 2.5G | 5.9G | 517M | 259M | 36.0 KB |
| S-300M | 1344M | 672M | 2688M | 7.1G | 15.7G | 1546M | 773M | 96.0 KB |
| S-1B | 3938M | 1969M | 7876M | 17.2G | 31.2G | 4290M | 2145M | 168.0 KB |

Local fit against the 13.4 GB free measured on this box:

| name | fits on this CPU? | reason |
|---|---|---|
| S-20M | yes | fp32 B=8 is 2.6G floor |
| S-50M | yes | fp32 B=8 is 5.1G floor |
| S-100M | yes, at small batch | fp32 B=1 2.5G; fp32 B=8 10.9G is near the edge, bf16 B=8 5.9G is comfortable |
| S-300M | no at B=8; B=1 fp32 7.1G is possible | needs bf16 and gradient checkpointing to train usefully |
| S-1B | no | 17.2G fp32 B=1 and 31.2G bf16 B=8 both exceed 13.4G; a separate `L=8, d=2048, T=2048` config crashed with an access violation under the same ceiling |

Checkpoint file size is 12.0 bytes per parameter, so each saved checkpoint is: S-20M 0.22 GB, S-50M 0.51 GB, S-100M 1.32 GB, S-300M 4.03 GB, S-1B 11.81 GB. Without rotation (P0-3), five S-300M checkpoints are 20 GB.

### 7.4 Throughput and time-to-token-budget on this CPU

Measured forward-plus-backward throughput with AdamW on this CPU:

| config | steps/s | tok/s |
|---|---|---|
| L6 / d320 / T512 / B2 | 1.290 | 1321 |
| L8 / d512 / T512 / B1 | 0.984 | 504 |
| L12 / d768 / T512 / B1 | 0.401 | 205 |
| L24 / d1024 / T512 / B1 | 0.141 | 72 |

Extrapolating linearly in parameter count to a 20-tokens-per-parameter budget (a Chinchilla-style reference point, not a quality claim):

| config | token budget | time on this CPU |
|---|---|---|
| S-20M | 359M | 3.1 days |
| S-50M | 842M | 19.3 days |
| S-100M | 2208M | 124.7 days |
| S-300M | 6722M | 1080.6 days |

These are a one-point-per-config extrapolation and the machine has no CUDA device, so they are a floor for time, not a forecast. They make the point that everything from S-100M up is a GPU workload. A single rented GPU would change these by orders of magnitude; nothing here is measured on such a device.

## 8. Efficiency techniques, evaluated against the code

| technique | addresses | expected effect | measured here? | priority |
|---|---|---|---|---|
| CLI model-config flags | P0-1 | unblocks every experiment | n/a | 1 |
| Atomic checkpoint write | P0-2 | prevents lost work | n/a, logic fix | 2 |
| Checkpoint rotation | P0-3 | caps disk at `keep-last * 12 B/param` | n/a, logic fix | 3 |
| Depth-safe init | P0-5 | prevents NaN blow-up at L>=24 | no, needs a deep-config test | 4 |
| Mixed precision (bf16) | P0-4, memory | halves weights/grads/activations, keeps fp32 AdamW | not measured (CPU has no bf16 tensor-core win; memory halving is arithmetic) | 5 |
| Gradient accumulation | P0-4, batch | decouples effective batch from memory | n/a, logic change | 6 |
| SDPA attention | P2-2, memory | removes the `T x T` weight tensor; 1.37x-1.91x faster | yes, on CPU | 7 |
| Fused QKV | P2-1 | 1.64x-2.71x faster with SDPA, identical params | yes, on CPU | 8 |
| Gradient checkpointing | P0-4, memory | trades about one extra forward for activation memory | not measured | 9 |
| Chunked loss | P1-1 | caps the `B*T*V` term | not measured | 10 |
| KV cache | P3 | speeds generation, no effect on training | not measured | later |
| Data loader workers / pin_memory | non-finding | loader is already 100-1000x ahead of compute | yes | do not do |
| TF32, flash-memory-efficient SDPA | P1/P2 on GPU | needs a CUDA device | no | on GPU only |
| Quantization for local inference | inference memory | shrinks weights for deploy | not measured | later |

## 9. Backward-compatibility analysis

- CLI flags for existing Config fields: safe. All new flags must default to the current `Config` defaults so an unmodified invocation behaves exactly as today.
- Atomic save and rotation: safe. On-disk format is unchanged; only the write path and retention policy change. Old checkpoints still load.
- SDPA: safe if implemented as a per-head replacement using the same `q,k,v` slices, so the state-dict layout does not move. A naive fused-QKV rewrite changes parameter names and would break existing checkpoints; either keep the per-head parameter names or add a migration. The benchmark variant that keeps state-dict compatibility is the one to ship first.
- Depth-safe init: changes initialization only, so it does not affect loading old checkpoints, but it does change training from scratch. It must be validated for the default 6-layer shape to confirm no regression before it is made the default.
- Mixed precision and accumulation: safe. They wrap the existing loop and leave the checkpoint schema alone (still store fp32 master weights, or store bf16 and document the change).
- Any change to `data/tokenizer.json` handling must preserve the existing file and the vocab-512 walkthrough.

## 10. Roadmap with dependencies

Stage 1 (unblocks everything else; no dependency):
- Add CLI flags for the sixteen missing `Config` fields, all defaulting to current values. Include `--n-layers`, `--n-heads`, `--d-model`, `--d-ff`, `--learning-rate`, `--dropout`, `--grad-clip`, `--eval-interval`, `--eval-batches`, `--warmup-steps`, `--seed`. Add a test that a bare invocation equals today's defaults and that a non-default shape constructs.

Stage 2 (depends on nothing; small):
- Atomic checkpoint write (temp file plus `os.replace`).
- `--keep-last N` rotation.
- A test that kills the write mid-way and confirms the previous checkpoint is intact, and a test that rotation keeps at most `N` files.

Stage 3 (depends on Stage 1 and Stage 2):
- Gradient accumulation (`--grad-accum-steps`), considering the existing DDP-compatible compute path.
- Depth-safe initialization plus a deep-config smoke test at `L=24` that runs a few steps and asserts finite loss.
- Chunked loss to cap the `B*T*V` term.

Stage 4 (depends on Stage 1; the memory and speed unlock):
- Replace the per-head attention with per-head SDPA, keeping parameter names. Then add fused QKV as an option.
- Add a benchmark harness under the test tree that records throughput and peak memory per config, so future changes are measured, not guessed.

Stage 5 (depends on Stage 3 and Stage 4):
- bf16 mixed precision with GradScaler and fp32 master weights, gated by a flag, default off until validated.
- Gradient checkpointing, gated by a flag.

Stage 6 (GPU track, depends on all of the above):
- Enable TF32 and the memory-efficient SDPA backend on CUDA, add DDP, then attempt S-300M, then S-1B on rented hardware. None of this is measurable on this machine.

Stage 7 (later):
- KV cache for generation.
- Quantization for local inference.
- RoPE and a shuffled split as separate, explicitly breaking changes.

## 11. Tests and benchmarks required before any large run

Tests:
- A bare `python -m src.train` still equals today's defaults (flag-regression test).
- A non-default shape constructs and runs two steps.
- Atomic save: simulated crash mid-write leaves the previous checkpoint loadable.
- Rotation keeps at most `N` checkpoint files.
- Deep config (`L=24`) runs a few steps with finite loss and finite grads.
- SDPA output matches the current attention output to a tight tolerance for the same weights, so the state dict is proven compatible.
- Gradient accumulation matches a single large batch on a fixed seed within tolerance.
- bf16 run reaches the same loss curve as fp32 within tolerance over a short run.

Benchmarks (recorded with the same command so numbers are comparable to this report):
- Peak RSS and tokens/s per proposed config, forward and forward-plus-backward.
- Checkpoint wall-clock write time and file size per config.
- Attention variant comparison (current, per-head SDPA, fused QKV) at `T=512, 1024, 2048`.

## 12. Recommended first task

Stage 1, and nothing else: add the sixteen CLI flags with defaults equal to the current `Config` values, plus one regression test that a bare invocation reproduces today's defaults and one that a non-default shape builds. This is the smallest change that unblocks every later experiment, carries no backward-compatibility risk, and is fully testable on this CPU-only box. Do not bundle Stage 2 into it.

## 13. The single biggest blocker

Everything runs in fp32 with no memory levers: full-precision weights and gradients, fp32 AdamW moments (16 bytes per parameter live, 12 bytes per parameter on disk, measured), a fully materialized `B*T*V` logits tensor, per-head `T*T` attention weights, no mixed precision, no gradient accumulation, no gradient checkpointing, and no way to pick a model size from the CLI. The measured consequence is that the 1B configuration needs about 17.2 GB just for a batch of one and about 31.2 GB at batch eight, against 13.4 GB actually free on this machine, and a smaller `L=8, d=2048, T=2048` configuration already crashed with an access violation under the same ceiling. Until model-size selection and at least one memory lever (mixed precision or gradient checkpointing) exist, no run above roughly S-100M is possible here, and no run at all is reproducible by config.
