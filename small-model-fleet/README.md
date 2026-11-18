# Small Model Fleet (`small-model-fleet`)

A fleet of small, specialised language models that plan and execute work on a Windows PC.
This directory holds the model research, the evaluation harness and the experiments behind it.

**Status:** Milestone 1–4 in progress (see `PROJECT_STATUS.md`).

## What this repo contains

| Path | Purpose |
|---|---|
| `bench/planner/` | Frozen held-out planner suite: 26 cases, one output schema, 11 categories |
| `bench/specialist/` | Frozen held-out PC-agent suite: 22 cases / 32 scored steps, 15 categories |
| `src/smf_bench.py` | Parsing + scoring core. Model-agnostic: scores parsed semantics, never formatting |
| `src/smf_runner.py` | Suite drivers. Identical across baseline, pilot and post-training runs |
| `src/smf_kaggle.py` | Kaggle-side driver: env record, model load, suite execution, artifact save |
| `src/smf_data.py` | Public-dataset download, normalisation, validation, tokenisation |
| `experiments/` | One folder per experiment, each with a reproducer `kernel/run.py` and results |
| `tests/test_harness.py` | Offline self-test of parsing/scoring (no model, runs on Windows) |
| `artifacts/` | Downloaded adapters, configs and reports that must survive Kaggle |
| `docs/` | Reports and write-ups |

## Compute model

All model execution happens on **Kaggle** free GPU. The Windows host is used only for
authoring, static checks, notebook submission and artifact retrieval. No model is ever
loaded, run or trained locally.

- Kaggle config: `D:\admin\code\secrets\kaggle` (OAuth, never committed)
- Kaggle bench dataset: `wataeshi/smf-bench` (private) — the single source of truth for
  the harness and both suites, so no two runs can grade differently
- Caches/temp: `D:\tmp\small-model-fleet` only

## Reproducing a run

```powershell
$env:KAGGLE_CONFIG_DIR = "D:\admin\code\secrets\kaggle"
kaggle kernels push -p experiments\planner\qwen3-4b-thinking-baseline\kernel
kaggle kernels status wataeshi/smf-qwen3-4b-thinking-planner
kaggle kernels output wataeshi/smf-qwen3-4b-thinking-planner -p D:\tmp\small-model-fleet\out
```

Each experiment folder keeps its own `kernel-metadata.json` (pinned dataset sources) and
`README.md` (what was run, what it concluded).

## Scoring rules

Both suites are graded field by field; a case passes only when every check passes.
Full definitions live in `bench/README.md`. The headline numbers are:

`case_success_rate`, `tool_selection_accuracy`, `argument_validity`, `parse_validity`,
`false_completion_rate`, `permission_violation_rate`, `recovery_success_rate`,
`avg_latency_s`, `p95_latency_s`, `avg_tokens_per_s`, `peak_vram_gb`.

Training loss is never treated as evidence of task improvement - only held-out suite
numbers are.

## Operational gotchas

These cost real time, so they are written down rather than rediscovered.

- **`kaggle datasets version` defaults to `--dir-mode skip`,** which silently uploads only
  top-level files and drops `src/` and `bench/`. Always publish through
  `py -3.12 tools\pack_bench.py --version X.Y.Z --publish`, which passes `--dir-mode zip`.
  A kernel whose dataset mounted empty fails with `ModuleNotFoundError` on the first import.
- **A kernel's URL slug is not always its metadata `id`.** `SMF LFM3 1.7B Baseline` resolved
  to `wataeshi/smf-lfm3-1-7b-baseline`, not `smf-lfm3-baseline`. `Permission 'kernels.get'
  was denied` usually means "wrong slug", not "not pushed".
- **`kaggle kernels status` says `ERROR` and nothing else.** The traceback is in
  `kaggle kernels logs <slug>`; `tools/fetch_results.py` now saves it as `kernel_log.txt`
  next to the artifacts.
