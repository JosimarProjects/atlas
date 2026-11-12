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

from . import (agenda_propria, audio, cerebro, cofre, config,
               confirmacao, espacos, ferramentas, lembrancas)
from .memoria import Memoria, conectar

PASTA_WEB = config.RAIZ / "web"

# Uma conversa por vez: a Atlas e pessoal, nao multiusuario. O lock evita
# que duas abas abertas embaralhem a memoria uma da outra.
# Uma trava POR CONVERSA, nao uma global. A global serializava tudo: uma
# chamada lenta ao Claude segurava as outras conversas atras dela, e uma fila
# de tres pedidos lentos fazia o quarto esperar minutos sem motivo. Dentro de
# uma mesma conversa a ordem importa -- entre conversas diferentes, nao.
_travas: dict[int, threading.Lock] = {}
_trava_das_travas = threading.Lock()


def _vez_da(conversa_id: int) -> threading.Lock:
    with _trava_das_travas:
        if conversa_id not in _travas:
            _travas[conversa_id] = threading.Lock()
        return _travas[conversa_id]

# Conversas em andamento: numero -> {pronto, resposta, erro, ...}. Ficam em
# memoria porque so interessam ate o navegador buscar o resultado.
_trabalhos: dict[int, dict[str, Any]] = {}
_proximo = [0]
_VIDA_TRABALHO_S = 1800   # meia hora depois de lido, some da memoria


def _marcar_orfas() -> None:
    """Pergunta registrada sem resposta vira um aviso visivel na conversa.

    Reiniciar o servidor mata as threads em andamento. A pergunta ja estava
    gravada, a resposta nunca chegou, e o historico ficava com um balao do
    dono seguido de nada -- sem nenhum sinal de que algo se perdeu. Agora
    fica dito.
    """
    from .memoria import conectar as _con
    con = _con()
    try:
        orfas = con.execute("""
            SELECT f.id, f.conversa_id FROM falas f
            WHERE f.papel = 'user'
              AND NOT EXISTS (
                SELECT 1 FROM falas g
                WHERE g.conversa_id IS f.conversa_id
                  AND g.id > f.id AND g.papel = 'assistant')
        """).fetchall()
        for o in orfas:
            con.execute(
                "INSERT INTO falas (quando, papel, conteudo, conversa_id) "
                "VALUES (?, 'assistant', ?, ?)",
                (datetime.now().isoformat(timespec="seconds"),
                 "(essa pergunta se perdeu quando a Atlas foi reiniciada — "
                 "pode repetir)", o["conversa_id"]))
        con.commit()
    except Exception:
        pass   # recuperacao e cortesia; nunca impede o servidor de subir


def _limpar_trabalhos() -> None:
    agora = time.time()
    for n in [n for n, t in _trabalhos.items()
              if t.get("lido") and agora - t["lido"] > _VIDA_TRABALHO_S]:
        _trabalhos.pop(n, None)
# Uma Memoria por conversa, reaproveitada entre pedidos: abrir conexao e
# reler o resumo a cada turno seria desperdicio.
_memorias: dict[int, Memoria] = {}

TOKEN: str = ""


