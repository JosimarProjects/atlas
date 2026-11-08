#!/usr/bin/env bash
# Baixa o projetor de visao do Qwen3.5-9B (~918 MB).
#
# O GGUF principal traz so a parte de texto. O mmproj e o encoder de imagem:
# com ele o mesmo modelo passa a enxergar -- descrever fotos, ler print de
# tela, analisar frame de video. Sem ele o llama-server ignora qualquer
# imagem que voce mandar, sem reclamar.
set -u
DEST="C:/Users/jos_i/atlas/models"
FILE="mmproj-Qwen_Qwen3.5-9B-f16.gguf"
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
if [ "$TAM" -gt 800000000 ]; then echo "VISAO_OK"; else echo "VISAO_INCOMPLETA"; fi
