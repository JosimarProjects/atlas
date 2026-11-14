#!/usr/bin/env bash
# Baixa um cerebro da Atlas com aria2 em 16 conexoes paralelas.
# Um unico socket pro HuggingFace fica em ~650 KB/s nesta rede; paralelizar
# contorna o limite por conexao. Pode rodar de novo: --continue retoma.
#
#   ./baixa-modelo.sh          # 9B  (~5,7 GB) -- o padrao, mais capaz
#   ./baixa-modelo.sh 4b       # 4B  (~2,8 GB) -- cabe sobrando, responde rapido
#   ./baixa-modelo.sh 4b visao # tambem o projetor de imagem do 4B (~640 MB)
set -u
DEST="C:/Users/jos_i/atlas/models"
ARIA="C:/Users/jos_i/atlas/engine/aria2c.exe"
TAMANHO="${1:-9b}"
EXTRA="${2:-}"
mkdir -p "$DEST"

case "$TAMANHO" in
  4b|4B) REPO="Qwen_Qwen3.5-4B-GGUF";  FILE="Qwen_Qwen3.5-4B-Q4_K_M.gguf";  MIN=2500000000 ;;
  9b|9B) REPO="Qwen_Qwen3.5-9B-GGUF";  FILE="Qwen_Qwen3.5-9B-Q4_K_M.gguf";  MIN=5000000000 ;;
  *) echo "tamanho desconhecido: $TAMANHO (use 4b ou 9b)"; exit 2 ;;
esac

baixar() {
  "$ARIA" \
    --dir="$DEST" --out="$1" \
    --continue=true --allow-overwrite=true --auto-file-renaming=false \
    --max-connection-per-server=16 --split=16 --min-split-size=1M \
    --max-tries=0 --retry-wait=10 --timeout=60 --connect-timeout=30 \
    --summary-interval=30 --console-log-level=warn \
    "https://huggingface.co/bartowski/${REPO}/resolve/main/$1"
}

baixar "$FILE"
TAM=$(stat -c %s "$DEST/$FILE" 2>/dev/null || echo 0)
echo "TAMANHO_FINAL=$TAM"
if [ "$TAM" -gt "$MIN" ]; then echo "MODELO_OK"; else echo "MODELO_INCOMPLETO"; fi

# O projetor de imagem e opcional e so vale a pena no 4B: no 9B ele nao cabe
# junto na placa de 8 GB (veja o README).
if [ "$EXTRA" = "visao" ]; then
  MM="mmproj-${REPO%-GGUF}-f16.gguf"
  baixar "$MM"
  echo "VISAO=$(stat -c %s "$DEST/$MM" 2>/dev/null || echo 0)"
fi
