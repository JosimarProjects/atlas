#!/usr/bin/env bash
# Baixa o build ROCm do llama.cpp (~255 MB) e extrai em engine/rocm.
#
# Em placas AMD RDNA3 o backend Vulkan corrompe a inferencia do Qwen3.5:
# a saida degenera conforme cresce (palavras inventadas, troca de idioma,
# raciocinio que nunca termina). O ROCm e o caminho nativo da AMD.
set -u
TAG="b12211"
FILE="llama-${TAG}-bin-win-rocm-10.0-x64.zip"
URL="https://github.com/ggml-org/llama.cpp/releases/download/${TAG}/${FILE}"
ENG="C:/Users/jos_i/atlas/engine"
ARIA="$ENG/aria2c.exe"

"$ARIA" --dir="$ENG" --out="rocm.zip" \
  --continue=true --allow-overwrite=true --auto-file-renaming=false \
  --max-connection-per-server=16 --split=16 \
  --max-tries=0 --retry-wait=10 --console-log-level=warn --summary-interval=30 \
  "$URL" || exit 1

# Deixe o aria2 FECHAR o arquivo antes de extrair. Interromper no meio
# produz um zip do tamanho certo e com conteudo invalido.
powershell.exe -NoProfile -Command \
  "Expand-Archive -Path '$ENG/rocm.zip' -DestinationPath '$ENG/rocm' -Force" || exit 1

find "$ENG/rocm" -name "llama-server.exe" -print -quit
echo "ROCM_OK"
