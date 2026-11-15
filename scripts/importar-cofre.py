#!/usr/bin/env python3
"""Importa um cofre exportado por meu-setup/backup.py.

As entradas chegam CIFRADAS, do jeito que sairam. Este script nao decifra
nada: ele so recoloca as linhas e o sal no banco da maquina nova. Quem abre
e a senha-mestra, na primeira consulta -- e por isso ela precisa ser a mesma
de antes: o sal viaja no export, mas a chave vem da senha, que nao viaja.

Uso:
    python scripts/importar-cofre.py caminho/cofre-cifrado.json
    python scripts/importar-cofre.py arquivo.json --substituir
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atlas import cofre   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("arquivo", type=Path)
    ap.add_argument("--substituir", action="store_true",
                    help="sobrescreve servicos que ja existem no destino")
    args = ap.parse_args()

    if not args.arquivo.is_file():
        print(f"arquivo nao encontrado: {args.arquivo}")
        return 1

    pacote = json.loads(args.arquivo.read_text(encoding="utf-8"))
    if pacote.get("formato") != "atlas-cofre-1":
        print("formato desconhecido; esperava 'atlas-cofre-1'")
        return 1

    con = cofre.abrir()

    # O sal PRIMEIRO, e so se o destino ainda nao tiver um.
    #
    # Trocar o sal de um cofre que ja tem entradas proprias torna essas
    # entradas ilegiveis para sempre: a chave delas foi derivada com o sal
    # antigo. Entao, se ja existe sal, o import so pode seguir se for o
    # mesmo -- caso contrario para aqui, antes de estragar qualquer coisa.
    sal_novo = next((c["valor"] for c in pacote.get("config", [])
                     if c.get("chave") == "sal"), None)
    atual = con.execute(
        "SELECT valor FROM cofre_config WHERE chave = 'sal'").fetchone()

    if sal_novo and atual and atual["valor"] != sal_novo:
        print("ABORTADO: esta maquina ja tem um cofre com OUTRO sal.")
        print("Importar aqui tornaria as entradas atuais ilegiveis.")
        print("Comece de um cofre vazio, ou importe noutra pasta de dados")
        print("(variavel ATLAS_BANCO apontando para outro arquivo).")
        return 2

    if sal_novo and not atual:
        con.execute("INSERT INTO cofre_config (chave, valor) VALUES ('sal', ?)",
                    (sal_novo,))

    entradas = pacote.get("entradas", [])
    if not entradas:
        print("o export nao tem entradas.")
        con.commit()
        return 0

    colunas = [c[1] for c in con.execute("PRAGMA table_info(cofre)")]
    postos = pulados = 0

    for linha in entradas:
        servico = linha.get("servico")
        if not servico:
            continue
        existe = con.execute(
            "SELECT 1 FROM cofre WHERE servico = ? COLLATE NOCASE",
            (servico,)).fetchone()
        if existe and not args.substituir:
            pulados += 1
            continue
        if existe:
            con.execute("DELETE FROM cofre WHERE servico = ? COLLATE NOCASE",
                        (servico,))
        # So as colunas que existem nos DOIS lados: um export de versao
        # anterior nao deve derrubar o import por causa de campo novo.
        campos = [c for c in colunas if c in linha and c != "id"]
        con.execute(
            f"INSERT INTO cofre ({', '.join(campos)}) "
            f"VALUES ({', '.join('?' for _ in campos)})",
            [linha[c] for c in campos],
        )
        postos += 1

    con.commit()
    print(f"importadas: {postos}")
    if pulados:
        print(f"ja existiam (use --substituir para sobrescrever): {pulados}")
    print()
    print("Confira com: python -m atlas.senhas listar")
    print("A senha-mestra e a MESMA de onde o export saiu.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
