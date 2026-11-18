"""CPU dry-run for the LFM3 SFT pipeline — no GPU, no quota.

This kernel exists because the real SFT run is expensive and scarce. It exercises
the exact modules the GPU job will import (`smf_data`, `smf_sft` from the bench
dataset) against the real datasets and the real LFM3 tokenizer, then proves the
TRL/PEFT wiring by training a deliberately tiny random model on the real tokenised
examples.

Nothing here is an experiment result. The model is random and the run produces no
benchmark number.
"""

import json
import os
import sys
import time

def bench_src():
    """Locate the mounted bench dataset regardless of Kaggle's input layout."""
    base = "/kaggle/input"
    for root, dirs, files in os.walk(base):
        if "smf_bench.py" in files:
            return root
        if root[len(base):].count(os.sep) >= 4:
            dirs[:] = []
    return None


def input_tree(base="/kaggle/input", depth=3):
    out = []
    for root, dirs, files in os.walk(base):
        rel = os.path.relpath(root, base)
        out.append(rel + "/" + (",".join(sorted(files)[:6]) if files else ""))
        if rel != "." and rel.count(os.sep) + 1 >= depth:
            dirs[:] = []
    return out[:80]


BENCH_SRC = bench_src()
print("bench harness:", BENCH_SRC)
if BENCH_SRC is None:
    print("input tree:", json.dumps(input_tree(), indent=1))
    raise SystemExit("smf-bench was not found anywhere under /kaggle/input")
os.environ["SMF_BENCH_ROOT"] = os.path.dirname(BENCH_SRC)  # smf_runner reads this at import time
sys.path.insert(0, BENCH_SRC)

INSTALL = (
    'pip install -qqq "trl==1.16.0" "transformers==5.20.2" "peft==0.21.2" '
    '"accelerate==1.15.0" "datasets==5.1.0" "huggingface-hub==2.3.0" '
    '"tokenizers==0.23.3" "safetensors==0.8.0" --progress-bar off'
)
print("pip rc:", os.system(INSTALL))

import torch
import transformers
import trl
import peft
import datasets

import smf_data as D
import smf_sft as S

OUT = "/kaggle/working/dryrun"
os.makedirs(OUT, exist_ok=True)
T0 = time.time()
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:1500]})
    print(("PASS " if ok else "FAIL ") + name + ("  | " + str(detail)[:300] if detail else ""))
    return ok


def save(name, payload):
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    print("wrote", path, os.path.getsize(path), "bytes")


def save_text(name, text):
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote", path, os.path.getsize(path), "bytes")


print("=" * 78)
print("versions:", json.dumps({
    "torch": torch.__version__,
    "transformers": transformers.__version__,
    "trl": trl.__version__,
    "peft": peft.__version__,
    "datasets": datasets.__version__,
    "python": sys.version.split()[0],
}, indent=2))

# ---------------------------------------------------------------- 1. tokenizer
print("\n### 1. tokenizer and chat template")
from transformers import AutoTokenizer

tok = AutoTokenizer.from_pretrained("LiquidAI/LFM3-1.7B", trust_remote_code=True)
if tok.pad_token_id is None:
    tok.pad_token = tok.eos_token
tok.padding_side = "right"
check("tokenizer loads", True, f"vocab={len(tok)} pad={tok.pad_token_id} eos={tok.eos_token_id}")

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "finish", "description": "Finish the task",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]
msgs = [
    {"role": "system", "content": "You are a helpful assistant with tools."},
    {"role": "user", "content": "Read C:/notes.txt"},
    {"role": "assistant", "content": "<tool_call>\n<function=read_file>\n<parameter=path>\nC:/notes.txt\n</parameter>\n</function>\n</tool_call>"},
    {"role": "tool", "content": "<tool_response>{\"ok\": true}</tool_response>"},
]
rendered = tok.apply_chat_template(msgs, tools=TOOLS, tokenize=False, add_generation_prompt=True,
                                   enable_thinking=False)
check("template accepts tools + tool role + enable_thinking", "<tools>" in rendered and "<tool_response>" in rendered,
      rendered[:200].replace("\n", " "))
save_text("rendered_example.txt", rendered)

# ---------------------------------------------------------------- 2. datasets
print("\n### 2. dataset download and normalisation")
sources = D.fetch_sources()
check("both source files downloaded", len(sources) == 2,
      {k: os.path.getsize(v) for k, v in sources.items()})

train_recs, val_recs, prep = D.build_dataset(sources, xlam_limit=200, seed=7)
check("dataset builds", len(train_recs) > 100 and len(val_recs) > 0,
      {"train": len(train_recs), "val": len(val_recs), "stats": prep})

bad = []
for r in train_recs[:200]:
    if not r.get("tools"):
        bad.append(("no_tools", r.get("source")))
    if not r.get("messages") or r["messages"][-1].get("role") != "assistant":
        bad.append(("bad_messages", r.get("source")))
    if "<tool_call>" not in r["messages"][-1].get("content", ""):
        bad.append(("no_tool_call_in_target", r.get("source")))
check("every record has tools, assistant target and a tool call", not bad, bad[:10])

# ---------------------------------------------------------------- 3. tokenising
print("\n### 3. tokenisation and label masking")
train_tok, tok_stats = D.prepare_tokenised(train_recs[:150], tok, max_prompt_tokens=2048, max_completion_tokens=512)
check("tokenisation keeps most records", len(train_tok) > 100, dict(tok_stats))

