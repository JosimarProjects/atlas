"""Agendamentos recorrentes: a Atlas avisa sem ser perguntada.

Lembrete so vale se chegar na hora. Ate aqui `lembretes` era uma lista que
alguem precisava pedir para ler -- util para "o que tenho pendente", inutil
para "remedio todo dia as 14h".

Aqui o fluxo e outro: um relogio roda em segundo plano, confere a cada minuto
quem venceu e dispara. O canal pode ser a tela ou o WhatsApp.

CRIAR PEDE CONFIRMACAO, DISPARAR NAO
------------------------------------
Criar um agendamento e criar uma regra permanente: a partir dali a Atlas
manda mensagem sozinha, todo dia, sem ninguem olhando. Isso passa pela
trava -- inclusive porque a ordem pode ter vindo de uma pagina que ela leu.

O disparo, nao. Um lembrete de remedio as 14h que espera um clique nao e
lembrete. Quem autorizou foi o dono, uma vez, quando criou.

E o destino e sempre o numero do PROPRIO dono. Agendamento nao serve para
mandar mensagem a terceiros no automatico -- isso seria uma maquina de spam
disparavel por injecao.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any

from . import config
from .memoria import conectar

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS agendamentos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    criado    TEXT NOT NULL,
    texto     TEXT NOT NULL,
    repete    TEXT NOT NULL DEFAULT 'diario',   -- diario | semanal | uma_vez
    hora      TEXT NOT NULL,                    -- HH:MM
    dia_semana INTEGER,                         -- 0=segunda .. 6=domingo
    data      TEXT,                             -- AAAA-MM-DD, so para uma_vez
    canal     TEXT NOT NULL DEFAULT 'tela',     -- tela | whatsapp
    ativo     INTEGER NOT NULL DEFAULT 1,
    ultimo    TEXT                              -- ultimo disparo
);
"""

_DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]

# Avisos que venceram e esperam a tela do dono.
_na_tela: list[dict[str, Any]] = []
_relogio: threading.Thread | None = None


def abrir():
    con = conectar()
    con.executescript(_ESQUEMA)
    return con


# ------------------------------------------------------------------ criar
def criar(texto: str, hora: str, repete: str = "diario",
          dia_semana: int | None = None, data: str = "",
          canal: str = "tela") -> int:
    con = abrir()
    cur = con.execute(
        "INSERT INTO agendamentos (criado, texto, repete, hora, dia_semana, "
        "data, canal) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), texto.strip(),
         repete, hora, dia_semana, data or None, canal))
    con.commit()
    return cur.lastrowid


def listar(so_ativos: bool = True) -> list[dict[str, Any]]:
    con = abrir()
    sql = "SELECT * FROM agendamentos"
    if so_ativos:
        sql += " WHERE ativo = 1"
    return [dict(l) for l in con.execute(sql + " ORDER BY hora")]


def remover(id_ag: int) -> bool:
    con = abrir()
    cur = con.execute("DELETE FROM agendamentos WHERE id = ?", (id_ag,))
    con.commit()
    return cur.rowcount > 0


def descrever(a: dict[str, Any]) -> str:
    canal = "no WhatsApp" if a["canal"] == "whatsapp" else "na tela"
    if a["repete"] == "diario":
        quando = f"todo dia as {a['hora']}"
    elif a["repete"] == "semanal":
        d = _DIAS[a["dia_semana"]] if a["dia_semana"] is not None else "?"
        quando = f"toda {d} as {a['hora']}"
    else:
        quando = f"em {a['data']} as {a['hora']}"
    return f"{a['texto']} — {quando}, {canal}"


# --------------------------------------------------------------- disparo
def _vencido(a: dict[str, Any], agora: datetime) -> bool:
    try:
        h, m = (int(x) for x in a["hora"].split(":"))
    except ValueError:
        return False
    alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
    if agora < alvo:
        return False
    # Tolerancia de 10 min: se a maquina estava dormindo na hora exata, o
    # aviso ainda sai -- mas nao se ja passou meia hora, para nao assustar.
    if (agora - alvo) > timedelta(minutes=10):
        return False
    if a["repete"] == "semanal" and a["dia_semana"] != agora.weekday():
        return False
    if a["repete"] == "uma_vez" and a["data"] != agora.strftime("%Y-%m-%d"):
        return False
    # Ja disparou hoje?
    if a["ultimo"] and a["ultimo"][:10] == agora.strftime("%Y-%m-%d"):
        return False
    return True


def _disparar(a: dict[str, Any]) -> None:
    from . import whatsapp

    texto = f"Lembrete: {a['texto']}"
    if a["canal"] == "whatsapp" and config.MEU_WHATSAPP:
        try:
            # Disparo direto, sem a trava: quem autorizou foi o dono quando
            # criou a regra, e o destino e o numero dele mesmo.
            whatsapp._pedir(f"/message/sendText/{whatsapp.INSTANCIA}",
                            {"number": whatsapp._normalizar(config.MEU_WHATSAPP),
                             "text": texto}, metodo="POST")
        except Exception:
            _na_tela.append({"texto": texto, "quando": datetime.now().isoformat()})
    else:
        _na_tela.append({"texto": texto, "quando": datetime.now().isoformat()})

    con = abrir()
    con.execute("UPDATE agendamentos SET ultimo = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), a["id"]))
    if a["repete"] == "uma_vez":
        con.execute("UPDATE agendamentos SET ativo = 0 WHERE id = ?", (a["id"],))
    con.commit()


def conferir() -> int:
    agora = datetime.now()
    n = 0
    for a in listar():
        if _vencido(a, agora):
            _disparar(a)
            n += 1
    return n


def recolher_da_tela() -> list[dict[str, Any]]:
    global _na_tela
    fila, _na_tela = _na_tela, []
    return fila


def ligar_relogio() -> None:
    """Sobe o laco que confere os agendamentos a cada minuto."""
    global _relogio
    if _relogio and _relogio.is_alive():
        return

    def laco():
        while True:
            try:
                conferir()
            except Exception:
                pass    # o relogio nunca pode morrer por causa de um erro
            time.sleep(30)

    _relogio = threading.Thread(target=laco, daemon=True)
    _relogio.start()
