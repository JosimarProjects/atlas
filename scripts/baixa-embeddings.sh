#!/usr/bin/env bash
# Baixa o EmbeddingGemma 2 (~310 MB), o modelo que transforma texto em vetor.
#
# E ele que da memoria semantica a Atlas: voce salva "Duna 3, ficcao
# cientifica, indicacao do Joao" e depois pergunta "quais filmes eu mandei
# guardar?" -- palavras diferentes, mesmo assunto. Busca por palavra-chave
# nao acha isso; busca por vetor acha.
set -u
DEST="C:/Users/jos_i/atlas/models"
FILE="embeddinggemma-2-Q8_0.gguf"
URL="https://huggingface.co/ggml-org/embeddinggemma-2-GGUF/resolve/main/${FILE}"
ARIA="C:/Users/jos_i/atlas/engine/aria2c.exe"
mkdir -p "$DEST"

"$ARIA" --dir="$DEST" --out="$FILE" \
  --continue=true --allow-overwrite=true --auto-file-renaming=false \
  --max-connection-per-server=16 --split=16 --min-split-size=1M \
  --max-tries=0 --retry-wait=10 --timeout=60 --connect-timeout=30 \
  --summary-interval=30 --console-log-level=warn \
  "$URL"

TAM=$(stat -c %s "$DEST/$FILE" 2>/dev/null || echo 0)
echo "TAMANHO_FINAL=$TAM"
if [ "$TAM" -gt 250000000 ]; then echo "EMBEDDINGS_OK"; else echo "EMBEDDINGS_INCOMPLETO"; fi