mask_ok, mask_detail = True, []
for ex in train_tok[:50]:
    labels = ex["labels"]
    n_sup = sum(1 for x in labels if x != -100)
    first_sup = next((i for i, x in enumerate(labels) if x != -100), None)
    if n_sup == 0 or first_sup == 0:
        mask_ok = False
        mask_detail.append({"n_supervised": n_sup, "first_supervised": first_sup})
check("prompt tokens masked, completion supervised", mask_ok and not mask_detail, mask_detail[:5])

lens = sorted(len(ex["input_ids"]) for ex in train_tok)
check("lengths within the configured window", lens[-1] <= 2560,
      {"p50": lens[len(lens) // 2], "p95": lens[int(0.95 * (len(lens) - 1))], "max": lens[-1]})

leak = D.check_bench_overlap(train_recs)
check("no bench leakage above threshold", not leak.get("leaks"), {k: v for k, v in leak.items() if k != "findings"})

audit = []
for i in [0, len(train_tok) // 2]:
    ex = train_tok[i]
    n_prompt = sum(1 for x in ex["labels"] if x == -100)
    n_sup = len(ex["labels"]) - n_prompt
    audit.append({"i": i, "n_tokens": len(ex["input_ids"]), "n_prompt": n_prompt, "n_supervised": n_sup,
                  "prompt_text": tok.decode(ex["input_ids"][:n_prompt])[:1200],
                  "target_text": tok.decode(ex["input_ids"][n_prompt:])[:600]})
save("audit_samples.json", audit)

# ---------------------------------------------------------------- 4. collator
print("\n### 4. collator")
collate = S.build_collator(tok, 2560)
batch = collate(train_tok[:3])
counts = batch["attention_mask"].sum(dim=1).tolist()
lens3 = [min(len(train_tok[i]["input_ids"]), 2560) for i in range(3)]
check("collator pads to a rectangle", batch["input_ids"].shape == batch["labels"].shape == batch["attention_mask"].shape,
      list(batch["input_ids"].shape))
check("attention mask counts real tokens", counts == lens3, {"mask": counts, "expected": lens3})
pad_positions_masked = bool((batch["labels"][batch["attention_mask"] == 0] == -100).all().item())
check("pad positions carry no loss", pad_positions_masked)

# ---------------------------------------------------------------- 5. trainer
print("\n### 5. TRL/PEFT wiring on a tiny random model")

from transformers import AutoModelForCausalLM
from datasets import Dataset

base = AutoModelForCausalLM.from_pretrained("hf-internal-testing/tiny-random-LlamaForCausalLM", dtype=torch.float32)
base.resize_token_embeddings(len(tok))  # let it read ids from the real LFM3 tokenizer
base.eval()

model, peft_cfg = S.attach_lora(base, r=8, alpha=16, dropout=0.05, target_modules=["q_proj", "v_proj"])
before = next(p.detach().clone() for n, p in model.named_parameters() if "lora_B" in n)

tiny = Dataset.from_list([{"input_ids": r["input_ids"], "labels": r["labels"]} for r in train_tok[:16]])
args = S.make_sft_config(
    output_dir=f"{OUT}/tiny_ckpt",
    max_steps=3,
    per_device_train_batch_size=1,
    gradient_accumulation_steps=1,
    learning_rate=1e-3,
    lr_scheduler_type="constant",
    warmup_steps=0,
    max_length=2560,
    logging_steps=1,
    save_strategy="no",
    report_to="none",
    gradient_checkpointing=False,
    remove_unused_columns=False,
    dataset_kwargs={"skip_prepare_dataset": True},
)
trainer = S.make_sft_trainer(model, args, tiny, collate, tok)
trainer.train()
losses = [h["loss"] for h in trainer.state.log_history if "loss" in h]
check("trainer runs 3 steps", len(losses) >= 3, losses)
check("losses are finite", all(x == x and abs(x) < 100 for x in losses), losses)

after = next(p.detach().clone() for n, p in model.named_parameters() if "lora_B" in n)
check("LoRA weights changed during training", not torch.equal(before, after),
      {"delta_abs_mean": float((after - before).abs().mean())})

trainer.save_model(f"{OUT}/tiny_adapter")
files = sorted(os.listdir(f"{OUT}/tiny_adapter"))
check("adapter saved", any(f.endswith(".safetensors") for f in files), files)

from peft import PeftModel

reload_base = AutoModelForCausalLM.from_pretrained("hf-internal-testing/tiny-random-LlamaForCausalLM",
                                                   dtype=torch.float32)
reload_base.resize_token_embeddings(len(tok))
reloaded = PeftModel.from_pretrained(reload_base, f"{OUT}/tiny_adapter")
check("adapter reloads into a fresh base", True,
      {"n_lora_modules": sum(1 for n, _ in reloaded.named_parameters() if "lora_" in n)})

# ---------------------------------------------------------------- report
ok = all(r["ok"] for r in RESULTS)
report = {
    "kind": "cpu-dryrun",
    "note": "Not an experiment. No benchmark number is produced here.",
    "ok": ok,
    "n_checks": len(RESULTS),
    "n_failed": sum(1 for r in RESULTS if not r["ok"]),
    "checks": RESULTS,
    "elapsed_s": round(time.time() - T0, 1),
    "versions": {"torch": torch.__version__, "transformers": transformers.__version__,
                 "trl": trl.__version__, "peft": peft.__version__, "datasets": datasets.__version__},
}
save("dryrun_report.json", report)
print("\nDRY-RUN:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT",
      f"({report['n_failed']}/{report['n_checks']} failed, {report['elapsed_s']}s)")
