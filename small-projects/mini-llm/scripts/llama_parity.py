"""Compare a mini-llm checkpoint with its GGUF running in a real llama-server.

    python scripts/llama_parity.py --checkpoint checkpoints/final.pt \
        --gguf model-f32.gguf --llama-cpp-dir C:\\llama.cpp

For each prompt it asks llama-server /v1/completions for the next token's top-k
logprobs (temperature 0, <bos> added by the server because the GGUF says
add_bos_token) and compares them with MiniGPT's softmax on [<bos>] + ids.
Reports whether the top-k ids/order match (exactly, or up to rank swaps between
near-tied tokens, |p_a - p_b| < --max-dp; the greedy token must match exactly)
and the max |dp|. Also compares
llama-tokenize against the HF tokenizer on a few strings when llama-tokenize is
present. Exits 1 if any comparison fails the thresholds.

llama-server returns no logprobs when the greedy token is an incomplete UTF-8
sequence; such prompts are reported as skipped, not as passes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import time
import urllib.request

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.generate import check_tokenizer_provenance  # noqa: E402
from src.tokenizer import load_tokenizer  # noqa: E402
from src.train import load_model, read_checkpoint  # noqa: E402

PROMPTS = ["The fox", "Once upon a time", "The cat sat on the", "A small dog",
           "She said", "In the garden", "One day"]
TOKENIZE = ["The fox jumped.", "Hello,\n\nworld!  two  spaces\ttab", "café ünïcode 🚀 3.14 don't"]


def topk_verdict(ours: list[int], theirs: list[int], p: np.ndarray, tie_tol: float) -> str:
    """"identical", "same up to near-ties", or "DIFFERENT".

    The greedy (top-1) token must match exactly. Below it, two tokens whose
    mini-llm probabilities differ by less than tie_tol may swap ranks: that is
    float rounding between two runtimes, not a mapping error.
    """
    if ours == theirs:
        return "identical"
    if len(ours) != len(theirs) or ours[0] != theirs[0]:
        return "DIFFERENT"
    if all(abs(p[a] - p[b]) < tie_tol for a, b in zip(ours, theirs, strict=True)):
        return "same up to near-ties"
    return "DIFFERENT"


def find_bin(root: str, name: str) -> str | None:
    for sub in ("", "bin", os.path.join("build", "bin")):
        for exe in (name, name + ".exe"):
            p = os.path.join(root, sub, exe)
            if os.path.isfile(p):
                return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--gguf", required=True)
    ap.add_argument("--llama-cpp-dir", default=os.environ.get("LLAMA_CPP_DIR"))
    ap.add_argument("--tokenizer", default=None)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--max-dp", type=float, default=2e-3)
    ap.add_argument("--extra-server-args", default="",
                    help="e.g. '-ngl 99' to offload to a Vulkan GPU")
    args = ap.parse_args()
    if not args.llama_cpp_dir:
        ap.error("--llama-cpp-dir (or LLAMA_CPP_DIR) is required")

    ckpt = read_checkpoint(args.checkpoint)
    model, cfg = load_model(ckpt)
    tok_path = args.tokenizer or cfg.tokenizer_path
    check_tokenizer_provenance(ckpt, tok_path)
    tok = load_tokenizer(tok_path)
    ok = True

    tokenize = find_bin(args.llama_cpp_dir, "llama-tokenize")
    if tokenize:
        for text in TOKENIZE:
            out = subprocess.run([tokenize, "-m", args.gguf, "-p", text, "--ids", "--no-bos",
                                  "--log-disable"], capture_output=True, text=True, check=True)
            theirs = json.loads([ln for ln in out.stdout.splitlines() if ln.startswith("[")][-1])
            same = theirs == tok.encode(text).ids
            ok &= same
            print(f"tokenize {text!r}: {'identical' if same else 'DIFFERENT'}")

    server = find_bin(args.llama_cpp_dir, "llama-server")
    if not server:
        print(f"no llama-server under {args.llama_cpp_dir}", file=sys.stderr)
        return 1
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([server, "-m", args.gguf, "--host", "127.0.0.1", "--port", str(port),
                             "-c", str(cfg.context_length), *args.extra_server_args.split()],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{port}"
    compared = 0
    try:
        deadline = time.time() + 120
        while True:
            try:
                if opener.open(base + "/health", timeout=2).status == 200:
                    break
            except OSError:
                pass
            if time.time() > deadline or proc.poll() is not None:
                print("llama-server did not become healthy", file=sys.stderr)
                return 1
            time.sleep(0.3)
        for prompt in PROMPTS:
            body = json.dumps({"prompt": prompt, "max_tokens": 1, "logprobs": args.top_k,
                               "temperature": 0}).encode()
            req = urllib.request.Request(base + "/v1/completions", data=body,
                                         headers={"Content-Type": "application/json"})
            lp = json.load(opener.open(req, timeout=60))["choices"][0]["logprobs"]
            if not lp:
                print(f"{prompt!r}: skipped (server returned no logprobs)")
                continue
            top = lp["content"][0]["top_logprobs"]
            ids = [tok.token_to_id("<bos>")] + tok.encode(prompt).ids
            with torch.no_grad():
                p = torch.softmax(model(torch.tensor([ids]))[0][0, -1].double(), -1).numpy()
            ours = [int(i) for i in np.argsort(-p)[: args.top_k]]
            theirs = [t["id"] for t in top]
            dp = max(abs(math.exp(t["logprob"]) - p[t["id"]]) for t in top)
            verdict = topk_verdict(ours, theirs, p, args.max_dp)
            ok &= verdict != "DIFFERENT" and dp < args.max_dp
            compared += 1
            print(f"{prompt!r}: top-{args.top_k} {verdict}, "
                  f"max |dp| {dp:.2e}, p(top1) mini-llm {p[ours[0]]:.4f} "
                  f"llama.cpp {math.exp(top[0]['logprob']):.4f}")
    finally:
        proc.terminate()
        proc.wait(10)
    if compared == 0:
        print("no prompt could be compared", file=sys.stderr)
        return 1
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
