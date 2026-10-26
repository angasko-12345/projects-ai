"""Reproducible TinyStories baseline for mini-llm on a Kaggle T4 (or any CUDA GPU).

The notebook `kaggle/train_tinystories.ipynb` is a thin wrapper around this
script; everything it does can be run from a shell too:

    cd small-projects/mini-llm
    python kaggle/run_recipe.py --stage smoke     # download + prepare + 200 fp16 steps
    python kaggle/run_recipe.py --stage full      # the fixed recipe, every seed
    python kaggle/run_recipe.py --stage export    # GGUF of the first seed (needs src/export_gguf.py)
    python kaggle/run_recipe.py --stage summary   # results.md from what was measured

Stages are re-entrant: prepared data and finished seeds are detected and not
redone, so a second "Run All" after an interruption picks up where it stopped.

Every number written to run.json / results.md is parsed from the logs of the
commands that ran. Nothing is estimated except the smoke stage's projected
wall time, which is labelled as a projection. Any `--override` of the recipe is
recorded, and results.md then says in its first line that it is NOT the
baseline recipe, so a tiny CPU test run can never pass as the baseline.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The fixed recipe. Changing a value here makes a new recipe: bump "name".
RECIPE = {
    "name": "tinystories-t4-v1",
    # Full TinyStories (V1, GPT-3.5 + GPT-4 stories), the same file the repo's
    # 2026-09-30 subset run was cut from. Stories are separated by
    # <|endoftext|> lines; convert_tinystories() turns that into the blank-line
    # passages prepare_data.py frames as <bos> ... <eos>.
    "data_url": ("https://huggingface.co/datasets/roneneldan/TinyStories/resolve/main/"
                 "TinyStories-train.txt"),
    "vocab_size": 8192,
    "min_frequency": 2,
    "val_frac": 0.01,
    "context_length": 512,
    "n_layers": 6,
    "n_heads": 6,
    "d_model": 384,
    "d_ff": 1536,
    "dropout": 0.0,
    "batch_size": 32,
    "grad_accum_steps": 1,
    # Untuned: GPT-3 "small" used 6e-4; not swept for this model.
    "learning_rate": 6e-4,
    "warmup_steps": 500,
    "weight_decay": 0.1,
    "min_lr_ratio": 0.1,
    "grad_clip": 1.0,
    # tokens = max_steps * batch_size * grad_accum_steps * context_length
    #        = 12000 * 32 * 1 * 512 = 196,608,000 (~0.4 epoch of the full set)
    "max_steps": 12000,
    "eval_interval": 1000,
    "eval_batches": 50,
    "keep_last": 1,
    "amp": "fp16",
    "seeds": [1337, 4242],
    "smoke_steps": 200,
    "smoke_resume_steps": 20,
    "eval_batch_size": 64,
    # Pinned llama.cpp release for the optional Q8_0 quantization (the release
    # the GGUF export was parity-tested against).
    "llama_cpp_tag": "b11874",
}

INT_KEYS = {k for k, v in RECIPE.items() if isinstance(v, int) and not isinstance(v, bool)}
FLOAT_KEYS = {k for k, v in RECIPE.items() if isinstance(v, float)}

ENDOFTEXT = "<|endoftext|>"


# ---------------------------------------------------------------- data

def convert_tinystories(src: str, dst: str, max_bytes: int | None = None) -> int:
    """Rewrite a TinyStories .txt as one passage per story, blank-line separated.

    The source separates stories with a line holding only <|endoftext|> (some
    copies carry a zero-width space inside it) and uses blank lines inside
    stories too, which prepare_data.py would split into separate passages. Each
    story's non-blank lines are kept, joined by single newlines. Streams line by
    line, so the 1.9 GB file is never in memory. `max_bytes` stops at the first
    story boundary past that many input bytes (for small test runs). Returns the
    number of stories written.
    """
    stories = 0
    read = 0
    buf: list[str] = []

    def flush(out) -> None:
        nonlocal stories
        if buf:
            out.write("\n".join(buf) + "\n\n")
            stories += 1
            buf.clear()

    tmp = dst + ".part"
    with open(src, encoding="utf-8", errors="strict") as f, \
            open(tmp, "w", encoding="utf-8", newline="\n") as out:
        for line in f:
            read += len(line.encode("utf-8"))
            text = line.strip().replace("\u200b", "")
            if text == ENDOFTEXT:
                flush(out)
                if max_bytes is not None and read >= max_bytes:
                    break
                continue
            if text:
                buf.append(line.rstrip("\r\n").rstrip())
        else:
            flush(out)
    os.replace(tmp, dst)
    return stories


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: str) -> None:
    """Fetch url to dest unless a complete copy (by Content-Length) is already there.

    GET only: some hosts (GitHub release assets behind a redirect) answer HEAD
    with 401, so the size is read from the GET response itself.
    """
    try:
        resp = urllib.request.urlopen(url, timeout=60)
    except Exception as exc:  # noqa: BLE001 - reported with the fix
        raise SystemExit(
            f"cannot download {url}: {exc}\nOn Kaggle check that Internet is on "
            "(Session options -> Internet) and run the cell again"
        ) from exc
    with resp:
        expected = int(resp.headers.get("Content-Length") or -1)
        if expected >= 0 and os.path.exists(dest) and os.path.getsize(dest) == expected:
            print(f"have {dest} ({expected:,} bytes)", flush=True)
            return
        print(f"downloading {url} ({expected:,} bytes) -> {dest}", flush=True)
        tmp = dest + ".part"
        t0 = last = time.time()
        done = 0
        with open(tmp, "wb") as out:
            while True:
                block = resp.read(1 << 22)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if time.time() - last > 15:
                    last = time.time()
                    print(f"  {done / 1e6:,.0f} MB", flush=True)
    if expected >= 0 and os.path.getsize(tmp) != expected:
        raise SystemExit(f"download of {url} is incomplete ({os.path.getsize(tmp)} of "
                         f"{expected} bytes); run the cell again")
    os.replace(tmp, dest)
    print(f"downloaded in {time.time() - t0:.0f}s", flush=True)


# ---------------------------------------------------------------- running

def run_logged(cmd: list[str], log_path: str) -> str:
    """Run cmd from the package dir, echo its output live, keep a copy; fail loudly."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    print("$ " + " ".join(cmd), flush=True)
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    env.pop("GIT_DIR", None)
    lines = []
    with open(log_path, "a", encoding="utf-8") as log, subprocess.Popen(
            cmd, cwd=PKG_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env) as proc:
        for line in proc.stdout:
            print(line, end="", flush=True)
            log.write(line)
            lines.append(line)
        code = proc.wait()
    if code != 0:
        raise SystemExit(f"command failed with exit code {code}: {' '.join(cmd)}\n"
                         f"log: {log_path}")
    return "".join(lines)


