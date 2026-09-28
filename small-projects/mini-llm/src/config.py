"""Central configuration for mini-llm. All defaults live here."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

META_FILENAME = "meta.json"


def meta_path_for(train_bin: str) -> str:
    """Path of the prepare_data.py metadata file sitting next to the .bin files."""
    return os.path.join(os.path.dirname(os.path.abspath(train_bin)), META_FILENAME)


def load_data_meta(train_bin: str) -> dict | None:
    """Read metadata written by prepare_data.py, or None if the data is unprepared."""
    path = meta_path_for(train_bin)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def config_for_data(train_bin: str, **overrides) -> "Config":
    """Build a Config whose vocab_size matches the tokenizer used for `train_bin`.

    prepare_data.py records the real tokenizer size in meta.json; a model built for a
    larger vocabulary than the data contains wastes rows and lets sampling land on
    ids that do not decode, so the size is taken from the data unless overridden.
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

    def __post_init__(self) -> None:
        assert self.d_model % self.n_heads == 0, "d_model must be divisible by n_heads"
        assert self.batch_size > 0 and self.max_steps > 0
        assert 0.0 < self.min_lr_ratio <= 1.0, "min_lr_ratio must be in (0, 1]"
        assert self.warmup_steps <= self.max_steps, "warmup_steps must fit in max_steps"
        for name in ("torch_threads", "torch_interop_threads"):
            value = getattr(self, name)
            assert value is None or (isinstance(value, int) and value >= 1), \
                f"{name} must be a positive int or None, got {value!r}"

    def validate_against_data(self) -> dict | None:
        """Fail if the model vocabulary does not match the prepared data."""
        meta = load_data_meta(self.train_bin)
        if meta is not None and int(meta["vocab_size"]) != self.vocab_size:
            raise ValueError(
                f"vocab_size={self.vocab_size} but {self.train_bin} was encoded with a "
                f"vocabulary of {meta['vocab_size']} (see {meta_path_for(self.train_bin)})"
            )
        return meta

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
