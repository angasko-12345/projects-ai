"""Token dataset: memory-mapped token IDs sliced into x/y next-token samples."""

from __future__ import annotations

import os

import numpy as np
import torch
from torch.utils.data import BatchSampler, DataLoader, Dataset, RandomSampler, SequentialSampler

DTYPE = np.uint16  # prepare_data.py refuses corpora/tokenizers above 65536 ids


def load_token_ids(path: str) -> np.memmap:
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found - run prepare_data.py first")
    if os.path.getsize(path) == 0:
        raise ValueError(f"{path} is empty - run prepare_data.py first")
    return np.memmap(path, dtype=DTYPE, mode="r")


class TokenDataset(Dataset):
    """Windows of context_length+1 tokens; returns x=tokens[:-1], y=tokens[1:].

    `stride` is the distance between window starts. The default 1 slides the window
    one token at a time, so every next-token transition in the corpus is supervised;
    a larger stride keeps non-overlapping chunks and drops the transitions that
    straddle the seams.
    """

    def __init__(self, token_ids: np.ndarray, context_length: int, stride: int = 1):
        n = len(token_ids)
        if n <= context_length + 1:
            raise ValueError(
                f"{n} tokens cannot form a single sample of context_length "
                f"{context_length + 1}; lower --context-length or use a larger corpus"
            )
        if stride < 1:
            raise ValueError(f"stride must be >= 1, got {stride}")
        self.tokens = token_ids
        self.context_length = context_length
        self.stride = stride
        self.seq_len = context_length + 1

    def __len__(self) -> int:
        return (len(self.tokens) - self.context_length - 1) // self.stride + 1

    def __getitem__(self, i: int) -> tuple[torch.Tensor, torch.Tensor]:
        start = i * self.stride
        seq = self.tokens[start : start + self.seq_len].astype(np.int64)
        x = torch.from_numpy(seq[:-1])
        y = torch.from_numpy(seq[1:])
        return x, y

    def fetch_windows(self, idxs) -> np.ndarray:
        """Stacked uint16 windows for a batch of sample indices, no conversion.

        Deliberately NOT named __getitems__: DataLoader auto-collation would
        prefer a __getitems__ method and change what a plain
        DataLoader(TokenDataset) yields. Only TokenDataLoader calls this, so
        direct dataset/DataLoader use keeps the exact old semantics. Returns
        a [B, seq_len] uint16 array; still a batch-sized copy, never the
        whole corpus.
        """
        idxs = [int(i) for i in idxs]
        batch = np.empty((len(idxs), self.seq_len), dtype=DTYPE)
        for row, i in enumerate(idxs):
            start = i * self.stride
            batch[row] = self.tokens[start : start + self.seq_len]
        return batch


def collate_windows(batch: np.ndarray) -> tuple[torch.Tensor, torch.Tensor]:
    """Split one fetch_windows uint16 block into int64 (x, y) next-token pairs.

    This is the single dtype conversion per batch: values are identical to
    stacking TokenDataset.__getitem__ results.
    """
    seq = torch.from_numpy(batch.astype(np.int64))
    return seq[:, :-1], seq[:, 1:]


class TokenDataLoader(DataLoader):
    """Batched (x, y) loader with the uint16 -> int64 conversion done per batch.

    Same batches, order and length semantics as
    DataLoader(TokenDataset(...), batch_size=..., shuffle=..., drop_last=...):
    shuffling still permutes individual sample indices (RandomSampler +
    BatchSampler, the same samplers DataLoader would build). The only
    difference is the fetch path: one fetch_windows + one conversion per
    batch instead of one conversion per sample.
    """

    def __init__(
        self,
        bin_path: str,
        context_length: int,
        batch_size: int,
        shuffle: bool = True,
        drop_last: bool | None = None,
        stride: int = 1,
    ):
        if drop_last is None:
            drop_last = shuffle
        tokens = load_token_ids(bin_path)
        ds = TokenDataset(tokens, context_length, stride=stride)
        n_samples = len(ds)
        if drop_last and n_samples < batch_size:
            # Without this a too-small corpus silently yields zero batches and
            # the training loop dies later on an unexplained StopIteration.
            needed = context_length + 1 + (batch_size - 1) * ds.stride
            raise ValueError(
                f"{bin_path} holds {len(tokens)} tokens = {n_samples} sample(s) of "
                f"context_length {context_length} at stride {ds.stride}, fewer than "
                f"batch_size {batch_size}; need at least {needed} tokens - lower "
                "--batch-size / --context-length / --stride or use a larger corpus"
            )
        sampler = RandomSampler(ds) if shuffle else SequentialSampler(ds)
        self._batches = BatchSampler(sampler, batch_size, drop_last)
        super().__init__(ds, batch_sampler=self._batches)

    def __iter__(self):
        for idxs in self._batches:
            yield collate_windows(self.dataset.fetch_windows(idxs))

    def __len__(self) -> int:
        return len(self._batches)


def build_dataloader(
    bin_path: str,
    context_length: int,
    batch_size: int,
    shuffle: bool = True,
    drop_last: bool | None = None,
    stride: int = 1,
) -> DataLoader:
    """Loader over a .bin file. Train drops the ragged tail; val keeps it."""
    return TokenDataLoader(bin_path, context_length, batch_size, shuffle=shuffle,
                           drop_last=drop_last, stride=stride)
