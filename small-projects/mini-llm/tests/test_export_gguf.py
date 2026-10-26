"""GGUF export contract: mini-llm checkpoint -> llama.cpp gpt2 GGUF.

Three layers:
- TestTokenizerFields: the pure tokenizer -> GGUF metadata mapping (tokenizers only).
- TestExportReadBack: export a tiny model, read it back with gguf-py's GGUFReader and
  check names, shapes, dtypes, values and metadata, then run a numpy forward pass of
  llama.cpp's gpt2 graph *from the GGUF tensors alone* and compare its logits with
  MiniGPT's. Needs gguf; skipped without it unless MINI_LLM_REQUIRE_GGUF=1 (CI sets
  it, so a missing dependency fails CI instead of silently skipping).
- TestLlamaCppParity: opt-in, runs only when LLAMA_CPP_DIR points at a llama.cpp
  build/release directory containing llama-tokenize and llama-server. Compares
  tokenization and next-token top-10 probabilities with the real llama.cpp runtime.
"""

import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.config import config_for_data, file_sha256  # noqa: E402
from src.export_gguf import (  # noqa: E402
    TOKEN_CONTROL,
    TOKEN_NORMAL,
    TOKEN_UNUSED,
    export_checkpoint,
    gguf_tensors,
    tokenizer_fields,
)
from src.model import from_config  # noqa: E402
from src.tokenizer import train_bpe_tokenizer  # noqa: E402
from src.train import build_param_groups, save_checkpoint  # noqa: E402

try:
    import gguf  # noqa: F401
    HAVE_GGUF = True
except ImportError:
    HAVE_GGUF = False

REQUIRE_GGUF = os.environ.get("MINI_LLM_REQUIRE_GGUF") == "1"
SHIPPED_TRAIN_BIN = os.path.join(ROOT, "data", "processed", "train.bin")
SHIPPED_TOKENIZER = os.path.join(ROOT, "data", "tokenizer.json")
TINY = dict(context_length=32, n_layers=2, n_heads=2, d_model=64, d_ff=256)
PARITY_PROMPTS = ["The fox jumped", "Once upon a time", "The cat sat on the",
                  "A small dog", "She said hello", "the quick brown fox", "In the garden",
                  "It was a sunny day", "He ran to the", "One day"]
TOKENIZE_SAMPLES = [
    "The fox jumped.",
    "Hello,\n\nworld!  two  spaces\ttab",
    "café ünïcode 🚀 3.14 don't",
]


def gguf_required(cls):
    if HAVE_GGUF or REQUIRE_GGUF:
        return cls  # with REQUIRE_GGUF and no gguf, the import inside fails loudly
    return unittest.skip("gguf not installed (pip install -r requirements-gguf.txt)")(cls)


def make_checkpoint(path: str, init_std: float = 0.3, seed: int = 0, step: int = 7):
    """Tiny MiniGPT on the shipped data set, saved as a real provenance-carrying checkpoint.

    A larger-than-default init std makes the next-token distribution peaked enough
    that a top-k comparison is meaningful.
    """
    cfg = config_for_data(SHIPPED_TRAIN_BIN, tokenizer_path=SHIPPED_TOKENIZER, **TINY)
    torch.manual_seed(seed)
    model = from_config(cfg)
    with torch.no_grad():
        for p in model.parameters():
            if p.ndim == 2:
                p.normal_(0.0, init_std)
            else:
                p.normal_(0.0 if p.abs().sum() == 0 else 1.0, 0.1)
    opt = torch.optim.AdamW(build_param_groups(model, cfg.weight_decay), lr=1e-3)
    save_checkpoint(path, model, opt, step=step, cfg=cfg, lr=1e-3)
    model.eval()
    return model, cfg


