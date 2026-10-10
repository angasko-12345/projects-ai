"""Central configuration for mini-llm. All defaults live here."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass

META_FILENAME = "meta.json"
HASH_CHUNK_BYTES = 1 << 20  # streaming read size for artifact digests

# Provenance keys prepare_data.py writes and every run re-verifies. A meta.json
# without all of them predates provenance tracking and cannot be trusted.
PROVENANCE_KEYS = ("train_bin", "train_sha256", "val_bin", "val_sha256",
                   "tokenizer_path", "tokenizer_sha256")

# Digests that decide whether two runs read the same data. Paths are recorded for
# humans and for error messages; content, not location, is the contract.
IDENTITY_KEYS = ("vocab_size", "train_sha256", "val_sha256", "tokenizer_sha256")


def meta_path_for(train_bin: str) -> str:
    """Path of the prepare_data.py metadata file sitting next to the .bin files."""
    return os.path.join(os.path.dirname(os.path.abspath(train_bin)), META_FILENAME)


def file_sha256(path: str) -> str:
    """sha256 of a file's bytes, streamed so corpus-size artifacts cost no RAM."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(HASH_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def provenance_mismatches(recorded: dict | None, current: dict) -> list[str]:
    """Differences between a checkpoint's recorded data and the current artifacts.

    Empty list means the two are the same data by content. Paths are deliberately
    not compared: a relocated but identical artifact tree is the same run, while
    a same-path file with different bytes is not.
    """
    if not recorded:
        return ["the checkpoint records no data provenance"]
    out = []
    for key in IDENTITY_KEYS:
        was, now = recorded.get(key), current.get(key)
        if was != now:
            out.append(f"{key}: checkpoint={was} current={now}")
    return out


def load_data_meta(train_bin: str) -> dict | None:
    """Read metadata written by prepare_data.py, or None if the data is unprepared."""
    path = meta_path_for(train_bin)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def config_for_data(train_bin: str, **overrides) -> "Config":
    """Build a Config that matches the prepared data set at `train_bin`.

    prepare_data.py records the real tokenizer size and the real artifact paths in
    meta.json; a model built for a larger vocabulary than the data contains wastes
    rows and lets sampling land on ids that do not decode, so size is taken from
    the data unless overridden. The tokenizer and val paths are adopted for the
    same reason: a config pointing at a different tokenizer than the one that
    produced the .bin files is not a valid description of this data, and deriving
    them here is not a fallback because it comes from the data's own record.
    """
    cfg = Config(train_bin=train_bin, **overrides)
    meta = load_data_meta(train_bin)
    if meta is None:
        raise FileNotFoundError(
            f"no {META_FILENAME} next to {train_bin} - run prepare_data.py first"
        )
    if "vocab_size" not in overrides:
        if meta["vocab_size"] != cfg.vocab_size:
            print(
                f"vocab_size {cfg.vocab_size} -> {meta['vocab_size']} "
                f"(from {meta_path_for(train_bin)})"
            )
        cfg.vocab_size = int(meta["vocab_size"])
    for field in ("tokenizer_path", "val_bin"):
        if field in overrides or field not in meta:
            continue
        if getattr(cfg, field) != meta[field]:
            print(f"{field} {getattr(cfg, field)} -> {meta[field]} "
                  f"(from {meta_path_for(train_bin)})")
        setattr(cfg, field, meta[field])
    cfg.validate_against_data()
    return cfg


