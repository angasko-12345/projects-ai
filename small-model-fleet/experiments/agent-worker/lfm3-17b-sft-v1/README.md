# Worker SFT v1 — LFM3-1.7B + LoRA

One session does data preparation, a pre-training reference loss, a bounded pilot, the full
run, the post-training evaluation and an adapter reload check. Reloading a 1.7B base four
times would spend GPU quota on nothing, so stages are separated by saved artifacts rather
than by kernels.

| | |
|---|---|
| Base | `LiquidAI/LFM3-1.7B` |
| Adapter | LoRA r=16, α=32, dropout 0.05, targets `q,k,v,o,in,out,w1,w2,w3` |
| Optimiser | lr 2e-4 cosine, 5% warmup, effective batch 16 (2 × 8 accumulation) |
| Precision | bf16 if the GPU supports it without emulation, else fp16 |
| Loss | completion only — prompt tokens are `-100` |
| Data | xLAM function-calling sample (CC-BY-4.0 mirror) + Hermes FC `func_calling` (Apache-2.0) |
| Suite | `specialist-v1`, identical to the baseline |
| Kernel | `wataeshi/smf-lfm3-17b-sft-v1` |
| Bench dataset | `wataeshi/smf-bench` v1.0.3 |
| Status | **prepared, not pushed** — GPU session cap |

## What this run is allowed to claim

The first fine-tune can only teach *tool discipline*: call a tool instead of describing one,
emit arguments that validate, do not claim completion that did not happen, respect a
permission boundary. It cannot teach Windows tool names, because no public corpus of
text-format desktop agent traces exists (`BLOCKERS.md` B-04). Results are reported with that
scope stated, not glossed over.

## Stage outline

| Stage | What it produces | Failure behaviour |
|---|---|---|
| A | dataset manifest, leakage check, tokenised splits, audit samples | hard stop before any training |
| B | pre-training validation loss | reference point for stage D |
| C | 60-step pilot: sec/step, VRAM, loss curve, adapter bytes | logged; stage D skipped if it fails |
| D | full run, step count chosen from measured pilot throughput | crash keeps the last checkpoint for stage E |
| E | reload the saved adapter into a fresh base, then run the suite | written even if training failed |

Stage E deliberately evaluates a *reloaded* adapter rather than the in-memory training model.
That makes "these metrics came from this adapter directory" true by construction, which is
also the artifact that gets shipped. Stage D reserves 35% of the time budget for it: a run
that trains well but never evaluates has produced no evidence.

## Correctness details that would be silent bugs

- Right padding, not left. Left padding puts pad tokens before the supervised span.
- `attention_mask` is emitted by the collator. Without it the model attends to pad positions
  and the loss is quietly wrong on every padded batch.
- LoRA is attached once, explicitly, instead of passing `peft_config` to two successive
  trainers — re-wrapping an already-PEFT model is version-dependent behaviour.
- `enable_input_require_grads()` so gradient checkpointing works over frozen embeddings.
- `results.json` is written before the adapter reload check, so a stage F failure cannot cost
  the evaluation it was meant to support.

## Dry-run

`dryrun/` holds a CPU kernel (`wataeshi/smf-sft-dryrun`) that installs the same pinned stack,
downloads the real datasets, tokenises with the real LFM3 tokenizer and trains a randomly
initialised tiny model for three steps. It is not an experiment and produces no benchmark
number; it exists to catch an API or data-pipeline failure while it is still free.

## Reproduce

```powershell
$env:KAGGLE_CONFIG_DIR = "D:\admin\code\secrets\kaggle"
kaggle kernels push -p experiments\agent-worker\lfm3-17b-sft-v1\kernel
kaggle kernels status wataeshi/smf-lfm3-17b-sft-v1
py -3.12 tools\fetch_results.py --only sft
```

## Result

**Pending.** Requires a free GPU slot (`DECISIONS.md` D-12). Adapter and metrics will be
recorded in `artifacts/` and `EXPERIMENT_LOG.md` when the run completes.
