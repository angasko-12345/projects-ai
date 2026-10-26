#!/usr/bin/env bash
# Train a tiny mini-llm on the shipped sample -> export GGUF -> serve it with
# llama.cpp's llama-server -> query /v1/completions. Run from small-projects/mini-llm.
#
#   LLAMA_CPP_DIR=/path/to/llama.cpp/bin bash scripts/gguf_demo.sh
#
# Needs: pip install -r requirements.txt -r requirements-gguf.txt, plus a llama.cpp
# build or release (https://github.com/ggml-org/llama.cpp/releases) whose directory
# contains llama-server. Without LLAMA_CPP_DIR it stops after the export.
set -euo pipefail

PY="${PYTHON:-python}"
OUT="${OUT:-checkpoints/gguf-demo}"
PORT="${PORT:-9931}"
STEPS="${STEPS:-1000}"
CTX=32   # the shipped 2.4 KB sample needs --context-length 32

"$PY" -m src.train --context-length "$CTX" --max-steps "$STEPS" \
    --n-layers 2 --n-heads 2 --d-model 64 --d-ff 256 \
    --batch-size 16 --warmup-steps 100 --eval-interval "$STEPS" --checkpoint-dir "$OUT" --seed 1337
"$PY" -m src.export_gguf --checkpoint "$OUT/final.pt" --out "$OUT/model-f32.gguf"

if [ -z "${LLAMA_CPP_DIR:-}" ]; then
    echo "LLAMA_CPP_DIR not set: skipping llama-server. GGUF is at $OUT/model-f32.gguf"
    exit 0
fi

"$LLAMA_CPP_DIR/llama-server" -m "$OUT/model-f32.gguf" -c "$CTX" --host 127.0.0.1 \
    --port "$PORT" > "$OUT/llama-server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do
    curl -sf --noproxy '*' "http://127.0.0.1:$PORT/health" > /dev/null && break
    sleep 0.5
done

# A base model has no chat template: use /v1/completions, not /v1/chat/completions.
curl -s --noproxy '*' "http://127.0.0.1:$PORT/v1/completions" \
    -H "Content-Type: application/json" \
    -d '{"prompt": "The fox", "max_tokens": 20, "temperature": 0.8, "top_k": 40, "seed": 1}'
echo
