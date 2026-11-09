"""Consolidacao: a memoria e revisada, nao so acumulada.

Extrair sem revisar produz um monte, nao uma memoria. Com o tempo aparecem
tres males:

  fragmentos   "trabalha na Flipnet" + "esta na Flipnet ha 3 anos"
  contradicoes "mora em Santo Andre" e depois "mora em Ribeirao Pires"
  poeira       fatos gravados uma vez e nunca mais recuperados

A consolidacao passa sobre grupos de fatos parecidos e devolve uma versao
unica, mais precisa. E o que faz a memoria ficar AFIADA com o tempo, em vez
de so maior.

PRINCIPIO: NA DUVIDA, NAO MEXE
------------------------------
Reescrever memoria e arriscado -- um resumo errado apaga o fato certo. Entao
a passagem e conservadora: so funde o que esta claramente duplicado, so
descarta o que o proprio modelo marca como superado, e qualquer falha deixa
tudo como estava. Nada e apagado sem que a versao nova exista.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from . import lembrancas

# Dois fatos com cosseno acima disto sao do mesmo assunto. Mais baixo que o
# limiar da deduplicacao (0.86), porque aqui queremos juntar aparentados,
# nao so identicos.
MESMO_ASSUNTO = 0.74
MIN_GRUPO = 2
MAX_GRUPOS_POR_VEZ = 6

_INSTRUCAO = """Voce recebe fatos guardados sobre uma pessoa, todos do mesmo
assunto, com a data de cada um. Devolva UMA versao consolidada.

Regras:
- Junte o que for complementar numa frase so.
- Se houver contradicao, fique com o MAIS RECENTE e descarte o antigo.
- Terceira pessoa, como ficha: "mora em Ribeirao Pires".
- Nao invente nada que nao esteja nos fatos.
- Se os fatos NAO forem do mesmo assunto, devolva manter=true e nao mexa.

Responda apenas o JSON."""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "manter": {"type": "boolean"},
        "assunto": {"type": "string"},
        "texto": {"type": "string"},
        "motivo": {"type": "string"},
        "categoria": {"type": "string"},
    },
    "required": ["manter"],
}


def _grupos() -> list[list[dict[str, Any]]]:
    """Agrupa fatos por parecenca de vetor."""
    con = lembrancas.abrir()
    linhas = con.execute(
        "SELECT id, criado, categoria, assunto, texto, motivo, acessos, vetor "
        "FROM fatos WHERE vetor IS NOT NULL ORDER BY id").fetchall()
    fatos = [dict(l) for l in linhas]
    for f in fatos:
        f["v"] = lembrancas._desempacotar(f.pop("vetor"))

    usados: set[int] = set()
    grupos = []
    for i, a in enumerate(fatos):
        if a["id"] in usados:
            continue
        grupo = [a]
        for b in fatos[i + 1:]:
            if b["id"] in usados:
                continue
            if lembrancas._cosseno(a["v"], b["v"]) >= MESMO_ASSUNTO:
                grupo.append(b)
        if len(grupo) >= MIN_GRUPO:
            for g in grupo:
                usados.add(g["id"])
            grupos.append(grupo)
    return grupos[:MAX_GRUPOS_POR_VEZ]


def _consolidar_grupo(grupo: list[dict[str, Any]]) -> str | None:
    from . import cerebro

    listados = "\n".join(
        f"- ({f['criado'][:10]}) {f['assunto']}: {f['texto']}"
        + (f" [porque: {f['motivo']}]" if f["motivo"] else "")
        for f in grupo)
    try:
        bruto = cerebro._chamar(
            [{"role": "system", "content": _INSTRUCAO},
             {"role": "user", "content": listados}],
            max_tokens=500, pensar=False, schema=_SCHEMA, temperatura=0.1)
        d = json.loads(bruto) or {}
    except Exception:
        return None     # falhou: o grupo fica como estava

    if d.get("manter") or not (d.get("texto") or "").strip():
        return None

    assunto = (d.get("assunto") or grupo[0]["assunto"]).strip()
    texto = d["texto"].strip()
    # Grava o novo ANTES de apagar os antigos: se algo falhar no meio, a
    # perda e uma duplicata, nunca o fato.
    lembrancas.guardar(
        assunto, texto, (d.get("motivo") or "").strip(),
        (d.get("categoria") or grupo[0]["categoria"]).strip(), "consolidacao")
    for f in grupo:
        lembrancas.esquecer(f["id"])
    return f"{len(grupo)} -> {assunto}: {texto[:60]}"


def rodar() -> dict[str, Any]:
    """Uma passagem de consolidacao. Devolve o que mudou."""
    grupos = _grupos()
    antes = lembrancas.total()
    mudancas = []
    for g in grupos:
        r = _consolidar_grupo(g)
        if r:
            mudancas.append(r)
    return {
        "grupos_vistos": len(grupos),
        "consolidados": len(mudancas),
        "antes": antes,
        "depois": lembrancas.total(),
        "mudancas": mudancas,
        "quando": datetime.now().isoformat(timespec="seconds"),
    }


def precisa() -> bool:
    """Vale a pena rodar? So se houver grupo parecido o bastante."""
    return lembrancas.total() >= 6 and bool(_grupos())