def field(reader, key):
    """A GGUF KV value as a Python value (str, number, bool or list)."""
    f = reader.fields[key]
    if hasattr(f, "contents"):
        return f.contents()
    parts = [f.parts[i] for i in f.data]  # pragma: no cover - older gguf-py
    vals = [bytes(p).decode("utf-8") if p.dtype == np.uint8 else p.tolist()[0] for p in parts]
    return vals if len(vals) != 1 else vals[0]


def numpy_gpt2_logits(tensors: dict, hp: dict, ids: list[int]) -> np.ndarray:
    """Logits of llama.cpp's gpt2 graph (src/models/gpt2.cpp), computed in float64
    from GGUF tensors only: fused qkv + bias, 1/sqrt(head_dim) scale, causal softmax,
    LayerNorm, GELU MLP, output = token_embd when output.weight is absent."""
    def t(name):
        return np.asarray(tensors[name], dtype=np.float64)

    def ln(x, w, b, eps):
        mu = x.mean(-1, keepdims=True)
        var = ((x - mu) ** 2).mean(-1, keepdims=True)
        return (x - mu) / np.sqrt(var + eps) * w + b

    erf = np.vectorize(math.erf)
    d, nh, eps = hp["d_model"], hp["n_heads"], hp["eps"]
    hd = d // nh
    n = len(ids)
    x = t("token_embd.weight")[ids] + t("position_embd.weight")[:n]
    mask = np.triu(np.full((n, n), -np.inf), 1)
    for i in range(hp["n_layers"]):
        b = f"blk.{i}."
        h = ln(x, t(b + "attn_norm.weight"), t(b + "attn_norm.bias"), eps)
        qkv = h @ t(b + "attn_qkv.weight").T + t(b + "attn_qkv.bias")
        q, k, v = qkv[:, :d], qkv[:, d:2 * d], qkv[:, 2 * d:]
        outs = []
        for j in range(nh):
            s = slice(j * hd, (j + 1) * hd)
            a = q[:, s] @ k[:, s].T / math.sqrt(hd) + mask
            a = np.exp(a - a.max(-1, keepdims=True))
            a /= a.sum(-1, keepdims=True)
            outs.append(a @ v[:, s])
        x = x + np.concatenate(outs, -1) @ t(b + "attn_output.weight").T + t(b + "attn_output.bias")
        h = ln(x, t(b + "ffn_norm.weight"), t(b + "ffn_norm.bias"), eps)
        u = h @ t(b + "ffn_up.weight").T + t(b + "ffn_up.bias")
        u = 0.5 * u * (1.0 + erf(u / math.sqrt(2.0)))
        x = x + u @ t(b + "ffn_down.weight").T + t(b + "ffn_down.bias")
    x = ln(x, t("output_norm.weight"), t("output_norm.bias"), eps)
    head = t("output.weight") if "output.weight" in tensors else t("token_embd.weight")
    return x @ head.T


def softmax(x):
    e = np.exp(x - x.max())
    return e / e.sum()


