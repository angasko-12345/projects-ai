"""Build kaggle/train_tinystories.ipynb from the cell sources below.

The notebook is generated, not hand-edited, so its JSON stays reviewable and a
test (tests/test_kaggle_recipe.py) can check the committed file is exactly what
this script writes. Stdlib only. Regenerate after editing a cell:

    python kaggle/build_notebook.py
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NOTEBOOK = os.path.join(HERE, "train_tinystories.ipynb")

md0 = """# mini-llm: TinyStories baseline on a Kaggle T4

Trains mini-llm (a ~14M-parameter GPT-2-style model built from scratch in this repo) on the
full TinyStories set with one fixed recipe, two seeds, fp16 mixed precision, and records
validation **bits per byte**, throughput and wall time. Everything is driven by
`small-projects/mini-llm/kaggle/run_recipe.py`; this notebook only calls it.

**Before running:** right panel -> *Session options* -> **Accelerator: GPU T4 x2** (only one GPU
is used) and **Internet: On**. For the full run use **Save Version -> Save & Run All (Commit)** so it
keeps running with the browser closed (12 h session limit).

Order: settings -> GPU check -> get code -> **200-step smoke test** (downloads + prepares data
first, ~1.9 GB) -> full recipe (both seeds) -> GGUF export (skipped with a message if this checkout
has no `src/export_gguf.py`) -> `results.md`. Outputs land in `/kaggle/working/minillm-t4/`.
Within a session, re-running a cell is safe: finished work is detected and skipped."""

settings = """# Settings
import json
import os

REPO_URL = "https://github.com/angasko-12345/projects-ai.git"
REPO_REF = "main"     # branch, tag or commit. Before the Kaggle PR merges use "upgrade/minillm-kaggle-t4".
RUN_FULL = True       # False: stop after the smoke test (use this for a first interactive check)
ONLY_SEED = None      # 1337 or 4242: one seed per saved version, if the smoke test says both won't fit 12 h
DEVICE = "cuda"
EXTRA_ARGS = []       # e.g. ["--override", "max_steps=6000"]; results are then marked NOT the baseline

# Local test hooks; unset on Kaggle. MINILLM_REPO_DIR reuses an existing checkout.
REPO_DIR = os.environ.get("MINILLM_REPO_DIR")
DEVICE = os.environ.get("MINILLM_DEVICE", DEVICE)
EXTRA_ARGS = EXTRA_ARGS + json.loads(os.environ.get("MINILLM_RECIPE_ARGS", "[]"))
if ONLY_SEED is not None:
    EXTRA_ARGS = EXTRA_ARGS + ["--only-seed", str(ONLY_SEED)]
print("ref:", REPO_REF, "| device:", DEVICE, "| full run:", RUN_FULL, "| extra:", EXTRA_ARGS)"""

gpu = """# GPU check
import torch

print("torch", torch.__version__, "| CUDA build", torch.version.cuda)
if DEVICE == "cuda":
    if not torch.cuda.is_available():
        raise RuntimeError("No GPU in this session. Right panel -> Session options -> "
                           "Accelerator -> GPU T4 x2, then run all cells again.")
    for i in range(torch.cuda.device_count()):
        print(f"GPU {i}: {torch.cuda.get_device_name(i)}")
    if "T4" not in torch.cuda.get_device_name(0):
        print("note: not a T4; the recipe still runs, but numbers are not comparable to a T4 run")"""

code = """# Get the code at REPO_REF and install mini-llm's dependencies
import subprocess
import sys


def sh(cmd, cwd=None):
    print("$", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        print(line, end="", flush=True)
    if proc.wait() != 0:
        raise RuntimeError(f"failed: {' '.join(cmd)}")


if REPO_DIR is None:
    REPO_DIR = "/tmp/projects-ai"   # outside /kaggle/working: the code is not part of the saved output
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        sh(["git", "init", "-q", REPO_DIR])
        sh(["git", "remote", "add", "origin", REPO_URL], cwd=REPO_DIR)
    sh(["git", "fetch", "-q", "--depth", "1", "origin", REPO_REF], cwd=REPO_DIR)
    sh(["git", "checkout", "-q", "--force", "FETCH_HEAD"], cwd=REPO_DIR)
sh(["git", "log", "-1", "--format=%H %s"], cwd=REPO_DIR)
PKG = os.path.join(REPO_DIR, "small-projects", "mini-llm")
sh([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=PKG)

sys.path.insert(0, os.path.join(PKG, "kaggle"))
import run_recipe  # noqa: E402


def recipe(stage):
    try:
        run_recipe.main(["--stage", stage, "--device", DEVICE, *EXTRA_ARGS])
    except SystemExit as exc:  # turn a script exit into a cell error, so Run All stops here
        if exc.code not in (0, None):
            raise RuntimeError(str(exc.code)) from None"""

smoke = """# 1) Smoke test: download + prepare TinyStories (first run only), 200 fp16 steps, then a short
#    resume that must restore the GradScaler. Prints tokens/sec and the projected full-run time.
recipe("smoke")"""

full = """# 2) Full recipe, every seed (or ONLY_SEED). Within one session, running this cell again skips
#    finished seeds and resumes an unfinished one from its newest checkpoint.
if RUN_FULL:
    recipe("full")
else:
    print("RUN_FULL is False: skipping the full run")"""

export = """# 3) GGUF export of the first seed (f16, plus Q8_0 via a pinned llama.cpp build).
#    Skips with a message when this checkout has no src/export_gguf.py.
if RUN_FULL:
    recipe("export")
else:
    print("RUN_FULL is False: nothing to export")"""

summary = """# 4) results.md from the measured values (blank where something did not run)
recipe("summary")"""


CELLS = [
    ("markdown", md0),
    ("code", settings),
    ("code", gpu),
    ("code", code),
    ("code", smoke),
    ("code", full),
    ("code", export),
    ("code", summary),
]

METADATA = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
    # Kaggle's own notebook metadata; whether an import honours it is not verified,
    # so the README still says to pick the accelerator and Internet by hand.
    "kaggle": {
        "accelerator": "nvidiaTeslaT4",
        "isGpuEnabled": True,
        "isInternetEnabled": True,
        "language": "python",
        "sourceType": "notebook",
    },
}


def _lines(text: str) -> list[str]:
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + [parts[-1]]


def build() -> dict:
    cells = []
    for i, (kind, source) in enumerate(CELLS):
        cell = {"cell_type": kind, "id": f"cell-{i}", "metadata": {}, "source": _lines(source)}
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)
    return {"cells": cells, "metadata": METADATA, "nbformat": 4, "nbformat_minor": 5}


def render() -> str:
    return json.dumps(build(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    with open(NOTEBOOK, "w", encoding="utf-8", newline="\n") as f:
        f.write(render())
    print(f"wrote {NOTEBOOK}")
