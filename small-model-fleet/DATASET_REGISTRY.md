# Dataset registry and audit

All candidates were checked against their Hugging Face metadata and, where possible, by
reading actual rows through the datasets-server API. "License" is what the dataset card
declares; a blank license is treated as *not usable for training*, not as "probably fine".

Audit date: **2026-11-17**. Licenses and cardinalities were read on that date.

## Summary of the decision

| Dataset | License | Size | Verdict |
|---|---|---|---|
| `lockon/xlam-function-calling-60k` (mirror of `Salesforce/xlam-function-calling-60k`) | CC-BY-4.0 | 60,000 | **USE** (2,600 sampled) — tool selection + argument generation |
| `NousResearch/hermes-function-calling-v1` (`func_calling`) | Apache-2.0 | 1,893 | **USE** — the only well-licensed source with real observations in the transcript |
| `jdaddyalbs/playwright-mcp-toolcalling` | MIT | 1K–10K | **INVESTIGATE** — closest to PC-agent behaviour but ships no tool schemas |
| `nvidia/When2Call` | CC-BY-4.0 | 10K–100K | **INVESTIGATE** — targets "should I call a tool at all", our weakest axis |
| `nvidia/Nemotron-RL-Agentic-Function-Calling-Pivot-v1` | CC-BY-4.0 | 1K–10K | **INVESTIGATE** — GRPO-style, for the later RL experiment |
| `gorilla-llm/Berkeley-Function-Calling-Leaderboard` | Apache-2.0 | eval only | **USE as external eval** (not in this milestone) |
| `glaiveai/glaive-function-calling-v2` | Apache-2.0 | 112,960 | **REJECT this round** — string format, known artefact issues |
| `Team-ACE/ToolACE` | *none declared* | ~10K | **REJECT** — license unclear |
| `internlm/Agent-FLAN` | *none declared* | — | **REJECT** — license unclear |
| `Maurus/ToolBench`, `Adorg/ToolBench` | *none declared* | — | **REJECT** — unofficial mirrors, no license |
| `liminghao1630/API-Bank` | *none declared* | — | **REJECT** — license unclear |
| `Ahren09/ToolAlpaca` | *none declared* | tiny | **REJECT** — unofficial mirror, no license |
| `mlfoundations/gelato-osworld-agent-trajectories` | MIT | 10K–100K | **REJECT for text SFT** — image modality |
| `xlangai/osworld_v2_tasks`, `ubuntu_osworld*` | varied | — | **REJECT for text SFT** — screenshot + pyautogui actions, not function calls |
| `smolagents/toolcalling` | *none declared* | 10K–100K | **REJECT** — no license |

## Detail: the two sources actually used

### 1. xLAM function calling (`Salesforce/xlam-function-calling-60k`)

- **Source:** https://huggingface.co/datasets/Salesforce/xlam-function-calling-60k
- **Accessed via:** `lockon/xlam-function-calling-60k` — an ungated mirror of the same file
  (`xlam_function_calling_60k.json`, 39k downloads, CC-BY-4.0). The original is gated
  (`gated: auto`), and the Kaggle notebook has no Hugging Face token, so the mirror is used
  to keep the pipeline token-free. Same data, same licence.
- **Size:** 60,000 rows.
- **Format:** `{query: str, tools: JSON-string[openai tool], answers: JSON-string[{name, arguments}]}`.
- **Teaches:** choosing among many candidate tools; producing arguments scoped to the query.
- **Quality checks applied:** tool list non-empty; every call names a tool that exists in
  that record's own tool list; every required argument present; every argument matches its
  declared JSON type; ≤ 4 calls per record. Rows failing any check are dropped and counted.
- **Known issues:** no observations at all (nothing after the call), tools are mostly
  web-API shaped, calling conventions are sometimes multi-call. Long prompts (large tool
  lists).
- **Training compatibility:** high. Prompt side renders through LFM3's `<tools>` block.
- **Cost:** ~2,600 rows ≈ 2.6k examples ≈ 1 epoch in roughly 10 minutes of T4 time.
- **Recommendation:** USE.

### 2. Hermes function calling (`NousResearch/hermes-function-calling-v1`, config `func_calling`)

- **Source:** https://huggingface.co/datasets/NousResearch/hermes-function-calling-v1
- **Size:** 1,893 rows in `func_calling` (11,578 across all five configs).
- **Format:** `{conversations: [{from: system|human|gpt|tool, value}], tools: JSON-string}`,
  with assistant turns using `<tool_call>{"name":...}</tool_call>`.
- **Teaches:** multi-turn tool use **with real observations** (`from: "tool"`), and closing
  the task after seeing them. This is the only well-licensed source that has both.
- **Quality checks applied:** same argument validation as xLAM; dropped if the first turn is
  not a user turn, or if a mid-conversation assistant turn makes no call.
- **Known issues:** synthetic multi-turn from GPT-4; some records end with a refusal with no
  call (kept deliberately only when it is the whole conversation, as a "do not invent a
  tool" example); English only.
- **Training compatibility:** high, and it is the source of the completion-detection signal.
- **Recommendation:** USE.

## The PC-agent gap (important)

No dataset surveyed provides **desktop/OS-level** tool calling in a text function-call
format with observations. The OSWorld family ships screenshots plus pyautogui actions;
`playwright-mcp-toolcalling` has the right shape (MCP tool calls with real browser results)
but does not publish the tool schemas, only a prose system prompt.

Consequence: the first SFT teaches **tool-use discipline** (select, serialise, validate,
read results, stop) rather than Windows-specific tool names. That is the transferable part
of PC-agent behaviour and matches the stated goal ("reliable execution through defined
tools"), but it is a real limitation and is stated as such in the results.

**Recommended data strategy for the next round:** build a ~2k-example PC-tool trajectory set
by recording our own `pc_agent` toolset (fs/proc/ui/shell) against a scripted VM, or by
converting `playwright-mcp-toolcalling` and deriving schemas from the observed argument
keys. Licence for the latter is already clean (MIT).

## Leakage control

- The held-out suites in `bench/` were written for this project and are not derived from any
  public dataset. A lexical overlap check between training prompts and the 48 bench prompts
  is computed at preparation time and stored in `dataset_manifest.json`.
- The 6% validation split is drawn from the training pool only, never from `bench/`.
- `bench/cases.json` and `bench/specialist/cases.json` are never read by any training script.