class TestTokenizerFields(unittest.TestCase):
    """Pure mapping from tokenizer.json to GGUF tokenizer metadata (no gguf needed)."""

    def test_shipped_tokenizer_maps_in_id_order_with_control_specials(self):
        with open(SHIPPED_TOKENIZER, encoding="utf-8") as f:
            spec = json.load(f)
        vocab = spec["model"]["vocab"]
        tok = tokenizer_fields(SHIPPED_TOKENIZER, n_vocab=len(vocab))
        self.assertEqual(len(tok["tokens"]), len(vocab))
        for text, i in vocab.items():
            self.assertEqual(tok["tokens"][i], text)
        self.assertEqual(tok["types"][:4], [TOKEN_CONTROL] * 4)
        self.assertTrue(all(t == TOKEN_NORMAL for t in tok["types"][4:]))
        self.assertEqual(tok["ids"], {"pad": 0, "unk": 1, "bos": 2, "eos": 3})
        self.assertEqual(len(tok["merges"]), len(spec["model"]["merges"]))
        self.assertTrue(all(len(m.split(" ")) == 2 for m in tok["merges"]))
        self.assertEqual(tok["missing_byte_symbols"], [])

    def test_extra_embedding_rows_become_unused_pad_tokens(self):
        with open(SHIPPED_TOKENIZER, encoding="utf-8") as f:
            n = len(json.load(f)["model"]["vocab"])
        tok = tokenizer_fields(SHIPPED_TOKENIZER, n_vocab=n + 3)
        self.assertEqual(tok["tokens"][n:], [f"[PAD{i}]" for i in range(n, n + 3)])
        self.assertEqual(tok["types"][n:], [TOKEN_UNUSED] * 3)

    def test_tokenizer_larger_than_embedding_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not the tokenizer"):
            tokenizer_fields(SHIPPED_TOKENIZER, n_vocab=100)

    def test_pre_alphabet_fix_tokenizer_is_reported_not_refused(self):
        # A tokenizer trained the old way (no initial_alphabet) must still export:
        # old checkpoints stay usable; the missing byte symbols are reported.
        from tokenizers import Tokenizer
        from tokenizers.decoders import ByteLevel as ByteLevelDecoder
        from tokenizers.models import BPE
        from tokenizers.pre_tokenizers import ByteLevel
        from tokenizers.trainers import BpeTrainer

        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "c.txt")
            with open(corpus, "w", encoding="utf-8") as f:
                f.write("the quick brown fox jumps over the lazy dog\n" * 5)
            tok = Tokenizer(BPE(unk_token="<unk>"))
            tok.pre_tokenizer = ByteLevel(add_prefix_space=False)
            tok.decoder = ByteLevelDecoder()
            tok.train([corpus], BpeTrainer(vocab_size=300, min_frequency=1,
                                           special_tokens=["<pad>", "<unk>", "<bos>", "<eos>"]))
            path = os.path.join(tmp, "old.json")
            tok.save(path)
            fields = tokenizer_fields(path, n_vocab=tok.get_vocab_size())
        self.assertGreater(len(fields["missing_byte_symbols"]), 200)

    def test_new_tokenizer_has_full_byte_alphabet(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "c.txt")
            with open(corpus, "w", encoding="utf-8") as f:
                f.write("ascii only text\n")
            path = os.path.join(tmp, "tok.json")
            tok = train_bpe_tokenizer([corpus], path, vocab_size=300, min_frequency=1)
            fields = tokenizer_fields(path, n_vocab=tok.get_vocab_size())
        self.assertEqual(fields["missing_byte_symbols"], [])


class TestTensorMapping(unittest.TestCase):
    """State dict -> GGUF tensor list (numpy only)."""

    def test_qkv_concatenation_reproduces_each_head(self):
        cfg_like = dict(vocab_size=300, **TINY)
        from src.model import MiniGPT

        torch.manual_seed(1)
        model = MiniGPT(**cfg_like)
        sd = model.state_dict()
        tensors = dict(gguf_tensors(sd, TINY["n_layers"], TINY["n_heads"]))
        d, nh = TINY["d_model"], TINY["n_heads"]
        hd = d // nh
        for i in range(TINY["n_layers"]):
            qkv = tensors[f"blk.{i}.attn_qkv.weight"]
            self.assertEqual(qkv.shape, (3 * d, d))
            np.testing.assert_array_equal(tensors[f"blk.{i}.attn_qkv.bias"], np.zeros(3 * d))
            for h in range(nh):
                for part, name in enumerate(("wq", "wk", "wv")):
                    rows = qkv[part * d + h * hd: part * d + (h + 1) * hd]
                    np.testing.assert_array_equal(
                        rows, sd[f"blocks.{i}.attn.heads.{h}.{name}.weight"].numpy())
        self.assertNotIn("output.weight", tensors)  # tied head relies on llama.cpp fallback

    def test_untied_head_is_written_explicitly(self):
        from src.model import MiniGPT

        model = MiniGPT(vocab_size=300, **TINY)
        sd = dict(model.state_dict())
        sd["lm_head.weight"] = sd["wte.weight"] + 1.0
        names = [n for n, _ in gguf_tensors(sd, TINY["n_layers"], TINY["n_heads"])]
        self.assertIn("output.weight", names)


