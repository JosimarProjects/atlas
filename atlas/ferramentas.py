"""Ferramentas de verdade da Atlas.

Cada funcao aqui e exposta ao modelo como tool call. O ponto e o do video:
quando ela diz que criou um lembrete, um lembrete foi criado mesmo -- nao e o
modelo fingindo que fez.

O clima usa o Open-Meteo, que nao exige chave de API. E a unica ferramenta
que toca a internet; sem rede ela falha de forma explicita em vez de inventar.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime
from typing import Any, Callable

from . import config
from .memoria import conectar

CIDADE_PADRAO = os.environ.get("ATLAS_CIDADE", "Ribeirao Pires")


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
def _localizar(cidade: str) -> tuple[float, float, str] | None:
    """Nome de cidade -> (lat, lon, nome oficial) pelo geocoder do Open-Meteo."""
    import urllib.request

    q = urllib.parse.quote(cidade)
    url = (f"https://geocoding-api.open-meteo.com/v1/search?name={q}"
           "&count=1&language=pt&format=json")
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            d = json.load(r)
    except Exception:
        return None
    res = (d.get("results") or [None])[0]
    if not res:
        return None
    nome = res["name"]
    if res.get("admin1"):
        nome += f", {res['admin1']}"
    return res["latitude"], res["longitude"], nome


def clima(dia: str = "hoje", cidade: str = "") -> str:
    """Previsao via Open-Meteo. Unica ferramenta que depende de internet.

    A cidade e geocodificada de verdade. Antes as coordenadas de Sao Paulo
    eram fixas no codigo, entao perguntar por qualquer outra cidade devolvia
    o tempo de Sao Paulo com o nome errado colado em cima.
    """
    import urllib.request

    alvo = (cidade or CIDADE_PADRAO).strip()
    local = _localizar(alvo)
    if local is None:
        return f"Nao encontrei a cidade '{alvo}' nem consegui consultar o clima."
    lat, lon, nome = local

    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
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

    partes = [f"Em {nome}, {dia}: maxima de {tmax:.0f} graus, minima de {tmin:.0f}"]
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
    _f("clima", "Previsao do tempo de uma cidade.",
       {"dia": {**_S, "description": "hoje, amanha ou depois."},
        "cidade": {**_S, "description": "Nome da cidade. Vazio = cidade do dono."}}, []),
    _f("relatorio_do_dia", "Resumo do dia: falas, lembretes e notas.", {}, []),
]


def executar(nome: str, argumentos: dict[str, Any]) -> str:
    """Roda uma ferramenta pedida pelo modelo, sem deixar excecao vazar."""
    fn = REGISTRO.get(nome)
    if fn is None:
        return f"Ferramenta desconhecida: {nome}"
    # O modelo manda null nos campos opcionais ("dia": null) em vez de omitir,
    # e isso chegava como None onde o codigo espera str. Descartar deixa o
    # valor padrao da funcao valer, que e o que o modelo queria dizer.
    argumentos = {k: v for k, v in argumentos.items() if v is not None}
    try:
        return fn(**argumentos)
    except TypeError as e:
        return f"Argumentos invalidos para {nome}: {e}"
    except Exception as e:
        return f"Erro em {nome}: {type(e).__name__}: {e}"
