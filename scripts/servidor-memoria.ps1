# Sobe o servidor de embeddings: EmbeddingGemma 2 na porta 8083.
#
# Separado do cerebro de proposito. Sao cargas diferentes -- o cerebro gera
# texto longo e segura VRAM; este responde em milissegundos e e chamado a
# cada fato guardado e a cada busca na memoria. Juntos no mesmo processo,
# uma consulta a memoria ficaria na fila atras de uma resposta de 40 tokens.
param(
    [int]$Port = 8083,
    [switch]$Gpu          # forca a GPU (nao recomendado: rouba VRAM do cerebro)
)

$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot
$server = Join-Path $root "engine\llama\llama-server.exe"
$model  = Join-Path $root "models\embeddinggemma-2-Q8_0.gguf"

if (-not (Test-Path $model)) {
    throw "Modelo de embeddings nao encontrado. Rode: bash scripts/baixa-embeddings.sh"
}

# CPU por PADRAO. Na GPU este modelo reserva ~311 MB que fazem falta ao
# cerebro: com eles, o Qwen nao cabe inteiro e o llama.cpp derruba TUDO para
# a CPU -- 5,7 tok/s em vez de 43. E na CPU o embedding sai em 0,04 s, entao
# nao ha o que ganhar na placa.
# --n-gpu-layers 0 nao basta: o backend Vulkan ainda reservava ~309 MB na
# placa. GGML_VK_VISIBLE_DEVICES="" tambem nao (string vazia nao filtra
# nada). O que resolve e --device none, que desliga o offload de verdade.
$camadas = if ($Gpu) { 999 } else { 0 }
$dispositivo = if ($Gpu) { "Vulkan0" } else { "none" }

& $server `
    --model $model `
    --alias "embed" `
    --host 127.0.0.1 --port $Port `
    --embedding `
    --pooling mean `
    --ctx-size 2048 `
    --n-gpu-layers $camadas `
    --device $dispositivo `
    --ubatch-size 2048
