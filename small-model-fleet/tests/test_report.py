"""Offline self-test for tools/report.py using synthetic result files.

Verifies the report renders every table, that deltas are computed with the right
direction, and that failing steps surface. No model, no network.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import report  # noqa: E402

TMP = tempfile.mkdtemp(dir=os.environ.get("SMF_TMP", None))
FAILS = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILS.append(name)


def make_run(exp_id, suite, case_rate, tool_rate, records, gpu="Tesla T4", gpu_mem=15.6):
    return {
        "config": {"experiment_id": exp_id, "suite": suite},
        "model": {"model_id": "LiquidAI/LFM3-1.7B", "revision_resolved": "1da06bec6a819da10b54c67e5c90972b46d81871"},
        "environment": {"gpu_names": [gpu], "gpu_mem_gb": [gpu_mem]},
        "metrics": {
            "suite": suite,
            "cases": 22,
            "case_success_rate": case_rate,
            "instruction_following_accuracy": case_rate,
            "tool_selection_accuracy": tool_rate,
            "argument_validity": 0.8,
            "parse_validity": 0.9,
            "false_completion_rate": 0.2 if case_rate < 0.5 else 0.05,
            "permission_violation_rate": 0.0,
            "recovery_success_rate": case_rate,
            "avg_latency_s": 4.5,
            "p95_latency_s": 9.0,
            "avg_tokens_per_s": 38.0,
            "peak_vram_gb": 3.3,
            "total_completion_tokens": 8000,
            "category_breakdown": {
                "tool_selection": {"passed": int(case_rate * 3), "total": 3},
                "permission_boundary": {"passed": 2, "total": 2},
            },
        },
        "per_case": [],
        "records": records,
        "elapsed_s": 900,
    }


records = [
    {"case_id": "S07", "step": 1, "category": "failure_detection", "passed": False,
     "failures": ["must_not_tool: forbidden call(s) ['finish']"]},
    {"case_id": "S01", "step": 0, "category": "tool_selection", "passed": True, "failures": []},
]

base = make_run("agent-worker/lfm3-17b-baseline", "specialist", 0.30, 0.45, records)
tuned = make_run("agent-worker/lfm3-17b-sft-v1", "specialist", 0.64, 0.88, records)
docs = [base, tuned]

print("== build ==")
out = report.build(docs, TMP)
md = open(out, encoding="utf-8").read()

check("provenance has both runs", md.count("`agent-worker/") >= 2)
check("specialist headline present", "Specialist worker" in md)
check("baseline row rendered", "lfm3-17b-baseline" in md)
check("rate formatted as percent", "30.0%" in md and "64.0%" in md)
check("delta section reached", "fine-tuned vs original" in md.lower())
check("delta is positive for success", "+34.0 pp" in md)
check("delta treats lower false-completion as better", "5.0%" in md and "-15.0 pp | yes" in md)
check("gpu name surfaced", "Tesla T4" in md)
check("failing step listed", "S07" in md and "forbidden call" in md)
check("empty planner section is explicit", "No runs recorded for this suite yet" in md)

cmp_path = os.path.join(TMP, "experiments", "comparisons", "model-comparison.md")
check("comparison doc written", os.path.exists(cmp_path) and os.path.getsize(cmp_path) > 0)

print("== missing-data behaviour ==")
out2 = report.build([], TMP)
md2 = open(out2, encoding="utf-8").read()
check("no-data run still writes a file", "No runs yet" in md2)
check("no-data run does not invent numbers", "%" not in md2.split("## Planner")[1].split("## Specialist")[0])

print("== planner winner selection ==")
p1 = make_run("planner/qwen3-4b-thinking-2507-baseline", "planner", 0.62, 0.62, [])
p2 = make_run("planner/qwen35-4b-baseline", "planner", 0.71, 0.71, [])
report.build([p1, p2], TMP)
cmp_md2 = open(cmp_path, encoding="utf-8").read()
check("picks higher planner", "qwen35-4b-baseline" in cmp_md2.split("Highest measured case success rate")[1][:200])
check("comparison states the significance caveat", "< 10 pp" in cmp_md2)

print("== find_results dedupe ==")
d = os.path.join(TMP, "runA")
os.makedirs(d, exist_ok=True)
json.dump(base, open(os.path.join(d, "results.json"), "w"))
found = report.find_results([d, TMP])
check("find_results locates nested results", any(str(d.get("_path","")).replace(chr(92),"/").endswith("runA/results.json") for d in found))
check("find_results dedupes", len(found) == len({d["_path"] for d in found}))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nFAILURES: {len(FAILS)}")
sys.exit(1 if FAILS else 0)

