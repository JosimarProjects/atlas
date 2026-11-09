"""Memoria da Atlas: janela curta literal + resumo de tamanho constante.

A ideia e a do video: a maioria dos assistentes guarda a conversa inteira
palavra por palavra, e o contexto so engorda. Aqui as falas antigas sao
comprimidas num resumo que ocupa sempre mais ou menos o mesmo espaco, entao
o custo de lembrar para de crescer com o tempo.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from . import config

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS falas (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    quando    TEXT NOT NULL,
    papel     TEXT NOT NULL,
    conteudo  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS estado (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lembretes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    criado   TEXT NOT NULL,
    quando   TEXT,
    texto    TEXT NOT NULL,
    feito    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notas (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    criado  TEXT NOT NULL,
    titulo  TEXT NOT NULL,
    texto   TEXT NOT NULL
);
"""


def conectar() -> sqlite3.Connection:
    # check_same_thread=False porque o servidor web atende cada pedido numa
    # thread diferente, e o objeto Memoria vive entre pedidos. E seguro aqui:
    # a Atlas e de uma pessoa so, e web.py serializa a conversa inteira num
    # lock -- nunca ha duas escritas ao mesmo tempo.
    con = sqlite3.connect(config.BANCO, check_same_thread=False, timeout=15)
    con.row_factory = sqlite3.Row
    con.executescript(_ESQUEMA)
    return con


class Memoria:
    """Janela curta de falas + resumo acumulado, ambos persistidos."""

    def __init__(self) -> None:
        self.con = conectar()

    # ------------------------------------------------------------- estado
    def _ler(self, chave: str, padrao: str = "") -> str:
        linha = self.con.execute(
            "SELECT valor FROM estado WHERE chave = ?", (chave,)
        ).fetchone()
        return linha["valor"] if linha else padrao

    def _gravar(self, chave: str, valor: str) -> None:
        self.con.execute(
            "INSERT INTO estado (chave, valor) VALUES (?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (chave, valor),
        )
        self.con.commit()

    @property
    def resumo(self) -> str:
        return self._ler("resumo")

    @resumo.setter
    def resumo(self, texto: str) -> None:
        self._gravar("resumo", texto[: config.MEMORIA_RESUMO_MAX])

    # -------------------------------------------------------------- falas
    def registrar(self, papel: str, conteudo: str) -> None:
        self.con.execute(
            "INSERT INTO falas (quando, papel, conteudo) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), papel, conteudo),
        )
        self.con.commit()

    def janela(self) -> list[dict[str, str]]:
        """As ultimas falas, em ordem cronologica, prontas para o chat."""
        linhas = self.con.execute(
            "SELECT papel, conteudo FROM falas ORDER BY id DESC LIMIT ?",
            (config.MEMORIA_JANELA,),
        ).fetchall()
        return [{"role": l["papel"], "content": l["conteudo"]} for l in reversed(linhas)]

    def precisa_resumir(self) -> bool:
        total = self.con.execute("SELECT COUNT(*) AS n FROM falas").fetchone()["n"]
        # Resume quando a conversa passa do dobro da janela, e so de vez em
        # quando, para nao gastar o modelo a cada turno.
        return total > config.MEMORIA_JANELA * 2 and total % config.MEMORIA_JANELA == 0

    def falas_para_resumir(self) -> list[dict[str, str]]:
        """Falas que ja sairam da janela literal e ainda cabem num resumo."""
        linhas = self.con.execute(
            "SELECT papel, conteudo FROM falas "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (config.MEMORIA_JANELA, config.MEMORIA_JANELA),
        ).fetchall()
        return [{"role": l["papel"], "content": l["conteudo"]} for l in reversed(linhas)]

    # ----------------------------------------------------------- contexto
    def contexto(self, gancho: str = "") -> list[dict[str, Any]]:
        """Mensagens prontas para mandar ao modelo: sistema + resumo + janela.

        O template do Qwen3.5 aceita UMA unica mensagem de sistema e rejeita o
        resto com "System message must be at the beginning" -- por isso prompt,
        data e resumo vao concatenados num bloco so.
        """
        agora = datetime.now()
        partes = [
            config.PROMPT_SISTEMA,
            f"Agora sao {agora:%H:%M} de {agora:%d/%m/%Y} ({_dia_semana(agora)}).",
        ]
        if self.resumo:
            partes.append(f"O que ja foi conversado antes:\n{self.resumo}")

        # Memoria de longo prazo: o que foi guardado em QUALQUER conversa e
        # tem a ver com o que esta sendo dito agora. E o que faz a Atlas
        # lembrar, semanas depois, do filme que voce mandou anotar.
        if gancho:
            try:
                from . import lembrancas
                achados = lembrancas.buscar(
                    gancho, limite=config.MEMORIA_FATOS_NO_CONTEXTO)
                if achados:
                    partes.append("Da sua memoria de longo prazo:" + chr(10)
                                  + lembrancas.formatar(achados))
            except Exception:
                pass   # memoria e enriquecimento; nunca derruba a conversa

        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(partes)}
        ]
        msgs.extend(self.janela())
        return msgs

    def esquecer_tudo(self) -> None:
        self.con.executescript("DELETE FROM falas; DELETE FROM estado;")
        self.con.commit()


_DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


def _dia_semana(d: datetime) -> str:
    return _DIAS[d.weekday()]


def exportar(caminho) -> int:
    """Despeja a conversa inteira em JSON. Util para depurar a memoria."""
    con = conectar()
    linhas = [dict(l) for l in con.execute("SELECT * FROM falas ORDER BY id")]
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(linhas, f, ensure_ascii=False, indent=2)
    return len(linhas)
