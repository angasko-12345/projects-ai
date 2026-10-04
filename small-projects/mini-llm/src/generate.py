"""Autoregressive text generation from a checkpoint."""

from __future__ import annotations

import argparse
import math
import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import file_sha256
from src.tokenizer import decode, load_tokenizer
from src.train import load_model, read_checkpoint

MIN_TEMPERATURE = 1e-3  # below this, logits/temperature overflow to inf/nan


@torch.no_grad()
def generate_tokens(
    model,
    idx: torch.Tensor,
    n_tokens: int,
    temperature: float = 1.0,
    top_k: int | None = None,
    eos_id: int | None = None,
) -> torch.Tensor:
    """Append up to n_tokens samples. Truncates conditioning to model context length.

    temperature 0 is greedy sampling. Stops early if eos_id is sampled.
    """
    if not math.isfinite(temperature) or temperature < 0:
        raise ValueError(f"temperature must be a finite value >= 0, got {temperature}")
    if top_k is not None and top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}")
    model.eval()
    for _ in range(n_tokens):
        cond = idx[:, -model.context_length :]
        logits, _ = model(cond)
        logits = logits[:, -1, :]
        if temperature == 0:
            next_id = logits.argmax(dim=-1, keepdim=True)
        else:
            logits = logits / max(temperature, MIN_TEMPERATURE)
            if top_k is not None:
                k = min(top_k, logits.size(-1))
                cutoff = torch.topk(logits, k).values[:, -1:]
                logits = torch.where(
                    logits < cutoff,
                    torch.tensor(float("-inf"), device=logits.device),
                    logits,
                )
            probs = torch.softmax(logits, dim=-1)
            next_id = torch.multinomial(probs, num_samples=1)
        done = eos_id is not None and bool((next_id == eos_id).all())
        idx = torch.cat([idx, next_id], dim=1)
        if done:
            break
    return idx


def check_tokenizer_provenance(ckpt: dict, tokenizer_path: str) -> None:
    """Refuse to decode with a tokenizer the checkpoint was not trained against.

    The checkpoint records the tokenizer by digest, so neither a --tokenizer
    override nor a moved artifact tree can quietly produce text from the wrong
    vocabulary. A checkpoint with no recorded provenance cannot be verified; that
    is reported, not assumed to be fine.
    """
    recorded = ckpt.get("data_provenance")
    if not recorded:
        print("warning: this checkpoint records no tokenizer provenance, so "
              f"{tokenizer_path} cannot be verified against it", file=sys.stderr)
        return
    actual = file_sha256(tokenizer_path)
    if actual != recorded["tokenizer_sha256"]:
        raise SystemExit(
            f"refusing to generate: {tokenizer_path} is not the tokenizer this checkpoint "
            f"was trained with (sha256 {actual[:12]}... != recorded "
            f"{str(recorded['tokenizer_sha256'])[:12]}...).\n"
            f"  checkpoint tokenizer: {recorded['tokenizer_path']}\n"
            "Pass --tokenizer with the tokenizer the run used, or generate from the "
            "checkpoint whose data matches."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate text with mini-llm")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--tokens", type=int, default=100)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--tokenizer", default=None,
                        help="defaults to the tokenizer path stored in the checkpoint config")
    parser.add_argument("--no-eos-stop", action="store_true",
                        help="keep sampling past <eos>")
    parser.add_argument("--seed", type=int, default=None,
                        help="seed the sampling RNG; omit for a different sample each run")
    args = parser.parse_args()

    if args.seed is not None:
        # Same checkpoint + prompt + seed + settings => same tokens.
        torch.manual_seed(args.seed)

    model, cfg = load_model(args.checkpoint)

    tokenizer_path = args.tokenizer or cfg.tokenizer_path
    check_tokenizer_provenance(read_checkpoint(args.checkpoint), tokenizer_path)
    tok = load_tokenizer(tokenizer_path)
    prompt_ids = tok.encode(args.prompt).ids if args.prompt else [tok.token_to_id("<bos>")]
    idx = torch.tensor([prompt_ids], dtype=torch.long)
    out = generate_tokens(
        model, idx, args.tokens, args.temperature, args.top_k,
        eos_id=None if args.no_eos_stop else tok.token_to_id("<eos>"),
    )
    print(decode(tok, out[0].tolist()))


if __name__ == "__main__":
    main()
