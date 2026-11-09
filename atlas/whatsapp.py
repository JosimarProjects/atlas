"""WhatsApp pela Evolution API.

Mandar mensagem e acao irreversivel e fora da maquina, entao NENHUMA funcao
daqui executa sozinha: todas passam por `confirmacao.pedir()`. O modelo monta
o pedido, voce aprova vendo numero e texto na tela. Ler estado da instancia
nao precisa de trava -- so escrever precisa.

A chave da API nao fica no codigo nem no Git. E lida em tempo de execucao de
ATLAS_EVOLUTION_KEY, ou da skill do Evolution que ja existe na maquina.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

from . import confirmacao

BASE = os.environ.get(
    "ATLAS_EVOLUTION_URL",
    "https://automacoes-evolution-api.fdsefm.easypanel.host").rstrip("/")
INSTANCIA = os.environ.get("ATLAS_EVOLUTION_INSTANCIA", "principal")

_SKILL = Path.home() / ".claude" / "skills" / "evolution-api" / "SKILL.md"


@lru_cache(maxsize=1)
def _chave() -> str:
    """A chave, do ambiente ou da skill. Nunca gravada aqui."""
    do_ambiente = os.environ.get("ATLAS_EVOLUTION_KEY", "").strip()
    if do_ambiente:
        return do_ambiente
    if _SKILL.exists():
        texto = _SKILL.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"apikey[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{16,})", texto)
        if m:
            return m.group(1)
    return ""


def configurado() -> bool:
    return bool(_chave())


def _pedir(caminho: str, corpo: dict | None = None, metodo: str = "GET") -> dict:
    url = f"{BASE}{caminho}"
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo, headers={
        "Content-Type": "application/json", "apikey": _chave()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            bruto = r.read().decode("utf-8", "replace")
        return json.loads(bruto) if bruto.strip() else {}
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"Evolution devolveu {e.code}: {detalhe}") from e


def _normalizar(numero: str) -> str:
    """Numero em texto livre -> formato que a Evolution aceita.

    Aceita "(11) 98888-7777", "11988887777", "+55 11 98888-7777". Sem DDI,
    assume Brasil -- e de onde o dono fala.
    """
    so_digitos = re.sub(r"\D", "", numero)
    if not so_digitos:
        return ""
    if not so_digitos.startswith("55") and len(so_digitos) in (10, 11):
        so_digitos = "55" + so_digitos
    return so_digitos


# -------------------------------------------------------------- leitura
def estado_whatsapp() -> str:
    """Se a instancia esta conectada. Leitura: nao precisa de confirmacao."""
    if not configurado():
        return "WhatsApp nao configurado (falta a chave da Evolution API)."
    try:
        d = _pedir(f"/instance/connectionState/{INSTANCIA}")
    except Exception as e:
        return f"Nao consegui falar com a Evolution ({type(e).__name__})."
    estado = (d.get("instance") or {}).get("state") or d.get("state") or "?"
    traducao = {"open": "conectado", "close": "desconectado",
                "connecting": "conectando"}
    return f"WhatsApp ({INSTANCIA}): {traducao.get(estado, estado)}."


# --------------------------------------------------------------- escrita
def mandar_whatsapp(numero: str, mensagem: str) -> str:
    """Pede para mandar. NAO manda: quem manda e o dono, confirmando."""
    if not configurado():
        return "WhatsApp nao configurado (falta a chave da Evolution API)."
    alvo = _normalizar(numero)
    if len(alvo) < 12:
        return f"Numero invalido: '{numero}'. Use DDD + numero."
    texto = mensagem.strip()
    if not texto:
        return "Mensagem vazia."

    def executar() -> str:
        _pedir(f"/message/sendText/{INSTANCIA}",
               {"number": alvo, "text": texto}, metodo="POST")
        return f"Mensagem enviada para {alvo}."

    return confirmacao.pedir(
        acao="whatsapp",
        resumo=f"Mandar WhatsApp para {alvo}",
        detalhes={"Para": alvo, "Mensagem": texto},
        executar=executar,
    )
