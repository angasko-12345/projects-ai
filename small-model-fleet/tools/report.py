"""Build the benchmark report from downloaded experiment result files.

Usage:
    python tools/report.py --inputs <dir> [<dir> ...] --out <project root>

Every figure written into BENCHMARK_RESULTS.md comes from a `results.json`
produced by a Kaggle run. Nothing is hand-typed, so the report cannot drift from
the evidence. Missing or partial runs are reported as such rather than skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

METRIC_ROWS = [
    ("case_success_rate", "Case success rate"),
    ("instruction_following_accuracy", "Instruction following"),
    ("tool_selection_accuracy", "Tool selection"),
    ("argument_validity", "Argument validity"),
    ("parse_validity", "Parse validity"),
    ("false_completion_rate", "False completion"),
    ("permission_violation_rate", "Permission violation"),
    ("recovery_success_rate", "Recovery success"),
    ("avg_latency_s", "Avg latency (s)"),
    ("p95_latency_s", "P95 latency (s)"),
    ("avg_tokens_per_s", "Tokens/s"),
    ("peak_vram_gb", "Peak VRAM (GB)"),
    ("total_completion_tokens", "Completion tokens"),
]

LOWER_IS_BETTER = {"false_completion_rate", "permission_violation_rate", "avg_latency_s", "p95_latency_s", "total_completion_tokens"}


def find_results(paths):
    found = []
    for p in paths:
        if os.path.isfile(p) and p.endswith(".json"):
            found.append(p)
            continue
        for root, _dirs, files in os.walk(p):
            if "results.json" in files:
                found.append(os.path.join(root, "results.json"))
    out = []
    for f in sorted(set(found)):
        try:
            with open(f, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as e:
            print(f"skip unreadable {f}: {e}", file=sys.stderr)
            continue
        if "metrics" not in doc or "config" not in doc:
            print(f"skip non-result {f}", file=sys.stderr)
            continue
        doc["_path"] = f
        doc["_dir"] = os.path.dirname(f)
        out.append(doc)
    return out


def gpu_of(doc):
    env = doc.get("environment") or {}
    names = env.get("gpu_names") or []
    mem = env.get("gpu_mem_gb") or []
    if not names:
        return "cpu / unknown"
    if len(names) == 1:
        return f"{names[0]} ({mem[0]} GB)" if mem else names[0]
    return f"{len(names)}x {names[0]}" + (f" ({mem[0]} GB each)" if mem else "")


def fmt(v, key=""):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if key.endswith("_rate") or key.endswith("_accuracy") or "validity" in key:
            return f"{v*100:.1f}%"
        return f"{v:g}"
    return str(v)


def suite_of(doc):
    return ((doc.get("metrics") or {}).get("suite")) or ("planner" if "planner" in doc["config"].get("experiment_id", "") else "specialist")


def label_of(doc):
    return doc["config"].get("experiment_id") or doc.get("_path") or "unknown"


def headline_table(docs, suite):
    rows = [d for d in docs if suite_of(d) == suite]
    if not rows:
        return "_No runs recorded for this suite yet._\n"
    header = ["Experiment", "Model", "Revision", "Cases", "Success", "Tool sel.", "Args valid", "Parse", "False comp.", "Perm. viol.", "Latency s", "Tok/s"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for d in rows:
        m = d.get("metrics") or {}
        model = (d.get("model") or {}).get("model_id", "?")
        rev = ((d.get("model") or {}).get("revision_resolved") or "")[:12] or "—"
        lines.append("| " + " | ".join([
            f"`{label_of(d)}`",
            f"`{model}`",
            rev,
            str(m.get("cases", "?")),
            fmt(m.get("case_success_rate"), "case_success_rate"),
            fmt(m.get("tool_selection_accuracy"), "tool_selection_accuracy"),
            fmt(m.get("argument_validity"), "argument_validity"),
            fmt(m.get("parse_validity"), "parse_validity"),
            fmt(m.get("false_completion_rate"), "false_completion_rate"),
            fmt(m.get("permission_violation_rate"), "permission_violation_rate"),
            fmt(m.get("avg_latency_s"), "avg_latency_s"),
            fmt(m.get("avg_tokens_per_s"), "avg_tokens_per_s"),
        ]) + " |")
    return "\n".join(lines) + "\n"


def category_table(docs, suite):
    rows = [d for d in docs if suite_of(d) == suite]
    if not rows:
        return ""
    cats = sorted({c for d in rows for c in ((d.get("metrics") or {}).get("category_breakdown") or {})})
    if not cats:
        return ""
    header = ["Category"] + [label_of(d).split("/")[-1] for d in rows]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for c in cats:
        cells = []
        for d in rows:
            b = ((d.get("metrics") or {}).get("category_breakdown") or {}).get(c)
            cells.append(f"{b['passed']}/{b['total']}" if b else "—")
        lines.append("| " + " | ".join([f"`{c}`"] + cells) + " |")
    return "\n".join(lines) + "\n"


def delta_table(baseline, tuned):
    """Compare a tuned run against its baseline using absolute rate deltas."""
    if not baseline or not tuned:
        return ""
    b, t = baseline.get("metrics") or {}, tuned.get("metrics") or {}
    keys = [k for k in ["case_success_rate", "tool_selection_accuracy", "argument_validity", "parse_validity",
                        "false_completion_rate", "permission_violation_rate", "recovery_success_rate",
                        "avg_latency_s", "avg_tokens_per_s"] if b.get(k) is not None and t.get(k) is not None]
    if not keys:
        return ""
    lines = ["| Metric | Baseline | Fine-tuned | Delta | Better? |", "|---|---|---|---|---|"]
    for k in keys:
        bv, tv = b[k], t[k]
        try:
            delta = tv - bv
        except TypeError:
            continue
        if k.endswith("rate") or "accuracy" in k or "validity" in k:
            better = delta > 0 if k not in LOWER_IS_BETTER else delta < 0
            lines.append(f"| {k} | {bv*100:.1f}% | {tv*100:.1f}% | {delta*100:+.1f} pp | {'yes' if better else 'no' if delta else 'same'} |")
        else:
            better = delta < 0 if k in LOWER_IS_BETTER else delta > 0
            lines.append(f"| {k} | {bv:g} | {tv:g} | {delta:+g} | {'yes' if better else 'no' if delta else 'same'} |")
    return "\n".join(lines) + "\n"


def failure_appendix(docs):
    lines = []
    for d in docs:
        fails = [r for r in (d.get("records") or []) if not r.get("passed")]
        if not fails:
            continue
        lines.append(f"\n### `{label_of(d)}` — {len(fails)} failing step(s)\n")
        lines.append("| Case | Step | Category | What failed |")
        lines.append("|---|---|---|---|")
        for r in fails:
            why = "; ".join(r.get("failures") or ["unknown"])[:300].replace("|", "\\|")
            lines.append(f"| {r.get('case_id')} | {r.get('step')} | `{r.get('category')}` | {why} |")
    return "\n".join(lines) + "\n" if lines else "_No failing steps recorded._\n"


def provenance_table(docs):
    lines = ["| Experiment | Suite | Accelerator | Elapsed | Kernel dir |", "|---|---|---|---|---|"]
    for d in docs:
        d.get("config") or {}
        lines.append("| " + " | ".join([
            f"`{label_of(d)}`",
            suite_of(d),
            gpu_of(d),
            f"{(d.get('elapsed_s') or 0)/60:.0f} min" if d.get("elapsed_s") else "—",
            f"`{(d.get('_dir') or '').replace(chr(92), '/').split('/')[-1]}`",
        ]) + " |")
    return "\n".join(lines) + "\n"


def build(docs, out_root):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    planner = [d for d in docs if suite_of(d) == "planner"]
    worker = [d for d in docs if suite_of(d) == "specialist"]

    md = []
    md.append("# Benchmark results\n")
    md.append(f"Generated **{stamp}** by `tools/report.py` from {len(docs)} `results.json` file(s). "
              "Every figure below is read from those files; nothing is hand-entered.\n")
    md.append("Suites: `planner-v1` (26 cases) and `specialist-v1` (22 cases / 32 scored steps). "
              "Definitions: `bench/README.md`.\n")

    md.append("## Provenance\n")
    md.append(provenance_table(docs) if docs else "_No runs yet._\n")

    md.append("\n## Planner\n")
    md.append(headline_table(docs, "planner"))
    md.append("\n### Planner by category\n")
    md.append(category_table(docs, "planner") or "_No planner category data._\n")

    md.append("\n## Specialist worker\n")
    md.append(headline_table(docs, "specialist"))
    md.append("\n### Specialist by category\n")
    md.append(category_table(docs, "specialist") or "_No specialist category data._\n")

    # baseline vs tuned, matched on model family
    base = next((d for d in worker if "baseline" in label_of(d)), None)
    tuned = next((d for d in worker if "sft" in label_of(d) and "pilot" not in label_of(d)), None)
    md.append("\n## Worker: fine-tuned vs original\n")
    if base and tuned:
        md.append(f"Baseline `{label_of(base)}` vs fine-tuned `{label_of(tuned)}`, identical suite and harness.\n")
        md.append(delta_table(base, tuned))
    else:
        md.append("_Awaiting both a baseline and a fine-tuned worker run._\n")

    md.append("\n## Failing steps\n")
    md.append(failure_appendix(docs))

    results_path = os.path.join(out_root, "BENCHMARK_RESULTS.md")
    with open(results_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # head-to-head planner comparison document
    cmp_dir = os.path.join(out_root, "experiments", "comparisons")
    os.makedirs(cmp_dir, exist_ok=True)
    c = ["# Planner head-to-head\n", f"Generated {stamp} by `tools/report.py`.\n"]
    if planner:
        c.append(headline_table(planner, "planner"))
        c.append("\n## Category detail\n")
        c.append(category_table(planner, "planner"))
        best = max(planner, key=lambda d: ((d.get("metrics") or {}).get("case_success_rate") or 0))
        c.append(f"\n**Highest measured case success rate:** `{label_of(best)}` at "
                 f"{fmt((best['metrics'] or {}).get('case_success_rate'), 'case_success_rate')} "
                 f"on {best['metrics'].get('cases')} cases.\n")
        c.append("\n> Small differences on 26 cases are not decisive. Treat < 10 pp as a tie and decide on "
                 "resource cost, licence and latency.\n")
    else:
        c.append("_No planner runs yet._\n")
    with open(os.path.join(cmp_dir, "model-comparison.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(c))

    print(f"wrote {results_path}")
    print(f"wrote {os.path.join(cmp_dir, 'model-comparison.md')}")
    return results_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    docs = find_results(args.inputs)
    print(f"found {len(docs)} result file(s)")
    build(docs, args.out)


if __name__ == "__main__":
    main()
