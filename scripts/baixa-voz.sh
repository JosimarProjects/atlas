#!/usr/bin/env bash
# Baixa a voz PT-BR do Piper (~63 MB). Duas opcoes boas:
#   pt_BR-faber-medium  -> masculina, clara (padrao)
#   pt_BR-edresson-low  -> feminina, mais leve
set -u
VOZ="${1:-pt_BR-faber-medium}"
DEST="C:/Users/jos_i/atlas/voices"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR"

case "$VOZ" in
  pt_BR-faber-medium)  SUB="faber/medium" ;;
  pt_BR-edresson-low)  SUB="edresson/low" ;;
  *) echo "Voz desconhecida: $VOZ"; exit 1 ;;
esac

mkdir -p "$DEST"
for EXT in onnx onnx.json; do
  echo "baixando $VOZ.$EXT"
  curl -L --fail --retry 5 --retry-delay 5 --retry-all-errors -C - \
       -o "$DEST/$VOZ.$EXT" "$BASE/$SUB/$VOZ.$EXT" || exit 1
done

ls -la "$DEST"
echo "VOZ_OK: $VOZ"
