"""Tira do banco o que as rodadas antigas do autoteste deixaram la dentro.

Ate agora o `scripts/autoteste.py` escrevia no banco do dono. Cada rodada
deixava um lembrete "autoteste", uma nota "nota de teste" e duas falas sem
conversa -- e o espacos.py, na subida seguinte, adotava as falas orfas numa
conversa nova. Na tela, uma "Conversas anteriores" cujo conteudo inteiro era
a Atlas dizendo "resposta de autoteste".

O autoteste ja nao faz mais isso (escreve num banco descartavel). Este script
e para o estrago que ja esta no banco. So apaga o que casa EXATAMENTE com os
textos fixos do teste, e so apaga uma conversa se ela tiver ficado vazia.

    python scripts/limpar_lixo_de_teste.py

Faz uma copia do banco antes de mexer. Nada aqui toca a memoria de longo
prazo: fato guardado e do dono, nao do teste.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atlas import config  # noqa: E402

FALAS = ("teste de autoteste", "resposta de autoteste")
LEMBRETE = "autoteste"
NOTA = ("autoteste", "nota de teste")


def main() -> int:
    if not config.BANCO.exists():
        print(f"banco nao encontrado em {config.BANCO}")
        return 0

    con = sqlite3.connect(config.BANCO)
    con.row_factory = sqlite3.Row

    # Copia pela API do SQLite, nao pelo sistema de arquivos: funciona mesmo
    # com o servidor aberto, sem risco de copiar um banco pela metade.
    copia = config.BANCO.with_suffix(
        f".antes-da-limpeza-{datetime.now():%Y%m%d-%H%M%S}.db")
    with sqlite3.connect(copia) as destino:
        con.backup(destino)
    print(f"copia de seguranca: {copia.name}")

    n_falas = con.execute(
        "DELETE FROM falas WHERE conteudo IN (?, ?)", FALAS).rowcount
    n_lemb = con.execute(
        "DELETE FROM lembretes WHERE texto = ? AND feito = 1", (LEMBRETE,)).rowcount
    n_notas = con.execute(
        "DELETE FROM notas WHERE titulo = ? AND texto = ?", NOTA).rowcount

    # Conversa que so existia para abrigar as falas do teste nao tem por que
    # continuar na barra lateral. Uma conversa de verdade que por acaso ficou
    # vazia tambem nao -- mas essa o dono apagou por conta propria, e nao e
    # deste script decidir isso: dai o filtro pelo titulo que a migracao da.
    vazias = [l["id"] for l in con.execute(
        "SELECT id FROM conversas WHERE titulo = 'Conversas anteriores' "
        "AND NOT EXISTS (SELECT 1 FROM falas WHERE conversa_id = conversas.id)")]
    for cid in vazias:
        con.execute("DELETE FROM conversas WHERE id = ?", (cid,))
    con.commit()

    print(f"falas do teste removidas: {n_falas}")
    print(f"lembretes 'autoteste' removidos: {n_lemb}")
    print(f"notas 'nota de teste' removidas: {n_notas}")
    print(f"conversas vazias removidas: {len(vazias)}"
          + (f" (ids {', '.join(map(str, vazias))})" if vazias else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
