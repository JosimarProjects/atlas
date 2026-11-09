"""API HTTP da Atlas e a interface web.

Esta e a camada que transforma a Atlas de script em servico. O llama-server
e o modelo; isto aqui e a assistente -- memoria, ferramentas, cofre -- com
uma porta por onde qualquer cliente entra: o navegador, um app, um script.

Usa so a biblioteca padrao. Uma dependencia a mais aqui seria uma dependencia
a mais para instalar na proxima maquina.

    python -m atlas.web                 # so esta maquina
    python -m atlas.web --rede          # tambem a rede local
    python -m atlas.web --rede --senha   # exige token na rede
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import cerebro, cofre, config, ferramentas, lembrancas
from .memoria import Memoria, conectar

PASTA_WEB = config.RAIZ / "web"

# Uma conversa por vez: a Atlas e pessoal, nao multiusuario. O lock evita
# que duas abas abertas embaralhem a memoria uma da outra.
_vez = threading.Lock()
_memoria: Memoria | None = None

TOKEN: str = ""


def _mem() -> Memoria:
    global _memoria
    if _memoria is None:
        _memoria = Memoria()
    return _memoria


class Atendente(BaseHTTPRequestHandler):
    server_version = "Atlas"

    # ------------------------------------------------------------ utilidades
    def log_message(self, formato, *args):
        pass    # o log padrao polui o terminal a cada pedido

    def _json(self, dados: Any, codigo: int = 200) -> None:
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(corpo)

    def _autorizado(self) -> bool:
        if not TOKEN:
            return True
        enviado = self.headers.get("X-Atlas-Token", "")
        if not enviado:
            enviado = parse_qs(urlparse(self.path).query).get("t", [""])[0]
        return secrets.compare_digest(enviado, TOKEN)

    def _corpo(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # --------------------------------------------------------------- rotas
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Atlas-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        rota = urlparse(self.path).path
        if rota.startswith("/api/") and not self._autorizado():
            return self._json({"erro": "token invalido"}, 401)

        if rota == "/api/estado":
            return self._json(self._estado())
        if rota == "/api/memoria":
            q = parse_qs(urlparse(self.path).query)
            busca = q.get("q", [""])[0]
            achados = (lembrancas.buscar(busca, limite=30) if busca
                       else lembrancas.listar(limite=50))
            return self._json({"fatos": achados, "total": lembrancas.total()})
        if rota == "/api/historico":
            con = conectar()
            linhas = con.execute(
                "SELECT quando, papel, conteudo FROM falas ORDER BY id DESC LIMIT 60"
            ).fetchall()
            return self._json({"falas": [dict(l) for l in reversed(linhas)]})
        if rota == "/api/lembretes":
            return self._json({"texto": ferramentas.listar_lembretes()})
        return self._arquivo(rota)

    def do_POST(self):
        rota = urlparse(self.path).path
        if not self._autorizado():
            return self._json({"erro": "token invalido"}, 401)

        if rota == "/api/conversar":
            pergunta = (self._corpo().get("pergunta") or "").strip()
            if not pergunta:
                return self._json({"erro": "pergunta vazia"}, 400)
            with _vez:
                try:
                    resposta = cerebro.responder(pergunta, _mem())
                except cerebro.CerebroIndisponivel as e:
                    return self._json({"erro": str(e)}, 503)
                # O cofre escreve aqui o que so o dono pode ver. Nao passou
                # pelo modelo e nao esta na memoria -- vai direto para a tela.
                reservado = cofre.recolher_para_o_dono()
            return self._json({"resposta": resposta, "reservado": reservado,
                               "quando": datetime.now().isoformat(timespec="seconds")})

        if rota == "/api/esquecer":
            with _vez:
                _mem().esquecer_tudo()
            return self._json({"ok": True})

        if rota == "/api/visao":
            # Ligar ou desligar a visao exige reiniciar o cerebro: o projetor
            # de imagem e carregado na subida do llama-server, e ocupa 918 MB
            # que, ligados, tiram o modelo da VRAM. Por isso e um botao, e
            # nao um parametro de cada pedido.
            ligar = bool(self._corpo().get("ligar"))
            with _vez:
                ok, aviso = _reiniciar_cerebro(com_visao=ligar)
            return self._json({"ok": ok, "visao": ligar and ok, "aviso": aviso})

        if rota == "/api/destrancar":
            senha = self._corpo().get("senha") or ""
            return self._json({"ok": bool(senha) and cofre.destrancar(senha)})

        return self._json({"erro": "rota desconhecida"}, 404)

    # ------------------------------------------------------------ estaticos
    def _arquivo(self, rota: str):
        nome = "index.html" if rota in ("/", "") else rota.lstrip("/")
        caminho = (PASTA_WEB / nome).resolve()
        # Impede sair da pasta web por "../".
        if not str(caminho).startswith(str(PASTA_WEB.resolve())) or not caminho.is_file():
            self.send_error(404)
            return
        dados = caminho.read_bytes()
        tipo = mimetypes.guess_type(str(caminho))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{tipo}; charset=utf-8"
                         if tipo.startswith("text/") or "json" in tipo else tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(dados)

    def _estado(self) -> dict[str, Any]:
        return _estado_atual()


def _visao_ligada() -> bool:
    """Le do log da ultima subida se o projetor de imagem foi carregado."""
    log = config.LOGS / "servidor.log"
    if not log.exists():
        return False
    cabeca = log.read_text(encoding="utf-8", errors="replace")[:400]
    return "visao: sim" in cabeca


def _reiniciar_cerebro(com_visao: bool) -> tuple[bool, str]:
    """Derruba e sobe o llama-server, com ou sem o projetor de imagem."""
    script = config.RAIZ / "scripts" / "servidor.ps1"
    log = config.LOGS / "servidor.log"
    subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                   capture_output=True)
    time.sleep(2)

    args = ["powershell.exe", "-NoProfile", "-File", str(script)]
    if com_visao:
        args.append("-ComVisao")
    with open(log, "w", encoding="utf-8") as f:
        subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    # O modelo leva alguns segundos para carregar; esperamos ele atender.
    for _ in range(90):
        if cerebro.no_ar():
            break
        time.sleep(1)
    else:
        return False, "O cerebro nao voltou. Veja logs/servidor.log"

    # Se nao coube na placa, avisa -- senao a Atlas fica 7x mais lenta em
    # silencio, que e exatamente o modo de falha que mais custou tempo aqui.
    texto = log.read_text(encoding="utf-8", errors="replace")
    if "failed to fit" in texto:
        return True, "Ligou, mas o modelo nao coube na GPU: vai responder bem mais devagar."
    return True, ""


def _estado_atual() -> dict[str, Any]:
    def _desempenho() -> float:
        try:
            with urllib.request.urlopen(
                    f"{config.LLM_URL.replace('/v1','')}/metrics", timeout=3) as r:
                for linha in r.read().decode().splitlines():
                    if linha.startswith("llamacpp:predicted_tokens_seconds"):
                        return round(float(linha.split()[-1]), 1)
        except Exception:
            pass
        return 0.0
    con = conectar()
    falas = con.execute("SELECT COUNT(*) AS n FROM falas").fetchone()["n"]
    pendentes = con.execute(
        "SELECT COUNT(*) AS n FROM lembretes WHERE feito = 0").fetchone()["n"]
    return {
            "cerebro": cerebro.no_ar(),
            "visao": _visao_ligada(),
            "tokens_s": _desempenho(),
            "memoria_semantica": bool(lembrancas.embutir("ping")),
            "cofre_destrancado": cofre.destrancado(),
            "cofre_existe": cofre.existe(),
            "fatos": lembrancas.total(),
            "falas": falas,
            "lembretes": pendentes,
            "ferramentas": len(ferramentas.ESQUEMAS),
            "dono": config.DONO,
        }


def main() -> int:
    global TOKEN
    ap = argparse.ArgumentParser(prog="atlas.web")
    ap.add_argument("--porta", type=int, default=8800)
    ap.add_argument("--rede", action="store_true",
                    help="aceita conexoes de outras maquinas da rede local")
    ap.add_argument("--senha", action="store_true",
                    help="exige um token (impresso na subida)")
    a = ap.parse_args()

    endereco = "0.0.0.0" if a.rede else "127.0.0.1"
    if a.senha:
        TOKEN = secrets.token_urlsafe(12)

    servidor = ThreadingHTTPServer((endereco, a.porta), Atendente)
    print(f"Atlas web em http://127.0.0.1:{a.porta}")
    if a.rede:
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            print(f"  da rede local:  http://{ip}:{a.porta}"
                  + (f"/?t={TOKEN}" if TOKEN else ""))
        except Exception:
            pass
        if not TOKEN:
            print("  AVISO: aberto na rede sem senha. Qualquer um na mesma rede")
            print("         conversa com a Atlas e le sua memoria. Use --senha.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nAtlas web encerrada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
