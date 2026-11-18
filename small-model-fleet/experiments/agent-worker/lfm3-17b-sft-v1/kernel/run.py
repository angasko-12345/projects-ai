"""Experiment driver: LFM3-1.7B SFT (pilot stage + full run + post-training eval).

One session does everything so quota is not spent on repeated base-model loads:
  stage A  prepare and validate the training data
  stage B  pre-training validation loss + tool-call exact match reference
  stage C  pilot run (bounded steps) -> throughput, VRAM, loss behaviour, snapshot
  stage D  full run with the schedule chosen from the pilot
  stage E  verify the saved adapter reloads, then evaluate it on the frozen suite
"""

import glob
import json
import os
import sys
import time
import traceback


def bench_src():
    """Locate the mounted bench dataset regardless of Kaggle's input layout.

    Older images mount a dataset source at /kaggle/input/<slug>; newer ones nest it
    under /kaggle/input/datasets/<owner>/<slug>. Hard-coding either is a time bomb,
    so the directory that actually contains smf_bench.py wins.
    """
    base = "/kaggle/input"
    for root, dirs, files in os.walk(base):
        if "smf_bench.py" in files:
            return root
        if root[len(base):].count(os.sep) >= 4:
            dirs[:] = []
    return None


BENCH_SRC = bench_src()
if BENCH_SRC is None:
    raise SystemExit("smf-bench was not found anywhere under /kaggle/input")
os.environ["SMF_BENCH_ROOT"] = os.path.dirname(BENCH_SRC)  # smf_runner reads this at import time
sys.path.insert(0, BENCH_SRC)
print("bench harness:", BENCH_SRC)

# --- pinned to the versions in Liquid's official LFM3 cookbook -----------------
INSTALL = (
    'pip install -qqq "trl==1.16.0" "transformers==5.20.2" "peft==0.21.2" '
    '"accelerate==1.15.0" "datasets==5.1.0" "huggingface-hub==2.3.0" '
    '"tokenizers==0.23.3" "safetensors==0.8.0" --progress-bar off'
)
rc = os.system(INSTALL)
print("pip rc:", rc)

import smf_kaggle as K
import smf_data as D
import smf_sft as S

CONFIG = {
    "experiment_id": "agent-worker/lfm3-17b-sft-v1",
    "model_id": "LiquidAI/LFM3-1.7B",
    "trust_remote_code": True,
    "suite": "specialist",
    "enable_thinking": False,
    "eval_max_new_tokens": 512,
    "xlam_limit": 2600,
    "seed": 7,
    "pilot_steps": 60,
    "target_epochs": 2,
    "max_steps_cap": 700,
    "per_device_batch": 2,
    "grad_accum": 8,
    "lr": 2e-4,
    "lora_r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "max_prompt_tokens": 2048,
    "max_completion_tokens": 512,
    "time_budget_min": 300,
    "notes": "LoRA SFT per Liquid's lfm3_sft_with_trl recipe: same target modules and LR, seq length reduced, completion-only loss.",
}

OUT = f"/kaggle/working/{CONFIG['experiment_id'].replace('/', '__')}"
os.makedirs(OUT, exist_ok=True)
RUN_START = time.time()

print("=" * 78)
print("EXPERIMENT", CONFIG["experiment_id"])
print("=" * 78)

import torch

env = K.record_environment()
print(json.dumps(env, indent=2))
K.save(OUT, "environment.json", env)

# ---------------------------------------------------------------- load model
model, tokenizer, minfo, env = K.load_model(CONFIG["model_id"], trust_remote_code=True, env=env)
# right padding for training (left padding would put pad tokens ahead of the
# supervised span); generation switches this back to left in the Generator.
tokenizer.padding_side = "right"
print(json.dumps(minfo, indent=2))
K.save(OUT, "model.json", minfo)

# ---------------------------------------------------------------- stage A
print("\n### STAGE A: data preparation")
sources = D.fetch_sources()
sources_meta = {
    "xlam": {"repo": "lockon/xlam-function-calling-60k (mirror of Salesforce/xlam-function-calling-60k)", "license": "cc-by-4.0", "file": "xlam_function_calling_60k.json"},
    "hermes": {"repo": "NousResearch/hermes-function-calling-v1", "license": "apache-2.0", "file": "func-calling.json"},
}
train_recs, val_recs, prep_stats = D.build_dataset(sources, xlam_limit=CONFIG["xlam_limit"], seed=CONFIG["seed"])
print("prep stats:", json.dumps(prep_stats, indent=2))
print(f"records: train={len(train_recs)} val={len(val_recs)}")

