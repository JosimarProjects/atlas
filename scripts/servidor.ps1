# Sobe o cerebro da Atlas: llama-server com o Qwen3.5-9B na RX 7600 (Vulkan).
# Expoe uma API compativel com OpenAI em http://127.0.0.1:8080/v1
param(
    [int]$Port = 8080,
    [int]$Ctx = 16384,       # contexto; 16k cabe junto do modelo nos 8 GB
    [int]$Gpu = 999          # todas as camadas na GPU
)

$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot
$server = Join-Path $root "engine\llama\llama-server.exe"
$model  = Join-Path $root "models\Qwen_Qwen3.5-9B-Q4_K_M.gguf"

if (-not (Test-Path $server)) { throw "llama-server nao encontrado em $server" }
if (-not (Test-Path $model))  { throw "modelo nao encontrado em $model" }

# A RX 7600 e o unico device Vulkan visivel (a iGPU 740M nao aparece),
# mas fixamos mesmo assim para o dia em que isso mudar.
$env:GGML_VK_VISIBLE_DEVICES = "0"

& $server `
    --model $model `
    --alias "atlas" `
    --host 127.0.0.1 --port $Port `
    --ctx-size $Ctx `
    --n-gpu-layers $Gpu `
    --device "Vulkan0" `
    --flash-attn on `
    --cache-type-k q8_0 --cache-type-v q8_0 `
    --jinja `
    --parallel 2 `
    --metrics
