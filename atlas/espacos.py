"""Espacos de trabalho e conversas.

Ate aqui a Atlas tinha UMA conversa: uma tabela de falas e um resumo so. Isso
cai por terra assim que voce usa ela para coisas diferentes -- o resumo do
trabalho contamina a conversa pessoal, e procurar o que foi dito sobre um
assunto significa rolar tudo.

Entao:

    espaco (Trabalho, Pessoal, Flipnet...)
      └── conversa (uma thread, com titulo e resumo proprios)
            └── falas

O que NAO se divide e a memoria de longo prazo. Fato e fato: se voce mandou
guardar o nome de um remedio no espaco Pessoal, a Atlas tem de lembrar dele
no Trabalho tambem. Dividir memoria por espaco criaria amnesias arbitrarias.
O espaco de origem fica registrado, para dar contexto -- nao para filtrar.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .memoria import conectar

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS espacos (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    nome    TEXT NOT NULL UNIQUE,
    icone   TEXT NOT NULL DEFAULT '',
    criado  TEXT NOT NULL,
    ordem   INTEGER NOT NULL DEFAULT 0,
    -- Instrucao extra do espaco, somada ao prompt base. E o que faz a Atlas
    -- falar diferente no Trabalho e em casa.
    instrucao TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS conversas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    espaco_id  INTEGER NOT NULL REFERENCES espacos(id) ON DELETE CASCADE,
    titulo     TEXT NOT NULL DEFAULT 'Nova conversa',
    criado     TEXT NOT NULL,
    atualizado TEXT NOT NULL,
    resumo     TEXT NOT NULL DEFAULT '',
    arquivada  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS i_conversas_espaco ON conversas(espaco_id, atualizado DESC);
CREATE INDEX IF NOT EXISTS i_falas_conversa ON falas(conversa_id, id);
"""


def preparar():
    """Cria o esquema e migra o banco antigo, que nao tinha espacos."""
    con = conectar()
    # A coluna conversa_id nasceu depois das falas; ALTER TABLE e a unica
    # forma de acrescenta-la sem perder o historico que ja existe.
    colunas = {l["name"] for l in con.execute("PRAGMA table_info(falas)")}
    if "conversa_id" not in colunas:
        con.execute("ALTER TABLE falas ADD COLUMN conversa_id INTEGER")
    con.executescript(_ESQUEMA)

    if not con.execute("SELECT 1 FROM espacos LIMIT 1").fetchone():
        agora = datetime.now().isoformat(timespec="seconds")
        con.executemany(
            "INSERT INTO espacos (nome, icone, criado, ordem) VALUES (?, ?, ?, ?)",
            [("Pessoal", "🏠", agora, 0), ("Trabalho", "💼", agora, 1)])
        con.commit()

    # As falas que existiam antes dos espacos vao para uma conversa propria,
    # em vez de sumirem ou se misturarem com a conversa nova.
    orfas = con.execute(
        "SELECT COUNT(*) AS n FROM falas WHERE conversa_id IS NULL").fetchone()["n"]
    if orfas:
        espaco = con.execute("SELECT id FROM espacos ORDER BY ordem LIMIT 1").fetchone()
        agora = datetime.now().isoformat(timespec="seconds")
        # INSERT direto, nao criar_conversa(): aquela funcao chama preparar()
        # de volta e a migracao entrava em recursao infinita.
        cur = con.execute(
            "INSERT INTO conversas (espaco_id, titulo, criado, atualizado) "
            "VALUES (?, ?, ?, ?)",
            (espaco["id"], "Conversas anteriores", agora, agora))
        con.execute("UPDATE falas SET conversa_id = ? WHERE conversa_id IS NULL",
                    (cur.lastrowid,))
        con.commit()
    return con


# ------------------------------------------------------------------ espacos
def listar_espacos() -> list[dict[str, Any]]:
    con = preparar()
    linhas = con.execute(
        "SELECT e.*, "
        "  (SELECT COUNT(*) FROM conversas c "
        "   WHERE c.espaco_id = e.id AND c.arquivada = 0) AS conversas "
        "FROM espacos e ORDER BY e.ordem, e.id").fetchall()
    return [dict(l) for l in linhas]


def criar_espaco(nome: str, icone: str = "📁", instrucao: str = "") -> int:
    con = preparar()
    ordem = con.execute("SELECT COALESCE(MAX(ordem), 0) + 1 AS o FROM espacos"
                        ).fetchone()["o"]
    cur = con.execute(
        "INSERT INTO espacos (nome, icone, criado, ordem, instrucao) "
        "VALUES (?, ?, ?, ?, ?)",
        (nome.strip(), icone.strip() or "📁",
         datetime.now().isoformat(timespec="seconds"), ordem, instrucao.strip()))
    con.commit()
    return cur.lastrowid


