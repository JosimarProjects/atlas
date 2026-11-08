"""O cerebro: conversa com o Qwen3.5-9B servido pelo llama-server.

Fala HTTP puro com a API compativel com OpenAI -- sem SDK, para a Atlas nao
depender de nada que possa quebrar numa atualizacao.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator

from . import config, ferramentas
from .memoria import Memoria

MAX_VOLTAS_FERRAMENTA = 4   # teto de chamadas encadeadas por pergunta


class CerebroIndisponivel(RuntimeError):
    """O llama-server nao respondeu."""


def _post(caminho: str, corpo: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{config.LLM_URL}{caminho}",
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        raise CerebroIndisponivel(
            f"llama-server nao respondeu em {config.LLM_URL}: {e}"
        ) from e


def no_ar() -> bool:
    try:
        with urllib.request.urlopen(f"{config.LLM_URL}/models", timeout=5):
            return True
    except Exception:
        return False


def _completar(mensagens: list[dict[str, Any]], usar_ferramentas: bool = True) -> dict:
    corpo: dict[str, Any] = {
        "model": config.LLM_MODELO,
        "messages": mensagens,
        "temperature": config.LLM_TEMPERATURA,
        "max_tokens": config.LLM_MAX_TOKENS,
    }
    if usar_ferramentas:
        corpo["tools"] = ferramentas.ESQUEMAS
        corpo["tool_choice"] = "auto"
    resposta = _post("/chat/completions", corpo)
    return resposta["choices"][0]["message"]


def responder(pergunta: str, memoria: Memoria) -> str:
    """Pergunta -> resposta final em texto, resolvendo tool calls no caminho."""
    memoria.registrar("user", pergunta)
    mensagens = memoria.contexto()

    for _ in range(MAX_VOLTAS_FERRAMENTA):
        msg = _completar(mensagens)
        chamadas = msg.get("tool_calls") or []

        if not chamadas:
            texto = (msg.get("content") or "").strip()
            memoria.registrar("assistant", texto)
            _talvez_resumir(memoria)
            return texto

        # O modelo pediu ferramentas: executa todas e devolve os resultados.
        mensagens.append(msg)
        for c in chamadas:
            fn = c["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            resultado = ferramentas.executar(fn["name"], args)
            mensagens.append({
                "role": "tool",
                "tool_call_id": c.get("id", fn["name"]),
                "content": resultado,
            })

    # Estourou o teto: pede um fechamento sem mais ferramentas.
    msg = _completar(mensagens, usar_ferramentas=False)
    texto = (msg.get("content") or "").strip()
    memoria.registrar("assistant", texto)
    return texto


def _talvez_resumir(memoria: Memoria) -> None:
    """Comprime falas antigas num resumo de tamanho constante."""
    if not memoria.precisa_resumir():
        return
    antigas = memoria.falas_para_resumir()
    if not antigas:
        return
    transcricao = "\n".join(f"{m['role']}: {m['content']}" for m in antigas)
    pedido = [
        {"role": "system", "content":
            "Resuma a conversa abaixo em ate 6 linhas, em portugues. Guarde fatos "
            "sobre o usuario, decisoes e pendencias. Descarte conversa fiada."},
        {"role": "user", "content":
            (f"Resumo anterior:\n{memoria.resumo}\n\n" if memoria.resumo else "")
            + f"Novas falas:\n{transcricao}"},
    ]
    try:
        msg = _completar(pedido, usar_ferramentas=False)
        novo = (msg.get("content") or "").strip()
        if novo:
            memoria.resumo = novo
    except CerebroIndisponivel:
        pass  # resumir e otimizacao; nunca derrubar a conversa por causa disso


def responder_em_pedacos(pergunta: str, memoria: Memoria) -> Iterator[str]:
    """Mesma resposta, mas fatiada em frases -- deixa a fala comecar antes.

    Sem isso a Atlas so abre a boca quando a resposta inteira terminou; assim
    ela comeca a falar a primeira frase enquanto o resto ainda e gerado.
    """
    texto = responder(pergunta, memoria)
    atual = ""
    for ch in texto:
        atual += ch
        if ch in ".!?" and len(atual.strip()) > 25:
            yield atual.strip()
            atual = ""
    if atual.strip():
        yield atual.strip()
