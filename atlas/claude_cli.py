"""O outro cerebro: Claude, pelo CLI do Claude Code ja instalado.

Nao usa chave de API nem SDK -- chama o `claude` que ja esta autenticado
nesta maquina. Zero configuracao extra, e a conta e a mesma que voce ja usa.

O QUE MUDA E SO ISTO
--------------------
Memoria, ferramentas, cofre, agendamentos, interface: nada disso sabe qual
cerebro esta ligado. `cerebro._chamar()` e o unico ponto que diverge, e e
por isso que a troca cabe num interruptor.

CUSTO
-----
Cada chamada carrega o prompt de sistema do Claude Code -- ~15 mil tokens
em cache, que o local nao tem. Medido: US$ 0,157 por mensagem em regime,
contra zero no Qwen. Em plano de assinatura isso consome cota em vez de
cobrar, mas continua nao sendo de graca. Por isso o padrao e o local.

Desligamos MCP e ferramentas: aqui ele e so um gerador de texto. Quem
decide ferramenta continua sendo o roteador da Atlas -- assim o
comportamento nao muda junto com o cerebro.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

TEMPO_LIMITE = 180

# Sem MCP e sem ferramentas: a Atlas ja tem as suas, e deixar o Claude usar
# as dele faria o comportamento mudar conforme o cerebro escolhido.
_SEM_FERRAMENTAS = [
    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    "--disallowed-tools",
    "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Agent,Task,"
    "NotebookEdit,TodoWrite,Skill",
]


class ClaudeIndisponivel(RuntimeError):
    """O CLI do Claude nao respondeu."""


def _executavel() -> str:
    """Caminho completo do claude.

    No Windows o comando e um .cmd, e o subprocess nao resolve shims pelo
    nome: ["claude", ...] estourava FileNotFoundError mesmo com o CLI
    instalado e funcionando no terminal.
    """
    import shutil
    return shutil.which("claude") or ""


def disponivel() -> bool:
    return bool(_executavel())


def _achatar(mensagens: list[dict[str, Any]]) -> tuple[str, str]:
    """Converte a conversa em (instrucao de sistema, texto do usuario).

    O CLI recebe um prompt so, entao o sistema vira --append-system-prompt e
    o historico entra transcrito, marcando quem falou.
    """
    sistema = []
    corpo = []
    for m in mensagens:
        papel = m.get("role")
        texto = (m.get("content") or "").strip()
        if not texto:
            continue
        if papel == "system":
            sistema.append(texto)
        elif papel == "user":
            corpo.append(f"Dono: {texto}")
        else:
            corpo.append(f"Atlas: {texto}")
    return "\n\n".join(sistema), "\n".join(corpo)


def chamar(mensagens: list[dict[str, Any]],
           schema: dict[str, Any] | None = None) -> str:
    """Mesma assinatura util de cerebro._chamar, pelo Claude."""
    sistema, corpo = _achatar(mensagens)
    if schema is not None:
        # Sem gramatica BNF aqui; o pedido explicito basta num modelo deste
        # porte, e o chamador ja trata JSON invalido.
        corpo += ("\n\nResponda APENAS com um JSON valido neste formato, sem "
                  "cercas de codigo e sem explicacao:\n"
                  + json.dumps(schema, ensure_ascii=False))

    exe = _executavel()
    if not exe:
        raise ClaudeIndisponivel("o CLI do claude nao esta instalado")
    args = [exe, "-p", corpo, "--output-format", "json", *_SEM_FERRAMENTAS]
    if sistema:
        args += ["--append-system-prompt", sistema]

    try:
        r = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            timeout=TEMPO_LIMITE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise ClaudeIndisponivel("o Claude demorou demais") from e

    if r.returncode != 0:
        raise ClaudeIndisponivel(
            f"claude saiu com {r.returncode}: {(r.stderr or '')[-200:]}")
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeIndisponivel("resposta do claude nao era JSON") from e
    if d.get("is_error"):
        raise ClaudeIndisponivel(str(d.get("result"))[:200])

    texto = (d.get("result") or "").strip()
    if schema is not None:
        # Mesmo pedindo sem cercas, as vezes vem ```json ... ```
        if texto.startswith("```"):
            texto = texto.split("```")[1]
            if texto.startswith("json"):
                texto = texto[4:]
            texto = texto.strip()
    return texto


def custo_ultimo(mensagens: list[dict[str, Any]]) -> float:
    """Nao usado no caminho normal; util para medir."""
    return 0.0
