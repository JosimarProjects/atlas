# Sobe o servidor de embeddings: EmbeddingGemma 2 na porta 8083.
#
# Separado do cerebro de proposito. Sao cargas diferentes -- o cerebro gera
# texto longo e segura VRAM; este responde em milissegundos e e chamado a
# cada fato guardado e a cada busca na memoria. Juntos no mesmo processo,
# uma consulta a memoria ficaria na fila atras de uma resposta de 40 tokens.
param(
    [int]$Port = 8083,
    [switch]$Cpu          # roda na CPU e deixa os 8 GB inteiros para o cerebro
)

$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot
$server = Join-Path $root "engine\llama\llama-server.exe"
$model  = Join-Path $root "models\embeddinggemma-2-Q8_0.gguf"

if (-not (Test-Path $model)) {
    throw "Modelo de embeddings nao encontrado. Rode: bash scripts/baixa-embeddings.sh"
}

# 310 MB cabem folgado junto do Qwen (6,2 GB) nos 8 GB, e na GPU a busca na
# memoria fica imperceptivel. -Cpu existe para quando o contexto do cerebro
# for aumentado e a VRAM apertar.
$camadas = if ($Cpu) { 0 } else { 999 }

& $server `
    --model $model `
    --alias "embed" `
    --host 127.0.0.1 --port $Port `
    --embedding `
    --pooling mean `
    --ctx-size 2048 `
    --n-gpu-layers $camadas `
    --ubatch-size 2048
