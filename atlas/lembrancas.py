"""Memoria de longo prazo da Atlas: fatos que atravessam conversas.

O resumo em `memoria.py` serve a conversa corrente -- ele comprime o que foi
dito e esquece o resto. Isto aqui e outra coisa: o que o dono mandou guardar,
ou o que o sistema julgou digno de guardar, permanece para sempre e e
recuperado quando volta a ser relevante, semanas depois, em outra conversa.

POR QUE BUSCA HIBRIDA
---------------------
Voce salva "Duna 3, ficcao cientifica, indicacao do Joao" e semanas depois
pergunta "quais filmes eu mandei guardar?". Nenhuma palavra bate: nao ha
"filme" no que foi salvo. Busca por palavra-chave (FTS5) falha nisso, mas e
imbativel para nome proprio e numero. Busca por vetor acerta o sentido, mas
erra o termo exato. Usamos as duas e fundimos os resultados.
"""
from __future__ import annotations

import json
import math
import re
import struct
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from .memoria import conectar

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS fatos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    criado    TEXT NOT NULL,
    acessado  TEXT,
    acessos   INTEGER NOT NULL DEFAULT 0,
    categoria TEXT NOT NULL DEFAULT 'geral',
    assunto   TEXT NOT NULL,
    texto     TEXT NOT NULL,
    motivo    TEXT NOT NULL DEFAULT '',
    origem    TEXT NOT NULL DEFAULT '',
    vetor     BLOB
);
CREATE VIRTUAL TABLE IF NOT EXISTS fatos_busca
    USING fts5(assunto, texto, motivo, categoria,
               content='fatos', content_rowid='id', tokenize='unicode61');

CREATE TRIGGER IF NOT EXISTS fatos_ai AFTER INSERT ON fatos BEGIN
    INSERT INTO fatos_busca(rowid, assunto, texto, motivo, categoria)
    VALUES (new.id, new.assunto, new.texto, new.motivo, new.categoria);
END;
CREATE TRIGGER IF NOT EXISTS fatos_ad AFTER DELETE ON fatos BEGIN
    INSERT INTO fatos_busca(fatos_busca, rowid, assunto, texto, motivo, categoria)
    VALUES ('delete', old.id, old.assunto, old.texto, old.motivo, old.categoria);
END;
CREATE TRIGGER IF NOT EXISTS fatos_au AFTER UPDATE ON fatos BEGIN
    INSERT INTO fatos_busca(fatos_busca, rowid, assunto, texto, motivo, categoria)
    VALUES ('delete', old.id, old.assunto, old.texto, old.motivo, old.categoria);
    INSERT INTO fatos_busca(rowid, assunto, texto, motivo, categoria)
    VALUES (new.id, new.assunto, new.texto, new.motivo, new.categoria);