def remover_espaco(espaco_id: int) -> bool:
    con = preparar()
    # O ultimo espaco nao pode sumir: sem espaco nao ha onde conversar.
    if con.execute("SELECT COUNT(*) AS n FROM espacos").fetchone()["n"] <= 1:
        return False
    con.execute("DELETE FROM falas WHERE conversa_id IN "
                "(SELECT id FROM conversas WHERE espaco_id = ?)", (espaco_id,))
    con.execute("DELETE FROM conversas WHERE espaco_id = ?", (espaco_id,))
    cur = con.execute("DELETE FROM espacos WHERE id = ?", (espaco_id,))
    con.commit()
    return cur.rowcount > 0


def instrucao_do_espaco(espaco_id: int) -> str:
    con = preparar()
    l = con.execute("SELECT instrucao FROM espacos WHERE id = ?",
                    (espaco_id,)).fetchone()
    return (l["instrucao"] if l else "") or ""


# ---------------------------------------------------------------- conversas
def listar_conversas(espaco_id: int, limite: int = 50) -> list[dict[str, Any]]:
    con = preparar()
    linhas = con.execute(
        "SELECT c.id, c.titulo, c.criado, c.atualizado, "
        "  (SELECT COUNT(*) FROM falas f WHERE f.conversa_id = c.id) AS falas "
        "FROM conversas c WHERE c.espaco_id = ? AND c.arquivada = 0 "
        "ORDER BY c.atualizado DESC LIMIT ?", (espaco_id, limite)).fetchall()
    return [dict(l) for l in linhas]


def criar_conversa(espaco_id: int, titulo: str = "Nova conversa") -> int:
    con = preparar()
    agora = datetime.now().isoformat(timespec="seconds")
    cur = con.execute(
        "INSERT INTO conversas (espaco_id, titulo, criado, atualizado) "
        "VALUES (?, ?, ?, ?)", (espaco_id, titulo.strip() or "Nova conversa",
                                agora, agora))
    con.commit()
    return cur.lastrowid


def remover_conversa(conversa_id: int) -> bool:
    con = preparar()
    con.execute("DELETE FROM falas WHERE conversa_id = ?", (conversa_id,))
    cur = con.execute("DELETE FROM conversas WHERE id = ?", (conversa_id,))
    con.commit()
    return cur.rowcount > 0


def renomear_conversa(conversa_id: int, titulo: str) -> bool:
    con = preparar()
    cur = con.execute("UPDATE conversas SET titulo = ? WHERE id = ?",
                      (titulo.strip()[:80], conversa_id))
    con.commit()
    return cur.rowcount > 0


def tocar(conversa_id: int) -> None:
    con = preparar()
    con.execute("UPDATE conversas SET atualizado = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), conversa_id))
    con.commit()


def titular_pela_primeira_fala(conversa_id: int) -> str:
    """Da nome a conversa usando o que foi perguntado primeiro.

    Barato e previsivel: pedir um titulo ao modelo custaria uma geracao
    inteira a cada conversa nova, e um 9B inventaria algo generico.
    """
    con = preparar()
    atual = con.execute("SELECT titulo FROM conversas WHERE id = ?",
                        (conversa_id,)).fetchone()
    if not atual or atual["titulo"] != "Nova conversa":
        return atual["titulo"] if atual else ""
    primeira = con.execute(
        "SELECT conteudo FROM falas WHERE conversa_id = ? AND papel = 'user' "
        "ORDER BY id LIMIT 1", (conversa_id,)).fetchone()
    if not primeira:
        return "Nova conversa"
    titulo = " ".join(primeira["conteudo"].split())[:60]
    if len(primeira["conteudo"]) > 60:
        titulo += "…"
    renomear_conversa(conversa_id, titulo)
    return titulo


def conversa_atual(espaco_id: int) -> int:
    """A conversa mais recente do espaco, ou uma nova se nao houver."""
    con = preparar()
    l = con.execute(
        "SELECT id FROM conversas WHERE espaco_id = ? AND arquivada = 0 "
        "ORDER BY atualizado DESC LIMIT 1", (espaco_id,)).fetchone()
    return l["id"] if l else criar_conversa(espaco_id)


def espaco_da_conversa(conversa_id: int) -> int | None:
    con = preparar()
    l = con.execute("SELECT espaco_id FROM conversas WHERE id = ?",
                    (conversa_id,)).fetchone()
    return l["espaco_id"] if l else None
