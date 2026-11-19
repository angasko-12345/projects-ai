# Small learning projects

This directory mixes maintained products with standalone educational scripts.
Install dependencies and run each project independently; there is no shared
Python environment for the directory.

## mini-llm

A maintained GPT-style language-model project with its own README, dependency
files, test suite, and CI workflow. See [`mini-llm/README.md`](mini-llm/README.md).

## verytinyllm

A small character-level GPT experiment. It requires PyTorch and reads
`input.txt` from the current directory (the included sample is used when run
from this project directory).

```powershell
cd small-projects/verytinyllm
python -m pip install torch
python tiny_llm.py
```

This script trains interactively and is a learning demo, not a packaged
application. It has no automated test suite; the source file documents its
model and training loop.

## tinyllm.py

An earlier standalone character-level version with the same PyTorch
requirement and a built-in sample fallback. Run it from `small-projects/`:

```powershell
cd small-projects
python -m pip install torch
python tinyllm.py
```

It trains for more iterations than `verytinyllm/tiny_llm.py`, so CPU runs can
take longer. This script also has no automated test suite and is retained as an
educational experiment rather than a supported product.