END;
"""


def _preparar(con) -> None:
    con.executescript(_ESQUEMA)
    # `expira` nasceu depois dos fatos; ALTER e a unica forma de acrescenta-la
    # sem perder o que ja esta guardado.
    colunas = {l["name"] for l in con.execute("PRAGMA table_info(fatos)")}
    if "expira" not in colunas:
        con.execute("ALTER TABLE fatos ADD COLUMN expira TEXT")


def abrir():
    con = conectar()
    _preparar(con)
    return con


# ------------------------------------------------------------------ vetores
def _empacotar(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _desempacotar(b: bytes | None) -> list[float]:
    if not b:
        return []
    return list(struct.unpack(f"{len(b) // 4}f", b))


def embutir(texto: str) -> list[float]:
    """Texto -> vetor, pelo servidor de embeddings. [] se ele estiver fora."""
    corpo = {"model": "embed", "input": texto[:2000]}
    req = urllib.request.Request(
        f"{config.EMBED_URL}/embeddings",
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.load(r)
        return d["data"][0]["embedding"]
    except Exception:
        # Sem embeddings a Atlas nao fica cega: a busca por palavra-chave
        # continua funcionando sozinha.
        return []


def _cosseno(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return num / (na * nb) if na and nb else 0.0


# ----------------------------------------------------------------- escrita
def interpretar_prazo(texto: str) -> str:
    """"31/12/2026", "2026-12-31", "30 dias", "2 semanas" -> AAAA-MM-DD.

    Devolve "" quando nao entende -- fato sem prazo e melhor do que fato com
    prazo errado, que sumiria sozinho na hora errada.
    """
    from datetime import timedelta

    t = (texto or "").strip().lower()
    if not t:
        return ""

    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return t
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", t)
    if m:
        d, mes, a = (int(x) for x in m.groups())
        return f"{a:04d}-{mes:02d}-{d:02d}"

    m = re.search(r"(\d+)\s*(dia|semana|mes|mês|ano)", t)
    if m:
        n = int(m.group(1))
        unidade = m.group(2)
        dias = {"dia": 1, "semana": 7, "mes": 30, "mês": 30, "ano": 365}[unidade]
        return (datetime.now() + timedelta(days=n * dias)).strftime("%Y-%m-%d")

    for palavra, dias in (("amanha", 1), ("amanhã", 1), ("semana que vem", 7),
                          ("mes que vem", 30), ("mês que vem", 30)):
        if palavra in t:
            return (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")
    return ""


def guardar(assunto: str, texto: str, motivo: str = "",
            categoria: str = "geral", origem: str = "",
            expira: str = "") -> int:
    """Grava um fato e ja calcula o vetor dele."""
    con = abrir()
    vetor = embutir(f"{assunto}. {texto}. {motivo}")
    cur = con.execute(
        "INSERT INTO fatos (criado, categoria, assunto, texto, motivo, origem, "
        "vetor, expira) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), categoria.strip() or "geral",
         assunto.strip(), texto.strip(), motivo.strip(), origem.strip(),
         _empacotar(vetor) if vetor else None,
         interpretar_prazo(expira) or None),
    )
    con.commit()
    return cur.lastrowid


def limpar_vencidos() -> int:
    """Apaga o que passou do prazo. Chamado pelo relogio, a cada volta."""
    con = abrir()
    hoje = datetime.now().strftime("%Y-%m-%d")
    cur = con.execute("DELETE FROM fatos WHERE expira IS NOT NULL AND expira < ?",
                      (hoje,))
    con.commit()
    return cur.rowcount


def arquivar_tudo(motivo: str = "") -> int:
    """Esvazia a memoria viva, mas guarda uma copia antes.

    Existe porque eu apaguei a memoria do dono tres vezes durante testes,
    com um DELETE direto, e nao havia de onde tirar de volta. Agora nada sai
    sem deixar copia.
    """
    import json as _json
    from . import config

    fatos = listar(limite=10000)
    if not fatos:
        return 0
    pasta = config.DADOS / "memoria-apagada"
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = pasta / f"{carimbo}.jsonl"
    with open(destino, "w", encoding="utf-8") as f:
        if motivo:
            f.write(_json.dumps({"_motivo": motivo}, ensure_ascii=False) + chr(10))
        for x in fatos:
            f.write(_json.dumps(x, ensure_ascii=False) + chr(10))
    con = abrir()
    con.execute("DELETE FROM fatos")
    con.commit()
    return len(fatos)


def restaurar(arquivo: str = "") -> int:
    """Traz de volta a copia mais recente (ou a indicada)."""
    import json as _json
    from . import config

    pasta = config.DADOS / "memoria-apagada"
    if not pasta.exists():
        return 0
    alvo = Path(arquivo) if arquivo else max(
        pasta.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, default=None)
    if not alvo or not alvo.exists():
        return 0
    voltaram = 0
    for linha in alvo.read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        d = _json.loads(linha)
        if "_motivo" in d:
            continue
        guardar(d.get("assunto", ""), d.get("texto", ""), d.get("motivo", ""),
                d.get("categoria", "geral"), d.get("origem", ""),
                d.get("expira") or "")
        voltaram += 1
    return voltaram


def esquecer(id_fato: int) -> bool:
    con = abrir()
    cur = con.execute("DELETE FROM fatos WHERE id = ?", (id_fato,))
    con.commit()
    return cur.rowcount > 0


def revetorizar() -> int:
    """Calcula o vetor dos fatos salvos enquanto o servidor estava fora."""
    con = abrir()
    pendentes = con.execute("SELECT id, assunto, texto, motivo FROM fatos "
                            "WHERE vetor IS NULL").fetchall()
    feitos = 0
    for f in pendentes:
        v = embutir(f"{f['assunto']}. {f['texto']}. {f['motivo']}")
        if v:
            con.execute("UPDATE fatos SET vetor = ? WHERE id = ?",
                        (_empacotar(v), f["id"]))
            feitos += 1
    con.commit()
    return feitos


# ----------------------------------------------------------------- leitura
_FTS_PROIBIDO = re.compile(r'["\':^*()\-]')


def _consulta_fts(texto: str) -> str:
    """Transforma a pergunta em consulta FTS5 segura (OR entre os termos)."""
    limpo = _FTS_PROIBIDO.sub(" ", texto)
    termos = [t for t in limpo.split() if len(t) > 2]
    return " OR ".join(termos[:12])


def buscar(consulta: str, limite: int = 5) -> list[dict[str, Any]]:
    """Busca hibrida: funde palavra-chave (FTS5) e sentido (vetor).

    A fusao e por posicao, nao por nota: as duas buscas devolvem escalas
    incomparaveis (BM25 negativo e cosseno de 0 a 1). Somar 1/(60+posicao)
    de cada lista -- Reciprocal Rank Fusion -- junta as duas sem precisar
    normalizar nada.
    """
    con = abrir()
    pontos: dict[int, float] = {}

    expr = _consulta_fts(consulta)
    if expr:
        try:
            linhas = con.execute(
                "SELECT rowid FROM fatos_busca WHERE fatos_busca MATCH ? "
                "ORDER BY rank LIMIT 20", (expr,)
            ).fetchall()
            for pos, l in enumerate(linhas):
                pontos[l["rowid"]] = pontos.get(l["rowid"], 0) + 1 / (60 + pos)
        except Exception:
            pass   # consulta FTS malformada nunca derruba a recuperacao

    v = embutir(consulta)
    if v:
        todos = con.execute(f"SELECT id, vetor FROM fatos "
                            f"WHERE vetor IS NOT NULL AND {_NAO_VENCIDO}"
                            ).fetchall()
        notas = [(l["id"], _cosseno(v, _desempacotar(l["vetor"]))) for l in todos]
        notas.sort(key=lambda x: x[1], reverse=True)
        for pos, (fid, nota) in enumerate(notas[:20]):
            if nota < 0.30:      # abaixo disso e ruido, nao parecenca
                break
            pontos[fid] = pontos.get(fid, 0) + 1 / (60 + pos)

    if not pontos:
        return []
    melhores = sorted(pontos.items(), key=lambda x: x[1], reverse=True)[:limite]
    ids = [i for i, _ in melhores]
    marcas = ",".join("?" * len(ids))
    linhas = con.execute(
        f"SELECT id, criado, categoria, assunto, texto, motivo, origem, expira "
        f"FROM fatos WHERE id IN ({marcas}) AND {_NAO_VENCIDO}", ids
    ).fetchall()
    por_id = {l["id"]: dict(l) for l in linhas}

    # Marca o acesso: fato recuperado com frequencia e fato que importa.
    con.executemany("UPDATE fatos SET acessos = acessos + 1, acessado = ? "
                    "WHERE id = ?",
                    [(datetime.now().isoformat(timespec="seconds"), i) for i in ids])
    con.commit()
    return [por_id[i] for i in ids if i in por_id]


# Nunca selecionamos `vetor`: e um BLOB de 3 KB que nao serve a ninguem fora
# da busca, e que quebrava a API web ("Object of type bytes is not JSON
# serializable") assim que a interface listava a memoria.
_COLUNAS = ("id, criado, acessado, acessos, categoria, assunto, texto, "
            "motivo, origem, expira")

# Um fato vencido nao pode aparecer so porque a faxina ainda nao rodou.
_NAO_VENCIDO = "(expira IS NULL OR expira >= date('now','localtime'))"


def listar(categoria: str = "", limite: int = 20) -> list[dict[str, Any]]:
    con = abrir()
    if categoria:
        linhas = con.execute(
            f"SELECT {_COLUNAS} FROM fatos WHERE categoria = ? "
            f"AND {_NAO_VENCIDO} ORDER BY id DESC LIMIT ?",
            (categoria, limite)).fetchall()
    else:
        linhas = con.execute(
            f"SELECT {_COLUNAS} FROM fatos WHERE {_NAO_VENCIDO} "
            "ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
    return [dict(l) for l in linhas]


def categorias() -> list[tuple[str, int]]:
    con = abrir()
    return [(l["categoria"], l["n"]) for l in con.execute(
        "SELECT categoria, COUNT(*) AS n FROM fatos GROUP BY categoria "
        "ORDER BY n DESC")]


def total() -> int:
    return abrir().execute("SELECT COUNT(*) AS n FROM fatos").fetchone()["n"]


def formatar(fatos: list[dict[str, Any]]) -> str:
    """Fatos -> texto para injetar no contexto do modelo."""
    if not fatos:
        return ""
    linhas = []
    for f in fatos:
        quando = (f.get("criado") or "")[:10]
        linha = f"- [{f['categoria']}] {f['assunto']}: {f['texto']}"
        if f.get("motivo"):
            linha += f" (porque: {f['motivo']})"
        linha += f" — guardado em {quando}"
        if f.get("expira"):
            linha += f", expira em {f['expira']}"
        linhas.append(linha)
    return "\n".join(linhas)