TRAIN_TIME_RE = re.compile(r"training time ([\d.]+)s over (\d+) steps, [\d.]+ steps/sec, "
                           r"(\d+) tokens/sec \((\d+) tokens\)")
EVAL_RE = re.compile(r"step (\d+) train_loss ([\d.naninf]+) val_loss ([\d.naninf]+) "
                     r"lr [\d.e-]+ val_bpb ([\d.naninf]+)")
FP16_RE = re.compile(r"fp16: (\d+) of (\d+) updates skipped .*final loss scale ([\d.]+)")
PARAMS_RE = re.compile(r"parameters: [\d.]+M \((\d+)\)")


def parse_train_log(text: str) -> dict:
    out: dict = {}
    m = TRAIN_TIME_RE.search(text)
    if m:
        out.update(train_seconds=float(m.group(1)), steps_run=int(m.group(2)),
                   tokens_per_sec=int(m.group(3)), tokens_run=int(m.group(4)))
    evals = [(int(s), float(tl), float(vl), float(vb))
             for s, tl, vl, vb in EVAL_RE.findall(text)]
    if evals:
        out["evals"] = [{"step": s, "train_loss": tl, "val_loss": vl, "val_bpb": vb}
                        for s, tl, vl, vb in evals]
    m = FP16_RE.search(text)
    if m:
        out.update(fp16_skipped_updates=int(m.group(1)), fp16_final_scale=float(m.group(3)))
    m = PARAMS_RE.search(text)
    if m:
        out["parameters"] = int(m.group(1))
    return out


