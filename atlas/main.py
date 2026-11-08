"""Loop principal da Atlas: ouve -> pensa -> fala.

Modos:
    python -m atlas.main            # voz (microfone + alto-falante)
    python -m atlas.main --texto    # so teclado, util para testar sem audio
    python -m atlas.main --diga "oi"  # uma pergunta e sai
"""
from __future__ import annotations

import argparse
import sys
import time

from . import cerebro, config, voz
from .memoria import Memoria

SAUDACAO = "Oi. Estou aqui."
ENCERRAR = {"tchau", "ate logo", "pode dormir", "desliga", "sair", "fim"}


def _esperar_cerebro(tentativas: int = 60) -> bool:
    """O llama-server leva alguns segundos para carregar o modelo na GPU."""
    for i in range(tentativas):
        if cerebro.no_ar():
            return True
        if i == 0:
            print("[atlas] esperando o llama-server subir...")
        time.sleep(2)
    return False


def modo_texto(memoria: Memoria) -> None:
    print(f"[atlas] modo texto. Ctrl+C para sair.\n")
    while True:
        try:
            pergunta = input("voce> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not pergunta:
            continue
        if pergunta.lower() in ENCERRAR:
            print("atlas> Ate logo.")
            return
        try:
            print(f"atlas> {cerebro.responder(pergunta, memoria)}")
        except cerebro.CerebroIndisponivel as e:
            print(f"[erro] {e}")


def modo_voz(memoria: Memoria) -> None:
    from .ouvido import Microfone, transcrever

    if not voz.disponivel():
        print("[atlas] voz nao instalada; rode scripts/baixa-voz.sh", file=sys.stderr)
        return

    mic = Microfone()
    mic.iniciar()
    print("[atlas] ouvindo. Fale.")
    voz.falar(SAUDACAO)
    mic.religar()

    while True:
        audio = mic.proxima_fala(timeout=1.0)
        if audio is None:
            continue

        pergunta = transcrever(audio)
        if not pergunta or len(pergunta) < 2:
            continue
        print(f"voce> {pergunta}")

        if pergunta.lower().strip(" .!?") in ENCERRAR:
            mic.silenciar()
            voz.falar("Ate logo.")
            return

        try:
            # Fala frase a frase: a primeira sai enquanto o resto e gerado.
            mic.silenciar()
            dito = []
            for frase in cerebro.responder_em_pedacos(pergunta, memoria):
                dito.append(frase)
                voz.falar(frase)
            print(f"atlas> {' '.join(dito)}")
        except cerebro.CerebroIndisponivel as e:
            print(f"[erro] {e}", file=sys.stderr)
            voz.falar("Meu cerebro nao esta respondendo.")
        finally:
            mic.religar()


def main() -> int:
    ap = argparse.ArgumentParser(prog="atlas", description="Assistente local")
    ap.add_argument("--texto", action="store_true", help="modo teclado, sem audio")
    ap.add_argument("--diga", metavar="FRASE", help="uma pergunta e sai")
    ap.add_argument("--esquecer", action="store_true", help="apaga a memoria e sai")
    args = ap.parse_args()

    memoria = Memoria()

    if args.esquecer:
        memoria.esquecer_tudo()
        print("[atlas] memoria apagada.")
        return 0

    if not _esperar_cerebro():
        print(
            f"[atlas] llama-server nao respondeu em {config.LLM_URL}.\n"
            f"        Suba com: powershell -File scripts\\servidor.ps1",
            file=sys.stderr,
        )
        return 1

    if args.diga:
        print(cerebro.responder(args.diga, memoria))
        return 0

    try:
        (modo_texto if args.texto else modo_voz)(memoria)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
