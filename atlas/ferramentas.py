"""Ferramentas de verdade da Atlas.

Cada funcao aqui e exposta ao modelo como tool call. O ponto e o do video:
quando ela diz que criou um lembrete, um lembrete foi criado mesmo -- nao e o
modelo fingindo que fez.

O clima usa o Open-Meteo, que nao exige chave de API. E a unica ferramenta
que toca a internet; sem rede ela falha de forma explicita em vez de inventar.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Callable

from . import config
from .memoria import conectar

# Coordenadas padrao: Sao Paulo. Trocar se a Atlas mudar de cidade.
LAT, LON = -23.5505, -46.6333
CIDADE = "Sao Paulo"


# ---------------------------------------------------------------- lembretes
def criar_lembrete(texto: str, quando: str = "") -> str:
    """Grava um lembrete. `quando` e texto livre ("amanha as 18h")."""
    con = conectar()
    con.execute(
        "INSERT INTO lembretes (criado, quando, texto) VALUES (?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), quando, texto),
    )
    con.commit()
    return f"Lembrete criado: {texto}" + (f" ({quando})" if quando else "")


def listar_lembretes() -> str:
    con = conectar()
    linhas = con.execute(
        "SELECT texto, quando FROM lembretes WHERE feito = 0 ORDER BY id"
    ).fetchall()
    if not linhas:
        return "Nenhum lembrete pendente."
    itens = [f"{l['texto']}" + (f" ({l['quando']})" if l["quando"] else "") for l in linhas]
    return "Lembretes pendentes: " + "; ".join(itens)


def concluir_lembrete(texto: str) -> str:
    con = conectar()
    cur = con.execute(
        "UPDATE lembretes SET feito = 1 "
        "WHERE feito = 0 AND texto LIKE ? ", (f"%{texto}%",)
    )
    con.commit()
    if cur.rowcount == 0:
        return f"Nao achei lembrete pendente parecido com '{texto}'."
    return f"Marquei como feito: {texto}"


# -------------------------------------------------------------------- notas
def anotar(titulo: str, texto: str) -> str:
    con = conectar()
    con.execute(
        "INSERT INTO notas (criado, titulo, texto) VALUES (?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), titulo, texto),
    )
    con.commit()
    return f"Anotado em '{titulo}'."


def ler_notas(titulo: str = "") -> str:
    con = conectar()
    if titulo:
        linhas = con.execute(
            "SELECT titulo, texto FROM notas WHERE titulo LIKE ? ORDER BY id DESC LIMIT 5",
            (f"%{titulo}%",),
        ).fetchall()
    else:
        linhas = con.execute(
            "SELECT titulo, texto FROM notas ORDER BY id DESC LIMIT 5"
        ).fetchall()
    if not linhas:
        return "Nenhuma nota encontrada."
    return " | ".join(f"{l['titulo']}: {l['texto']}" for l in linhas)


# --------------------------------------------------------------------- hora
def que_horas() -> str:
    agora = datetime.now()
    return f"Sao {agora:%H:%M} de {agora:%d/%m/%Y}."


# --------------------------------------------------------------------- clima
def clima(dia: str = "hoje") -> str:
    """Previsao via Open-Meteo. Unica ferramenta que depende de internet."""
    import urllib.request

    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&current=temperature_2m&timezone=America%2FSao_Paulo&forecast_days=3"
    )
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            d = json.load(r)
    except Exception as e:  # sem rede: falhar explicito, nunca inventar
        return f"Nao consegui consultar o clima agora ({type(e).__name__})."

    idx = {"hoje": 0, "amanha": 1, "depois": 2}.get(dia.lower().strip(), 0)
    diario = d["daily"]
    tmax = diario["temperature_2m_max"][idx]
    tmin = diario["temperature_2m_min"][idx]
    chuva = diario["precipitation_probability_max"][idx]
    agora = d.get("current", {}).get("temperature_2m")

    partes = [f"Em {CIDADE}, {dia}: maxima de {tmax:.0f} graus, minima de {tmin:.0f}"]
    if chuva is not None:
        partes.append(f"chance de chuva de {chuva:.0f} por cento")
    if idx == 0 and agora is not None:
        partes.append(f"agora esta {agora:.0f} graus")
    return ", ".join(partes) + "."


# ------------------------------------------------------------------ relatorio
def relatorio_do_dia() -> str:
    """Resume o dia: o que foi conversado, lembretes abertos, notas novas."""
    con = conectar()
    hoje = datetime.now().strftime("%Y-%m-%d")
    n_falas = con.execute(
        "SELECT COUNT(*) AS n FROM falas WHERE quando LIKE ?", (f"{hoje}%",)
    ).fetchone()["n"]
    pendentes = con.execute(
        "SELECT COUNT(*) AS n FROM lembretes WHERE feito = 0"
    ).fetchone()["n"]
    notas = con.execute(
        "SELECT COUNT(*) AS n FROM notas WHERE criado LIKE ?", (f"{hoje}%",)
    ).fetchone()["n"]
    return (
        f"Hoje trocamos {n_falas} falas, voce tem {pendentes} lembretes pendentes "
        f"e criou {notas} notas."
    )


# ------------------------------------------------------- registro p/ o modelo
REGISTRO: dict[str, Callable[..., str]] = {
    "criar_lembrete": criar_lembrete,
    "listar_lembretes": listar_lembretes,
    "concluir_lembrete": concluir_lembrete,
    "anotar": anotar,
    "ler_notas": ler_notas,
    "que_horas": que_horas,
    "clima": clima,
    "relatorio_do_dia": relatorio_do_dia,
}


def _f(nome: str, descricao: str, props: dict, obrig: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": nome,
            "description": descricao,
            "parameters": {"type": "object", "properties": props, "required": obrig},
        },
    }


_S = {"type": "string"}

ESQUEMAS: list[dict[str, Any]] = [
    _f("criar_lembrete", "Cria um lembrete para o dono.",
       {"texto": {**_S, "description": "O que lembrar."},
        "quando": {**_S, "description": "Quando, em texto livre. Opcional."}},
       ["texto"]),
    _f("listar_lembretes", "Lista os lembretes ainda pendentes.", {}, []),
    _f("concluir_lembrete", "Marca um lembrete pendente como feito.",
       {"texto": {**_S, "description": "Trecho do lembrete a concluir."}}, ["texto"]),
    _f("anotar", "Salva uma anotacao com titulo.",
       {"titulo": _S, "texto": _S}, ["titulo", "texto"]),
    _f("ler_notas", "Le as notas salvas, opcionalmente filtrando por titulo.",
       {"titulo": {**_S, "description": "Filtro por titulo. Opcional."}}, []),
    _f("que_horas", "Diz a data e a hora atual.", {}, []),
    _f("clima", "Previsao do tempo.",
       {"dia": {**_S, "description": "hoje, amanha ou depois."}}, []),
    _f("relatorio_do_dia", "Resumo do dia: falas, lembretes e notas.", {}, []),
]


def executar(nome: str, argumentos: dict[str, Any]) -> str:
    """Roda uma ferramenta pedida pelo modelo, sem deixar excecao vazar."""
    fn = REGISTRO.get(nome)
    if fn is None:
        return f"Ferramenta desconhecida: {nome}"
    try:
        return fn(**argumentos)
    except TypeError as e:
        return f"Argumentos invalidos para {nome}: {e}"
    except Exception as e:
        return f"Erro em {nome}: {type(e).__name__}: {e}"
