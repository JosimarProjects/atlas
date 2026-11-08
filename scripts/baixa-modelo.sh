#!/usr/bin/env bash
# Baixa o Qwen3.5-9B Q4_K_M (~5,6 GB) com aria2 em 16 conexoes paralelas.
# Um unico socket pro HuggingFace fica em ~650 KB/s nesta rede; paralelizar
# contorna o limite por conexao. Pode rodar de novo: --continue retoma.
set -u
DEST="C:/Users/jos_i/atlas/models"
FILE="Qwen_Qwen3.5-9B-Q4_K_M.gguf"
URL="https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF/resolve/main/${FILE}"
ARIA="C:/Users/jos_i/atlas/engine/aria2c.exe"
mkdir -p "$DEST"

"$ARIA" \
  --dir="$DEST" --out="$FILE" \
  --continue=true --allow-overwrite=true --auto-file-renaming=false \
  --max-connection-per-server=16 --split=16 --min-split-size=1M \
  --max-tries=0 --retry-wait=10 --timeout=60 --connect-timeout=30 \
  --summary-interval=30 --console-log-level=warn \
  "$URL"

TAM=$(stat -c %s "$DEST/$FILE" 2>/dev/null || echo 0)
echo "TAMANHO_FINAL=$TAM"
# Q4_K_M fica em ~5,6 GB; abaixo de 5 GB = download truncado.
if [ "$TAM" -gt 5000000000 ]; then echo "MODELO_OK"; else echo "MODELO_INCOMPLETO"; fi