def provenance(device: str) -> dict:
    info = {
        "utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "kaggle_run_type": os.environ.get("KAGGLE_KERNEL_RUN_TYPE"),
    }
    for key, cmd in (("git_commit", ["git", "rev-parse", "HEAD"]),
                     ("git_status", ["git", "status", "--porcelain", "--", "."])):
        try:
            env = dict(os.environ)
            env.pop("GIT_DIR", None)
            info[key] = subprocess.run(cmd, cwd=PKG_DIR, capture_output=True, text=True,
                                       env=env, timeout=30).stdout.strip()
        except Exception as exc:  # noqa: BLE001
            info[key] = f"unavailable: {exc}"
    info["git_dirty"] = bool(info.get("git_status"))
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
            info["gpu_count"] = torch.cuda.device_count()
            info["cuda"] = torch.version.cuda
    except Exception as exc:  # noqa: BLE001
        info["torch"] = f"unavailable: {exc}"
    info["device_requested"] = device
    return info


# ---------------------------------------------------------------- stages

class Recipe:
    def __init__(self, args):
        self.args = args
        self.r = dict(RECIPE)
        self.overrides = {}
        for item in args.override or []:
            key, _, value = item.partition("=")
            if key not in RECIPE or not _:
                raise SystemExit(f"--override {item!r}: expected key=value with key in "
                                 f"{', '.join(sorted(RECIPE))}")
            if key == "seeds":
                parsed = [int(s) for s in value.split(",") if s]
            elif key in INT_KEYS:
                parsed = int(value)
            elif key in FLOAT_KEYS:
                parsed = float(value)
            else:
                parsed = value
            self.r[key] = parsed
            self.overrides[key] = parsed
        if args.input:
            self.overrides["input"] = os.path.abspath(args.input)
        if args.max_input_bytes:
            self.overrides["max_input_bytes"] = args.max_input_bytes
        if args.device != "cuda":
            self.overrides["device"] = args.device
        self.seeds = list(self.r["seeds"])
        if args.only_seed is not None:
            if args.only_seed not in self.seeds:
                raise SystemExit(f"--only-seed {args.only_seed} is not a recipe seed "
                                 f"({self.seeds}); use --override seeds=... for other seeds")
            self.seeds = [args.only_seed]
        self.out = os.path.abspath(args.out_dir)
        self.data = os.path.abspath(args.data_dir)
        os.makedirs(self.out, exist_ok=True)
        os.makedirs(self.data, exist_ok=True)
        self.run_json = os.path.join(self.out, "run.json")

    # -- state
    def state(self) -> dict:
        if os.path.exists(self.run_json):
            with open(self.run_json, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save_state(self, state: dict) -> None:
        state["recipe"] = self.r
        state["recipe_overrides"] = self.overrides
        tmp = self.run_json + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.run_json)

    @property
    def tokens_per_step(self) -> int:
        r = self.r
        return r["batch_size"] * r["grad_accum_steps"] * r["context_length"]

    # -- data
    def prepare(self) -> dict:
        state = self.state()
        passages = os.path.join(self.data, "tinystories-passages.txt")
        if self.args.input:
            raw = os.path.abspath(self.args.input)
        else:
            raw = os.path.join(self.data, os.path.basename(self.r["data_url"]))
            download(self.r["data_url"], raw)
        prep_key = {k: self.r[k] for k in ("vocab_size", "min_frequency", "val_frac",
                                            "context_length")}
        prep_key["max_input_bytes"] = self.args.max_input_bytes
        marker = os.path.join(self.data, "prepared.json")
        if os.path.exists(marker):
            with open(marker, encoding="utf-8") as f:
                done = json.load(f)
            if done.get("prep_key") == prep_key and done.get("raw_path") == raw and all(
                    os.path.exists(os.path.join(self.data, n))
                    for n in ("train.bin", "val.bin", "tokenizer.json", "meta.json")):
                print(f"prepared data already in {self.data}", flush=True)
                state["data"] = done
                self.save_state(state)
                return done
        t0 = time.time()
        print("hashing the raw corpus ...", flush=True)
        raw_sha = sha256_file(raw)
        n = convert_tinystories(raw, passages, self.args.max_input_bytes)
        print(f"converted {n:,} stories -> {passages}", flush=True)
        log = run_logged([sys.executable, "prepare_data.py", "--input", passages,
                          "--tokenizer-out", os.path.join(self.data, "tokenizer.json"),
                          "--train-out", os.path.join(self.data, "train.bin"),
                          "--val-out", os.path.join(self.data, "val.bin"),
                          "--vocab-size", str(self.r["vocab_size"]),
                          "--min-frequency", str(self.r["min_frequency"]),
                          "--val-frac", str(self.r["val_frac"]),
                          "--context-length", str(self.r["context_length"])],
                         os.path.join(self.out, "prepare.log"))
        del log
        os.remove(passages)  # 1.9 GB, reproducible from the raw file; free the disk
        with open(os.path.join(self.data, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        done = {"raw_path": raw, "raw_bytes": os.path.getsize(raw), "raw_sha256": raw_sha,
                "source_url": None if self.args.input else self.r["data_url"],
                "stories": n, "prep_key": prep_key, "prepare_seconds": round(time.time() - t0, 1),
                "vocab_size": meta["vocab_size"], "train_tokens": meta.get("train_tokens"),
                "val_tokens": meta.get("val_tokens"),
                "train_sha256": meta["train_sha256"], "val_sha256": meta["val_sha256"],
                "tokenizer_sha256": meta["tokenizer_sha256"]}
        with open(marker, "w", encoding="utf-8") as f:
            json.dump(done, f, indent=2)
        # The small artifacts a checkpoint needs to be used later travel with the
        # results; the .bin files are large and reproducible, so they stay in data_dir.
        for name in ("tokenizer.json", "meta.json", "prepared.json"):
            shutil.copy2(os.path.join(self.data, name), os.path.join(self.out, name))
        state["data"] = done
        self.save_state(state)
        return done

    # -- training
    def train_cmd(self, ckpt_dir: str, seed: int, max_steps: int, eval_interval: int,
                  eval_batches: int, resume: str | None = None) -> list[str]:
        r = self.r
        cmd = [sys.executable, "-m", "src.train", "--device", self.args.device]
        if resume:
            return cmd + ["--resume", resume, "--max-steps", str(max_steps)]
        return cmd + [
            "--train-bin", os.path.join(self.data, "train.bin"),
            "--val-bin", os.path.join(self.data, "val.bin"),
            "--tokenizer", os.path.join(self.data, "tokenizer.json"),
            "--checkpoint-dir", ckpt_dir,
            "--context-length", str(r["context_length"]),
            "--n-layers", str(r["n_layers"]), "--n-heads", str(r["n_heads"]),
            "--d-model", str(r["d_model"]), "--d-ff", str(r["d_ff"]),
            "--dropout", str(r["dropout"]),
            "--batch-size", str(r["batch_size"]),
            "--grad-accum-steps", str(r["grad_accum_steps"]),
            "--learning-rate", str(r["learning_rate"]),
            "--warmup-steps", str(min(r["warmup_steps"], max_steps)),
            "--weight-decay", str(r["weight_decay"]),
            "--min-lr-ratio", str(r["min_lr_ratio"]),
            "--grad-clip", str(r["grad_clip"]),
            "--max-steps", str(max_steps),
            "--eval-interval", str(eval_interval),
            "--eval-batches", str(eval_batches),
            "--keep-last", str(r["keep_last"]),
            "--seed", str(seed),
            "--amp", r["amp"],
        ]

    def evaluate(self, ckpt: str, out_json: str, log: str) -> dict:
        run_logged([sys.executable, "-m", "src.evaluate", "--checkpoint", ckpt,
                    "--tokenizer", os.path.join(self.data, "tokenizer.json"),
                    "--device", self.args.device, "--amp", "off",
                    "--batch-size", str(self.r["eval_batch_size"]),
                    "--samples", "--json-out", out_json], log)
        with open(out_json, encoding="utf-8") as f:
            return json.load(f)

    def smoke(self) -> dict:
        data = self.prepare()
        r = self.r
        sdir = os.path.join(self.out, "smoke")
        ckpt_dir = os.path.join(sdir, "ckpt")
        if os.path.isdir(sdir):
            shutil.rmtree(sdir)  # a smoke test always starts from nothing
        steps = r["smoke_steps"]
        log = os.path.join(sdir, "train.log")
        text = run_logged(self.train_cmd(ckpt_dir, r["seeds"][0], steps,
                                         max(1, steps // 2), 10), log)
        first = parse_train_log(text)
        # Resume a few steps: proves checkpoint + GradScaler restore on this GPU.
        resumed = run_logged(self.train_cmd(ckpt_dir, r["seeds"][0],
                                            steps + r["smoke_resume_steps"], 0, 0,
                                            resume=os.path.join(ckpt_dir, "final.pt")), log)
        checks = {
            "val_loss_finite": bool(first.get("evals")) and all(
                e["val_loss"] == e["val_loss"] and e["val_loss"] < float("inf")
                for e in first["evals"]),
            "resume_ran": "resuming" in resumed,
            "scaler_restored": ("restored GradScaler" in resumed) if r["amp"] == "fp16" else None,
        }
        tps = first.get("tokens_per_sec") or 0
        full_tokens = r["max_steps"] * self.tokens_per_step
        projection = None
        if tps:
            hours = full_tokens / tps / 3600
            projection = {
                "full_run_tokens": full_tokens,
                "projected_hours_per_seed": round(hours, 2),
                "projected_hours_all_seeds": round(hours * len(r["seeds"]), 2),
                "note": "projection from smoke tokens/sec; excludes evals and data prep",
                # A Kaggle session ends at 12 h; leave room for prep, evals and export.
                "all_seeds_fit_one_session": hours * len(r["seeds"]) < 10,
            }
        smoke = {"provenance": provenance(self.args.device), "train": first,
                 "checks": checks, "projection": projection}
        state = self.state()
        state["smoke"] = smoke
        self.save_state(state)
        print("\nSMOKE RESULT")
        print(json.dumps({"checks": checks, "tokens_per_sec": tps,
                          "fp16_skipped_updates": first.get("fp16_skipped_updates"),
                          "projection": projection}, indent=2), flush=True)
        if projection and not projection["all_seeds_fit_one_session"]:
            print("NOTE: both seeds will not fit one 12 h Kaggle session. Run one seed per "
                  "saved version with --only-seed (ONLY_SEED in the notebook).", flush=True)
        failed = [k for k, v in checks.items() if v is False]
        if failed:
            raise SystemExit(f"smoke test failed: {', '.join(failed)} (see {log})")
        del data
        return smoke

    def full(self) -> dict:
        self.prepare()
        r = self.r
        state = self.state()
        seeds = state.setdefault("seeds", {})
        for seed in self.seeds:
            key = str(seed)
            if seeds.get(key, {}).get("eval"):
                print(f"seed {seed}: already done, skipping", flush=True)
                continue
            sdir = os.path.join(self.out, f"seed_{seed}")
            ckpt_dir = os.path.join(sdir, "ckpt")
            final = os.path.join(ckpt_dir, "final.pt")
            log = os.path.join(sdir, "train.log")
            t0 = time.time()
            if not os.path.exists(final):
                # Resume an interrupted seed from its newest step checkpoint.
                steps = sorted((int(m.group(1)), n) for n in (os.listdir(ckpt_dir)
                               if os.path.isdir(ckpt_dir) else [])
                               if (m := re.match(r"step_(\d+)\.pt$", n)))
                resume = os.path.join(ckpt_dir, steps[-1][1]) if steps else None
                text = run_logged(self.train_cmd(ckpt_dir, seed, r["max_steps"],
                                                 r["eval_interval"], r["eval_batches"],
                                                 resume=resume), log)
            else:
                with open(log, encoding="utf-8") as f:
                    text = f.read()
            entry = {"provenance": provenance(self.args.device),
                     "train": parse_train_log(text),
                     "wall_seconds_this_session": round(time.time() - t0, 1),
                     "checkpoint": final}
            entry["eval"] = self.evaluate(final, os.path.join(sdir, "eval.json"),
                                          os.path.join(sdir, "eval.log"))
            seeds[key] = entry
            self.save_state(state)
        return state

    def export(self) -> dict:
        state = self.state()
        done = [s for s in self.seeds
                if os.path.exists(os.path.join(self.out, f"seed_{s}", "ckpt", "final.pt"))]
        seed = str(done[0] if done else self.seeds[0])
        final = os.path.join(self.out, f"seed_{seed}", "ckpt", "final.pt")
        result: dict = {}
        if not os.path.exists(os.path.join(PKG_DIR, "src", "export_gguf.py")):
            result["skipped"] = ("src/export_gguf.py is not in this checkout (it lands with "
                                 "the GGUF export PR #35). Training results are unaffected; "
                                 "re-run this stage on a checkout that has it.")
        elif not os.path.exists(final):
            result["skipped"] = f"no {final}: run the full stage first"
        if result.get("skipped"):
            print("GGUF export skipped: " + result["skipped"], flush=True)
            state["export"] = result
            self.save_state(state)
            return result
        try:
            import gguf  # noqa: F401
        except ImportError:
            req = os.path.join(PKG_DIR, "requirements-gguf.txt")
            run_logged([sys.executable, "-m", "pip", "install", "-q", "-r", req]
                       if os.path.exists(req) else
                       [sys.executable, "-m", "pip", "install", "-q", "gguf"],
                       os.path.join(self.out, "export.log"))
        gdir = os.path.join(self.out, "gguf")
        os.makedirs(gdir, exist_ok=True)
        f16 = os.path.join(gdir, "model-f16.gguf")
        run_logged([sys.executable, "-m", "src.export_gguf", "--checkpoint", final,
                    "--tokenizer", os.path.join(self.data, "tokenizer.json"),
                    "--out", f16, "--dtype", "f16"], os.path.join(self.out, "export.log"))
        result.update(seed=int(seed), f16_path=f16, f16_bytes=os.path.getsize(f16))
        if not self.args.no_quantize:
            try:
                quantize = self.llama_quantize()
                q8 = os.path.join(gdir, "model-q8_0.gguf")
                run_logged([quantize, f16, q8, "Q8_0"], os.path.join(self.out, "export.log"))
                result.update(q8_0_path=q8, q8_0_bytes=os.path.getsize(q8),
                              llama_cpp_tag=self.r["llama_cpp_tag"])
            except (SystemExit, OSError, RuntimeError) as exc:
                result["q8_0_skipped"] = f"{exc}"
                print(f"Q8_0 quantization skipped: {exc}", flush=True)
        state["export"] = result
        self.save_state(state)
        return result

    def llama_quantize(self) -> str:
        """Path to llama-quantize from the pinned llama.cpp CPU release (downloaded once)."""
        tag = self.r["llama_cpp_tag"]
        arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64",
                "arm64": "arm64"}.get(platform.machine().lower())
        if arch is None or sys.platform != "linux":
            raise RuntimeError(f"no pinned llama.cpp build for {sys.platform}/"
                               f"{platform.machine()}; quantize on your PC instead")
        root = os.path.join(self.data, f"llama.cpp-{tag}")
        found = [os.path.join(d, "llama-quantize") for d, _, files in os.walk(root)
                 if "llama-quantize" in files] if os.path.isdir(root) else []
        if not found:
            url = (f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/"
                   f"llama-{tag}-bin-ubuntu-{arch}.tar.gz")
            tgz = root + ".tar.gz"
            download(url, tgz)
            with tarfile.open(tgz) as t:
                try:
                    t.extractall(root, filter="data")
                except TypeError:  # Python without tarfile extraction filters
                    t.extractall(root)  # noqa: S202 - pinned official release archive
            found = [os.path.join(d, "llama-quantize") for d, _, files in os.walk(root)
                     if "llama-quantize" in files]
        if not found:
            raise RuntimeError(f"llama-quantize not found in {root}")
        os.chmod(found[0], 0o755)
        return found[0]

    # -- report
    def summary(self) -> str:
        state = self.state()
        r = self.r
        text = render_results(state, r, self.overrides)
        path = os.path.join(self.out, "results.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(text)
        print(f"wrote {path}", flush=True)
        return text


def _mean_spread(values: list[float]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    mean = sum(vals) / len(vals)
    if len(vals) == 1:
        return f"{mean:.4f} (1 seed)"
    return f"{mean:.4f} ± {(max(vals) - min(vals)) / 2:.4f} (half-range, {len(vals)} seeds)"


def render_results(state: dict, r: dict, overrides: dict) -> str:
    """results.md from measured values only; a missing value stays blank."""
    lines = []
    if overrides:
        lines.append(f"> **NOT the baseline recipe** - overridden: "
                     f"{json.dumps(overrides, sort_keys=True)}")
        lines.append("")
    data = state.get("data", {})
    seeds = state.get("seeds", {})
    first = next(iter(seeds.values()), {})
    prov = first.get("provenance") or (state.get("smoke") or {}).get("provenance") or {}
    lines += [
        f"# mini-llm {r['name']} results",
        "",
        f"- Run date (UTC): {prov.get('utc', '')}",
        f"- GPU: {prov.get('gpu', '')} (count {prov.get('gpu_count', '')}; only GPU 0 is used)",
        f"- torch / CUDA / Python: {prov.get('torch', '')} / {prov.get('cuda', '')} / "
        f"{prov.get('python', '')}",
        f"- Repo commit: {prov.get('git_commit', '')} (dirty: {prov.get('git_dirty', '')})",
        f"- Data: {data.get('source_url') or data.get('raw_path', '')}, "
        f"{data.get('raw_bytes', '')} bytes, sha256 {data.get('raw_sha256', '')}",
        f"- Stories: {data.get('stories', '')}; tokens train {data.get('train_tokens', '')} / "
        f"val {data.get('val_tokens', '')}; vocab {data.get('vocab_size', '')}",
        f"- Recipe: d{r['d_model']}/L{r['n_layers']}/H{r['n_heads']}/ff{r['d_ff']}, ctx "
        f"{r['context_length']}, batch {r['batch_size']}x{r['grad_accum_steps']}, lr "
        f"{r['learning_rate']}, {r['max_steps']} steps = "
        f"{r['max_steps'] * r['batch_size'] * r['grad_accum_steps'] * r['context_length']:,} "
        f"tokens, --amp {r['amp']}",
        "",
        "| seed | val_loss | val_bpb (full val) | tokens/sec | train time (s) | "
        "fp16 skipped updates | final loss scale |",
        "|---|---|---|---|---|---|---|",
    ]
    for seed in r["seeds"]:
        e = seeds.get(str(seed), {})
        ev, tr = e.get("eval", {}), e.get("train", {})

        def fmt(v, spec):
            return format(v, spec) if isinstance(v, (int, float)) else ""

        lines.append(
            f"| {seed} | {fmt(ev.get('val_loss'), '.4f')} | {fmt(ev.get('val_bpb'), '.4f')} | "
            f"{fmt(tr.get('tokens_per_sec'), ',')} | {fmt(tr.get('train_seconds'), '.0f')} | "
            f"{fmt(tr.get('fp16_skipped_updates'), 'd')} | "
            f"{fmt(tr.get('fp16_final_scale'), '.0f')} |")
    lines += [
        "",
        f"- val_bpb mean: {_mean_spread([seeds.get(str(s), {}).get('eval', {}).get('val_bpb') for s in r['seeds']])}",
        f"- val_loss mean: {_mean_spread([seeds.get(str(s), {}).get('eval', {}).get('val_loss') for s in r['seeds']])}",
    ]
    exp = state.get("export", {})
    if exp.get("skipped"):
        lines.append(f"- GGUF: skipped ({exp['skipped']})")
    else:
        lines.append(f"- GGUF f16 bytes: {exp.get('f16_bytes', '')}; Q8_0 bytes: "
                     f"{exp.get('q8_0_bytes', exp.get('q8_0_skipped', ''))}")
    smoke = state.get("smoke") or {}
    if smoke:
        lines.append(f"- Smoke: {json.dumps(smoke.get('checks'))}; projection "
                     f"{json.dumps(smoke.get('projection'))}")
    first_eval = first.get("eval", {})
    if first_eval.get("samples"):
        lines += ["", "## Sample sheet (first seed in this run)", ""]
        for row in first_eval["samples"]:
            lines += [f"**{row['prompt']}**", "", f"- greedy: {row['greedy']!r}",
                      f"- sampled: {row['sampled']!r}", ""]
    return "\n".join(lines) + "\n"


def default_out_dir() -> str:
    return ("/kaggle/working/minillm-t4" if os.path.isdir("/kaggle/working")
            else os.path.join(PKG_DIR, "checkpoints", "kaggle-t4"))


def default_data_dir() -> str:
    # Large, reproducible files (raw corpus, .bin) stay out of /kaggle/working so
    # they do not count against its 20 GB saved-output limit.
    return ("/tmp/minillm-data" if os.path.isdir("/kaggle/working") else
            os.path.join(PKG_DIR, "checkpoints", "kaggle-data"))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--stage", required=True,
                   choices=("prepare", "smoke", "full", "export", "summary", "all"))
    p.add_argument("--out-dir", default=default_out_dir())
    p.add_argument("--data-dir", default=default_data_dir())
    p.add_argument("--device", default="cuda", choices=("cuda", "cpu", "auto"))
    p.add_argument("--input", default=None,
                   help="local TinyStories-format .txt instead of downloading data_url")
    p.add_argument("--max-input-bytes", type=int, default=None,
                   help="use only the first N bytes of the corpus (test runs only)")
    p.add_argument("--override", action="append", metavar="KEY=VALUE",
                   help="change one recipe value (recorded; results are then marked "
                        "as not the baseline)")
    p.add_argument("--only-seed", type=int, default=None,
                   help="run just this one of the recipe's seeds (one seed per Kaggle "
                        "session when both do not fit 12 h); not a recipe change")
    p.add_argument("--no-quantize", action="store_true",
                   help="skip downloading llama.cpp for the Q8_0 step")
    args = p.parse_args(argv)
    rec = Recipe(args)
    stages = (["smoke", "full", "export", "summary"] if args.stage == "all"
              else [args.stage])
    for stage in stages:
        print(f"\n===== stage: {stage} =====", flush=True)
        getattr(rec, stage)()


if __name__ == "__main__":
    main()
