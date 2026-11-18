# Decisions

Numbered, dated, with the evidence that justified each one. A decision here is reversible
work, not policy. Superseded decisions stay in the file.

---

## D-01 — All model execution happens on Kaggle, never locally
**Date:** 2026-11-17
**Decision:** The Windows host is used for authoring, static checks, submission and
artifact retrieval only. No weights are downloaded to Windows, no inference is run locally.
**Why:** The host has no GPU budget for this work and the brief forbids local inference.
**Consequence:** Every measurable number in this repo comes from a Kaggle kernel whose
identifier is recorded in `EXPERIMENT_LOG.md`.

## D-02 — One frozen bench dataset, not per-run copies
**Decision:** `bench/` plus the harness is published as the private Kaggle dataset
`wataeshi/smf-bench`; every kernel mounts it instead of embedding a copy.
**Why:** Two runs that grade differently cannot be compared. A dataset version is a single
immutable id that can be cited next to a result.
**Consequence:** Editing the suite requires a version bump and invalidates comparability
with earlier runs, which is the desired friction.

## D-03 — Score parsed semantics, support both tool-call serialisations
**Decision:** The harness parses both `<tool_call>{json}</tool_call>` (Qwen) and
`<tool_call><function=…><parameter=…></parameter></function></tool_call>` (LFM3) into the
same `{name, arguments}` shape, and scores only that.
**Why:** Otherwise the suite measures template conformance instead of tool-use competence,
and one model family would be structurally disadvantaged.
**Verified:** `tests/test_harness.py` covers both forms plus a bare-JSON fallback.

## D-04 — 48 cases, greedy decoding, field-level scoring
**Decision:** 26 planner cases and 22 specialist cases (32 scored steps). Greedy only, one
sample per case. A case passes only when every check in every step passes.
**Why:** Greedy removes sampling variance, so a delta is attributable to the model. Field
level checks give a diagnostic, not just a scalar.
**Limitation:** 48 cases separates models that differ by ~15 points, not 3. Stated in
`bench/README.md` and repeated in the results.

## D-05 — Planner benchmark runs with thinking enabled; LFM3 runs with thinking disabled
**Decision:** Both planner models run with their reasoning mode on, capped at 2048 new
tokens. LFM3 runs with `enable_thinking=False`.
**Why:** The planner's whole job is decomposition and judgement; suppressing reasoning
would test a different model. LFM3 is a non-reasoning edge model whose official SFT recipe
(`lfm3_sft_with_trl.ipynb`) trains with `enable_thinking: False`, so training and evaluation
must agree or the comparison measures a format mismatch.
**Consequence:** Planner latency is high and that is reported honestly rather than hidden by
truncating reasoning further.

## D-06 — LFM Open License v1.0 accepted for research, flagged for product use
**Decision:** Use `LiquidAI/LFM3-1.7B` for this milestone.
**Why:** It is the specified worker, it is explicitly designed for on-device tool calling,
and the licence grants use and modification. Section 5 conditions *commercial* use on the
user's legal entity staying under a revenue Threshold.
**Action required before shipping a product:** confirm the Threshold and whether the fleet
would cross it; if so, obtain a commercial licence from Liquid AI or substitute a model
under Apache-2.0. Recorded in `BLOCKERS.md` as a non-blocking product risk.

## D-07 — SFT data: xLAM + Hermes, no synthetic generation
**Decision:** Train on `xlam-function-calling-60k` (2,600 sampled, CC-BY-4.0) and
`hermes-function-calling-v1/func_calling` (Apache-2.0). Reject every dataset with no
declared licence.
**Why:** These are the only high-volume, clearly-licensed sources that together provide
tool selection, argument generation, **and** real observations. The brief forbids generating
synthetic training data in the first experiment, so nothing was generated.
**Consequence:** See D-08.

## D-08 — Accept that the first SFT teaches tool discipline, not Windows tool names
**Decision:** Proceed even though no surveyed dataset provides desktop/OS tool calls in a
text function-call format.
**Why:** The transferable skill — choose a tool, serialise valid arguments, read the result,
stop at the right time — is exactly what the specialist benchmark measures. Waiting for a
perfect PC-agent dataset would stall the milestone.
**Consequence:** The improved-adapter claim is scoped to tool-use discipline. A Windows tool
dataset is the top data recommendation for the next round (`DATASET_REGISTRY.md`).

## D-09 — Official Liquid recipe as the training baseline, minimally adapted
**Decision:** Adopt Liquid's `lfm3_sft_with_trl` settings: LoRA r=16, α=32, dropout 0.05 on
`q,k,v,o,in,out,w1,w2,w3`; lr 2e-4, cosine, warmup 5%; fp16 on T4 with fp32 adapters.
Change only what the data requires: sequence length, completion-only loss, and a bounded
step budget.
**Why:** The vendor tuned these for this exact architecture. Deviating without a reason
would confound the experiment.
**Consequence:** Any improvement is attributable to the data and the LoRA, not to a lucky
hyperparameter sweep.

## D-10 — Mask prompt tokens; compute loss on the completion only
**Decision:** Pre-tokenise each example into `input_ids` and `labels` with `-100` over the
prompt span, using LFM3's own chat template with the real `tools` list.
**Why:** Training on the prompt teaches the model to reproduce tool schemas it will be given
at inference time, wastes capacity, and inflates the loss curve into looking better than the
model is.
**Consequence:** `bench/README.md` metric `parse_validity` is the honest analogue of training
loss; training loss is never used as evidence of improvement.

## D-11 — Adapters only, never merged full weights
**Decision:** Persist LoRA adapters and configs. Do not write merged 1.7B checkpoints to
Kaggle output.
**Why:** Kaggle gives ~21 GB of `/kaggle/working`; a merged model plus base plus caches
risks filling it and losing the session's outputs. An adapter is ~30 MB and is the actual
artifact of the experiment.
**Consequence:** Reproducing a merged model requires base revision + adapter, both of which
are pinned.

## D-12 — Two concurrent Kaggle GPU sessions maximum, used deliberately
**Decision:** Keep at most two GPU kernels in flight. Queue the rest.
**Why:** Kaggle rejected a third push ("Maximum batch GPU session count of 2 reached").
**Consequence:** Experiment scheduling is planned two slots ahead; verification and artifact
retrieval happen between waves.
