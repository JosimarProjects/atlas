"""CLI do cofre: cadastrar, listar, ver e remover senhas.

Fica separado do loop da assistente porque aqui a digitacao e sua e o valor
nunca passa por lugar nenhum alem do seu terminal e do arquivo cifrado.

    python -m atlas.senhas criar        # define a senha-mestra
    python -m atlas.senhas guardar      # cadastra um servico
    python -m atlas.senhas listar       # quais servicos existem
    python -m atlas.senhas ver NETFLIX  # mostra uma senha
    python -m atlas.senhas remover X
"""
from __future__ import annotations

import getpass
import sys

from . import cofre


def _pedir_mestra(confirmar: bool = False) -> str | None:
    # getpass nao ecoa na tela e nao entra no historico do shell.
    senha = getpass.getpass("Senha-mestra: ")
    if not senha:
        print("Cancelado.")
        return None
    if confirmar and getpass.getpass("Repita a senha-mestra: ") != senha:
        print("As senhas nao conferem.")
        return None
    return senha


def _destrancar(primeira_vez: bool = False) -> bool:
    senha = _pedir_mestra(confirmar=primeira_vez)
    if senha is None:
        return False
    if not cofre.destrancar(senha):
        print("Senha-mestra incorreta.")
        return False
    return True


def criar() -> int:
    if cofre.existe():
        print("Ja existe um cofre nesta maquina. Use 'guardar' para adicionar.")
        return 1
    print("Definindo a senha-mestra do cofre.")
    print("Ela nao e gravada em lugar nenhum: se perder, as senhas vao junto.\n")
    if not _destrancar(primeira_vez=True):
        return 1
    print("\nCofre criado. Agora: python -m atlas.senhas guardar")
    return 0


def guardar() -> int:
    if not cofre.existe():
        print("Nao ha cofre. Rode antes: python -m atlas.senhas criar")
        return 1
    if not _destrancar():
        return 1
    servico = input("Servico (ex: Netflix): ").strip()
    if not servico:
        print("Cancelado.")
        return 1
    usuario = input("Usuario/e-mail (opcional): ").strip()
    segredo = getpass.getpass("Senha: ")
    if not segredo:
        print("Cancelado.")
        return 1
    nota = input("Observacao (opcional): ").strip()
    cofre.guardar(servico, segredo, usuario, nota)
    print(f"\nGuardado: {servico}. O valor foi cifrado; nem eu nem o modelo o veem.")
    return 0


def listar() -> int:
    itens = cofre.listar()
    if not itens:
        print("O cofre esta vazio.")
        return 0
    print(f"{len(itens)} servicos guardados:\n")
    for c in itens:
        linha = f"  {c['servico']}"
        if c["usuario"]:
            linha += f"  ({c['usuario']})"
        if c["nota"]:
            linha += f"  — {c['nota']}"
        print(linha)
    return 0


def ver(servico: str) -> int:
    if not _destrancar():
        return 1
    valor = cofre.revelar(servico)
    if valor is None:
        print(f"Nao encontrei '{servico}'.")
        return 1
    print(f"\n{servico}: {valor}\n")
    return 0


def remover(servico: str) -> int:
    if not _destrancar():
        return 1
    if cofre.remover(servico):
        print(f"Removido: {servico}")
        return 0
    print(f"Nao encontrei '{servico}'.")
    return 1


def main() -> int:
    acao = sys.argv[1] if len(sys.argv) > 1 else "listar"
    alvo = " ".join(sys.argv[2:])
    if acao == "criar":
        return criar()
    if acao == "guardar":
        return guardar()
    if acao == "listar":
        return listar()
    if acao == "ver":
        return ver(alvo) if alvo else (print("Informe o servico.") or 1)
    if acao == "remover":
        return remover(alvo) if alvo else (print("Informe o servico.") or 1)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
