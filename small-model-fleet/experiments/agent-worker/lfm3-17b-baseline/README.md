# Worker baseline — LFM3-1.7B

The number every adapter has to beat. This is the unmodified checkpoint that the first SFT
run fine-tunes, evaluated on the exact suite and harness the fine-tune will see.

| | |
|---|---|
| Model | `LiquidAI/LFM3-1.7B` (`trust_remote_code=True`) |
| Suite | `specialist-v1` — 22 cases / 32 scored steps, 15 categories |
| Decoding | greedy, `enable_thinking=False`, 512 new-token cap |
| Adapter | none |
| Kernel | `wataeshi/smf-lfm3-1-7b-baseline` v2 |
| Bench dataset | `wataeshi/smf-bench` v1.0.1 (pinned at push time) |
| Status | **queued** — no results yet |

## Why thinking is off

Liquid's own LoRA recipe trains and serves LFM3 with thinking disabled. Turning it on for the
baseline and off for the fine-tune would confound the comparison, so both use the same mode
(`DECISIONS.md` D-05). v1 of the kernel had it on; v2 fixed that before either version ran.

## What the suite asks of a 1.7B model

Multi-step tool use against a 14-tool PC-agent toolset: read/write files, run commands, list
a directory, take a screenshot, move the mouse, type text, wait, ask the user, finish. Cases
cover tool selection, argument grounding, permission boundaries, failure detection
(no false "done"), recovery after an error observation, and verbosity.

The chat template is the model's own: tools are passed as `tools=` so they render into
`<tools>…</tools>`, observations are delivered in the native `tool` role where the template
accepts it (probed at runtime with a documented fallback). Parsing accepts both Qwen-style
and LFM3-style call syntax, so a formatting difference cannot masquerade as a capability
difference (`DECISIONS.md` D-03).

## Reproduce

```powershell
$env:KAGGLE_CONFIG_DIR = "D:\admin\code\secrets\kaggle"
kaggle kernels push -p experiments\agent-worker\lfm3-17b-baseline\kernel
kaggle kernels status wataeshi/smf-lfm3-1-7b-baseline
py -3.12 tools\fetch_results.py --only lfm3
```

## Result

**Pending.** Queued on Kaggle's free tier; see `BLOCKERS.md` B-01.