@gguf_required
class TestExportReadBack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gguf

        cls.gguf = gguf
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ckpt = os.path.join(cls.tmp.name, "ckpt.pt")
        cls.model, cls.cfg = make_checkpoint(cls.ckpt)
        cls.paths = {}
        cls.readers = {}
        for dtype in ("f32", "f16"):
            out = os.path.join(cls.tmp.name, f"model-{dtype}.gguf")
            cls.summary = export_checkpoint(cls.ckpt, out, SHIPPED_TOKENIZER, dtype=dtype)
            cls.paths[dtype] = out
            cls.readers[dtype] = gguf.GGUFReader(out)

    @classmethod
    def tearDownClass(cls):
        cls.readers.clear()
        cls.tmp.cleanup()

    def tensors(self, dtype):
        return {t.name: t for t in self.readers[dtype].tensors}

    def test_expected_tensors_shapes_and_dtypes(self):
        d, ff, v, ctx = TINY["d_model"], TINY["d_ff"], self.cfg.vocab_size, TINY["context_length"]
        expected = {"token_embd.weight": (v, d), "position_embd.weight": (ctx, d),
                    "output_norm.weight": (d,), "output_norm.bias": (d,)}
        for i in range(TINY["n_layers"]):
            b = f"blk.{i}."
            expected.update({
                b + "attn_norm.weight": (d,), b + "attn_norm.bias": (d,),
                b + "attn_qkv.weight": (3 * d, d), b + "attn_qkv.bias": (3 * d,),
                b + "attn_output.weight": (d, d), b + "attn_output.bias": (d,),
                b + "ffn_norm.weight": (d,), b + "ffn_norm.bias": (d,),
                b + "ffn_up.weight": (ff, d), b + "ffn_up.bias": (ff,),
                b + "ffn_down.weight": (d, ff), b + "ffn_down.bias": (d,),
            })
        T = self.gguf.GGMLQuantizationType
        for dtype in ("f32", "f16"):
            tensors = self.tensors(dtype)
            self.assertEqual(set(tensors), set(expected), dtype)
            for name, shape in expected.items():
                self.assertEqual(tuple(tensors[name].data.shape), shape, name)
                want = T.F16 if dtype == "f16" and len(shape) == 2 else T.F32
                self.assertEqual(tensors[name].tensor_type, want, f"{dtype} {name}")

    def test_f32_values_are_exact(self):
        tensors = self.tensors("f32")
        sd = self.model.state_dict()
        for name, arr in gguf_tensors(sd, TINY["n_layers"], TINY["n_heads"]):
            np.testing.assert_array_equal(np.asarray(tensors[name].data), arr, name)

    def test_f16_values_round_to_half(self):
        tensors = self.tensors("f16")
        for name, arr in gguf_tensors(self.model.state_dict(), TINY["n_layers"], TINY["n_heads"]):
            want = arr.astype(np.float16) if arr.ndim == 2 else arr
            np.testing.assert_array_equal(np.asarray(tensors[name].data), want, name)

    def test_metadata(self):
        r = self.readers["f32"]
        self.assertEqual(field(r, "general.architecture"), "gpt2")
        self.assertEqual(field(r, "gpt2.context_length"), TINY["context_length"])
        self.assertEqual(field(r, "gpt2.embedding_length"), TINY["d_model"])
        self.assertEqual(field(r, "gpt2.feed_forward_length"), TINY["d_ff"])
        self.assertEqual(field(r, "gpt2.block_count"), TINY["n_layers"])
        self.assertEqual(field(r, "gpt2.attention.head_count"), TINY["n_heads"])
        self.assertAlmostEqual(field(r, "gpt2.attention.layer_norm_epsilon"), 1e-5, places=9)
        self.assertEqual(field(r, "general.file_type"), 0)
        self.assertEqual(field(self.readers["f16"], "general.file_type"), 1)
        self.assertEqual(field(r, "tokenizer.ggml.model"), "gpt2")
        self.assertEqual(field(r, "tokenizer.ggml.pre"), "gpt-2")
        self.assertEqual(field(r, "tokenizer.ggml.bos_token_id"), 2)
        self.assertEqual(field(r, "tokenizer.ggml.eos_token_id"), 3)
        self.assertEqual(field(r, "tokenizer.ggml.padding_token_id"), 0)
        self.assertEqual(field(r, "tokenizer.ggml.unknown_token_id"), 1)
        self.assertIs(field(r, "tokenizer.ggml.add_bos_token"), True)
        self.assertIs(field(r, "tokenizer.ggml.add_eos_token"), False)
        self.assertEqual(field(r, "mini_llm.checkpoint_step"), 7)
        self.assertEqual(field(r, "mini_llm.tokenizer_sha256"), file_sha256(SHIPPED_TOKENIZER))
        self.assertIs(field(r, "mini_llm.full_byte_alphabet"), True)
        expected = tokenizer_fields(SHIPPED_TOKENIZER, self.cfg.vocab_size)
        self.assertEqual(field(r, "tokenizer.ggml.tokens"), expected["tokens"])
        self.assertEqual(field(r, "tokenizer.ggml.token_type"), expected["types"])
        self.assertEqual(field(r, "tokenizer.ggml.merges"), expected["merges"])

    def _reference_vs_gguf(self, dtype):
        tensors = {t.name: np.asarray(t.data, dtype=np.float32) for t in self.readers[dtype].tensors}
        hp = dict(d_model=TINY["d_model"], n_heads=TINY["n_heads"], n_layers=TINY["n_layers"],
                  eps=field(self.readers[dtype], "gpt2.attention.layer_norm_epsilon"))
        ids = [2, 40, 77, 5, 300, 12, 99, 256, 3, 18]
        ref = numpy_gpt2_logits(tensors, hp, ids)
        with torch.no_grad():
            ours = self.model(torch.tensor([ids]))[0][0].double().numpy()
        return ref, ours

    def test_gguf_graph_reproduces_minigpt_logits_f32(self):
        ref, ours = self._reference_vs_gguf("f32")
        np.testing.assert_allclose(ref, ours, atol=2e-3, rtol=0)
        for pos in range(ref.shape[0]):
            self.assertEqual(list(np.argsort(-ref[pos])[:10]), list(np.argsort(-ours[pos])[:10]))

    def test_gguf_graph_reproduces_minigpt_probs_f16(self):
        ref, ours = self._reference_vs_gguf("f16")
        dp = max(np.abs(softmax(ref[p]) - softmax(ours[p])).max() for p in range(ref.shape[0]))
        self.assertLess(dp, 5e-3)

    def test_wrong_tokenizer_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = os.path.join(tmp, "other.json")
            shutil.copy(SHIPPED_TOKENIZER, other)
            with open(other, "a", encoding="utf-8") as f:
                f.write("\n")
            with self.assertRaises(SystemExit):
                export_checkpoint(self.ckpt, os.path.join(tmp, "x.gguf"), other)

    def test_cli_writes_file(self):
        from src.export_gguf import main

        out = os.path.join(self.tmp.name, "cli.gguf")
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["--checkpoint", self.ckpt, "--out", out, "--tokenizer", SHIPPED_TOKENIZER])
        self.assertTrue(os.path.getsize(out) > 0)
        self.assertIn("llama-server -m", buf.getvalue())


