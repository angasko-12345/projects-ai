"""Pooled-episode comparisons for RESULTS.md (Welch t + bootstrap CI on mean difference).

Usage: python experiments/generalization/stats.py <artifacts> [<out.json>]
Reads <artifacts>/runs/*/result.json and <artifacts>/baselines.json (numpy only).
"""
import json
import sys
from pathlib import Path

import numpy as np

art = Path(sys.argv[1])
runs = {p.parent.name: json.loads(p.read_text()) for p in (art / "runs").glob("*/result.json")}
base = json.loads((art / "baselines.json").read_text())


def welch(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a.mean() - b.mean()
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    rng = np.random.default_rng(0)
    boots = [rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(5000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"diff": round(float(d), 3), "t": round(float(d / se), 2) if se > 0 else None,
            "ci95": [round(float(lo), 3), round(float(hi), 3)], "n": [len(a), len(b)]}


def eps(rid, game, which="final"):
    r = runs[rid]
    ev = r["final_eval"] if which == "final" else r["curve"][which]["eval"]
    return ev[game]["episode_rewards"]


def base_eps(game, pol):
    return sum((p["episode_rewards"] for p in base["results"][game][pol]["per_seed"]), [])


out = {}
seeds = sorted({r["seed"] for r in runs.values() if r["arm"] == "multi"})
for g in ("volley", "dodge", "pong", "catch", "breakout"):
    m = sum((eps(f"multi_multi_s{s}", g) for s in seeds), [])
    out[f"multi_vs_random_{g}"] = welch(m, base_eps(g, "random"))
    out[f"multi_per_seed_{g}"] = [float(np.mean(eps(f"multi_multi_s{s}", g))) for s in seeds]
for g in ("pong", "catch", "breakout"):
    sp = [f"specialist_{g}_s{s}" for s in seeds if f"specialist_{g}_s{s}" in runs]
    if sp:
        a = sum((eps(f"multi_multi_s{s}", g) for s in seeds if f"specialist_{g}_s{s}" in runs), [])
        b = sum((eps(r, g) for r in sp), [])
        out[f"multi_vs_specialist_{g}"] = welch(a, b)
ft = [f"finetune_volley_s{s}" for s in seeds if f"finetune_volley_s{s}" in runs]
sc = [f"scratch_volley_s{s}" for s in seeds if f"scratch_volley_s{s}" in runs]
if ft and sc:
    npts = len(runs[ft[0]]["curve"])
    for i in range(npts):
        steps = runs[ft[0]]["curve"][i]["steps"]
        a = sum((runs[r]["curve"][i]["eval"]["volley"]["episode_rewards"] for r in ft), [])
        b = sum((runs[r]["curve"][i]["eval"]["volley"]["episode_rewards"] for r in sc), [])
        out[f"finetune_vs_scratch_volley_at_{steps}"] = welch(a, b)
    a = sum((runs[r]["curve"][i]["eval"]["volley"]["episode_rewards"] for r in ft for i in range(npts)), [])
    b = sum((runs[r]["curve"][i]["eval"]["volley"]["episode_rewards"] for r in sc for i in range(npts)), [])
    out["finetune_vs_scratch_volley_all_points"] = welch(a, b)
    out["finetune_minus_scratch_auc_per_seed"] = [
        round(float(np.mean([c["eval"]["volley"]["mean_reward"] for c in runs[f"finetune_volley_s{s}"]["curve"]])
                    - np.mean([c["eval"]["volley"]["mean_reward"] for c in runs[f"scratch_volley_s{s}"]["curve"]])), 3)
        for s in seeds if f"finetune_volley_s{s}" in runs and f"scratch_volley_s{s}" in runs]
    # forgetting: finetuned agent back on training games vs multi
    for g in ("pong", "catch", "breakout"):
        out[f"finetune_final_{g}_vs_multi"] = welch(sum((eps(r, g) for r in ft), []),
                                                   sum((eps(f"multi_multi_s{s}", g) for s in seeds), []))
print(json.dumps(out, indent=1))
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps(out, indent=2) + "\n")
