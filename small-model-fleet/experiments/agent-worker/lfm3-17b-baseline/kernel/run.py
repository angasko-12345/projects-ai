"""Experiment driver: LFM3-1.7B specialist baseline.

Run on Kaggle (GPU). Produces the reference numbers that every fine-tuned
adapter must beat on the identical held-out suite.
"""

import json
import os
import sys


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
    "experiment_id": "agent-worker/lfm3-17b-baseline",
    "model_id": "LiquidAI/LFM3-1.7B",
    "revision": None,
    "trust_remote_code": True,
    "adapter_dir": None,
    "quant": "none",
    "suite": "specialist",
    "max_new_tokens": 512,
    "enable_thinking": False,
    "notes": "Unmodified LFM3-1.7B on the frozen specialist-v1 suite. enable_thinking=False to match the SFT recipe and keep the tool-call comparison clean.",
}

OUT = f"/kaggle/working/{CONFIG['experiment_id'].replace('/', '__')}"

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

gen = K.Generator(model, tokenizer, max_new_tokens=CONFIG["max_new_tokens"], name=CONFIG["model_id"],
                  enable_thinking=CONFIG["enable_thinking"])

smoke = K.smoke_test(gen)
print("SMOKE:", json.dumps(smoke)[:800])

caps = {
    "tools_kwarg": K.sb.template_supports(tokenizer, "tools"),
    "native_tool_role": K.runner.probe_tool_role(tokenizer),
}
print("CAPS:", caps)

result = K.run_suite(
    gen,
    CONFIG["suite"],
    max_new_tokens=CONFIG["max_new_tokens"],
    tools_kwarg=caps["tools_kwarg"],
    native_tool_role=caps["native_tool_role"],
)

payload = {
    "config": CONFIG,
    "model": minfo,
    "environment": env,
    "capabilities": caps,
    "smoke_test": smoke,
    "metrics": result["metrics"],
    "per_case": result["per_case"],
    "records": result["records"],
    "peak_process_ram_gb": K.peak_ram_gb(),
}
K.save(OUT, "results.json", payload)
K.save(OUT, "per_case.json", result["per_case"])

print("\n===== RAW PREDICTIONS =====")
for r in result["records"]:
    print(f"\n--- {r['case_id']} step {r['step']} ({r['category']}) passed={r['passed']} tools={r['called_tools']}")
    print("FAILS:", r["failures"])
    print("RAW:", (r["raw"] or "")[:700].replace("\n", " | "))

K.print_summary(CONFIG["experiment_id"], result["metrics"])


