"""Cliente MCP por stdio, para a Atlas usar servidores que voce ja tem.

O MCP e JSON-RPC sobre entrada e saida padrao: nasce um processo, trocam-se
mensagens em linhas de JSON. Sao ~150 linhas -- menos do que custaria
depender de um SDK que muda de versao.

CURADORIA, NAO ESPELHO
----------------------
Um servidor MCP exporta dezenas ou centenas de ferramentas; o
google-workspace sozinho passa de cem. Despejar tudo no roteador seria
garantir o erro: ja medimos que a precisao cai quando a lista cresce, e isto
aqui e um 9B. Entao `PERMITIDAS` lista nominalmente o que entra -- poucas
ferramentas, as que alguem usa por voz.

E as que agem fora da maquina (mandar e-mail, criar evento) passam pela
trava de confirmacao, pelo mesmo motivo do WhatsApp.
"""
from __future__ import annotations

import json
import subprocess
import threading
from typing import Any

from . import confirmacao

# Nome curto -> (servidor, ferramenta real, descricao, tem efeito externo?)
PERMITIDAS: dict[str, tuple[str, str, str, bool]] = {
    "ler_emails": (
        "google-workspace", "search_gmail_messages",
        "Le os e-mails da caixa. Use para 'tenho e-mail', 'o que chegou', "
        "'procura o e-mail do fulano'.", False),
    "ler_email": (
        "google-workspace", "get_gmail_message_content",
        "Le o conteudo de um e-mail especifico pelo id.", False),
    "agenda_de_hoje": (
        "google-workspace", "get_events",
        "Compromissos da agenda. Use para 'minha agenda', 'o que tenho hoje', "
        "'tenho reuniao'.", False),
    "criar_evento": (
        "google-workspace", "manage_event",
        "Cria um compromisso na agenda.", True),
    "mandar_email": (
        "google-workspace", "send_gmail_message",
        "Envia um e-mail.", True),
}

def _ambiente_do_claude(servidor: str) -> dict[str, str]:
    """Pega as variaveis que o .claude.json ja define para este servidor.

    O workspace-mcp precisa de GOOGLE_OAUTH_CLIENT_ID e CLIENT_SECRET para
    sequer iniciar o fluxo de autenticacao. Elas ja estao configuradas na
    maquina, no .claude.json -- repassa-las e melhor do que pedir ao dono
    que configure a mesma coisa duas vezes, e evita copiar segredo para
    dentro deste repositorio.
    """
    import json
    from pathlib import Path as _P

    try:
        conf = json.loads((_P.home() / ".claude.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    entrada = (conf.get("mcpServers") or {}).get(servidor) or {}
    return {k: str(v) for k, v in (entrada.get("env") or {}).items()}


# Como subir cada servidor. Os caminhos saem do .claude.json do dono.
SERVIDORES: dict[str, list[str]] = {
    "google-workspace": [
        str(__import__("pathlib").Path.home() / ".local" / "bin" / "uvx"),
        "workspace-mcp", "--single-user", "--tool-tier", "complete",
        "--transport", "stdio",
    ],
}


def _executavel(nome: str) -> str:
    """Caminho real do executavel do servidor.

    No Windows o uvx e uvx.exe, e o caminho registrado no .claude.json vem
    sem a extensao -- Path(...).exists() dava False e a Atlas achava que o
    google-workspace nao estava instalado.
    """
    import shutil
    from pathlib import Path

    cmd = SERVIDORES.get(nome)
    if not cmd:
        return ""
    alvo = cmd[0]
    for tentativa in (alvo, alvo + ".exe", alvo + ".cmd"):
        if Path(tentativa).exists():
            return tentativa
    return shutil.which(Path(alvo).name) or ""


class Servidor:
    """Um processo MCP vivo, com as chamadas serializadas."""

    def __init__(self, nome: str, comando: list[str]) -> None:
        self.nome = nome
        self.comando = comando
        self.proc: subprocess.Popen | None = None
        self.seq = 0
        self.trava = threading.Lock()

    def _subir(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        import os
        ambiente = {**os.environ, **_ambiente_do_claude(self.nome)}
        self.proc = subprocess.Popen(
            [_executavel(self.nome) or self.comando[0], *self.comando[1:]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            bufsize=1, env=ambiente,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "atlas", "version": "0.1.0"},
        })
        self._notificar("notifications/initialized")

    def _escrever(self, msg: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _notificar(self, metodo: str, params: dict | None = None) -> None:
        self._escrever({"jsonrpc": "2.0", "method": metodo,
                        "params": params or {}})

    def _rpc(self, metodo: str, params: dict | None = None,
             timeout: int = 90) -> dict[str, Any]:
        assert self.proc and self.proc.stdout
        self.seq += 1
        meu = self.seq
        self._escrever({"jsonrpc": "2.0", "id": meu, "method": metodo,
                        "params": params or {}})
        # Notificacoes e respostas de outros ids podem chegar no meio; so
        # para quando vier a resposta do id que pedimos.
        while True:
            linha = self.proc.stdout.readline()
            if not linha:
                raise RuntimeError(f"servidor {self.nome} encerrou")
            try:
                msg = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != meu:
                continue
            if "error" in msg:
                raise RuntimeError(msg["error"].get("message", "erro no MCP"))
            return msg.get("result") or {}

    def chamar(self, ferramenta: str, argumentos: dict[str, Any]) -> str:
        with self.trava:
            self._subir()
            r = self._rpc("tools/call", {"name": ferramenta,
                                         "arguments": argumentos})
        partes = []
        for item in r.get("content") or []:
            if item.get("type") == "text":
                partes.append(item.get("text", ""))
        texto = "\n".join(partes).strip()
        return texto or "(sem conteudo)"

    def encerrar(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


_vivos: dict[str, Servidor] = {}


def _servidor(nome: str) -> Servidor:
    if nome not in _vivos:
        if nome not in SERVIDORES:
            raise RuntimeError(f"servidor MCP desconhecido: {nome}")
        _vivos[nome] = Servidor(nome, SERVIDORES[nome])
    return _vivos[nome]


def disponivel(nome: str) -> bool:
    return bool(_executavel(nome))


def executar(curto: str, argumentos: dict[str, Any]) -> str:
    """Roda uma ferramenta MCP, com trava se ela agir fora da maquina."""
    if curto not in PERMITIDAS:
        return f"Ferramenta MCP desconhecida: {curto}"
    servidor, real, _desc, externa = PERMITIDAS[curto]
    if not disponivel(servidor):
        return f"O servidor {servidor} nao esta instalado nesta maquina."

    def agir() -> str:
        return _servidor(servidor).chamar(real, argumentos)

    if not externa:
        try:
            return agir()
        except Exception as e:
            return f"Erro no {servidor}: {type(e).__name__}: {e}"

    legiveis = {k: str(v)[:300] for k, v in argumentos.items() if v}
    return confirmacao.pedir(
        acao=curto,
        resumo=f"{_desc.rstrip('.')} ({servidor})",
        detalhes=legiveis,
        executar=agir,
    )


def encerrar_todos() -> None:
    for s in _vivos.values():
        s.encerrar()
    _vivos.clear()
