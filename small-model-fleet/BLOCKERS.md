# Blockers and external dependencies

Updated 2026-11-17. Anything here is either waiting on a human or waiting on an external
system. Everything not listed here is being worked on.

## B-01 — Kaggle GPU queue latency (active, environmental)

**Symptom:** Two GPU kernels (`wataeshi/smf-lfm3-1-7b-baseline`,
`wataeshi/smf-qwen3-4b-thinking-planner`) sat in `QUEUED` for an extended period after
push. A CPU-only probe kernel ran immediately, so this is specific to GPU workers.
**Cause:** free-tier GPU worker availability. Not an error in the notebooks.
**Impact:** wall-clock only. Quota is charged when the session runs, not while queued.
**Mitigation in place:** at most two GPU kernels in flight (the account hard-caps at two:
`Maximum batch GPU session count of 2 reached`); every suite writes `partial_results.json`
after each case so a killed session still yields evidence.
**Human action needed:** none.

## B-02 — Hugging Face token absent on Kaggle (resolved by design)

**Symptom:** `Salesforce/xlam-function-calling-60k` is `gated: auto`; the Kaggle image is
anonymous (`hf_token_present: false`), so the original repo cannot be downloaded.
**Resolution:** the identical file is consumed from the ungated CC-BY-4.0 mirror
`lockon/xlam-function-calling-60k`. No credential is needed anywhere in the pipeline.
**Human action needed:** none, unless the mirror is ever withdrawn — then an `HF_TOKEN`
would have to be added as a Kaggle secret.

## B-03 — LFM Open License v1.0 commercial threshold (product risk, non-blocking)

**Symptom:** `LiquidAI/LFM3-1.7B` is not Apache-2.0. Section 5 conditions *commercial* use
on the licensee remaining under a revenue Threshold defined in the licence.
**Impact on this milestone:** none. Research, benchmarking and internal fine-tuning are
permitted.
**Human action needed (only before shipping a product):** read section 1's Threshold
definition, confirm whether the fleet operator would cross it, and if so obtain a Liquid AI
commercial licence or substitute an Apache-2.0 worker. Recorded as `DECISIONS.md` D-06.

## B-04 — No text-format PC-agent training corpus exists (research gap)

**Symptom:** Every surveyed desktop/OS dataset ships screenshots plus pyautogui actions, not
typed function calls with observations. `playwright-mcp-toolcalling` (MIT) is the closest but
publishes no tool schemas.
**Impact:** the first SFT teaches tool-use discipline and the LFM3-native serialisation, not
Windows-specific tool names. Stated plainly in the results.
**Human action needed:** none for this milestone. Recommended next data investment is
documented in `DATASET_REGISTRY.md`.

## Not a blocker, but worth stating

- No paid compute was used, and no paid fallback exists in any script.
- Nothing was published: the bench dataset and all kernels are private.
- No credential was read, printed or written by this project. `KAGGLE_CONFIG_DIR` is
  `D:\admin\code\secrets\kaggle`, which is used by the Kaggle CLI for OAuth state.
