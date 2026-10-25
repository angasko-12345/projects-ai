"""BPE tokenizer built on the `tokenizers` package.

Special tokens (fixed IDs, in order): <pad>=0, <unk>=1, <bos>=2, <eos>=3.
Trained model is saved to data/tokenizer.json and supports encode/decode.

The trainer is seeded with the full 256-symbol ByteLevel alphabet, so every byte
has a token even when the training corpus never contained it. Without that seed
an unseen character (an accent, an emoji, a tab) encodes to <unk> and cannot
round-trip, and llama.cpp's byte-level tokenizer silently drops those bytes
instead, so the two tokenizers disagree. The smallest valid vocabulary is
therefore 256 byte symbols + the special tokens (MIN_VOCAB_SIZE).
"""

from __future__ import annotations

import os

from tokenizers import Tokenizer
from tokenizers.decoders import ByteLevel as ByteLevelDecoder
from tokenizers.models import BPE
from tokenizers.pre_tokenizers import ByteLevel
from tokenizers.trainers import BpeTrainer

SPECIAL_TOKENS = ["<pad>", "<unk>", "<bos>", "<eos>"]
BYTE_ALPHABET_SIZE = len(ByteLevel.alphabet())  # 256
MIN_VOCAB_SIZE = BYTE_ALPHABET_SIZE + len(SPECIAL_TOKENS)  # 260


def check_vocab_size(vocab_size: int) -> None:
    """Refuse a vocabulary too small to hold every byte plus the special tokens."""
    if vocab_size < MIN_VOCAB_SIZE:
        raise ValueError(
            f"vocab_size {vocab_size} is too small: the tokenizer needs at least "
            f"{MIN_VOCAB_SIZE} ids ({BYTE_ALPHABET_SIZE} byte symbols + "
            f"{len(SPECIAL_TOKENS)} special tokens)"
        )


def train_bpe_tokenizer(
    input_files: list[str],
    save_path: str,
    vocab_size: int = 8192,
    min_frequency: int = 2,
    show_progress: bool = False,
) -> Tokenizer:
    check_vocab_size(vocab_size)
    tok = Tokenizer(BPE(unk_token="<unk>"))
    tok.pre_tokenizer = ByteLevel(add_prefix_space=False)
    tok.decoder = ByteLevelDecoder()
    trainer = BpeTrainer(
        vocab_size=vocab_size,
        min_frequency=min_frequency,
        special_tokens=SPECIAL_TOKENS,
        initial_alphabet=ByteLevel.alphabet(),
        show_progress=show_progress,
    )
    tok.train(input_files, trainer)
    tok.save(save_path)
    return tok


def load_tokenizer(path: str) -> Tokenizer:
    # Tokenizer.from_file reports a missing file as a bare OSError from Rust with no
    # path in it, which reads as "the system cannot find the file specified" and hides
    # which artifact is missing. Name it, and say how to make it.
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"no tokenizer at {os.path.abspath(path)} - run prepare_data.py "
            "(or pass --tokenizer)"
        )
    return Tokenizer.from_file(path)


def encode(tok: Tokenizer, text: str, add_bos: bool = False) -> list[int]:
    ids = tok.encode(text).ids
    if add_bos:
        bos = tok.token_to_id("<bos>")
        ids = [bos] + ids
    return ids


def decode(tok: Tokenizer, ids: list[int]) -> str:
    return tok.decode(ids)