def _mem(conversa_id: int) -> Memoria:
    if conversa_id not in _memorias:
        _memorias[conversa_id] = Memoria(conversa_id)
    return _memorias[conversa_id]


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

    def _de_fora(self) -> bool:
        """O pedido chegou pela internet, via tunel?

        O cloudflared roda nesta maquina, entao o IP de origem e sempre
        127.0.0.1 e nao distingue nada. O que distingue sao os cabecalhos
        que a Cloudflare acrescenta a tudo que passa por ela.
        """
        return bool(self.headers.get("CF-Ray")
                    or self.headers.get("CF-Connecting-IP")
                    or self.headers.get("Cf-Warp-Tag-Id"))

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
        if rota == "/api/espacos":
            return self._json({"espacos": espacos.listar_espacos()})
        if rota == "/api/conversas":
            q = parse_qs(urlparse(self.path).query)
            eid = int(q.get("espaco", ["0"])[0] or 0)
            if not eid:
                lista = espacos.listar_espacos()
                eid = lista[0]["id"] if lista else 0
            return self._json({"espaco": eid,
                               "conversas": espacos.listar_conversas(eid)})
        if rota == "/api/sobre-mim":
            # O que a Atlas sabe do dono, do mais acessado para o menos:
            # fato recuperado com frequencia e fato que importa.
            con = conectar()
            linhas = con.execute(
                "SELECT id, criado, categoria, assunto, texto, motivo, acessos "
                "FROM fatos ORDER BY acessos DESC, id DESC LIMIT 200").fetchall()
            return self._json({"fatos": [dict(l) for l in linhas],
                               "total": lembrancas.total()})
        if rota == "/api/avisos":
            # Avisos que venceram e ainda nao apareceram na tela.
            return self._json({"avisos": agenda_propria.recolher_da_tela(),
                               "agendados": [agenda_propria.descrever(a)
                                             for a in agenda_propria.listar()]})
        if rota == "/api/trabalhos":
            # O que esta sendo processado agora nesta conversa. E por aqui
            # que a interface, ao reabrir, descobre que ha resposta a
            # caminho em vez de mostrar a conversa parada.
            q = parse_qs(urlparse(self.path).query)
            cid = int(q.get("conversa", ["0"])[0] or 0)
            emcurso = [{"id": n, "pronto": t["pronto"]}
                       for n, t in _trabalhos.items()
                       if t.get("conversa") == cid]
            return self._json({"trabalhos": emcurso})
        if rota == "/api/resposta":
            # O navegador pergunta aqui se ja ficou pronto. Requisicao curta:
            # nenhuma rede movel nem tunel derruba.
            q = parse_qs(urlparse(self.path).query)
            numero = int(q.get("id", ["0"])[0] or 0)
            t = _trabalhos.get(numero)
            if t is None:
                return self._json({"erro": "trabalho desconhecido"}, 404)
            if not t["pronto"]:
                return self._json({"pronto": False})
            if t.get("reservado") and (self._de_fora() or not t.get("de_dentro")):
                # Veio de fora: o valor nao viaja junto da conversa. A
                # interface pede a senha-mestra e busca em /api/revelar.
                t = {**t, "reservado": [], "pedir_mestra": t.get("servico_pedido", "")}
            # Nao apaga ao ler: se o dono fechou o navegador e voltou, ou
            # abriu noutra aba, o resultado precisa continuar ali. A limpeza
            # e por idade, logo abaixo.
            t["lido"] = time.time()
            return self._json({**t, "pronto": True})
        if rota == "/api/pendentes":
            return self._json({"pedidos": confirmacao.listar()})
        if rota == "/api/audio-pronto":
            return self._json(audio.pronto())
        if rota == "/api/historico":
            con = conectar()
            q = parse_qs(urlparse(self.path).query)
            cid = int(q.get("conversa", ["0"])[0] or 0)
            if cid:
                linhas = con.execute(
                    "SELECT quando, papel, conteudo FROM falas "
                    "WHERE conversa_id = ? ORDER BY id DESC LIMIT 120",
                    (cid,)).fetchall()
            else:
                linhas = con.execute(
                    "SELECT quando, papel, conteudo FROM falas "
                    "ORDER BY id DESC LIMIT 60").fetchall()
            return self._json({"falas": [dict(l) for l in reversed(linhas)]})
        if rota == "/api/lembretes":
            return self._json({"texto": ferramentas.listar_lembretes()})
        return self._arquivo(rota)

    def do_POST(self):
        rota = urlparse(self.path).path
        if not self._autorizado():
            return self._json({"erro": "token invalido"}, 401)

        if rota == "/api/conversar":
            corpo = self._corpo()
            pergunta = (corpo.get("pergunta") or "").strip()
            if not pergunta:
                return self._json({"erro": "pergunta vazia"}, 400)
            cid = int(corpo.get("conversa") or 0)
            if not cid:
                lista = espacos.listar_espacos()
                cid = espacos.conversa_atual(lista[0]["id"])

            _limpar_trabalhos()
            # Fica registrado de onde veio o pedido: o segredo revelado so
            # volta para quem perguntou de dentro de casa.
            de_dentro = not self._de_fora()
            _proximo[0] += 1
            numero = _proximo[0]
            _trabalhos[numero] = {"pronto": False, "conversa": cid}

            def trabalhar():
                try:
                    with _vez_da(cid):
                        resposta = cerebro.responder(pergunta, _mem(cid))
                        # O cofre escreve aqui o que so o dono pode ver. Nao
                        # passou pelo modelo nem esta na memoria.
                        reservado = cofre.recolher_para_o_dono()
                        titulo = espacos.titular_pela_primeira_fala(cid)
                    _trabalhos[numero].update({
                        "pronto": True, "resposta": resposta,
                        "reservado": reservado, "de_dentro": de_dentro,
                        "servico_pedido": cofre.ultimo_servico_pedido(),
                        "motor": cerebro.ULTIMO_MOTOR,
                        "titulo": titulo,
                        "pendentes": confirmacao.listar(),
                        "quando": datetime.now().isoformat(timespec="seconds")})
                except cerebro.CerebroIndisponivel as e:
                    _trabalhos[numero].update({"pronto": True, "erro": str(e)})
                except Exception as e:
                    _trabalhos[numero].update(
                        {"pronto": True, "erro": f"{type(e).__name__}: {e}"})

            threading.Thread(target=trabalhar, daemon=True).start()
            return self._json({"trabalho": numero, "conversa": cid})

        if rota == "/api/esquecer-fato":
            # Caminho deterministico: a interface manda o id e o Python apaga.
            # Nao passa pelo modelo, entao nao ha como "confirmar" sem fazer --
            # que foi exatamente o que aconteceu antes de existir a ferramenta
            # de apagar: ele dizia ter apagado e os fatos continuavam la.
            fid = self._corpo().get("id")
            return self._json({"ok": bool(fid) and lembrancas.esquecer(int(fid))})

        if rota == "/api/esquecer":
            cid = int(self._corpo().get("conversa") or 0)
            if cid:
                with _vez_da(cid):
                    _mem(cid).esquecer_tudo()
                    _memorias.pop(cid, None)
            return self._json({"ok": bool(cid)})

        if rota == "/api/sobre-mim-add":
            c = self._corpo()
            texto = (c.get("texto") or "").strip()
            if not texto:
                return self._json({"erro": "texto vazio"}, 400)
            # O dono escrevendo direto: nao passa pelo modelo, nao pode ser
            # reinterpretado, e fica igual ao que ele digitou.
            fid = lembrancas.guardar(
                (c.get("assunto") or texto[:40]).strip(), texto,
                (c.get("motivo") or "o dono contou").strip(),
                (c.get("categoria") or "pessoa").strip(), "sobre-mim")
            return self._json({"id": fid})

        # ------------------------------------------------ confirmacao
        if rota == "/api/confirmar":
            ok, msg = confirmacao.confirmar(self._corpo().get("id") or "")
            return self._json({"ok": ok, "mensagem": msg})
        if rota == "/api/recusar":
            return self._json({"ok": confirmacao.recusar(
                self._corpo().get("id") or "")})

        # ---------------------------------------------------- espacos
        if rota == "/api/espaco-criar":
            c = self._corpo()
            return self._json({"id": espacos.criar_espaco(
                c.get("nome") or "Novo", c.get("icone") or "📁",
                c.get("instrucao") or "")})
        if rota == "/api/espaco-remover":
            return self._json({"ok": espacos.remover_espaco(
                int(self._corpo().get("id") or 0))})
        if rota == "/api/conversa-criar":
            c = self._corpo()
            return self._json({"id": espacos.criar_conversa(
                int(c.get("espaco") or 0), c.get("titulo") or "Nova conversa")})
        if rota == "/api/conversa-remover":
            cid = int(self._corpo().get("id") or 0)
            _memorias.pop(cid, None)
            return self._json({"ok": espacos.remover_conversa(cid)})
        if rota == "/api/conversa-renomear":
            c = self._corpo()
            return self._json({"ok": espacos.renomear_conversa(
                int(c.get("id") or 0), c.get("titulo") or "")})

        # ------------------------------------------------------ audio
        if rota == "/api/ouvir":
            n = int(self.headers.get("Content-Length", 0))
            if not n:
                return self._json({"erro": "audio vazio"}, 400)
            try:
                texto = audio.transcrever_bytes(self.rfile.read(n))
            except Exception as e:
                return self._json({"erro": f"{type(e).__name__}: {e}"}, 500)
            return self._json({"texto": texto})

        if rota == "/api/falar":
            texto = (self._corpo().get("texto") or "").strip()
            if not texto:
                return self._json({"erro": "texto vazio"}, 400)
            try:
                wav = audio.sintetizar_wav(texto)
            except Exception as e:
                return self._json({"erro": f"{type(e).__name__}: {e}"}, 500)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(wav)
            return None

        if rota == "/api/motor":
            # Troca de cerebro nao reinicia nada: vale da proxima resposta
            # em diante, inclusive no meio de uma conversa.
            qual = (self._corpo().get("motor") or "").strip()
            novo = cerebro.trocar_motor(qual)
            return self._json({"motor": novo,
                               "ok": novo == qual or not qual})

        if rota == "/api/pensamento":
            longo = bool(self._corpo().get("longo"))
            return self._json({"longo": cerebro.trocar_pensamento(longo)})

        if rota == "/api/visao":
            # Ligar ou desligar a visao exige reiniciar o cerebro: o projetor
            # de imagem e carregado na subida do llama-server, e ocupa 918 MB
            # que, ligados, tiram o modelo da VRAM. Por isso e um botao, e
            # nao um parametro de cada pedido.
            ligar = bool(self._corpo().get("ligar"))
            ok, aviso = _reiniciar_cerebro(com_visao=ligar)
            return self._json({"ok": ok, "visao": ligar and ok, "aviso": aviso})

        if rota == "/api/revelar":
            # Caminho de fora: a senha-mestra vale para ESTE segredo e nao
            # deixa o cofre aberto depois. O valor nao passa pelo modelo nem
            # entra na memoria -- sai daqui direto para a tela de quem pediu.
            c = self._corpo()
            servico = (c.get("servico") or "").strip()
            senha = c.get("senha") or ""
            if not servico or not senha:
                return self._json({"erro": "informe servico e senha-mestra"}, 400)
            valor = cofre.revelar_com_senha(servico, senha)
            if valor is None:
                return self._json({"ok": False,
                                   "erro": "Senha-mestra incorreta, ou servico "
                                           "nao encontrado."})
            return self._json({"ok": True, "servico": servico, "valor": valor})

        if rota == "/api/destrancar":
            # Nem com a senha certa: o cofre nao abre para quem chegou de
            # fora. A senha-mestra nao deve sequer trafegar pelo tunel.
            if self._de_fora():
                return self._json({
                    "ok": False,
                    "erro": "O cofre so abre do proprio computador ou da rede "
                            "local. Pelo link publico, nao."}, 403)
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


