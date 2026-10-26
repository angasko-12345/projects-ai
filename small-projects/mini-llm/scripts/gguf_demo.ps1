# Train a tiny mini-llm on the shipped sample -> export GGUF -> serve it with
# llama.cpp's llama-server -> query /v1/completions. Run from small-projects\mini-llm:
#
#   $env:LLAMA_CPP_DIR = "C:\llama.cpp"   # folder with llama-server.exe (Vulkan release works)
#   .\scripts\gguf_demo.ps1
#
# Needs: pip install -r requirements.txt -r requirements-gguf.txt.
# Without LLAMA_CPP_DIR it stops after the export.
$ErrorActionPreference = "Stop"
$Out = "checkpoints\gguf-demo"
$Port = 9931
$Steps = 1000
$Ctx = 32   # the shipped 2.4 KB sample needs --context-length 32

python -m src.train --context-length $Ctx --max-steps $Steps `
    --n-layers 2 --n-heads 2 --d-model 64 --d-ff 256 `
    --batch-size 16 --warmup-steps 100 --eval-interval $Steps --checkpoint-dir $Out --seed 1337
if ($LASTEXITCODE -ne 0) { throw "training failed" }
python -m src.export_gguf --checkpoint "$Out\final.pt" --out "$Out\model-f32.gguf"
if ($LASTEXITCODE -ne 0) { throw "export failed" }

if (-not $env:LLAMA_CPP_DIR) {
    Write-Host "LLAMA_CPP_DIR not set: skipping llama-server. GGUF is at $Out\model-f32.gguf"
    exit 0
}

$server = Start-Process -FilePath (Join-Path $env:LLAMA_CPP_DIR "llama-server.exe") `
    -ArgumentList "-m", "$Out\model-f32.gguf", "-c", $Ctx, "--host", "127.0.0.1", "--port", $Port `
    -RedirectStandardError "$Out\llama-server.log" -PassThru -NoNewWindow
try {
    $ready = $false
    for ($i = 0; $i -lt 60 -and -not $ready; $i++) {
        try { Invoke-RestMethod "http://127.0.0.1:$Port/health" | Out-Null; $ready = $true }
        catch { Start-Sleep -Milliseconds 500 }
    }
    # A base model has no chat template: use /v1/completions, not /v1/chat/completions.
    $body = @{ prompt = "The fox"; max_tokens = 20; temperature = 0.8; top_k = 40; seed = 1 } | ConvertTo-Json
    $resp = Invoke-RestMethod "http://127.0.0.1:$Port/v1/completions" -Method Post `
        -ContentType "application/json" -Body $body
    $resp.choices[0].text
}
finally {
    Stop-Process -Id $server.Id -ErrorAction SilentlyContinue
}
