"""Train BPE tokenizer on raw text, then encode + split into .bin files + meta.json."""

from __future__ import annotations

import argparse
import json
import os

import numpy as np

from src.config import meta_path_for
from src.tokenizer import encode, load_tokenizer, train_bpe_tokenizer

DTYPE = np.uint16
MAX_VOCAB = 65536  # DTYPE capacity; ids above this would wrap silently


def encode_corpus(tok, text: str) -> list[int]:
    """Encode text as <bos> passage <eos> ... so the model learns to start and stop.

    Generation prompts with <bos> and is expected to emit <eos>; a corpus without
    them never trains either token.
    """
    eos = tok.token_to_id("<eos>")
    ids: list[int] = []
    for passage in text.split("\n\n"):
        passage = passage.strip()
        if not passage:
            continue
        ids.extend(encode(tok, passage, add_bos=True))
        ids.append(eos)
    if not ids:
        raise SystemExit("no non-empty passages in the input text")
    return ids


def split_tokens(ids: np.ndarray, val_frac: float, context_length: int) -> tuple[np.ndarray, np.ndarray]:
    """Hold out the last share of tokens, but never less than one val sample."""
    n_val = max(context_length + 2, int(len(ids) * val_frac))
    n_train = len(ids) - n_val
    if n_train < context_length + 2:
        raise SystemExit(
            f"corpus has {len(ids)} tokens; context_length {context_length} needs at "
            f"least {2 * (context_length + 2)} for a train and a val sample "
            f"({n_val} would go to val, {n_train} to train). Lower --context-length, "
            "raise --val-frac, or use a larger corpus"
        )
    return ids[:n_train], ids[n_train:]


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare tokenizer + binary datasets")
    parser.add_argument("--input", default="data/raw/train.txt")
    parser.add_argument("--tokenizer-out", default="data/tokenizer.json")
    parser.add_argument("--train-out", default="data/processed/train.bin")
    parser.add_argument("--val-out", default="data/processed/val.bin")
    parser.add_argument("--vocab-size", type=int, default=8192)
    parser.add_argument("--val-frac", type=float, default=0.01)
    parser.add_argument("--min-frequency", type=int, default=2)
    parser.add_argument(
        "--context-length",
        type=int,
        default=512,
        help="model context the split must support; also the val set minimum size",
    )
    parser.add_argument("--skip-training", action="store_true",
                        help="reuse existing tokenizer instead of training")
    args = parser.parse_args()

    if args.vocab_size <= 0 or args.vocab_size > MAX_VOCAB:
        raise SystemExit(f"--vocab-size must be in 1..{MAX_VOCAB} (DTYPE is {DTYPE.__name__})")

    if args.skip_training:
        tok = load_tokenizer(args.tokenizer_out)
    else:
        print(f"training BPE tokenizer (vocab={args.vocab_size}) on {args.input}")
        tok = train_bpe_tokenizer(
            [args.input], args.tokenizer_out,
            vocab_size=args.vocab_size, min_frequency=args.min_frequency,
        )
        print(f"saved tokenizer to {args.tokenizer_out} (vocab={tok.get_vocab_size()})")

    with open(args.input, encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        raise SystemExit(f"input {args.input} is empty")

    ids = np.array(encode_corpus(tok, text), dtype=np.int64)
    vocab_size = tok.get_vocab_size()
    if ids.max() >= MAX_VOCAB:
        raise SystemExit(f"token id {ids.max()} does not fit in {DTYPE.__name__}")
    if vocab_size > MAX_VOCAB:
        raise SystemExit(f"tokenizer has {vocab_size} ids, {DTYPE.__name__} holds {MAX_VOCAB}")

    train_ids, val_ids = split_tokens(ids, args.val_frac, args.context_length)
    print(f"tokens: total={len(ids)} train={len(train_ids)} val={len(val_ids)}")

    os.makedirs(os.path.dirname(args.train_out) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(args.val_out) or ".", exist_ok=True)
    train_ids.astype(DTYPE).tofile(args.train_out)
    val_ids.astype(DTYPE).tofile(args.val_out)

    # The model must be built for the vocabulary the data was actually encoded
    # with, so publish it where Config can pick it up.
    meta = {
        "vocab_size": vocab_size,
        "train_tokens": int(len(train_ids)),
        "val_tokens": int(len(val_ids)),
        "dtype": DTYPE.__name__,
        "context_length": args.context_length,
    }
    meta_path = meta_path_for(args.train_out)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"wrote {args.train_out} and {args.val_out}")
    print(f"wrote {meta_path} (vocab_size={vocab_size})")


if __name__ == "__main__":
    main()
