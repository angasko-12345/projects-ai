"""Experiment driver: planner baseline for Qwen3.5-4B.

Qwen3.5-4B is a *multimodal* checkpoint (`Qwen3_5ForConditionalGeneration`,
pipeline_tag image-text-to-text). `AutoModelForCausalLM` may not resolve, so the
loader tries several auto classes in order and records which one worked.

The suite is executed with resume support: `SKIP_CASES` lets a slow or interrupted
run continue in a later session without redoing paid work.
"""

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

import smf_kaggle as K

CONFIG = {
    "experiment_id": "planner/qwen35-4b-baseline",
    "model_id": "Qwen/Qwen3.5-4B",
    "revision": None,
    "suite": "planner",
    "max_new_tokens": 1536,
    "enable_thinking": True,
    "notes": "Unmodified Qwen3.5-4B on frozen planner-v1. Greedy. Thinking is on by default for Qwen3.5; "
             "it is left on so the planner is measured as designed. Cap is 1536 new tokens (lower than the "
             "Qwen3-4B cap of 2048) because this is a multimodal-hybrid stack and decode is the cost driver.",
}

# case ids already completed in an earlier session (resume support)
SKIP_CASES = []

OUT = f"/kaggle/working/{CONFIG['experiment_id'].replace('/', '__')}"
os.makedirs(OUT, exist_ok=True)
T0 = time.time()

print("=" * 78)
print("EXPERIMENT", CONFIG["experiment_id"])
print("=" * 78)


env = K.record_environment()
print(json.dumps(env, indent=2))
K.save(OUT, "environment.json", env)


def load_any_auto_class(model_id, revision, env):
    """Try the auto classes that can host a qwen3_5 multimodal checkpoint."""
    from transformers import AutoConfig, AutoTokenizer
    import transformers

    dt = K.dtype_for(env)
    errors = []
    candidates = []
    for name in ["AutoModelForImageTextToText", "AutoModelForCausalLM", "AutoModelForVision2Seq"]:
        cls = getattr(transformers, name, None)
        if cls is not None:
            candidates.append((name, cls))

    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    cfg = AutoConfig.from_pretrained(model_id, revision=revision)
    declared = getattr(cfg, "architectures", None)
    print("declared architectures:", declared, "| model_type:", getattr(cfg, "model_type", None))

    for name, cls in candidates:
        try:
            print(f"trying {name} ...")
            model = cls.from_pretrained(model_id, revision=revision, dtype=dt, device_map={"": 0})
            model.eval()
            print(f"LOADED with {name}")
            return model, tokenizer, {"loaded_with": name, "declared_architectures": declared}
        except Exception:
            errors.append({name: traceback.format_exc()[-1200:]})
            print(f"{name} failed")
    raise RuntimeError("no auto class could load the checkpoint: " + json.dumps(errors)[:3000])


load_meta = {}
try:
    model, tokenizer, load_meta = load_any_auto_class(CONFIG["model_id"], CONFIG["revision"], env)
except Exception:
    K.save(OUT, "load_failure.json", {"config": CONFIG, "environment": env, "error": traceback.format_exc()})
    print("LOAD FAILED — see load_failure.json")
    sys.exit(0)  # exit clean so the kernel records COMPLETE with a documented failure

from huggingface_hub import HfApi

resolved = None
try:
    resolved = HfApi().model_info(CONFIG["model_id"], revision=CONFIG["revision"]).sha
except Exception:
    pass

minfo = {
    "model_id": CONFIG["model_id"],
    "revision_requested": CONFIG["revision"],
    "revision_resolved": resolved,
    "n_params": sum(p.numel() for p in model.parameters()),
    "load": load_meta,
}
print(json.dumps(minfo, indent=2))
K.save(OUT, "model.json", minfo)

gen = K.Generator(model, tokenizer, max_new_tokens=CONFIG["max_new_tokens"],
                  name=CONFIG["model_id"], enable_thinking=CONFIG["enable_thinking"])
smoke = K.smoke_test(gen)
print("SMOKE:", json.dumps(smoke)[:600])
tps = (smoke.get("rec") or {}).get("tokens_per_s")
print("smoke tokens/s:", tps)

cases_doc, system_prompt, _ = K.runner.load_suite(CONFIG["suite"])
todo = [c for c in cases_doc["cases"] if c["id"] not in SKIP_CASES]
print(f"running {len(todo)} of {len(cases_doc['cases'])} cases")

records, per_case = [], []
for i, case in enumerate(todo):
    try:
        recs, pcs = K.runner.run_planner_suite(
            gen, cases_doc, [case], system_prompt, max_new_tokens=CONFIG["max_new_tokens"]
        )
    except Exception:
        traceback.print_exc()
        recs, pcs = [], [{"case_id": case["id"], "category": case["category"], "passed": False, "error": True}]
    records.extend(recs)
    per_case.extend(pcs)
    for r in recs:
        print(f"[{i+1}/{len(todo)}] {r['case_id']} passed={r['passed']} fails={r['failures']} "
              f"lat={r.get('latency_s')}s tok={r.get('completion_tokens')} tps={r.get('tokens_per_s')}")
    K.save(OUT, "partial_results.json", {
        "config": CONFIG, "model": minfo, "environment": env,
        "completed_case_ids": [c["case_id"] for c in per_case],
        "progress": f"{i+1}/{len(todo)}",
        "metrics": K.runner.suite_metrics(records, per_case),
        "per_case": per_case, "records": records,
        "elapsed_s": round(time.time() - T0, 1),
    })

metrics = K.runner.suite_metrics(records, per_case)
metrics["suite"] = cases_doc.get("suite")
metrics["suite_version"] = cases_doc.get("version")
metrics["partial_suite"] = bool(SKIP_CASES)

payload = {
    "config": CONFIG,
    "model": minfo,
    "environment": env,
    "smoke_test": smoke,
    "metrics": metrics,
    "per_case": per_case,
    "records": records,
    "peak_process_ram_gb": K.peak_ram_gb(),
    "elapsed_s": round(time.time() - T0, 1),
}
K.save(OUT, "results.json", payload)
K.save(OUT, "per_case.json", per_case)

print("\n===== RAW ANSWERS =====")
for r in records:
    print(f"\n--- {r['case_id']} ({r['category']}) passed={r['passed']} parse={r['parse_status']}")
    print("FAILS:", r["failures"])
    print("ANSWER:", (r.get("answer") or "")[:800].replace("\n", " "))

K.print_summary(CONFIG["experiment_id"], metrics)
