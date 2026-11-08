"""Autoteste da Atlas: valida tudo que nao depende do modelo estar baixado.

Roda com `python scripts/autoteste.py` a partir da raiz do projeto.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK, FALHA = "  ok  ", " FALHA"
problemas: list[str] = []


def checa(nome: str, fn):
    try:
        detalhe = fn()
        print(f"[{OK}] {nome}" + (f" -- {detalhe}" if detalhe else ""))
    except Exception as e:
        print(f"[{FALHA}] {nome} -- {type(e).__name__}: {e}")
        problemas.append(nome)


# --------------------------------------------------------------- importacoes
def _importa():
    from atlas import cerebro, config, ferramentas, memoria, voz  # noqa: F401
    return "todos os modulos carregam"


# -------------------------------------------------------------------- memoria
def _memoria():
    from atlas.memoria import Memoria
    m = Memoria()
    antes = len(m.janela())
    m.registrar("user", "teste de autoteste")
    m.registrar("assistant", "resposta de autoteste")
    depois = len(m.janela())
    assert depois > antes or depois > 0, "janela nao cresceu"
    ctx = m.contexto()
    assert ctx[0]["role"] == "system", "contexto sem prompt de sistema"
    assert any("Agora sao" in c["content"] for c in ctx if c["role"] == "system"), \
        "contexto sem data/hora"
    return f"{len(ctx)} mensagens no contexto, janela com {depois}"


# ---------------------------------------------------------------- ferramentas
def _ferramentas():
    from atlas import ferramentas as f
    r1 = f.executar("criar_lembrete", {"texto": "autoteste", "quando": "hoje"})
    assert "autoteste" in r1, r1
    r2 = f.executar("listar_lembretes", {})
    assert "autoteste" in r2, r2
    r3 = f.executar("concluir_lembrete", {"texto": "autoteste"})
    assert "feito" in r3.lower(), r3
    r4 = f.executar("que_horas", {})
    assert "Sao" in r4, r4
    f.executar("anotar", {"titulo": "autoteste", "texto": "nota de teste"})
    assert "autoteste" in f.executar("ler_notas", {"titulo": "autoteste"})
    # erros nao podem explodir, tem que virar texto para o modelo ler
    assert "desconhecida" in f.executar("nao_existe", {})
    assert "invalidos" in f.executar("criar_lembrete", {"campo_errado": 1})
    return f"{len(f.ESQUEMAS)} ferramentas, erros tratados"


# --------------------------------------------------------------------- audio
def _audio():
    import sounddevice as sd
    entrada = saida = None
    for d in sd.query_devices():
        if entrada is None and d["max_input_channels"] > 0:
            entrada = d["name"]
        if saida is None and d["max_output_channels"] > 0:
            saida = d["name"]
    assert entrada, "nenhum microfone encontrado"
    assert saida, "nenhuma saida de audio encontrada"
    return f"mic: {entrada[:34]} | saida: {saida[:34]}"


# ----------------------------------------------------------------------- gpu
def _gpu():
    import subprocess
    from atlas import config
    exe = config.RAIZ / "engine" / "llama" / "llama-server.exe"
    assert exe.exists(), f"llama-server ausente em {exe}"
    p = subprocess.run([str(exe), "--list-devices"], capture_output=True, text=True)
    linhas = [l.strip() for l in p.stdout.splitlines() if "Vulkan" in l or "CUDA" in l]
    assert linhas, f"nenhum device de GPU detectado:\n{p.stdout}"
    return linhas[0]


# -------------------------------------------------------------------- modelo
def _modelo():
    from atlas import config
    g = config.MODELOS / "Qwen_Qwen3.5-9B-Q4_K_M.gguf"
    parcial = g.with_suffix(g.suffix + ".aria2")
    assert g.exists(), "modelo ainda nao baixado"
    tam = g.stat().st_size / 1e9
    if parcial.exists():
        raise AssertionError(f"download incompleto ({tam:.1f} GB ate agora)")
    return f"{tam:.1f} GB, completo"


# ----------------------------------------------------------------------- voz
def _voz():
    from atlas import voz
    assert voz.disponivel(), "voz do Piper ainda nao baixada"
    return "modelo de voz presente"


if __name__ == "__main__":
    print("Autoteste da Atlas\n" + "-" * 58)
    checa("importacoes", _importa)
    checa("memoria (SQLite + resumo)", _memoria)
    checa("ferramentas", _ferramentas)
    checa("dispositivos de audio", _audio)
    checa("GPU / llama.cpp", _gpu)
    checa("modelo Qwen3.5-9B", _modelo)
    checa("voz Piper", _voz)
    print("-" * 58)
    if problemas:
        print(f"{len(problemas)} pendencia(s): {', '.join(problemas)}")
        sys.exit(1)
    print("tudo pronto. Suba o servidor e rode: python -m atlas.main")
