# Run your mini-llm in llama.cpp (`llama-server`)

`src/export_gguf.py` turns a mini-llm checkpoint into a GGUF file that llama.cpp
loads as its built-in `gpt2` architecture. The same file runs in `llama-server`,
`llama-cli`, LM Studio and other llama.cpp-based tools. No architecture change is
involved: mini-llm's blocks (learned positions, LayerNorm with bias, GELU MLP,
tied output head) are exactly GPT-2's.

## Quick path: train tiny → export → serve → query

Install the optional export dependency once:

```powershell
cd small-projects\mini-llm
python -m pip install -r requirements.txt -r requirements-gguf.txt
```

Get llama.cpp from its [releases page](https://github.com/ggml-org/llama.cpp/releases)
(on Windows with an AMD iGPU, the `win-vulkan-x64` zip), unzip it, and point
`LLAMA_CPP_DIR` at the folder that holds `llama-server.exe`. Then:

```powershell
$env:LLAMA_CPP_DIR = "C:\llama.cpp"
.\scripts\gguf_demo.ps1
```

On Linux/macOS: `LLAMA_CPP_DIR=/path/to/llama.cpp bash scripts/gguf_demo.sh`.

The script trains a 2-layer, 64-wide model on the shipped 2.4 KB sample
(1,000 steps, well under a minute on a CPU), exports `checkpoints\gguf-demo\model-f32.gguf`,
starts `llama-server` on port 9931 and asks it for 20 tokens. The text is
nonsense-shaped English: the sample corpus teaches shape, not the world. A real
corpus (see `docs/EXPERIMENT-tinystories.md`) is what makes the output readable.

## Step by step

```powershell
# 1. export (f32 is exact; f16 halves the size, norms and biases stay f32)
python -m src.export_gguf --checkpoint checkpoints\final.pt --out model-f32.gguf
python -m src.export_gguf --checkpoint checkpoints\final.pt --out model-f16.gguf --dtype f16

# 2. optional: quantize with llama.cpp
& "$env:LLAMA_CPP_DIR\llama-quantize.exe" model-f32.gguf model-q8_0.gguf Q8_0

# 3. serve. -c = the model's context_length (the export prints it);
#    add -ngl 99 to offload layers to the GPU on a Vulkan build
& "$env:LLAMA_CPP_DIR\llama-server.exe" -m model-f32.gguf -c 512 --port 9931

# 4. query (from another terminal)
$body = @{ prompt = "Once upon a time"; max_tokens = 40; temperature = 0.8; top_k = 40 } | ConvertTo-Json
(Invoke-RestMethod http://127.0.0.1:9931/v1/completions -Method Post -ContentType application/json -Body $body).choices[0].text
```

```bash
curl -s http://127.0.0.1:9931/v1/completions -H "Content-Type: application/json" \
  -d '{"prompt": "Once upon a time", "max_tokens": 40, "temperature": 0.8, "top_k": 40}'
```

Use `/v1/completions`, not `/v1/chat/completions`: mini-llm is a base model and
the GGUF carries no chat template.

The tokenizer is checked the same way `generate.py` checks it: the export reads
the tokenizer path stored in the checkpoint (or `--tokenizer`) and refuses one
whose sha256 differs from the digest the checkpoint recorded.

## What the exporter writes

| mini-llm | GGUF (`gpt2` arch) |
|---|---|
| `wte.weight` | `token_embd.weight` (tied head: no `output.weight`; llama.cpp falls back to `token_embd`) |
| `wpe.weight` | `position_embd.weight` |
| `blocks.i.ln1` / `ln2` | `blk.i.attn_norm` / `blk.i.ffn_norm` (weight + bias) |
| `blocks.i.attn.heads.h.{wq,wk,wv}` | `blk.i.attn_qkv.weight`: all q heads in order, then all k, then all v; `blk.i.attn_qkv.bias` = zeros (llama.cpp requires it, mini-llm has none) |
| `blocks.i.attn.proj` | `blk.i.attn_output` |
| `blocks.i.mlp.fc1` / `fc2` | `blk.i.ffn_up` / `blk.i.ffn_down` |
| `ln_f` | `output_norm` |

Metadata: `gpt2.context_length`, `embedding_length`, `feed_forward_length` (the
real `d_ff`), `block_count`, `attention.head_count`, `layer_norm_epsilon` = 1e-5;
tokenizer `gpt2` model with pre-tokenizer `gpt-2`, tokens in id order (specials
`<pad>`/`<unk>`/`<bos>`/`<eos>` = 0–3 as CONTROL), merges, and
`add_bos_token = true` because training frames every passage as `<bos> … <eos>`.
Provenance: `mini_llm.checkpoint_step`, `mini_llm.tokenizer_sha256`,
`mini_llm.full_byte_alphabet`.

### Why not llama.cpp's `convert_hf_to_gguf.py`

It identifies a BPE tokenizer by hashing the ids it produces for a fixed test
string and raises `NotImplementedError` ("BPE pre-tokenizer was not recognized")
for any unknown hash, which a tokenizer trained from scratch always is. mini-llm's
tokenizer is plain GPT-2 byte-level BPE, so the exporter writes the GGUF directly
with `gguf-py` and declares `tokenizer.ggml.pre = "gpt-2"`.

## Tokenizers from before the full-alphabet fix

Tokenizers prepared since the `initial_alphabet` fix contain all 256 byte
symbols, so llama.cpp and mini-llm tokenize any text identically. An older
tokenizer (prepared before that fix) still exports, and the GGUF is still an
exact copy of the weights, but the exporter warns and writes
`mini_llm.full_byte_alphabet = false`: a character the old corpus never contained
becomes `<unk>` in mini-llm and is dropped by llama.cpp, so such text tokenizes
differently in the two. Pass the old tokenizer with `--tokenizer` (its digest
must match the checkpoint). Re-running `prepare_data.py` and retraining removes
the difference.

## Checking parity yourself

```powershell
python scripts\llama_parity.py --checkpoint checkpoints\final.pt --gguf model-f32.gguf --llama-cpp-dir $env:LLAMA_CPP_DIR
# GPU: add --extra-server-args "-ngl 99"
```

It compares `llama-tokenize` with the HF tokenizer on three strings (whitespace,
tabs, accents, emoji), then for several prompts compares llama-server's
next-token top-10 (`/v1/completions`, `logprobs: 10`, temperature 0) with
mini-llm's softmax. The greedy token must match exactly; lower ranks may swap
only between tokens whose probabilities differ by less than `--max-dp`
(default 2e-3), which is float rounding between two runtimes. llama-server
returns no logprobs when the greedy token is an incomplete UTF-8 byte; those
prompts are reported as skipped.

The test suite has the same check as `tests/test_export_gguf.py::TestLlamaCppParity`,
which runs when `LLAMA_CPP_DIR` is set and is skipped otherwise (and in CI).

## Quantization notes

- **Q8_0** works for any width and is the recommended small format (about 30%
  of f32 here). Expect top-1 to stay the same but probabilities to move by up
  to a few 1e-3; use `--max-dp 1e-2` when checking a quantized file.
- **k-quants (Q4_K_M etc.)** need rows whose length is a multiple of 256.
  `d_model` 64 and the default 384 are not, so `llama-quantize` falls back to
  another type tensor by tensor (it warns). Prefer Q8_0 or F16 at this size.