def _tem_claude() -> bool:
    try:
        from . import claude_cli
        return claude_cli.disponivel()
    except Exception:
        return False


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
            "motor": cerebro.motor_atual(),
            "pensamento_longo": cerebro.pensamento_longo(),
            "claude_disponivel": _tem_claude(),
            "avisos": len(agenda_propria.listar()),
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
    ap.add_argument("--novo-token", action="store_true",
                    help="sorteia um token novo, invalidando os links antigos")
    a = ap.parse_args()

    endereco = "0.0.0.0" if a.rede else "127.0.0.1"
    if a.senha:
        # O token fica em disco e sobrevive ao reinicio. Antes era sorteado
        # a cada subida: o link que voce tinha no celular parava de valer, e
        # a interface mostrava "undefined" em todo canto em vez de dizer que
        # o token expirou. Use --novo-token para trocar de proposito.
        guardado = config.DADOS / "token.txt"
        if a.novo_token or not guardado.exists():
            TOKEN = secrets.token_urlsafe(12)
            guardado.write_text(TOKEN, encoding="utf-8")
        else:
            TOKEN = guardado.read_text(encoding="utf-8").strip()

    # O relogio dos agendamentos sobe junto do servidor: e ele que faz
    # o lembrete das 14h chegar sem ninguem pedir.
    agenda_propria.ligar_relogio()
    _marcar_orfas()

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
