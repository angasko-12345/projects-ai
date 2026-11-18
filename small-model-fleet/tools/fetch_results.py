"""Pull Kaggle kernel outputs into the project and record what was retrieved.

Usage:
    python tools/fetch_results.py                 # every experiment in the registry
    python tools/fetch_results.py --only lfm3     # substring filter on experiment_id
    python tools/fetch_results.py --status        # just report kernel status, no download

Artifacts land in `results/<experiment_id with '/' -> '__'>/` and a
`fetch_manifest.json` next to them records sha256, size and retrieval time so a
downloaded artifact can be proven identical to what the kernel wrote.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "experiments", "registry.json")
RESULTS = os.path.join(ROOT, "results")


def kaggle(args, config_dir):
    env = dict(os.environ)
    env["KAGGLE_CONFIG_DIR"] = config_dir
    proc = subprocess.run(["kaggle"] + args, capture_output=True, text=True, env=env)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def status_of(slug, config_dir):
    rc, out = kaggle(["kernels", "status", slug], config_dir)
    out = out.strip()
    if "Permission 'kernels.get' was denied" in out:
        return "NOT_PUSHED"
    for token in ["COMPLETE", "ERROR", "CANCEL", "RUNNING", "QUEUED"]:
        if token in out:
            return token
    return out[:120] or f"rc={rc}"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def kernel_log(slug, config_dir):
    """Return the kernel's execution log as plain text.

    `kaggle kernels status` only reports ERROR; the traceback lives in the log.
    The CLI returns a JSON stream of chunks, which is decoded here.
    """
    rc, out = kaggle(["kernels", "logs", slug], config_dir)
    if rc != 0:
        return f"log fetch failed (rc={rc}): {out[:500]}"
    try:
        chunks = json.loads(out)
        return "".join(c.get("data", "") for c in chunks)
    except Exception:
        return out


def fetch(exp, config_dir, force=False):
    slug = exp["kernel"]
    dest = os.path.join(RESULTS, exp["experiment_id"].replace("/", "__"))
    os.makedirs(dest, exist_ok=True)

    log_text = kernel_log(slug, config_dir)
    with open(os.path.join(dest, "kernel_log.txt"), "w", encoding="utf-8") as f:
        f.write(log_text)
    print(f"  log: {len(log_text)} chars")

    args = ["kernels", "output", slug, "-p", dest]
    if force:
        args.append("-o")
    rc, out = kaggle(args, config_dir)
    print(out.strip()[:500])
    if rc != 0:
        return {"kernel": slug, "ok": False, "error": out.strip()[:1500]}

    files = []
    for root, _dirs, names in os.walk(dest):
        for n in sorted(names):
            if n == "fetch_manifest.json":
                continue
            p = os.path.join(root, n)
            files.append({
                "path": os.path.relpath(p, dest).replace("\\", "/"),
                "bytes": os.path.getsize(p),
                "sha256": sha256(p),
            })
    manifest = {
        "experiment_id": exp["experiment_id"],
        "kernel": slug,
        "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status_at_fetch": status_of(slug, config_dir),
        "files": files,
    }
    with open(os.path.join(dest, "fetch_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"  {len(files)} file(s) -> {dest}")
    return {"kernel": slug, "ok": True, "n_files": len(files), "dest": dest}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--config-dir", default=os.environ.get("KAGGLE_CONFIG_DIR", r"D:\admin\code\secrets\kaggle"))
    args = ap.parse_args()

    reg = json.load(open(REGISTRY, encoding="utf-8"))
    exps = reg["experiments"]
    if args.only:
        exps = [e for e in exps if args.only in e["experiment_id"]]

    if args.status:
        for e in exps:
            print(f"{e['experiment_id']:<45} {e['kernel']:<45} {status_of(e['kernel'], args.config_dir)}")
        return

    report = []
    for e in exps:
        print(f"\n== {e['experiment_id']} ({e['kernel']}) ==")
        st = status_of(e["kernel"], args.config_dir)
        print(f"  status: {st}")
        if st != "COMPLETE" and not args.force:
            print("  skipped download (not COMPLETE); use --force to try anyway")
            report.append({"experiment_id": e["experiment_id"], "ok": False, "status": st})
            continue
        report.append({"experiment_id": e["experiment_id"], **fetch(e, args.config_dir)})

    out = os.path.join(RESULTS, "fetch_report.json")
    os.makedirs(RESULTS, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nwrote {out}")
    ok = sum(1 for r in report if r.get("ok"))
    print(f"{ok}/{len(report)} experiments retrieved")


if __name__ == "__main__":
    sys.exit(main() or 0)
