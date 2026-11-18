# Model registry

Every entry records the exact revision that was executed, not just the model name.
Revisions are the Hugging Face commit SHAs resolved at run time and stored again in each
experiment's `results.json → model.revision_resolved`.

Researched: **2026-11-17**.

## Planner candidates

### Qwen/Qwen3.5-4B — primary candidate

| Field | Value |
|---|---|
| Revision (resolved) | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` |
| Licence | Apache-2.0 (clean for any use, no revenue threshold) |
| Architecture | `Qwen3_5ForConditionalGeneration` — **multimodal**, hybrid linear/full attention |
| Params / layers | 4B class; 32 text layers, hidden 2560, vocab 248,320, tied embeddings |
| Attention | 28 `linear_attention` layers + 4 `full_attention`; `full_attention_interval=4`, `head_dim=256`, `attn_output_gate=true` |
| Context | 262,144 |
| Extras | `vision_config` (24 layers) and one MTP head ship in the same checkpoint |
| Declared transformers | 4.57.0.dev0; Kaggle image has **5.20.2**, which knows `qwen3_5` |
| Likelihood on free GPU | medium risk: needs the linear-attention path, ships an unused vision tower, so more VRAM and more decode time than a plain 4B |

Why it matters: it is the newest generation and its stated strength is exactly the planner
job (reasoning, instruction following). The risk is infrastructure, not capability.

### Qwen/Qwen3-4B-Thinking-2507 — challenger

| Field | Value |
|---|---|
| Revision (resolved) | `768f209d9ea81521153ed38c47d515654e938aea` |
| Licence | Apache-2.0 |
| Architecture | `Qwen3ForCausalLM` — plain decoder, dense |
| Params / layers | 4B class; 36 layers, hidden 2560, GQA 32 heads / 8 KV, head_dim 128, vocab 151,936, tied embeddings |
| Context | 262,144, rope theta 5e6, no sliding window |
| Declared transformers | 4.51.0 — comfortable on any recent stack |
| Likelihood on free GPU | high: standard architecture, runs in fp16 on a T4 |

Why it matters: the low-risk reference. A 2507 revision with an explicit thinking mode.
Whatever wins on the planner suite is chosen on measured behaviour, not on generation.

### Why not a 4B coder or a mixture-of-experts planner

A 4B dense model is the largest thing that runs comfortably on the free tier with room for
the harness. MoE models with ~4B active parameters need substantially more VRAM for the
router and expert tables, which pushes a free T4 into 4-bit and destroys latency. Rejected
for milestone 1.

## First specialist worker

### LiquidAI/LFM3-1.7B

| Field | Value |
|---|---|
| Revision (resolved) | `1da06bec6a819da10b54c67e5c90972b46d81871` |
| Licence | **LFM Open License v1.0** — Apache-style grant *with* a commercial threshold clause (section 5). Research and internal use are fine; a legal entity above the Threshold cannot use it commercially under this licence. Tracked in `DECISIONS.md` D-06. |
| Architecture | `Lfm3ForCausalLM`, custom code (`auto_map` → `trust_remote_code=True` required) |
| Params | 1.73B |
| Layers | 24 total: 13 gated short convolutions (`conv`), 5 sliding-window attention, 6 global attention |
| Details | hidden 2048, 16 heads / 4 KV, head_dim 128, vocab 128,000, sliding window 256, sandwich norm, `partial_rotary_factor` 0.5 on global layers |
| Context | 262,144 |
| Post-training | Liquid post-trained it explicitly for agentic harnesses (Hermes Agent, Pi, OpenClaw); the card claims tool calling and structured output as primary skills |
| Declared transformers | 5.12.1. Kaggle has 5.20.2, but `lfm3` is **not** a native config in it — the repo's own code must load via `trust_remote_code` |
| 4-bit footprint | ~1 GB (vendor figure), which is the point of this model |
| Official recipes | `Liquid4All/cookbook`: `lfm3_sft_with_trl.ipynb`, `lfm3_dpo_with_trl.ipynb`, `lfm3_grpo_with_trl.ipynb` (GRPO recipe = "call the right tool out of 108 phone-app tools", directly relevant to a later experiment) |
| Likelihood on free GPU | high: fp16 on a T4 is the documented path, and Liquid's own notebook handles the no-bf16 case explicitly |

Vendor caveat, quoted from the card: *"not recommended for agentic coding or complex
multi-step reasoning due to its small size."* That is consistent with using it as a
bounded-tool-executor and keeping planning on a 4B model.

## Supporting models referenced but not trained in this milestone

| Model | Role | Note |
|---|---|---|
| `LiquidAI/LFM3-1.7B-Base` | base for future continued pretraining | same licence |
| `LiquidAI/LFM3-VL-*` | `vision` worker in the planner taxonomy | image modality |
| Qwen Coder family (≤4B dense) | `coder` worker | Apache-2.0, same toolchain |
| `LiquidAI/LFM2.5-1.2B-Instruct` | fallback/smaller worker | LFM Open License v1.0, 1.2B, newer than LFM2-1.2B |

## Compatibility matrix (as executed)

| Requirement | Qwen3.5-4B | Qwen3-4B-Thinking-2507 | LFM3-1.7B |
|---|---|---|---|
| `AutoModelForCausalLM` in transformers 5.20.2 | yes (`qwen3_5`) | yes (`qwen3`) | via `trust_remote_code` |
| fp16 on T4 (no bf16) | untested at registry time | yes | yes — Liquid documents fp16 explicitly |
| TRL SFT + PEFT LoRA | n/a (not trained) | n/a (not trained) | yes — official notebook pinned to `trl==1.16.0`, `peft==0.21.2` |
| Native tool-call template | yes | yes | yes — `<tool_call><function=…><parameter=…>` |
| Bench-relevant risk | linear-attention speed on sm75 | long reasoning traces inflate latency | custom code must load |
