"""Train BPE tokenizer on raw text, then encode + split into .bin files + meta.json.

The encoder streams the input in fixed-size text chunks and spills encoded
token IDs to a temporary file, so a TinyStories-scale corpus substantially
larger than RAM never sits in memory whole. Peak usage is one text chunk plus
one bounded write buffer plus a batch of passages (TinyStories passages are
kilobytes); there is no whole-file read and no whole-corpus ID list.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

import numpy as np

from src.config import file_sha256, meta_path_for
from src.tokenizer import encode, load_tokenizer, train_bpe_tokenizer

DTYPE = np.uint16
MAX_VOCAB = 65536  # DTYPE capacity; ids above this would wrap silently

READ_CHARS = 1 << 20  # text streaming chunk: 1 MiB of UTF-8 text per read
WRITE_TOKENS = 1 << 18  # bounded write buffer: 256k ids (~0.5 MiB as uint16)
COPY_TOKENS = 1 << 20  # file-split copy chunk: 1M ids (~2 MiB) per read
ENCODE_BATCH_SIZE = 1000  # passages per batch for tokenizer.encode_batch()
PROGRESS_INTERVAL = 10000  # report progress every N passages


def encode_passage(tok, eos: int, passage: str) -> list[int]:
    """Encode one stripped non-empty passage as <bos> ... <eos>.

    Only this passage's IDs are materialized; callers must not accumulate them.
    """
    ids = encode(tok, passage, add_bos=True)
    ids.append(eos)
    return ids


def encode_corpus(tok, text: str) -> list[int]:
    """Encode text as <bos> passage <eos> ... so the model learns to start and stop.

    Generation prompts with <bos> and is expected to emit <eos>; a corpus without
    them never trains either token.

    Small-corpus reference only: materializes the whole ID list, so main() never
    calls this on the real input. Kept for tests and as the semantics contract.
    """
    eos = tok.token_to_id("<eos>")
    ids: list[int] = []
    for passage in text.split("\n\n"):
        passage = passage.strip()
        if not passage:
            continue
        ids.extend(encode_passage(tok, eos, passage))
    if not ids:
        raise SystemExit("no non-empty passages in the input text")
    return ids


def iter_passages(path: str):
    """Yield stripped non-empty passages with exact ``text.split("\\n\\n")`` semantics.

    The carry holds the text after the last delimiter seen so far, which no
    future chunk can alter, so chunk boundaries never change the split.
    """
    with open(path, encoding="utf-8") as f:
        carry = ""
        while True:
            chunk = f.read(READ_CHARS)
            if not chunk:
                break
            carry += chunk
            parts = carry.split("\n\n")
            carry = parts.pop()
            for part in parts:
                passage = part.strip()
                if passage:
                    yield passage
        tail = carry.strip()
        if tail:
            yield tail


def encode_file_to_temp(tok, input_path: str, tmp_path: str) -> tuple[int, int]:
    """Encode input_path passage-by-passage into tmp_path as raw uint16 IDs.

    Uses batched tokenizer.encode_batch() for throughput. Streams passages from
    disk, accumulates them into batches, encodes the batch, adds <bos>/<eos>,
    and spills to a bounded write buffer. Peak memory is one text chunk plus
    one batch of passages plus one write buffer; no whole-corpus data in RAM.

    Returns (total_ids, max_id). Raises the same SystemExit messages the old
    whole-file path raised for empty / passage-free input.
    """
    bos = tok.token_to_id("<bos>")
    eos = tok.token_to_id("<eos>")
    total = 0
    id_max = -1
    pending: list[int] = []  # bounded write buffer, flushed every WRITE_TOKENS ids
    batch: list[str] = []  # passages waiting for batch encoding
    passage_count = 0
    start_time = time.time()
    last_report_time = start_time
    last_report_passages = 0

    def flush_batch(batch_texts: list[str]) -> None:
        nonlocal total, id_max, pending
        if not batch_texts:
            return
        # Batch encode without special tokens; we add <bos>/<eos> manually.
        encodings = tok.encode_batch(batch_texts, add_special_tokens=False)
        for enc in encodings:
            ids = [bos] + enc.ids + [eos]
            m = max(ids)
            if m > id_max:
                id_max = m
            pending.extend(ids)
            total += len(ids)
            if len(pending) >= WRITE_TOKENS:
                np.array(pending, dtype=np.int64).astype(DTYPE).tofile(out)
                del pending[:]

    def maybe_report_progress() -> None:
        nonlocal last_report_time, last_report_passages
        now = time.time()
        # Report every PROGRESS_INTERVAL passages or every 30 seconds, whichever comes first
        if (passage_count - last_report_passages >= PROGRESS_INTERVAL) or \
           (now - last_report_time >= 30.0):
            elapsed = now - start_time
            rate = passage_count / elapsed if elapsed > 0 else 0
            print(f"  encoded {passage_count} passages ({total:,} tokens, {rate:.0f} passages/s)", file=sys.stderr)
            last_report_time = now
            last_report_passages = passage_count

    with open(tmp_path, "wb") as out:
        for passage in iter_passages(input_path):
            batch.append(passage)
            passage_count += 1
            if len(batch) >= ENCODE_BATCH_SIZE:
                flush_batch(batch)
                batch.clear()
                maybe_report_progress()
        # Flush any remaining passages
        if batch:
            flush_batch(batch)
            batch.clear()
        if pending:
            np.array(pending, dtype=np.int64).astype(DTYPE).tofile(out)
            del pending[:]

    # Final progress report
    elapsed = time.time() - start_time
    if passage_count > 0:
        print(f"  encoded {passage_count} passages ({total:,} tokens, {passage_count/elapsed:.0f} passages/s, {elapsed:.1f}s)", file=sys.stderr)

    if total == 0:
        # Whitespace-only input (or empty file) has no passages; match the old
        # whole-file error. A yielded passage implies non-blank text, so the
        # "no non-empty passages" branch is unreachable here but kept for parity
        # with encode_corpus.
        with open(input_path, encoding="utf-8") as f:
            has_text = bool(f.read(READ_CHARS).strip()) or _has_more_text(f)
        if not has_text:
            raise SystemExit(f"input {input_path} is empty")
        raise SystemExit("no non-empty passages in the input text")
    return total, id_max


def _has_more_text(f) -> bool:
    """True if any non-whitespace text remains in the already-opened stream."""
    while True:
        chunk = f.read(READ_CHARS)
        if not chunk:
            return False
        if chunk.strip():
            return True


def split_counts(n_total: int, val_frac: float, context_length: int) -> tuple[int, int]:
    """Return (n_train, n_val): hold out the last share of tokens, minimum one val sample."""
    n_val = max(context_length + 2, int(n_total * val_frac))
    n_train = n_total - n_val
    if n_train < context_length + 2:
        raise SystemExit(
            f"corpus has {n_total} tokens; context_length {context_length} needs at "
            f"least {2 * (context_length + 2)} for a train and a val sample "
            f"({n_val} would go to val, {n_train} to train). Lower --context-length, "
            "raise --val-frac, or use a larger corpus"
        )
    return n_train, n_val


def split_tokens(ids: np.ndarray, val_frac: float, context_length: int) -> tuple[np.ndarray, np.ndarray]:
    """Hold out the last share of tokens, but never less than one val sample."""
    n_train, n_val = split_counts(len(ids), val_frac, context_length)
    return ids[:n_train], ids[n_train:]


def split_temp_file(tmp_path: str, train_out: str, val_out: str, n_train: int) -> None:
    """Copy the first n_train IDs to train_out and the rest to val_out, in chunks."""
    itemsize = np.dtype(DTYPE).itemsize
    with open(tmp_path, "rb") as src, \
            open(train_out, "wb") as f_train, \
            open(val_out, "wb") as f_val:
        remaining = n_train
        while remaining > 0:
            data = src.read(min(remaining, COPY_TOKENS) * itemsize)
            if not data:
                raise SystemExit(f"temporary encoded file {tmp_path} is short")
            f_train.write(data)
            remaining -= len(data) // itemsize
        while True:
            data = src.read(COPY_TOKENS * itemsize)
            if not data:
                break
            f_val.write(data)


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

    os.makedirs(os.path.dirname(args.train_out) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(args.val_out) or ".", exist_ok=True)
    tmp_dir = os.path.dirname(os.path.abspath(args.train_out))
    fd, tmp_path = tempfile.mkstemp(dir=tmp_dir, prefix=".encode_all_", suffix=".bin")
    os.close(fd)
    try:
        total, id_max = encode_file_to_temp(tok, args.input, tmp_path)
        vocab_size = tok.get_vocab_size()
        if id_max >= MAX_VOCAB:
            raise SystemExit(f"token id {id_max} does not fit in {DTYPE.__name__}")
        if vocab_size > MAX_VOCAB:
            raise SystemExit(f"tokenizer has {vocab_size} ids, {DTYPE.__name__} holds {MAX_VOCAB}")

        n_train, n_val = split_counts(total, args.val_frac, args.context_length)
        print(f"tokens: total={total} train={n_train} val={n_val}")

        split_temp_file(tmp_path, args.train_out, args.val_out, n_train)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # The model must be built for the vocabulary the data was actually encoded
    # with, so publish it where Config can pick it up.
    #
    # The paths below are recorded as given on the command line, which keeps a
    # re-run byte-identical to the committed file. They are provenance for humans;
    # the sha256 digests are the contract, because a later run can verify the
    # artifacts are the same bytes even from a different directory.
    meta = {
        "vocab_size": vocab_size,
        "train_tokens": int(n_train),
        "val_tokens": int(n_val),
        "dtype": DTYPE.__name__,
        "context_length": args.context_length,
        "train_bin": args.train_out,
        "val_bin": args.val_out,
        "tokenizer_path": args.tokenizer_out,
        "train_sha256": file_sha256(args.train_out),
        "val_sha256": file_sha256(args.val_out),
        "tokenizer_sha256": file_sha256(args.tokenizer_out),
    }
    meta_path = meta_path_for(args.train_out)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")  # keep re-runs byte-identical to the committed file
    print(f"wrote {args.train_out} and {args.val_out}")
    print(f"wrote {meta_path} (vocab_size={vocab_size}, "
          f"tokenizer={args.tokenizer_out} sha256={meta['tokenizer_sha256'][:12]}...)")


if __name__ == "__main__":
    main()
