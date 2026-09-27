"""Token dataset: memory-mapped token IDs sliced into x/y next-token samples."""

from __future__ import annotations

import os

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

DTYPE = np.uint16  # prepare_data.py refuses corpora/tokenizers above 65536 ids


def load_token_ids(path: str) -> np.memmap:
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found - run prepare_data.py first")
    if os.path.getsize(path) == 0:
        raise ValueError(f"{path} is empty - run prepare_data.py first")
    return np.memmap(path, dtype=DTYPE, mode="r")


class TokenDataset(Dataset):
    """Each sample is context_length+1 tokens; returns x=tokens[:-1], y=tokens[1:]."""

    def __init__(self, token_ids: np.ndarray, context_length: int):
        n = len(token_ids)
        if n <= context_length + 1:
            raise ValueError(
                f"{n} tokens cannot form a single sample of context_length "
                f"{context_length + 1}; lower --context-length or use a larger corpus"
            )
        self.tokens = token_ids
        self.context_length = context_length
        self.seq_len = context_length + 1

    def __len__(self) -> int:
        return (len(self.tokens) - 1) // self.seq_len

    def __getitem__(self, i: int) -> tuple[torch.Tensor, torch.Tensor]:
        start = i * self.seq_len
        seq = self.tokens[start : start + self.seq_len].astype(np.int64)
        x = torch.from_numpy(seq[:-1])
        y = torch.from_numpy(seq[1:])
        return x, y


def build_dataloader(
    bin_path: str,
    context_length: int,
    batch_size: int,
    shuffle: bool = True,
    drop_last: bool | None = None,
) -> DataLoader:
    """Loader over a .bin file. Train drops the ragged tail; val keeps it."""
    if drop_last is None:
        drop_last = shuffle
    tokens = load_token_ids(bin_path)
    ds = TokenDataset(tokens, context_length)
    n_samples = len(ds)
    if drop_last and n_samples < batch_size:
        # Without this a too-small corpus silently yields zero batches and the
        # training loop dies later on an unexplained StopIteration.
        raise ValueError(
            f"{bin_path} holds {len(tokens)} tokens = {n_samples} sample(s) of "
            f"context_length {context_length}, fewer than batch_size {batch_size}; "
            f"need at least {batch_size * (context_length + 1) + 1} tokens - lower "
            "--batch-size / --context-length or use a larger corpus"
        )
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle, drop_last=drop_last)
