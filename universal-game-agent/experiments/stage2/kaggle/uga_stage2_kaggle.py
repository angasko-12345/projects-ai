"""Run the UGA Stage 2 benchmark on a Kaggle GPU notebook (T4 x2), with resume.

Paste into a Kaggle notebook cell as ``!python uga_stage2_kaggle.py ...`` after
downloading it, or run it from a clone. It never signs in anywhere; it needs the
notebook's Internet toggle ON (git clone + pip).

    # 1) measure this session's real speed (about 3-5 min), prints steps/s per GPU process
    python uga_stage2_kaggle.py --throughput
    # 2) one arm/seed (resumes automatically from /kaggle/working or --restore-from)
    python uga_stage2_kaggle.py --arm specialist --game pong --seed 0 --max-hours 11
    # 3) whole plan for two seeds, one process per GPU, stop cleanly before the 12 h limit
    python uga_stage2_kaggle.py --all-seeds 0 1 --max-hours 11
    # next session: attach the previous version's output as an input, then
    python uga_stage2_kaggle.py --all-seeds 0 1 --max-hours 11 \
        --restore-from /kaggle/input/<your-notebook-output>/uga_stage2

Artifacts (checkpoints, telemetry.csv, progress.json, result.json, summary/) go to
``--artifacts`` (default /kaggle/working/uga_stage2). Exit code 3 from the runner
means "paused by the wall-clock guard": rerun the same command next session.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = "https://github.com/angasko-12345/projects-ai.git"
BRANCH = "upgrade/uga-stage2-benchmark"
CONFIG = "experiments/stage2/benchmark.yaml"


def sh(cmd, **kw):
    print("+", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(cmd, check=False, **kw).returncode


def ensure_repo(workdir: Path, branch: str) -> Path:
    repo = workdir / "projects-ai"
    if not (repo / ".git").exists():
        if sh(["git", "clone", "--depth", "1", "-b", branch, REPO, str(repo)]):
            raise SystemExit("git clone failed (is the notebook's Internet toggle on?)")
    else:
        sh(["git", "-C", str(repo), "pull", "--ff-only"])
    uga = repo / "universal-game-agent"
    # torch is preinstalled on Kaggle GPU images; this adds gymnasium/pyyaml/mss + ale-py (ROMs bundled)
    if sh([sys.executable, "-m", "pip", "install", "-q", "-r", str(uga / "requirements.txt"),
           "-r", str(uga / "requirements-atari.txt")]):
        raise SystemExit("pip install failed")
    return uga


def restore(src: Path | None, artifacts: Path) -> None:
    if src is None:
        return
    if not src.exists():
        raise SystemExit(f"--restore-from {src} does not exist (attach the previous output as an input)")
    for path in src.rglob("*"):
        if path.is_file():
            dst = artifacts / path.relative_to(src)
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)
    print(f"restored {src} -> {artifacts}", flush=True)


def runner(uga: Path, args, gpu: int | None, log: Path):
    env = dict(os.environ)
    if gpu is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu)
    cmd = [sys.executable, "-m", "training.stage2", *args]
    print("+", " ".join(cmd), f"(gpu={gpu}, log={log})", flush=True)
    return subprocess.Popen(cmd, cwd=str(uga), env=env, stdout=open(log, "a"), stderr=subprocess.STDOUT)


def gpu_count() -> int:
    try:
        import torch

        return torch.cuda.device_count()
    except ImportError:
        return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workdir", default="/kaggle/working")
    p.add_argument("--artifacts", default="/kaggle/working/uga_stage2")
    p.add_argument("--branch", default=BRANCH)
    p.add_argument("--restore-from", default=None)
    p.add_argument("--throughput", action="store_true")
    p.add_argument("--throughput-scale", type=float, default=0.005,
                   help="budget scale of the throughput probe (0.005 = 50k multi-game steps)")
    p.add_argument("--arm")
    p.add_argument("--game")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--all-seeds", type=int, nargs="*")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--max-hours", type=float, default=11.0)
    p.add_argument("--threads", type=int, default=2, help="torch CPU threads per process (4 CPUs / 2 GPUs)")
    a = p.parse_args(argv)
    work, art = Path(a.workdir), Path(a.artifacts)
    art.mkdir(parents=True, exist_ok=True)
    uga = ensure_repo(work, a.branch)
    restore(Path(a.restore_from) if a.restore_from else None, art)
    n_gpu = gpu_count()
    print(f"GPUs visible: {n_gpu}", flush=True)
    common = ["--config", CONFIG, "--threads", str(a.threads), "--device", "cuda" if n_gpu else "cpu"]
    t0 = time.time()
    if a.throughput:
        procs = []
        for g in range(max(1, min(2, n_gpu))):
            tmp = art / f"throughput_gpu{g}"
            shutil.rmtree(tmp, ignore_errors=True)
            procs.append((tmp, runner(uga, ["run", *common, "--artifacts", str(tmp), "--arm", "multi",
                                            "--seed", "99", "--scale", str(a.throughput_scale), "--eval-episodes", "1",
                                            "--eval-max-frames", "2000"], g if n_gpu else None,
                                     art / f"throughput_gpu{g}.log")))
        out = {}
        for tmp, proc in procs:
            proc.wait()
            res = json.loads((tmp / "runs" / "multi_multi_s99" / "result.json").read_text())
            out[tmp.name] = {"steps": res["total_timesteps"], "train_seconds": round(res["train_seconds"], 1),
                             "steps_per_second": round(res["steps_per_second"], 1), "device": res["device"],
                             "hardware": res["hardware"]}
        (art / "throughput.json").write_text(json.dumps(out, indent=2) + "\n")
        print(json.dumps(out, indent=2))
        sps = sum(v["steps_per_second"] for v in out.values())
        print(f"aggregate {sps:.0f} steps/s -> full benchmark (96.0M steps) ~ {96e6 / max(sps, 1) / 3600:.1f} h "
              f"of session time, plus evaluation", flush=True)
        return 0
    if a.all_seeds is not None:
        gpus = list(range(n_gpu)) or [None]
        procs = []
        for i, s in enumerate(a.all_seeds):
            procs.append(runner(uga, ["all", *common, "--artifacts", str(art), "--seeds", str(s),
                                      "--scale", str(a.scale), "--max-hours", str(a.max_hours)],
                                gpus[i % len(gpus)], art / f"seed{s}.log"))
            if len(procs) >= len(gpus):  # at most one process per GPU at a time
                procs.pop(0).wait()
        codes = [pr.wait() for pr in procs]
        print(f"exit codes {codes} (3 = paused, rerun to resume)", flush=True)
    elif a.arm:
        code = runner(uga, ["run", *common, "--artifacts", str(art), "--arm", a.arm, "--seed", str(a.seed),
                            *(["--game", a.game] if a.game else []), "--scale", str(a.scale),
                            "--max-hours", str(a.max_hours)], 0 if n_gpu else None,
                      art / f"{a.arm}_{a.game or 'multi'}_s{a.seed}.log").wait()
        print(f"runner exit code {code} (3 = paused, rerun to resume)", flush=True)
    else:
        p.error("give --throughput, --arm or --all-seeds")
    runner(uga, ["random", "--config", CONFIG, "--artifacts", str(art), "--seeds", "0", "1", "2"], None,
           art / "random.log").wait()
    runner(uga, ["aggregate", "--config", CONFIG, "--artifacts", str(art), "--out-dir", str(art / "summary")],
           None, art / "aggregate.log").wait()
    print(f"session wall time {(time.time() - t0) / 3600:.2f} h; summary in {art / 'summary'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
