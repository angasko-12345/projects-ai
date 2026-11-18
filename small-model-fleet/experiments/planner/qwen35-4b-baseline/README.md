# Planner baseline — Qwen3.5-4B

The challenger planner. Same suite, same decoding policy, same scorer as the Qwen3 baseline;
only the checkpoint differs.

| | |
|---|---|
| Model | `Qwen/Qwen3.5-4B` (`Qwen3_5ForConditionalGeneration`, image-text-to-text) |
| Suite | `planner-v1` — 26 frozen cases, 11 categories |
| Decoding | greedy, thinking on, 1536 new-token cap |
| Adapter | none |
| Kernel | `wataeshi/smf-qwen35-4b-planner` |
| Bench dataset | `wataeshi/smf-bench` v1.0.3 |
| Status | **prepared, not pushed** — GPU session cap |

## Why the cap is 1536, not 2048

Qwen3.5-4B is a multimodal hybrid, so decode is more expensive per token than Qwen3-4B at
the same parameter count. 1536 keeps a 26-case run inside one session. The consequence is
stated in the result: if a case fails because the answer was truncated, that is a real
limitation of this configuration and is not hidden by re-running with a larger cap.

## Load path

`AutoModelForCausalLM` may not resolve for a `qwen3_5` checkpoint, so the driver tries
`AutoModelForImageTextToText`, `AutoModelForCausalLM`, then `AutoModelForVision2Seq`, and
records which class succeeded in `model.json`. A total failure writes `load_failure.json`
and exits clean so the kernel records `COMPLETE` with a documented failure instead of
`ERROR` with a stack trace only.

## Resume support

`SKIP_CASES` in `kernel/run.py` lists case ids already completed in an earlier session.
`partial_results.json` is rewritten after every case, so a killed session still leaves
evidence. This exists because GPU sessions here are scarce, not because the suite is slow.

## Reproduce

```powershell
$env:KAGGLE_CONFIG_DIR = "D:\admin\code\secrets\kaggle"
kaggle kernels push -p experiments\planner\qwen35-4b-baseline\kernel
kaggle kernels status wataeshi/smf-qwen35-4b-planner
py -3.12 tools\fetch_results.py --only qwen35
```

## Result

**Pending.** Requires a free GPU slot (`DECISIONS.md` D-12). No number is claimed here.