@dataclass
class Config:
    # Model architecture
    vocab_size: int = 8192
    context_length: int = 512
    n_layers: int = 6
    n_heads: int = 6
    d_model: int = 384
    d_ff: int = 1536
    dropout: float = 0.0

    # Training
    batch_size: int = 8
    learning_rate: float = 3e-4
    weight_decay: float = 0.1
    max_steps: int = 10000
    warmup_steps: int = 500
    eval_interval: int = 500
    eval_batches: int = 50
    grad_clip: float = 1.0
    # Microbatches accumulated per optimizer update. The effective batch is
    # batch_size * grad_accum_steps; 1 means one update per batch.
    grad_accum_steps: int = 1
    # Sequence positions per LM-loss chunk; 0 (default) uses the whole sequence.
    # >= 1 recomputes each chunk in backward so full B x T x vocab logits never exist.
    loss_chunk_size: int = 0
    seed: int = 42

    # LR schedule
    min_lr_ratio: float = 0.1  # cosine decays to learning_rate * min_lr_ratio

    # AdamW betas (fixed by spec)
    beta1: float = 0.9
    beta2: float = 0.95

    # CPU throughput (all optional; None/False = PyTorch defaults)
    torch_threads: int | None = None  # torch.set_num_threads
    torch_interop_threads: int | None = None  # torch.set_num_interop_threads
    compile: bool = False  # torch.compile the model before training

    # Data / checkpoints
    train_bin: str = "data/processed/train.bin"
    val_bin: str = "data/processed/val.bin"
    tokenizer_path: str = "data/tokenizer.json"
    checkpoint_dir: str = "checkpoints"
    # Numbered training checkpoints to retain. Rotation never touches final.pt or
    # files that are not step_<n>.pt. 3 keeps the newest and the two before it,
    # enough margin to resume after a bad latest step without unbounded disk use.
    keep_last: int = 3

    def __post_init__(self) -> None:
        for name in ("n_layers", "n_heads", "d_model", "d_ff"):
            value = getattr(self, name)
            assert isinstance(value, int) and value > 0, \
                f"{name} must be a positive int, got {value!r}"
        assert self.d_model % self.n_heads == 0, "d_model must be divisible by n_heads"
        assert self.vocab_size > 0 and self.context_length > 0
        assert self.batch_size > 0 and self.max_steps > 0
        assert isinstance(self.grad_accum_steps, int) and self.grad_accum_steps >= 1, \
            f"grad_accum_steps must be a positive int (>= 1), got {self.grad_accum_steps!r}"
        assert isinstance(self.loss_chunk_size, int) and self.loss_chunk_size >= 0, \
            f"loss_chunk_size must be an int >= 0 (0 disables), got {self.loss_chunk_size!r}"
        assert 0.0 < self.min_lr_ratio <= 1.0, "min_lr_ratio must be in (0, 1]"
        assert self.warmup_steps <= self.max_steps, "warmup_steps must fit in max_steps"
        for name in ("torch_threads", "torch_interop_threads"):
            value = getattr(self, name)
            assert value is None or (isinstance(value, int) and value >= 1), \
                f"{name} must be a positive int or None, got {value!r}"
        assert isinstance(self.keep_last, int) and self.keep_last >= 1, \
            f"keep_last must be a positive int (>= 1), got {self.keep_last!r}"

    def validate_against_data(self) -> dict | None:
        """Fail unless the model, data and tokenizer are one consistent set.

        Checked, in order: the vocabulary the model was built for, that the
        prepared data records provenance at all, and that every artifact named
        by the current config still hashes to what prepare_data.py wrote.
        A missing artifact or a stale one is an error, never a fallback.
        """
        meta = load_data_meta(self.train_bin)
        if meta is None:
            return None
        if int(meta["vocab_size"]) != self.vocab_size:
            raise ValueError(
                f"vocab_size={self.vocab_size} but {self.train_bin} was encoded with a "
                f"vocabulary of {meta['vocab_size']} (see {meta_path_for(self.train_bin)})"
            )
        missing = [k for k in PROVENANCE_KEYS if k not in meta]
        if missing:
            raise ValueError(
                f"{meta_path_for(self.train_bin)} records no provenance for "
                f"{', '.join(missing)} - it predates provenance tracking, so the data, "
                "tokenizer and their contents cannot be verified; re-run prepare_data.py"
            )
        for key, path in (("train_sha256", self.train_bin),
                          ("val_sha256", self.val_bin),
                          ("tokenizer_sha256", self.tokenizer_path)):
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"{key} names {os.path.abspath(path)}, which does not exist - pass the "
                    "artifact prepare_data.py produced or re-run it"
                )
            actual = file_sha256(path)
            if actual != meta[key]:
                raise ValueError(
                    f"{key} mismatch: {os.path.abspath(path)} has changed since "
                    f"{meta_path_for(self.train_bin)} was written "
                    f"(sha256 {actual[:12]}... != recorded {str(meta[key])[:12]}...) - "
                    "point --train-bin/--val-bin/--tokenizer at the prepared artifacts"
                )
        return meta

    def data_provenance(self) -> dict:
        """Identity of the data/tokenizer set this config reads.

        Stored in every checkpoint the training loop writes, so a later resume can
        prove it is continuing the same data instead of an unrelated artifact that
        happens to sit at a default path.
        """
        meta = self.validate_against_data()
        if meta is None:
            raise FileNotFoundError(
                f"no {META_FILENAME} next to {self.train_bin} - run prepare_data.py first"
            )
        return {
            "vocab_size": int(meta["vocab_size"]),
            "train_bin": os.path.abspath(self.train_bin),
            "train_sha256": meta["train_sha256"],
            "val_bin": os.path.abspath(self.val_bin),
            "val_sha256": meta["val_sha256"],
            "tokenizer_path": os.path.abspath(self.tokenizer_path),
            "tokenizer_sha256": meta["tokenizer_sha256"],
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
