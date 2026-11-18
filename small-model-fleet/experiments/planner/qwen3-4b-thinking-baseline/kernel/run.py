"""Experiment driver: planner baseline for one model.

Generic across the planner candidates: the only thing that changes between
experiments is the CONFIG block. Results are saved per case so a session that
dies mid-run still leaves usable evidence.
"""

import json
import os
import sys
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
    "experiment_id": "planner/qwen3-4b-thinking-2507-baseline",
    "model_id": "Qwen/Qwen3-4B-Thinking-2507",
    "revision": None,
    "trust_remote_code": False,
    "adapter_dir": None,
    "quant": "none",
    "suite": "planner",
    "max_new_tokens": 2048,
    "enable_thinking": True,
    "notes": "Unmodified Qwen3-4B-Thinking-2507 on frozen planner-v1. Greedy decoding, thinking enabled.",
}

OUT = f"/kaggle/working/{CONFIG['experiment_id'].replace('/', '__')}"
os.makedirs(OUT, exist_ok=True)

print("=" * 78)
print("EXPERIMENT", CONFIG["experiment_id"])
print("=" * 78)

env = K.record_environment()
print(json.dumps(env, indent=2))
K.save(OUT, "environment.json", env)

model, tokenizer, minfo, env = K.load_model(
    CONFIG["model_id"],
    revision=CONFIG["revision"],
    trust_remote_code=CONFIG["trust_remote_code"],
    quant=CONFIG["quant"],
    adapter_dir=CONFIG["adapter_dir"],
    env=env,
)
print(json.dumps(minfo, indent=2))

gen = K.Generator(model, tokenizer, max_new_tokens=CONFIG["max_new_tokens"],
                  name=CONFIG["model_id"], enable_thinking=CONFIG["enable_thinking"])
smoke = K.smoke_test(gen)
print("SMOKE:", json.dumps(smoke)[:600])

cases_doc, system_prompt, _ = K.runner.load_suite(CONFIG["suite"])
cases = cases_doc["cases"]

# incremental, crash-safe execution
records, per_case = [], []
t_start = __import__("time").time()
for i, case in enumerate(cases):
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
        print(f"[{i+1}/{len(cases)}] {r['case_id']} passed={r['passed']} fails={r['failures']} "
              f"lat={r.get('latency_s')}s tok={r.get('completion_tokens')} raw={(r['raw'] or '')[:120]!r}")
    # checkpoint every case so an interrupted session keeps its evidence
    K.save(OUT, "partial_results.json", {
        "config": CONFIG, "model": minfo, "environment": env,
        "progress": f"{i+1}/{len(cases)}",
        "metrics": K.runner.suite_metrics(records, per_case),
        "per_case": per_case, "records": records,
        "elapsed_s": round(__import__("time").time() - t_start, 1),
    })

metrics = K.runner.suite_metrics(records, per_case)
metrics["suite"] = cases_doc.get("suite")
metrics["suite_version"] = cases_doc.get("version")

payload = {
    "config": CONFIG,
    "model": minfo,
    "environment": env,
    "capabilities": {
        "tools_kwarg": K.sb.template_supports(tokenizer, "tools"),
        "native_tool_role": K.runner.probe_tool_role(tokenizer),
    },
    "smoke_test": smoke,
    "metrics": metrics,
    "per_case": per_case,
    "records": records,
    "peak_process_ram_gb": K.peak_ram_gb(),
    "elapsed_s": round(__import__("time").time() - t_start, 1),
}
K.save(OUT, "results.json", payload)
K.save(OUT, "per_case.json", per_case)

print("\n===== RAW ANSWERS =====")
for r in records:
    print(f"\n--- {r['case_id']} ({r['category']}) passed={r['passed']} parse={r['parse_status']}")
    print("FAILS:", r["failures"])
    print("ANSWER:", (r.get("answer") or "")[:900].replace("\n", " "))

K.print_summary(CONFIG["experiment_id"], metrics)