train_tok, train_tok_stats = D.prepare_tokenised(
    train_recs, tokenizer,
    max_prompt_tokens=CONFIG["max_prompt_tokens"],
    max_completion_tokens=CONFIG["max_completion_tokens"],
)
val_tok, val_tok_stats = D.prepare_tokenised(
    val_recs, tokenizer,
    max_prompt_tokens=CONFIG["max_prompt_tokens"],
    max_completion_tokens=CONFIG["max_completion_tokens"],
)
print("tokenised train:", dict(train_tok_stats), "->", len(train_tok))
print("tokenised val:", dict(val_tok_stats), "->", len(val_tok))

if len(train_tok) < 50 or len(val_tok) < 4:
    K.save(OUT, "dataset_failure.json", {
        "config": CONFIG, "prep_stats": prep_stats,
        "n_train": len(train_tok), "n_val": len(val_tok),
        "token_stats_train": dict(train_tok_stats), "token_stats_val": dict(val_tok_stats),
        "reason": "too few usable records to train; stopping before any GPU training",
    })
    raise SystemExit("dataset too small after tokenisation - see dataset_failure.json")

man = D.manifest(train_recs, val_recs, prep_stats, sources_meta)
man["leakage_check"] = D.check_bench_overlap(train_recs)
print("leakage:", json.dumps({k: v for k, v in man["leakage_check"].items() if k != "findings"}))
man["token_stats_train"] = dict(train_tok_stats)
man["token_stats_val"] = dict(val_tok_stats)
man["n_train_tokenised"] = len(train_tok)
man["n_val_tokenised"] = len(val_tok)
lens = [len(x["input_ids"]) for x in train_tok]
lens.sort()
man["token_len_p50"] = lens[len(lens) // 2]
man["token_len_p95"] = lens[int(0.95 * (len(lens) - 1))]
man["config"] = CONFIG
K.save(OUT, "dataset_manifest.json", man)

# audit samples: a human-readable trace of what the model actually sees
def audit_render(rec):
    kwargs = dict(tools=rec["tools"], tokenize=False, add_generation_prompt=True)
    try:
        return tokenizer.apply_chat_template(rec["messages"][:-1], enable_thinking=False, **kwargs)
    except TypeError:
        return tokenizer.apply_chat_template(rec["messages"][:-1], **kwargs)


audit = []
for i in [0, len(train_recs) // 3, len(train_recs) // 2]:
    rec = train_recs[i]
    ex, why = D.to_prompt_completion(rec, tokenizer, CONFIG["max_prompt_tokens"], CONFIG["max_completion_tokens"])
    audit.append({
        "source": rec["source"],
        "render_status": why,
        "rendered_prompt": audit_render(rec),
        "completion": rec["messages"][-1]["content"],
"n_prompt_tokens": sum(1 for label in ex["labels"] if label == -100) if ex else None,
        "n_supervised_tokens": sum(1 for label in ex["labels"] if label != -100) if ex else None,
    })
K.save(OUT, "audit_samples.json", audit)

# ---------------------------------------------------------------- stage B
print("\n### STAGE B: pre-training reference on the validation split")

MAX_LEN = CONFIG["max_prompt_tokens"] + CONFIG["max_completion_tokens"]
collate = S.build_collator(tokenizer, MAX_LEN)

val_batches = [collate(val_tok[i:i + 2]) for i in range(0, min(len(val_tok), 48), 2)]
pre_loss = S.val_loss(model, val_batches, device="cuda", n=12)
print("pre-training val loss:", round(pre_loss, 4))
K.save(OUT, "stage_b.json", {"val_loss_pre": pre_loss, "n_val_batches": len(val_batches)})

# ---------------------------------------------------------------- stage C/D
peft_config_doc = {
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "in_proj", "out_proj", "w1", "w2", "w3"],
    "r": CONFIG["lora_r"], "alpha": CONFIG["lora_alpha"], "dropout": CONFIG["lora_dropout"],
}
model, _ = S.attach_lora(
    model, CONFIG["lora_r"], CONFIG["lora_alpha"], CONFIG["lora_dropout"], peft_config_doc["target_modules"]
)
model.print_trainable_parameters()
n_trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
n_total = sum(p.numel() for p in model.parameters())
K.save(OUT, "lora_setup.json", {**peft_config_doc, "n_trainable": n_trainable, "n_total": n_total,
                                 "trainable_pct": round(100 * n_trainable / n_total, 4)})

from datasets import Dataset

train_ds = Dataset.from_list([{"input_ids": r["input_ids"], "labels": r["labels"]} for r in train_tok])
print("train dataset:", train_ds)

use_bf16 = torch.cuda.is_bf16_supported(including_emulation=False)
print("bf16 supported:", use_bf16)


def make_trainer(max_steps, out_dir, epochs):
    args = S.make_sft_config(
        output_dir=out_dir,
        max_steps=max_steps,
        num_train_epochs=epochs,
        per_device_train_batch_size=CONFIG["per_device_batch"],
        gradient_accumulation_steps=CONFIG["grad_accum"],
        learning_rate=CONFIG["lr"],
        lr_scheduler_type="cosine",
        warmup_steps=max(1, int(0.05 * max_steps)),
        max_length=MAX_LEN,
        logging_steps=5,
        save_strategy="steps",
        save_steps=max(50, max_steps // 3),
        save_total_limit=2,
        bf16=use_bf16,
        fp16=not use_bf16,
        report_to="none",
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        remove_unused_columns=False,
        dataset_kwargs={"skip_prepare_dataset": True},
    )
    return S.make_sft_trainer(model, args, train_ds, collate, tokenizer)


print("\n### STAGE C: pilot run")
pilot_log = {"pilot_steps": CONFIG["pilot_steps"], "failed": False}
try:
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    pilot = make_trainer(max_steps=CONFIG["pilot_steps"], out_dir=f"{OUT}/pilot_ckpt", epochs=1)
    pilot.train()
    pilot_secs = time.time() - t0
    pilot_log.update({
        "pilot_secs": round(pilot_secs, 1),
        "sec_per_step": round(pilot_secs / CONFIG["pilot_steps"], 3),
        "examples_per_sec": round(CONFIG["pilot_steps"] * CONFIG["per_device_batch"] * CONFIG["grad_accum"] / pilot_secs, 2),
        "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
        "loss_history": [h for h in pilot.state.log_history if "loss" in h],
    })
    pilot.save_model(f"{OUT}/pilot_adapter")
    pilot_log["adapter_bytes"] = sum(
        os.path.getsize(os.path.join(f"{OUT}/pilot_adapter", f)) for f in os.listdir(f"{OUT}/pilot_adapter")
    )
    pilot_log["val_loss_after_pilot"] = S.val_loss(pilot.model, val_batches, device="cuda", n=12)
except Exception:
    pilot_log["failed"] = True
    pilot_log["error"] = traceback.format_exc()[-3000:]
    traceback.print_exc()
K.save(OUT, "stage_c_pilot.json", pilot_log)
print(json.dumps({k: v for k, v in pilot_log.items() if k != "loss_history"}, indent=2))

# ---------------------------------------------------------------- stage D
train_log = {"failed": True, "reason": "pilot stage failed"}
best_adapter = f"{OUT}/pilot_adapter" if pilot_log.get("adapter_bytes") else None

if not pilot_log["failed"]:
    steps_per_epoch = max(1, len(train_tok) // (CONFIG["per_device_batch"] * CONFIG["grad_accum"]))
    target_steps = min(CONFIG["max_steps_cap"], steps_per_epoch * CONFIG["target_epochs"])
    est_secs = target_steps * pilot_log["sec_per_step"]
    remaining_budget_s = CONFIG["time_budget_min"] * 60 - (time.time() - RUN_START)
    # 0.65 leaves room for the post-training eval, which is the whole point of the run
    budget_steps = int(remaining_budget_s * 0.65 / max(pilot_log["sec_per_step"], 0.05))
    full_steps = int(min(target_steps, max(40, budget_steps)))
    print(f"\n### STAGE D: full run  steps_per_epoch={steps_per_epoch} target={target_steps} "
          f"est={est_secs/60:.1f}min budget_steps={budget_steps} chosen={full_steps}")
    if budget_steps < 40:
        train_log = {"failed": False, "skipped": True,
                     "reason": f"only {remaining_budget_s/60:.0f} min left, too few steps to be worth it"}
        print("skipping stage D:", train_log["reason"])
    else:
        try:
            torch.cuda.reset_peak_memory_stats()
            t0 = time.time()
            trainer = make_trainer(max_steps=full_steps, out_dir=f"{OUT}/full_ckpt", epochs=CONFIG["target_epochs"])
            trainer.train()
            full_secs = time.time() - t0
            trainer.save_model(f"{OUT}/final_adapter")
            best_adapter = f"{OUT}/final_adapter"
            train_log = {
                "failed": False,
                "full_steps": full_steps,
                "steps_per_epoch": steps_per_epoch,
                "full_secs": round(full_secs, 1),
                "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
                "val_loss_pre": pre_loss,
                "val_loss_post": S.val_loss(trainer.model, val_batches, device="cuda", n=12),
                "loss_history": [h for h in trainer.state.log_history if "loss" in h],
                "trainable_params": sum(p.numel() for p in trainer.model.parameters() if p.requires_grad),
                "total_params": sum(p.numel() for p in trainer.model.parameters()),
                "effective_batch": CONFIG["per_device_batch"] * CONFIG["grad_accum"],
            }
        except Exception:
            train_log = {"failed": True, "error": traceback.format_exc()[-3000:], "chosen_steps": full_steps}
            traceback.print_exc()
            # whatever the last checkpoint wrote is still evaluable
            ckpts = sorted(glob.glob(f"{OUT}/full_ckpt/checkpoint-*"), key=lambda p: int(p.rsplit("-", 1)[-1]))
            if ckpts:
                best_adapter = ckpts[-1]
K.save(OUT, "stage_d_train.json", train_log)
print(json.dumps({k: v for k, v in train_log.items() if k != "loss_history"}, indent=2))

# ---------------------------------------------------------------- stage E
# Evaluate the artifact that was actually saved, reloaded into a fresh base. That
# is what downstream use looks like, and it makes "the number came from this
# adapter directory" true by construction rather than by assumption.
print("\n### STAGE E: reload the saved adapter and evaluate on the frozen suite")

import gc

adapter_check = {"adapter_dir": best_adapter, "reload_ok": False}
eval_model = model
eval_source = "in-memory training model (no adapter directory was saved)"

if best_adapter:
    # free the training copy first: two 1.7B models plus optimizer state do not
    # comfortably fit a 16 GB card
    for _name in ("trainer", "pilot"):
        if _name in globals():
            del globals()[_name]
    del model
    gc.collect()
    torch.cuda.empty_cache()
    try:
        from peft import PeftModel

        base2, _, _, _ = K.load_model(CONFIG["model_id"], trust_remote_code=True, env=env)
        eval_model = PeftModel.from_pretrained(base2, best_adapter)
        eval_model.eval()
        adapter_check["reload_ok"] = True
        adapter_check["files"] = sorted(os.listdir(best_adapter))
        adapter_check["bytes"] = sum(os.path.getsize(os.path.join(best_adapter, f))
                                     for f in adapter_check["files"])
        eval_source = f"reloaded {best_adapter}"
    except Exception:
        adapter_check["error"] = traceback.format_exc()[-2500:]
        traceback.print_exc()
        eval_source = "reload failed - evaluation skipped"
K.save(OUT, "stage_e_adapter_check.json", adapter_check)
print("eval source:", eval_source)

gen = K.Generator(eval_model, tokenizer, max_new_tokens=CONFIG["eval_max_new_tokens"],
                  name=CONFIG["model_id"] + "+lora-v1", enable_thinking=CONFIG["enable_thinking"])
eval_model.eval()
caps = {
    "tools_kwarg": K.sb.template_supports(tokenizer, "tools"),
    "native_tool_role": K.runner.probe_tool_role(tokenizer),
}
smoke = K.smoke_test(gen)
print("SMOKE:", json.dumps(smoke)[:500])
print("CAPS:", caps)

eval_error = None
result = {"metrics": {}, "per_case": [], "records": []}
if adapter_check["reload_ok"] or not best_adapter:
    try:
        result = K.run_suite(gen, CONFIG["suite"], max_new_tokens=CONFIG["eval_max_new_tokens"],
                             tools_kwarg=caps["tools_kwarg"], native_tool_role=caps["native_tool_role"])
    except Exception:
        eval_error = traceback.format_exc()[-3000:]
        traceback.print_exc()
else:
    eval_error = "adapter reload failed; see stage_e_adapter_check.json"

payload = {
    "config": CONFIG,
    "model": minfo,
    "environment": env,
    "dataset_manifest": man,
    "lora_setup": {"n_trainable": n_trainable, "n_total": n_total,
                   "trainable_pct": round(100 * n_trainable / n_total, 4)},
    "pilot": pilot_log,
    "train": train_log,
    "best_adapter": best_adapter,
    "adapter_check": adapter_check,
    "eval_source": eval_source,
    "eval_error": eval_error,
    "capabilities": caps,
    "smoke_test": smoke,
    "metrics": result["metrics"],
    "per_case": result["per_case"],
    "records": result["records"],
    "peak_process_ram_gb": K.peak_ram_gb(),
    "total_elapsed_min": round((time.time() - RUN_START) / 60, 1),
}
K.save(OUT, "results.json", payload)
K.save(OUT, "per_case.json", result["per_case"])

print("\n===== RAW PREDICTIONS =====")
for r in result["records"]:
    print(f"\n--- {r['case_id']} step {r['step']} ({r['category']}) passed={r['passed']} tools={r['called_tools']}")
    print("FAILS:", r["failures"])
    print("RAW:", (r["raw"] or "")[:600].replace("\n", " | "))

K.print_summary(CONFIG["experiment_id"], result["metrics"])
print("\nARTIFACTS:")
for root, _dirs, files in os.walk(OUT):
    for f in files:
        p = os.path.join(root, f)
        print(f"  {p}  {os.path.getsize(p)} bytes")
