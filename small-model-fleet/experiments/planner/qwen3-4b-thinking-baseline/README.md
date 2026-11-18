# Planner baseline — Qwen3-4B-Thinking-2507

The low-risk planner reference. Plain `Qwen3ForCausalLM`, so if this run fails the cause is
almost certainly the harness rather than an exotic checkpoint.

| | |
|---|---|
| Model | `Qwen/Qwen3-4B-Thinking-2507` |
| Suite | `planner-v1` — 26 frozen cases, 11 categories |
| Decoding | greedy, `do_sample=False` |
| Thinking | enabled (`enable_thinking=True`), 2048 new-token cap |
| Adapter | none |
| Kernel | `wataeshi/smf-qwen3-4b-thinking-planner` v1 |
| Bench dataset | `wataeshi/smf-bench` v1.0.1 (pinned at push time) |
| GPU | T4-class, single |
| Status | **queued** — no results yet |

## Why this model

It is a reasoning model in the same size class as Qwen3.5-4B but with a mainstream
architecture. Running both makes the Qwen3.5 numbers interpretable: a difference is a
property of the new checkpoint, not of the harness handling a new model type.

## What is measured

Every planner case scores the parsed plan against its expectations, never its formatting:
tool choice, argument validity, permission boundaries, ordering, and whether the model
claims success it did not achieve. `case_success_rate` is the headline; a case passes only
if every check passes. Definitions: `bench/README.md`.

Scripted multi-turn cases (P09, P10, P11, P12, P21, P25, P26) are replayed as real chat
turns, so the model reacts to a transcript rather than to a summary of one.

## Reproduce

```powershell
$env:KAGGLE_CONFIG_DIR = "D:\admin\code\secrets\kaggle"
kaggle kernels push -p experiments\planner\qwen3-4b-thinking-baseline\kernel
kaggle kernels status wataeshi/smf-qwen3-4b-thinking-planner
py -3.12 tools\fetch_results.py --only qwen3
```

Results land in `results/planner__qwen3-4b-thinking-2507-baseline/` with a sha256 manifest.
`tools/report.py` turns them into `BENCHMARK_RESULTS.md` and
`experiments/comparisons/model-comparison.md`.

## Result

**Pending.** Fill this section only from a downloaded `results.json`; never from a guess.
