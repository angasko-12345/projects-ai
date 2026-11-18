"""Reproducibly rebuild the private bench dataset from the working tree.

Usage:
    python tools/pack_bench.py --version 1.0.3                 # stage only
    python tools/pack_bench.py --version 1.0.3 --publish       # stage + push

The staging directory mirrors what Kaggle will serve at /kaggle/input/smf-bench,
so a kernel that works against the stage also works against the published copy.
MANIFEST.json records every file's sha256, which is what makes "the baseline and
the fine-tune were graded by the same code" checkable rather than asserted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STAGE = r"D:\tmp\small-model-fleet\stage\smf-bench"
DATASET_ID = "wataeshi/smf-bench"

DESCRIPTION = (
    "Frozen held-out evaluation suite for the small-model-fleet project: planner suite "
    "(26 cases) + PC-agent specialist suite (22 cases / 32 steps) + shared scoring harness. "
    "Private. Do not use for training."
)

# Published under a different name than they live in the repo, so a kernel can run
# the harness self-test against the exact bytes it was graded with.
EXTRA_SOURCES = {"src/self_test.py": "tests/test_harness.py"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files():
    """Map published path -> repo path for the harness and the frozen suites."""
    files = {}
    for sub in ("src", "bench"):
        base = os.path.join(ROOT, sub)
        for root, _dirs, names in os.walk(base):
            for n in sorted(names):
                if n.endswith(".pyc") or n == "__pycache__":
                    continue
                p = os.path.join(root, n)
                rel = os.path.relpath(p, ROOT).replace("\\", "/")
                files[rel] = rel
    files.update(EXTRA_SOURCES)
    return dict(sorted(files.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--stage", default=DEFAULT_STAGE)
    ap.add_argument("--publish", action="store_true")
    # Kaggle's default dir-mode is "skip", which silently drops src/ and bench/.
    # The dataset was created in zip mode, so every version must reuse it.
    ap.add_argument("--dir-mode", default="zip", choices=["zip", "tar", "skip"])
    ap.add_argument("--config-dir", default=os.environ.get("KAGGLE_CONFIG_DIR", r"D:\admin\code\secrets\kaggle"))
    ap.add_argument("--notes", default=None)
    args = ap.parse_args()

    if os.path.isdir(args.stage):
        shutil.rmtree(args.stage)
    os.makedirs(args.stage, exist_ok=True)

    manifest = {"name": "smf-bench", "frozen": date.today().isoformat(), "files": {}, "version": args.version}
    for dest_rel, src_rel in collect_files().items():
        dest = os.path.join(args.stage, dest_rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, src_rel), dest)
        entry = {"bytes": os.path.getsize(dest), "sha256": sha256(dest)}
        if src_rel != dest_rel:
            entry["from"] = src_rel
        manifest["files"][dest_rel] = entry

    with open(os.path.join(args.stage, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    with open(os.path.join(args.stage, "dataset-metadata.json"), "w", encoding="utf-8") as f:
        json.dump({"id": DATASET_ID, "title": "SMF Bench",
                   "licenses": [{"name": "CC0-1.0"}], "description": DESCRIPTION}, f, indent=2)

    print(f"staged {len(manifest['files'])} file(s) in {args.stage}")
    for rel, meta in manifest["files"].items():
        print(f"  {meta['bytes']:>7}  {meta['sha256'][:12]}  {rel}")

    if not args.publish:
        print("\nnot published (pass --publish)")
        return

    env = dict(os.environ)
    env["KAGGLE_CONFIG_DIR"] = args.config_dir
    cmd = ["kaggle", "datasets", "version", "-p", args.stage, "--dir-mode", args.dir_mode,
           "-m", args.notes or f"harness {args.version}"]
    print("\n" + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    print((proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0:
        sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
