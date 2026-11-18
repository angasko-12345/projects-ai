"""Environment probe for small-model-fleet Kaggle runs.

Reports runtime versions, accelerator identity, memory/disk, internet reachability
and whether the transformers build bundled in the image knows the model
architectures we intend to run. Consumes no GPU quota when run CPU-only.
"""

import json
import os
import platform
import subprocess
import sys


def sh(cmd):
    try:
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=120
        ).stdout.strip()
    except Exception as e:  # pragma: no cover
        return f"ERR {e}"


report = {
    "python": sys.version,
    "platform": platform.platform(),
    "cpu_count": os.cpu_count(),
    "cwd": os.getcwd(),
}

for pkg in [
    "torch",
    "torchvision",
    "transformers",
    "trl",
    "peft",
    "datasets",
    "accelerate",
    "bitsandbytes",
    "tokenizers",
    "huggingface_hub",
    "safetensors",
    "vllm",
    "timm",
    "numpy",
    "pandas",
]:
    try:
        mod = __import__(pkg)
        report[pkg] = getattr(mod, "__version__", "unknown")
    except Exception as e:
        report[pkg] = f"missing ({type(e).__name__})"

report["nvidia_smi"] = sh("nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv")
report["nvidia_smi_all"] = sh("nvidia-smi -L")

try:
    import torch

    report["cuda_available"] = torch.cuda.is_available()
    report["cuda_device_count"] = torch.cuda.device_count()
    if torch.cuda.is_available():
        report["gpu_names"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        report["gpu_mem_gb"] = [
            round(torch.cuda.get_device_properties(i).total_memory / 1e9, 2)
            for i in range(torch.cuda.device_count())
        ]
        report["bf16_supported"] = torch.cuda.is_bf16_supported()
        report["torch_cuda_version"] = torch.version.cuda
except Exception as e:
    report["torch_err"] = f"{type(e).__name__}: {e}"

try:
    import psutil

    vm = psutil.virtual_memory()
    report["ram_total_gb"] = round(vm.total / 1e9, 1)
    report["ram_available_gb"] = round(vm.available / 1e9, 1)
except Exception as e:
    report["psutil_err"] = str(e)

# Disk space on the paths that matter
for path in ["/kaggle/working", "/kaggle/temp", "/tmp", "/root/.cache"]:
    if os.path.exists(path):
        try:
            st = os.statvfs(path)
            report[f"disk_{path}"] = {
                "free_gb": round(st.f_bavail * st.f_frsize / 1e9, 1),
                "total_gb": round(st.f_blocks * st.f_frsize / 1e9, 1),
            }
        except Exception as e:
            report[f"disk_{path}"] = f"ERR {e}"

# Hugging Face reachability + token presence (never print the token)
try:
    from huggingface_hub import HfApi, whoami
    import urllib.request

    with urllib.request.urlopen("https://huggingface.co/api/models/Qwen/Qwen3-4B-Thinking-2507", timeout=30) as r:
        report["hf_reachable"] = r.status
    report["hf_token_present"] = bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"))
    try:
        report["hf_user"] = whoami().get("name")
    except Exception as e:
        report["hf_user"] = f"anonymous ({type(e).__name__})"
except Exception as e:
    report["hf_reachable"] = f"ERR {type(e).__name__}: {e}"

# Does the installed transformers know our architectures?
try:
    import transformers
    from transformers import CONFIG_MAPPING

    report["transformers_version_full"] = transformers.__version__
    for arch in ["qwen3_5", "qwen3_5_text", "lfm3", "lfm2", "qwen3"]:
        report[f"config_mapping_has_{arch}"] = arch in CONFIG_MAPPING
except Exception as e:
    report["transformers_probe_err"] = f"{type(e).__name__}: {e}"

# Can we resolve remote configs without downloading weights?
for repo in ["Qwen/Qwen3.5-4B", "Qwen/Qwen3-4B-Thinking-2507", "LiquidAI/LFM3-1.7B"]:
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo, files_metadata=False)
        report[f"resolve_{repo}"] = {
            "sha": info.sha,
            "last_modified": str(info.last_modified),
        }
    except Exception as e:
        report[f"resolve_{repo}"] = f"ERR {type(e).__name__}: {e}"

# Confirm pip can install into the image without touching a protected prefix
report["pip_version"] = sh(f"{sys.executable} -m pip --version")
report["writable_working"] = os.access("/kaggle/working", os.W_OK) if os.path.exists("/kaggle/working") else None

out = "/kaggle/working/env_report.json"
with open(out, "w") as f:
    json.dump(report, f, indent=2, default=str)

print(json.dumps(report, indent=2, default=str))
print(f"\nWROTE {out}")