def _llama_bin(name):
    root = os.environ.get("LLAMA_CPP_DIR")
    if not root:
        return None
    for sub in ("", "bin", os.path.join("build", "bin")):
        for exe in (name, name + ".exe"):
            p = os.path.join(root, sub, exe)
            if os.path.isfile(p):
                return p
    return None


@unittest.skipUnless(HAVE_GGUF and _llama_bin("llama-server") and _llama_bin("llama-tokenize"),
                     "set LLAMA_CPP_DIR to a llama.cpp build with llama-server and llama-tokenize")
class TestLlamaCppParity(unittest.TestCase):
    """Real llama.cpp vs mini-llm: tokenization and next-token top-10 probabilities."""

    TOP_K = 10
    MAX_DP = 2e-3

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ckpt = os.path.join(cls.tmp.name, "ckpt.pt")
        cls.model, cls.cfg = make_checkpoint(cls.ckpt)
        cls.gguf_path = os.path.join(cls.tmp.name, "model.gguf")
        export_checkpoint(cls.ckpt, cls.gguf_path, SHIPPED_TOKENIZER)
        from src.tokenizer import load_tokenizer

        cls.tok = load_tokenizer(SHIPPED_TOKENIZER)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tokenization_matches(self):
        for text in TOKENIZE_SAMPLES:
            out = subprocess.run(
                [_llama_bin("llama-tokenize"), "-m", self.gguf_path, "-p", text,
                 "--ids", "--no-bos", "--log-disable"],
                capture_output=True, text=True, timeout=60, check=True)
            line = [ln for ln in out.stdout.splitlines() if ln.startswith("[")][-1]
            self.assertEqual(json.loads(line), self.tok.encode(text).ids, repr(text))

    def test_server_top10_matches_minigpt(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen(
            [_llama_bin("llama-server"), "-m", self.gguf_path, "--host", "127.0.0.1",
             "--port", str(port), "-c", str(self.cfg.context_length), "-t", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 60
        while True:
            try:
                if opener.open(base + "/health", timeout=2).status == 200:
                    break
            except OSError:
                pass
            if time.time() > deadline or proc.poll() is not None:
                self.fail("llama-server did not become healthy")
            time.sleep(0.3)
        checked = 0
        for prompt in PARITY_PROMPTS:
            ids = [2] + self.tok.encode(prompt).ids  # add_bos_token=true
            with torch.no_grad():
                p = torch.softmax(self.model(torch.tensor([ids]))[0][0, -1].double(), -1).numpy()
            # llama-server returns no logprobs when the sampled token is an incomplete
            # UTF-8 sequence (a lone byte of a multi-byte character), so only prompts
            # whose greedy token decodes to complete text are comparable.
            greedy = int(np.argmax(p))
            if greedy < 4 or "\ufffd" in self.tok.decode([greedy]):
                continue
            checked += 1
            body = json.dumps({"prompt": prompt, "max_tokens": 1, "logprobs": self.TOP_K,
                               "temperature": 0}).encode()
            req = urllib.request.Request(base + "/v1/completions", data=body,
                                         headers={"Content-Type": "application/json"})
            resp = json.load(opener.open(req, timeout=60))
            top = resp["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
            ours = list(np.argsort(-p)[: self.TOP_K])
            theirs = [t["id"] for t in top]
            self.assertEqual(theirs[0], ours[0], prompt)  # same greedy token
            # Ranks below it may swap only between near-tied tokens (float rounding).
            for a, b in zip(ours, theirs, strict=True):
                self.assertLess(abs(p[a] - p[b]), self.MAX_DP, f"{prompt}: {ours} vs {theirs}")
            exact = theirs == ours
            dp = max(abs(math.exp(t["logprob"]) - p[t["id"]]) for t in top)
            print(f"\n[llama.cpp parity] {prompt!r}: top-{self.TOP_K} "
                  f"{'identical' if exact else 'same up to near-ties'}, "
                  f"max |dp| = {dp:.2e}", file=sys.stderr)
            self.assertLess(dp, self.MAX_DP, prompt)
        self.assertGreaterEqual(checked, 2, "too few prompts with a complete-UTF-8 greedy token")


if __name__ == "__main__":
    unittest.main()
