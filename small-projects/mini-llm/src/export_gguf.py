"""Export a mini-llm checkpoint to a GGUF file that llama.cpp loads as arch "gpt2".

    python -m src.export_gguf --checkpoint checkpoints/final.pt --out model-f32.gguf
    python -m src.export_gguf --checkpoint checkpoints/final.pt --out model-f16.gguf --dtype f16

The result runs in `llama-server`, `llama-cli`, LM Studio and anything else built on
llama.cpp. See docs/GGUF-llama-server.md for the full train -> export -> serve path.

Why this writes the file itself instead of calling llama.cpp's convert_hf_to_gguf.py:
that converter identifies a BPE tokenizer by hashing the ids it produces for a fixed
test string and raises NotImplementedError for any hash it does not know, which a
tokenizer trained from scratch always is. mini-llm's tokenizer is a plain GPT-2
style byte-level BPE (ByteLevel pre-tokenizer with the GPT-2 regex, no normalizer),
so the GGUF declares tokenizer.ggml.pre = "gpt-2" directly.

mini-llm -> llama.cpp gpt2 mapping (torch (out, in) layout; gguf-py reverses the dims
on write, which yields the {n_embd, 3*n_embd} layout llama.cpp expects):

    wte.weight                      -> token_embd.weight   (tied head: no output.weight)
    wpe.weight                      -> position_embd.weight
    ln_f.{weight,bias}              -> output_norm.{weight,bias}
    blocks.i.ln1                    -> blk.i.attn_norm
    blocks.i.attn.heads.h.wq/wk/wv  -> blk.i.attn_qkv.weight: every head's wq in head
                                       order, then every wk, then every wv, along dim 0;
                                       blk.i.attn_qkv.bias = zeros (llama.cpp requires
                                       the tensor, mini-llm has no QKV bias)
    blocks.i.attn.proj              -> blk.i.attn_output
    blocks.i.ln2                    -> blk.i.ffn_norm
    blocks.i.mlp.fc1 / fc2          -> blk.i.ffn_up / blk.i.ffn_down

`gguf` (pip install gguf, see requirements-gguf.txt) is only needed for this
module; nothing else in mini-llm imports it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import file_sha256  # noqa: E402

GGUF_ARCH = "gpt2"
TOKENIZER_MODEL = "gpt2"
TOKENIZER_PRE = "gpt-2"
LAYER_NORM_EPS = 1e-5  # nn.LayerNorm's default, which mini-llm uses everywhere
DTYPES = ("f32", "f16")
SPECIAL_TOKEN_NAMES = {"bos": "<bos>", "eos": "<eos>", "pad": "<pad>", "unk": "<unk>"}

# GGUF token types (gguf.TokenType), repeated here so the pure tokenizer mapping
# below can be tested without gguf installed.
TOKEN_NORMAL = 1
TOKEN_CONTROL = 3
TOKEN_UNUSED = 5


def require_gguf():
    """Import gguf-py lazily, with an install hint instead of a bare ImportError."""
    try:
        import gguf
    except ImportError as exc:  # pragma: no cover - exercised only without gguf
        raise SystemExit(
            "GGUF export needs the `gguf` package (llama.cpp's gguf-py). Install it with\n"
            "  python -m pip install -r requirements-gguf.txt"
        ) from exc
    return gguf


def _np(t) -> np.ndarray:
    return t.detach().to("cpu").float().numpy()


def gguf_tensors(model_state: dict, n_layers: int, n_heads: int) -> list[tuple[str, np.ndarray]]:
    """The (gguf name, float32 array) list for a mini-llm state dict, in write order.

    Arrays keep torch's (out, in) layout. Pure mapping: no file I/O and no gguf import.
    """
    sd = model_state
    tensors: list[tuple[str, np.ndarray]] = [
        ("token_embd.weight", _np(sd["wte.weight"])),
        ("position_embd.weight", _np(sd["wpe.weight"])),
    ]
    for i in range(n_layers):
        p = f"blocks.{i}."
        heads = [f"{p}attn.heads.{h}." for h in range(n_heads)]
        qkv = np.concatenate(
            [_np(sd[h + "wq.weight"]) for h in heads]
            + [_np(sd[h + "wk.weight"]) for h in heads]
            + [_np(sd[h + "wv.weight"]) for h in heads],
            axis=0,
        )
        b = f"blk.{i}."
        tensors += [
            (b + "attn_norm.weight", _np(sd[p + "ln1.weight"])),
            (b + "attn_norm.bias", _np(sd[p + "ln1.bias"])),
            (b + "attn_qkv.weight", qkv),
            (b + "attn_qkv.bias", np.zeros(qkv.shape[0], dtype=np.float32)),
            (b + "attn_output.weight", _np(sd[p + "attn.proj.weight"])),
            (b + "attn_output.bias", _np(sd[p + "attn.proj.bias"])),
            (b + "ffn_norm.weight", _np(sd[p + "ln2.weight"])),
            (b + "ffn_norm.bias", _np(sd[p + "ln2.bias"])),
            (b + "ffn_up.weight", _np(sd[p + "mlp.fc1.weight"])),
            (b + "ffn_up.bias", _np(sd[p + "mlp.fc1.bias"])),
            (b + "ffn_down.weight", _np(sd[p + "mlp.fc2.weight"])),
            (b + "ffn_down.bias", _np(sd[p + "mlp.fc2.bias"])),
        ]
    tensors += [
        ("output_norm.weight", _np(sd["ln_f.weight"])),
        ("output_norm.bias", _np(sd["ln_f.bias"])),
    ]
    # MiniGPT always ties lm_head to wte, so llama.cpp's fallback (output ->
    # token_embd) is exact. A head that is not the embedding is written explicitly
    # rather than silently replaced by it.
    head = sd.get("lm_head.weight")
    if head is not None and not np.array_equal(_np(head), tensors[0][1]):
        tensors.append(("output.weight", _np(head)))
    return tensors


def tokenizer_fields(tokenizer_path: str, n_vocab: int) -> dict:
    """GGUF tokenizer metadata for a mini-llm tokenizer.json, padded to n_vocab rows.

    Tokens are the byte-level strings from the BPE vocab (plus added tokens) in id
    order, which is what llama.cpp's gpt2 tokenizer reads. Special/added tokens are
    CONTROL. Embedding rows past the tokenizer (a --vocab-size larger than the
    tokenizer reached) get UNUSED "[PAD<id>]" entries so the token count matches the
    embedding; a tokenizer larger than the embedding is refused.
    """
    with open(tokenizer_path, encoding="utf-8") as f:
        spec = json.load(f)
    model = spec.get("model") or {}
    if model.get("type") != "BPE":
        raise ValueError(f"{tokenizer_path}: expected a BPE tokenizer, got {model.get('type')!r}")
    pre = spec.get("pre_tokenizer") or {}
    if pre.get("type") != "ByteLevel" or spec.get("normalizer") is not None:
        raise ValueError(
            f"{tokenizer_path}: only mini-llm's ByteLevel tokenizer with no normalizer "
            f"maps onto llama.cpp's gpt-2 pre-tokenizer (got pre_tokenizer={pre!r}, "
            f"normalizer={spec.get('normalizer')!r})"
        )

    by_id: dict[int, str] = {i: tok for tok, i in model["vocab"].items()}
    control: set[int] = set()
    for added in spec.get("added_tokens") or []:
        by_id[added["id"]] = added["content"]
        if added.get("special", True):
            control.add(added["id"])
    n_tok = max(by_id) + 1
    missing = [i for i in range(n_tok) if i not in by_id]
    if missing:
        raise ValueError(f"{tokenizer_path}: token ids are not contiguous (missing {missing[:5]})")
    if n_tok > n_vocab:
        raise ValueError(
            f"{tokenizer_path} has {n_tok} tokens but the model embedding has only "
            f"{n_vocab} rows; this is not the tokenizer the checkpoint was trained with"
        )

    tokens = [by_id[i] for i in range(n_tok)] + [f"[PAD{i}]" for i in range(n_tok, n_vocab)]
    types = [TOKEN_CONTROL if i in control else TOKEN_NORMAL for i in range(n_tok)]
    types += [TOKEN_UNUSED] * (n_vocab - n_tok)
    merges = [m if isinstance(m, str) else " ".join(m) for m in model.get("merges", [])]

    ids = {}
    for key, name in SPECIAL_TOKEN_NAMES.items():
        tid = model["vocab"].get(name)
        if tid is None:
            tid = next((a["id"] for a in spec.get("added_tokens") or [] if a["content"] == name), None)
        if tid is None:
            raise ValueError(f"{tokenizer_path}: special token {name} is missing")
        ids[key] = tid

    # Byte-level symbols the vocab lacks. A tokenizer trained before the
    # initial_alphabet fix can be missing some; HF tokenizers maps those bytes to
    # <unk> while llama.cpp drops them, so text containing them tokenizes
    # differently in the two. Reported, not refused: such a checkpoint is still
    # valid and exports exactly; only out-of-alphabet input text diverges.
    from tokenizers.pre_tokenizers import ByteLevel

    vocab_set = set(model["vocab"])
    missing_bytes = sorted(s for s in ByteLevel.alphabet() if s not in vocab_set)
    return {
        "tokens": tokens,
        "types": types,
        "merges": merges,
        "ids": ids,
        "n_tokenizer": n_tok,
        "missing_byte_symbols": missing_bytes,
    }


def write_gguf(
    out_path: str,
    tensors: list[tuple[str, np.ndarray]],
    tok: dict,
    hparams: dict,
    dtype: str = "f32",
    provenance: dict | None = None,
) -> None:
    """Write the GGUF file. hparams: context_length, d_model, d_ff, n_layers, n_heads."""
    if dtype not in DTYPES:
        raise ValueError(f"dtype must be one of {DTYPES}, got {dtype!r}")
    gguf = require_gguf()
    provenance = provenance or {}
    w = gguf.GGUFWriter(out_path, GGUF_ARCH)
    w.add_name("mini-llm")
    step = provenance.get("step")
    desc = "mini-llm GPT trained from scratch"
    if step is not None:
        desc += f", checkpoint step {step}"
    if provenance.get("tokenizer_sha256"):
        desc += f", tokenizer sha256 {provenance['tokenizer_sha256']}"
    w.add_description(desc)
    w.add_file_type(gguf.LlamaFileType.ALL_F32 if dtype == "f32" else gguf.LlamaFileType.MOSTLY_F16)

    w.add_context_length(int(hparams["context_length"]))
    w.add_embedding_length(int(hparams["d_model"]))
    w.add_feed_forward_length(int(hparams["d_ff"]))
    w.add_block_count(int(hparams["n_layers"]))
    w.add_head_count(int(hparams["n_heads"]))
    w.add_layer_norm_eps(LAYER_NORM_EPS)

    w.add_tokenizer_model(TOKENIZER_MODEL)
    w.add_tokenizer_pre(TOKENIZER_PRE)
    w.add_token_list(tok["tokens"])
    w.add_token_types(tok["types"])
    w.add_token_merges(tok["merges"])
    w.add_bos_token_id(tok["ids"]["bos"])
    w.add_eos_token_id(tok["ids"]["eos"])
    w.add_pad_token_id(tok["ids"]["pad"])
    w.add_unk_token_id(tok["ids"]["unk"])
    # Training frames every passage as <bos> ... <eos>, so a prompt starts with <bos>.
    w.add_add_bos_token(True)
    w.add_add_eos_token(False)

    # mini-llm's own provenance, so a GGUF can be traced back to its checkpoint.
    if step is not None:
        w.add_uint32("mini_llm.checkpoint_step", int(step))
    if provenance.get("tokenizer_sha256"):
        w.add_string("mini_llm.tokenizer_sha256", str(provenance["tokenizer_sha256"]))
    w.add_bool("mini_llm.full_byte_alphabet", not tok["missing_byte_symbols"])

    for name, arr in tensors:
        # Matrices go to f16 on request; 1-D norms/biases stay f32, the llama.cpp
        # convention (MOSTLY_F16 means "except 1d tensors").
        data = arr.astype(np.float16) if dtype == "f16" and arr.ndim == 2 else arr.astype(np.float32)
        w.add_tensor(name, np.ascontiguousarray(data))

    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()


def export_checkpoint(checkpoint: str, out_path: str, tokenizer_path: str | None = None,
                      dtype: str = "f32") -> dict:
    """Read a checkpoint (validated, provenance-checked tokenizer) and write a GGUF.

    Returns a summary dict (paths, sizes, warnings) for the CLI and tests.
    """
    if dtype not in DTYPES:
        raise ValueError(f"dtype must be one of {DTYPES}, got {dtype!r}")
    require_gguf()  # fail before reading a large checkpoint
    from src.generate import check_tokenizer_provenance
    from src.train import load_model, read_checkpoint

    ckpt = read_checkpoint(checkpoint)
    model, cfg = load_model(ckpt)
    tok_path = tokenizer_path or cfg.tokenizer_path
    check_tokenizer_provenance(ckpt, tok_path)

    state = model.state_dict()
    tensors = gguf_tensors(state, cfg.n_layers, cfg.n_heads)
    tok = tokenizer_fields(tok_path, n_vocab=int(state["wte.weight"].shape[0]))
    warnings = []
    if tok["missing_byte_symbols"]:
        warnings.append(
            f"tokenizer lacks {len(tok['missing_byte_symbols'])} of 256 byte symbols "
            "(trained before the full-alphabet fix): characters outside its training "
            "corpus become <unk> in mini-llm but are dropped by llama.cpp. The export "
            "is exact; only such input text tokenizes differently. Retrain the "
            "tokenizer with the current prepare_data.py to remove this."
        )
    if tok["n_tokenizer"] < len(tok["tokens"]):
        warnings.append(
            f"embedding has {len(tok['tokens'])} rows but the tokenizer {tok['n_tokenizer']}; "
            "the extra rows are written as UNUSED [PAD<id>] tokens"
        )
    for msg in warnings:
        print(f"warning: {msg}", file=sys.stderr)

    meta = ckpt.get("checkpoint_metadata") or {}
    recorded = ckpt.get("data_provenance") or {}
    provenance = {
        "step": int(ckpt.get("step", meta.get("progress", {}).get("step", 0))),
        "tokenizer_sha256": recorded.get("tokenizer_sha256") or file_sha256(tok_path),
    }
    hparams = {
        "context_length": cfg.context_length, "d_model": cfg.d_model, "d_ff": cfg.d_ff,
        "n_layers": cfg.n_layers, "n_heads": cfg.n_heads,
    }
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    write_gguf(out_path, tensors, tok, hparams, dtype=dtype, provenance=provenance)
    return {
        "out": out_path,
        "bytes": os.path.getsize(out_path),
        "dtype": dtype,
        "tensors": len(tensors),
        "n_vocab": len(tok["tokens"]),
        "context_length": cfg.context_length,
        "tokenizer": tok_path,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Export a mini-llm checkpoint to GGUF (llama.cpp arch gpt2)")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--out", required=True, help="output .gguf path")
    parser.add_argument("--tokenizer", default=None,
                        help="defaults to the tokenizer path stored in the checkpoint; "
                             "verified against the digest the checkpoint recorded")
    parser.add_argument("--dtype", choices=DTYPES, default="f32",
                        help="f32 (default, exact) or f16 (matrices only; norms and "
                             "biases stay f32)")
    args = parser.parse_args(argv)
    summary = export_checkpoint(args.checkpoint, args.out, args.tokenizer, args.dtype)
    print(f"wrote {summary['out']} ({summary['bytes']} bytes, {summary['dtype']}, "
          f"{summary['tensors']} tensors, vocab {summary['n_vocab']}, "
          f"context {summary['context_length']})")
    print(f"run it: llama-server -m {summary['out']} -c {summary['context_length']} --port 9931")


if __name__ == "__main__":
    main()
