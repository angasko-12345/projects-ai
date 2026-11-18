# Experiment log

One row per submitted Kaggle run. A row is only written once the kernel exists, so the
history cannot contain runs that were planned but never executed. `—` means the value is not
yet available because the run has not finished.

Retrieval: `python tools/fetch_results.py` writes into `results/` with a sha256 manifest.

| # | Experiment ID | Kernel | Suite | Model | Pushed | Status | GPU | Result |
|---|---|---|---|---|---|---|---|---|
| 1 | `env/probe` | `wataeshi/smf-probe-env` | — | — | v1 | COMPLETE | none (CPU) | see below |
| 2 | `agent-worker/lfm3-17b-baseline` | `wataeshi/smf-lfm3-1-7b-baseline` | specialist-v1 | `LiquidAI/LFM3-1.7B` | v1, v2 | QUEUED | pending | pending |
| 3 | `planner/qwen3-4b-thinking-2507-baseline` | `wataeshi/smf-qwen3-4b-thinking-planner` | planner-v1 | `Qwen/Qwen3-4B-Thinking-2507` | v1 | QUEUED | pending | pending |
| 4 | `agent-worker/lfm3-17b-sft-v1` | `wataeshi/smf-lfm3-17b-sft-v1` | specialist-v1 | `LiquidAI/LFM3-1.7B` + LoRA | not yet | PREPARED | pending | pending |
| 5 | `planner/qwen35-4b-baseline` | `wataeshi/smf-qwen35-4b-planner` | planner-v1 | `Qwen/Qwen3.5-4B` | not yet | PREPARED | pending | pending |
| 6 | `agent-worker/lfm3-17b-sft-v1/dryrun` | `wataeshi/smf-sft-dryrun` | none (pipeline check) | tiny random | v1, v2 | see below | none (CPU) | no metrics by design |

---

## Run 1 — `env/probe` (complete, CPU only)

Kernel: `wataeshi/smf-probe-env` v1. Purpose: learn the actual runtime before spending GPU
quota. Cost: no GPU quota (CPU kernel).

Measured:

| Item | Value |
|---|---|
| Python | 3.13.18 |
| torch | 2.11.0+cpu |
| transformers | **5.20.2** |
| peft / datasets / accelerate | 0.21.2 / 4.8.5 / 1.15.0 |
| trl | **missing** |
| bitsandbytes | **missing** |
| vllm | missing |
| RAM | 33.7 GB total, 32.2 GB available |
| /kaggle/working | 20.9 GB free of 21.0 GB |
| /root/.cache and /tmp | ~1 TB free |
| HF reachable | yes, HTTP 200 |
| HF token | none (anonymous) |
| `config_mapping has qwen3_5` | **True** |
| `config_mapping has lfm3` | **False** (must use `trust_remote_code`) |
| `config_mapping has qwen3`, `lfm2` | True / True |

Resolved model SHAs at probe time:

- `Qwen/Qwen3.5-4B` → `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- `Qwen/Qwen3-4B-Thinking-2507` → `768f209d9ea81521153ed38c47d515654e938aea`
- `LiquidAI/LFM3-1.7B` → `1da06bec6a819da10b54c67e5c90972b46d81871`

Consequences that shaped every later run:

1. `trl` must be installed; it is pinned to `1.16.0` because that is what the official LFM3
   cookbook pins against `transformers==5.20.2`.
2. LFM3 always needs `trust_remote_code=True` — it is not a native config in this image.
3. Qwen3.5 needs no install work at all: the image already knows `qwen3_5`.
4. `/kaggle/working` is the tight resource (21 GB), not the cache disks. Strategies that
   write merged 4B checkpoints there would fail, which is why only adapters are persisted
   (`DECISIONS.md` D-11).

## Run 2 — `agent-worker/lfm3-17b-baseline` (queued)

Purpose: the reference number every adapter must beat. First real exercise of the harness
on a remote model.

Configuration: `enable_thinking=False`, greedy, `max_new_tokens=512`, tool list passed via
the model's own chat template, native `tool` role for observations (probed at runtime, with
a documented fallback to user-prefixed results if the template rejects it).

Two versions were pushed; v2 only changed `enable_thinking` from True to False so the
baseline matches the official SFT recipe and the post-training comparison is like-for-like.

## Run 3 — `planner/qwen3-4b-thinking-baseline` (queued)

Purpose: the low-risk planner reference. Plain `Qwen3ForCausalLM`, so a failure here would
indicate a harness bug rather than an exotic architecture.

Configuration: `enable_thinking=True`, greedy, `max_new_tokens=2048`. Scripted context turns
(P09, P10, P11, P12, P21, P25, P26) are replayed as real chat turns so the model is reacting
to a transcript, not to a summary of one.

## Runs 4 and 5 — prepared, not submitted

Both kernels are written, compile-checked and pinned in `experiments/registry.json`, but
were not pushed because the account allows at most two concurrent GPU sessions
(`Maximum batch GPU session count of 2 reached`) and the first two occupied both slots.

- `agent-worker/lfm3-17b-sft-v1` does data prep → pre-training val loss → 60-step pilot →
  full run → adapter verification → post-training suite evaluation in a single session, so
  quota is not spent reloading a 1.7B base model four times.
- `planner/qwen35-4b-baseline` carries a multi-class loader fallback and a `SKIP_CASES`
  resume hook, because the checkpoint is multimodal and may not resolve under
  `AutoModelForCausalLM`.

## Run 6 — `agent-worker/lfm3-17b-sft-v1/dryrun` (CPU pipeline check, no quota)

Kernel: `wataeshi/smf-sft-dryrun`. Not an experiment: the model is randomly initialised and
no benchmark number is produced. It exists to catch an install, API or data-pipeline failure
while it is still free.

It installs the exact pinned stack the SFT kernel installs, downloads both datasets, builds
and tokenises with the real LFM3 tokenizer, checks label masking and leakage, then trains a
tiny random model for three steps through the same `smf_sft` helpers the GPU job uses, and
reloads the saved adapter.

**v1 failed**, and the failure was worth having: `ModuleNotFoundError: No module named
'smf_data'`. The bench dataset version published minutes earlier had been uploaded with the
CLI default `--dir-mode skip`, which copies top-level files and silently drops `src/` and
`bench/`. Republished with `--dir-mode zip`; `tools/pack_bench.py` now always passes it.
Every file present in v1.0.2 is byte-identical in v1.0.3 by sha256, so the queued baselines
and the future SFT run are still graded by the same code.

**v2** is the rerun against the intact dataset.

Diagnostic note: `kaggle kernels status` reports only `ERROR`; the traceback came from
`kaggle kernels logs`, which `tools/fetch_results.py` now stores as `kernel_log.txt`.
