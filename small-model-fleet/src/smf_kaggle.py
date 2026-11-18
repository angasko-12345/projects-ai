"""Kaggle-side driver shared by every experiment.

Each experiment folder holds a tiny run.py with a CONFIG dict; all real logic
lives here so that baseline, pilot and post-SFT runs cannot drift apart.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import traceback

SRC = os.path.dirname(os.path.abspath(__file__))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import smf_bench as sb
import smf_runner as runner

WORK = "/kaggle/working"


# --------------------------------------------------------------------------
# environment record
# --------------------------------------------------------------------------


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as e:
        return f"ERR {e}"


def record_environment():
    import torch

    env = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
    }
    for pkg in ["transformers", "peft", "datasets", "accelerate", "tokenizers", "huggingface_hub", "bitsandbytes", "trl", "safetensors", "numpy"]:
        try:
            env[pkg] = __import__(pkg).__version__
        except Exception:
            env[pkg] = None
    try:
        import psutil

        vm = psutil.virtual_memory()
        env["ram_total_gb"] = round(vm.total / 1e9, 1)
    except Exception:
        env["ram_total_gb"] = None
    env["cuda_available"] = torch.cuda.is_available()
    if torch.cuda.is_available():
        env["gpu_names"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        env["gpu_count"] = torch.cuda.device_count()
        env["gpu_mem_gb"] = [round(torch.cuda.get_device_properties(i).total_memory / 1e9, 2) for i in range(torch.cuda.device_count())]
        env["bf16_supported"] = torch.cuda.is_bf16_supported()
        env["compute_cap"] = [f"{torch.cuda.get_device_properties(i).major}.{torch.cuda.get_device_properties(i).minor}" for i in range(torch.cuda.device_count())]
    env["nvidia_smi"] = sh("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader")
    env["free_disk_working_gb"] = round(os.statvfs(WORK).f_bavail * os.statvfs(WORK).f_frsize / 1e9, 1)
    return env


def peak_ram_gb():
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 1e9, 2)
    except Exception:
        return None


# --------------------------------------------------------------------------
# model loading
# --------------------------------------------------------------------------


def dtype_for(env):
    import torch

    if env.get("bf16_supported"):
        return torch.bfloat16
    return torch.float16


def load_model(model_id, revision=None, trust_remote_code=True, quant="none", adapter_dir=None, env=None):
    from transformers import AutoModelForCausalLM, AutoTokenizer

    env = env or record_environment()
    dt = dtype_for(env)
    kwargs = dict(revision=revision, trust_remote_code=trust_remote_code)
    if quant == "4bit":
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=dt, bnb_4bit_use_double_quant=True
        )
        kwargs["device_map"] = {"": 0}
    else:
        kwargs["dtype"] = dt
        kwargs["device_map"] = {"": 0}

    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision, trust_remote_code=trust_remote_code)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
    model.eval()

    resolved_revision = None
    try:
        from huggingface_hub import HfApi

        resolved_revision = HfApi().model_info(model_id, revision=revision).sha
    except Exception:
        pass

    if adapter_dir:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter_dir)
        model.eval()

    info = {
        "model_id": model_id,
        "revision_requested": revision,
        "revision_resolved": resolved_revision,
        "dtype": str(dt),
        "quantization": quant,
        "adapter_dir": adapter_dir,
        "n_params": sum(p.numel() for p in model.parameters()),
    }
    return model, tokenizer, info, env


# --------------------------------------------------------------------------
# run helpers
# --------------------------------------------------------------------------


def smoke_test(gen, expect_tool=False):
    """Cheap sanity check: the model must produce coherent non-empty text."""
    try:
        out, rec = gen.chat([{"role": "user", "content": "Reply with the single word: ready"}], max_new_tokens=48, tools=None)
        ok = bool(sb.strip_reasoning(out).strip())
        return {"ok": ok, "raw": out[:500], "rec": rec}
    except Exception:
        return {"ok": False, "error": traceback.format_exc()[:2000]}


def run_suite(gen, kind, max_new_tokens=None, tools_kwarg=True, native_tool_role=True):
    cases_file, system_prompt, tools = runner.load_suite(kind)
    meta = cases_file["cases"]
    caps = {
        "tools_kwarg": tools_kwarg,
        "native_tool_role": native_tool_role,
    }
    print(f"[{kind}] {len(meta)} cases; caps={caps}")
    if kind == "specialist":
        records, per_case = runner.run_specialist_suite(
            gen, cases_file, meta, system_prompt, tools,
            tools_kwarg=tools_kwarg, native_tool_role=native_tool_role,
            max_new_tokens=max_new_tokens or 512,
        )
    else:
        records, per_case = runner.run_planner_suite(gen, cases_file, meta, system_prompt, max_new_tokens=max_new_tokens or 4096)
    metrics = runner.suite_metrics(records, per_case)
    metrics["suite"] = cases_file.get("suite")
    metrics["suite_version"] = cases_file.get("version")
    return {"records": records, "per_case": per_case, "metrics": metrics, "caps": caps}


def save(out_dir, name, payload):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    print(f"WROTE {path} ({os.path.getsize(path)} bytes)")
    return path


def print_summary(label, metrics):
    print(f"\n===== {label} =====")
    for k in [
        "cases", "case_success_rate", "instruction_following_accuracy", "tool_selection_accuracy",
        "argument_validity", "parse_validity", "false_completion_rate", "permission_violation_rate",
        "recovery_success_rate", "avg_latency_s", "p95_latency_s", "avg_tokens_per_s", "peak_vram_gb",
        "total_completion_tokens",
    ]:
        if k in metrics:
            print(f"  {k}: {metrics[k]}")
    print("  by category:")
    for cat, d in (metrics.get("category_breakdown") or {}).items():
        print(f"    {cat}: {d['passed']}/{d['total']}")
