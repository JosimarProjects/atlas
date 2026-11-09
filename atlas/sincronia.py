"""Exporta e importa a memoria da Atlas, para ela sobreviver a maquina.

O banco SQLite e a fonte da verdade, mas um .db binario e pessimo no Git:
nao da para ler no GitHub, nao gera diff util e conflita a cada commit. Entao
exportamos para JSONL -- uma linha por registro, legivel, comentavel na
interface do GitHub e com diff limpo quando um fato muda.

O caminho de volta reconstroi o banco a partir do JSONL. Instalar a Atlas num
computador novo e: clonar o repositorio da memoria e importar.

ATENCAO: isto e dado pessoal. O repositorio da memoria tem de ser PRIVADO --
`preparar_repositorio()` recusa criar publico.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from .memoria import conectar

# Tabelas exportadas e a coluna que identifica duplicata na reimportacao.
TABELAS = {
    "falas": ("quando", "papel", "conteudo"),
    "fatos": ("criado", "categoria", "assunto", "texto", "motivo", "origem"),
    "lembretes": ("criado", "quando", "texto", "feito"),
    "notas": ("criado", "titulo", "texto"),
    "estado": ("chave", "valor"),
}


def _pasta(destino: str | Path | None = None) -> Path:
    p = Path(destino) if destino else config.RAIZ / "memoria-sync"
    p.mkdir(parents=True, exist_ok=True)
    return p


def exportar(destino: str | Path | None = None) -> dict[str, int]:
    """Banco -> JSONL. Devolve quantas linhas saíram de cada tabela."""
    from . import lembrancas
    lembrancas.abrir()          # garante que as tabelas de fatos existem
    con = conectar()
    pasta = _pasta(destino)
    contagem: dict[str, int] = {}

    for tabela, colunas in TABELAS.items():
        try:
            linhas = con.execute(
                f"SELECT {', '.join(colunas)} FROM {tabela} ORDER BY rowid"
            ).fetchall()
        except Exception:
            contagem[tabela] = 0
            continue
        arquivo = pasta / f"{tabela}.jsonl"
        with open(arquivo, "w", encoding="utf-8", newline="\n") as f:
            for l in linhas:
                f.write(json.dumps(dict(l), ensure_ascii=False) + "\n")
        contagem[tabela] = len(linhas)

    # Os vetores sao recalculaveis a partir do texto, entao nao vao para o
    # Git: sao 3 KB binarios por fato, ilegiveis e que mudam de modelo para
    # modelo. Na importacao, `lembrancas.revetorizar()` refaz todos.
    (pasta / "LEIAME.md").write_text(_leiame(contagem), encoding="utf-8")
    return contagem


def _leiame(contagem: dict[str, int]) -> str:
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    linhas = [f"| {t} | {n} |" for t, n in contagem.items()]
    return f"""# Memoria da Atlas

Exportado em {agora}.

| tabela | registros |
|---|---|
{chr(10).join(linhas)}

## O que e cada arquivo

- `fatos.jsonl` — memoria de longo prazo: o que foi mandado guardar, com o
  motivo. E o que faz a Atlas lembrar de um filme semanas depois.
- `falas.jsonl` — historico das conversas, com data.
- `lembretes.jsonl`, `notas.jsonl` — o que esta pendente e o que foi anotado.
- `estado.jsonl` — resumo corrente da conversa.

## Restaurar em outro computador

```bash
python -m atlas.sincronia importar
```

Os vetores de busca semantica nao sao versionados (sao binarios e
recalculaveis). A importacao os reconstroi sozinha.
"""


def importar(origem: str | Path | None = None) -> dict[str, int]:
    """JSONL -> banco. Nao duplica o que ja existe."""
    from . import lembrancas
    lembrancas.abrir()
    con = conectar()
    pasta = _pasta(origem)
    contagem: dict[str, int] = {}

    for tabela, colunas in TABELAS.items():
        arquivo = pasta / f"{tabela}.jsonl"
        if not arquivo.exists():
            continue
        novos = 0
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            d = json.loads(linha)
            valores = tuple(d.get(c) for c in colunas)
            if tabela == "estado":
                con.execute(
                    "INSERT INTO estado (chave, valor) VALUES (?, ?) "
                    "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                    valores)
                novos += 1
                continue
            # Evita duplicar ao reimportar o mesmo arquivo duas vezes.
            onde = " AND ".join(f"{c} IS ?" for c in colunas)
            ja = con.execute(
                f"SELECT 1 FROM {tabela} WHERE {onde} LIMIT 1", valores).fetchone()
            if ja:
                continue
            marcas = ", ".join("?" * len(colunas))
            con.execute(
                f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES ({marcas})",
                valores)
            novos += 1
        contagem[tabela] = novos
    con.commit()

    refeitos = lembrancas.revetorizar()
    contagem["vetores_refeitos"] = refeitos
    return contagem


# --------------------------------------------------------------------- git
def _git(pasta: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(pasta), *args],
                          capture_output=True, text=True)


def preparar_repositorio(pasta: Path | None = None) -> str:
    """Inicia o repositorio local da memoria, se ainda nao existir."""
    p = _pasta(pasta)
    if not (p / ".git").exists():
        _git(p, "init", "-q", "-b", "main")
        (p / ".gitignore").write_text("*.db\n*.db-journal\n", encoding="utf-8")
    return str(p)


def publicar(mensagem: str = "", pasta: Path | None = None) -> str:
    """Exporta, commita e empurra. Devolve um resumo do que aconteceu."""
    contagem = exportar(pasta)
    p = _pasta(pasta)
    preparar_repositorio(p)

    _git(p, "add", "-A")
    if not _git(p, "diff", "--cached", "--quiet").returncode:
        return "Nada mudou na memoria desde o ultimo envio."

    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    msg = mensagem or (f"Memoria da Atlas em {agora} — "
                       + ", ".join(f"{t}: {n}" for t, n in contagem.items()))
    _git(p, "commit", "-q", "-m", msg)

    tem_remoto = _git(p, "remote").stdout.strip()
    if not tem_remoto:
        return (f"Commitado localmente em {p}.\n"
                "Falta o remoto. Crie um repositorio PRIVADO e aponte:\n"
                f'  gh repo create atlas-memoria --private --source "{p}" '
                "--remote origin --push")
    r = _git(p, "push", "-q", "origin", "main")
    if r.returncode:
        return f"Commitado, mas o push falhou:\n{r.stderr[-300:]}"
    return f"Memoria publicada: " + ", ".join(f"{t}: {n}" for t, n in contagem.items())


if __name__ == "__main__":
    import sys
    acao = sys.argv[1] if len(sys.argv) > 1 else "exportar"
    if acao == "importar":
        print(importar())
    elif acao == "publicar":
        print(publicar(" ".join(sys.argv[2:])))
    else:
        print(exportar())
