"""Validation bits-per-byte and a fixed sample sheet for a mini-llm checkpoint.

Per-token validation loss depends on the tokenizer: a bigger vocabulary packs
more bytes into a token, so its loss per token is higher even when the model is
just as good. Bits per byte (bpb) divides out the tokenizer:

    bpb = total target NLL (nats) / (ln 2 * total UTF-8 bytes of those targets)

The NLL is the same per-target-token weighted sum `train.evaluate()` reports as
val_loss (so val_loss * tokens == the numerator). The byte count comes from the
exact target ids that were scored, looked up in a per-id byte-length table built
from the byte-level vocabulary. Special tokens (<pad> <unk> <bos> <eos>) decode
to no text, so they count 0 bytes; their NLL is still counted, which charges the
model for predicting story boundaries. (nanochat instead drops 0-byte targets
from the NLL as well, so numbers are close to but not identical with its bpb.)

Run as a module:

    python -m src.evaluate --checkpoint checkpoints/final.pt
    python -m src.evaluate --checkpoint checkpoints/final.pt --samples --json-out eval.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LN2 = math.log(2.0)

# Fixed prompts and sampling settings for the sample sheet. Changing any of them
# makes sheets from different runs incomparable, so they are constants, not flags.
SAMPLE_PROMPTS = (
    "Once upon a time",
    "One day, a little girl named Lily",
    "The dog was very",
    "Tom and his mom went to the",
    "The sun was shining and",
)
SAMPLE_TOKENS = 60
SAMPLE_SEED = 1234
SAMPLE_TEMPERATURE = 0.8
SAMPLE_TOP_K = 40


def _byte_decoder() -> dict[str, int]:
    """GPT-2 byte-level map, unicode symbol -> byte value (the inverse of bytes_to_unicode).

    The `tokenizers` ByteLevel pre-tokenizer uses this exact table; a test checks
    that its alphabet() is the same 256 symbols.
    """
    bs = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return {chr(c): b for b, c in zip(bs, cs, strict=True)}


def token_byte_lengths(tok, vocab_size: int | None = None) -> torch.Tensor:
    """UTF-8 byte length of every token id, as an int64 tensor of length vocab_size.

    Added/special tokens decode to nothing (decode() skips them) and count 0.
    Ids beyond the tokenizer's vocabulary (a model built wider than its
    tokenizer) also count 0; a target can only land there if the data was
    encoded with a different tokenizer, which train/generate already refuse.
    """
    decoder = _byte_decoder()
    vocab = tok.get_vocab(with_added_tokens=False)
    special_ids = set()
    for added in (
        tok.get_added_tokens_decoder().values() if hasattr(tok, "get_added_tokens_decoder") else ()
    ):
        special_ids.add(tok.token_to_id(added.content))
    size = vocab_size if vocab_size is not None else tok.get_vocab_size(with_added_tokens=True)
    lengths = torch.zeros(size, dtype=torch.long)
    for token, idx in vocab.items():
        if idx >= size or idx in special_ids:
            continue
        try:
            lengths[idx] = len(bytes(decoder[ch] for ch in token))
        except KeyError as exc:
            raise ValueError(
                f"token {token!r} (id {idx}) is not byte-level BPE; bits per byte "
                "needs a ByteLevel tokenizer like the one prepare_data.py trains"
            ) from exc
    return lengths


def bits_per_byte(total_nll_nats: float, total_bytes: int) -> float:
    """Convert a summed NLL in nats over targets holding total_bytes bytes into bpb."""
    if total_bytes <= 0:
        return float("nan")
    return total_nll_nats / (LN2 * total_bytes)


def _null_context():
    return contextlib.nullcontext()


@torch.no_grad()
def evaluate_stats(
    model,
    loader,
    batches: int,
    device: torch.device,
    chunk_size: int = 0,
    byte_lengths: torch.Tensor | None = None,
    autocast=None,
) -> dict:
    """Token-weighted val loss over the first `batches` batches, plus bytes/bpb.

    The loss arithmetic is exactly what train.evaluate() has always done (it now
    delegates here), so val_loss is unchanged. With byte_lengths the same pass
    also counts the UTF-8 bytes of the scored targets and reports bpb; no second
    forward pass is needed because the NLL sum is already being accumulated.
    `autocast` is an optional zero-argument callable returning the context the
    forward runs under (mixed precision); None means plain fp32.
    """
    # Imported here: train imports this module, so a top-level import would cycle.
    from src.train import _chunked_loss

    # batches <= 0 would average nothing and report a fake 0.0 loss.
    if not isinstance(batches, int) or batches < 1:
        raise ValueError(f"batches must be a positive int (>= 1), got {batches!r}")
    model.eval()
    n_batches = len(loader)
    if n_batches == 0:
        raise ValueError("cannot evaluate on an empty loader")
    ctx = autocast or _null_context
    table = byte_lengths.to(device) if byte_lengths is not None else None
    # Never cycle: re-reading a short val set would weight its batches more than
    # their share and report a loss that is not the corpus loss. Weight by target
    # tokens so a ragged final batch counts for the tokens it actually has.
    limit = min(batches, n_batches)
    total_loss, total_tokens, total_bytes = 0.0, 0, 0
    for i, (x, y) in enumerate(loader):
        if i >= limit:
            break
        x, y = x.to(device), y.to(device)
        with ctx():
            if chunk_size > 0:
                loss = _chunked_loss(model, x, y, chunk_size)
            else:
                _, loss = model(x, y)
        n_tokens = y.numel()
        total_loss += loss.item() * n_tokens
        total_tokens += n_tokens
        if table is not None:
            total_bytes += int(table[y].sum().item())
    model.train()
    loss = total_loss / max(1, total_tokens)
    stats = {"loss": loss, "tokens": total_tokens, "nll_nats": total_loss}
    if table is not None:
        stats["bytes"] = total_bytes
        stats["bpb"] = bits_per_byte(total_loss, total_bytes)
    return stats


def sample_sheet(model, tok, device: torch.device, autocast=None) -> list[dict]:
    """Greedy and seeded T=0.8/top-k 40 continuations of the fixed prompts.

    Seeded per prompt, so the sheet is the same for the same checkpoint no
    matter how many prompts ran before it or what the training RNG did.
    """
    from src.generate import generate_tokens
    from src.tokenizer import decode, encode

    ctx = autocast or _null_context
    eos = tok.token_to_id("<eos>")
    sheet = []
    for i, prompt in enumerate(SAMPLE_PROMPTS):
        ids = encode(tok, prompt, add_bos=True)
        idx = torch.tensor([ids], dtype=torch.long, device=device)
        row = {"prompt": prompt}
        with ctx():
            greedy = generate_tokens(model, idx, SAMPLE_TOKENS, temperature=0.0, eos_id=eos)
            # fork_rng restores the caller's CPU (and CUDA) RNG afterwards, so the
            # sheet neither depends on nor disturbs any other random stream.
            cuda_devices = [device] if device.type == "cuda" else []
            with torch.random.fork_rng(devices=cuda_devices):
                torch.manual_seed(SAMPLE_SEED + i)
                sampled = generate_tokens(
                    model,
                    idx,
                    SAMPLE_TOKENS,
                    temperature=SAMPLE_TEMPERATURE,
                    top_k=SAMPLE_TOP_K,
                    eos_id=eos,
                )
        row["greedy"] = decode(tok, greedy[0, len(ids) :].tolist())
        row["sampled"] = decode(tok, sampled[0, len(ids) :].tolist())
        sheet.append(row)
    model.train()
    return sheet


def main(argv: list[str] | None = None) -> dict:
    from src.config import Config
    from src.dataset import build_dataloader
    from src.generate import check_tokenizer_provenance
    from src.tokenizer import load_tokenizer
    from src.train import (
        amp_autocast_factory,
        load_model,
        read_checkpoint,
        resolve_amp,
        resolve_device,
    )

    parser = argparse.ArgumentParser(description="Validation bits-per-byte + sample sheet")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument(
        "--val-bin", default=None, help="defaults to the val .bin the checkpoint recorded"
    )
    parser.add_argument(
        "--tokenizer",
        default=None,
        help="defaults to the tokenizer the checkpoint recorded; verified against its digest",
    )
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument(
        "--batches", type=int, default=None, help="val batches to score (default: all of them)"
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=None,
        help="val window stride (default: context_length, i.e. "
        "non-overlapping windows, so every val token is scored once)",
    )
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument(
        "--amp",
        choices=("off", "fp16", "bf16"),
        default="off",
        help="autocast the forward pass (fp16 needs CUDA); off is fp32",
    )
    parser.add_argument(
        "--samples", action="store_true", help="also print the fixed seeded sample sheet"
    )
    parser.add_argument("--json-out", default=None, help="write the results as JSON")
    args = parser.parse_args(argv)

    ckpt = read_checkpoint(args.checkpoint)
    model, cfg = load_model(ckpt)
    assert isinstance(cfg, Config)
    tokenizer_path = args.tokenizer or cfg.tokenizer_path
    check_tokenizer_provenance(ckpt, tokenizer_path)
    tok = load_tokenizer(tokenizer_path)
    device = resolve_device(args.device)
    autocast = amp_autocast_factory(device, resolve_amp(args.amp, device))
    model.to(device)

    val_bin = args.val_bin or cfg.val_bin
    stride = args.stride or cfg.context_length
    loader = build_dataloader(
        val_bin, cfg.context_length, args.batch_size or cfg.batch_size, shuffle=False, stride=stride
    )
    batches = args.batches or len(loader)
    stats = evaluate_stats(
        model,
        loader,
        batches,
        device,
        byte_lengths=token_byte_lengths(tok, cfg.vocab_size),
        autocast=autocast,
    )
    result = {
        "checkpoint": os.path.abspath(args.checkpoint),
        "step": int(ckpt["step"]),
        "val_bin": os.path.abspath(val_bin),
        "stride": stride,
        "batches": min(batches, len(loader)),
        "amp": args.amp,
        "val_loss": stats["loss"],
        "val_bpb": stats["bpb"],
        "val_tokens": stats["tokens"],
        "val_bytes": stats["bytes"],
    }
    print(
        f"val_loss {stats['loss']:.4f} val_bpb {stats['bpb']:.4f} "
        f"({stats['tokens']} target tokens, {stats['bytes']} bytes, stride {stride})"
    )
    if args.samples:
        result["samples"] = sample_sheet(model, tok, device, autocast)
        for row in result["samples"]:
            print(f"\n## {row['prompt']}\n[greedy]  {row['greedy']}\n[sampled] {row['sampled']}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"wrote {args.json_out}")
    return result


if __name__ == "__main__":
    main()
