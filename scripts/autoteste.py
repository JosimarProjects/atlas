"""Autoteste da Atlas: valida tudo que nao depende do modelo estar baixado.

Roda com `python scripts/autoteste.py` a partir da raiz do projeto.

O teste escreve de VERDADE -- e esse e o ponto: ferramenta que so finge nao
prova nada. Entao ele escreve num banco descartavel, nunca no do dono. Antes
disso, cada rodada deixava um lembrete "autoteste", uma nota "nota de teste" e
duas falas soltas no atlas.db; as falas nasciam sem conversa, e o espacos.py,
na subida seguinte, adotava toda fala orfa numa conversa nova. A barra lateral
ganhava uma "Conversas anteriores" cujo conteudo inteiro era o teste -- e foi
assim que a Atlas apareceu na tela dizendo "resposta de autoteste".
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Antes de qualquer import da atlas: o config le o ambiente na importacao.
# Apagar no comeco (e nao no fim) e de proposito -- no Windows o arquivo
# segue aberto pelas conexoes do teste. Assim cada rodada nasce do zero, com
# o esquema criado na hora, que e o mesmo caminho de uma instalacao nova.
BANCO_TESTE = Path(tempfile.gettempdir()) / "atlas-autoteste.db"
BANCO_TESTE.unlink(missing_ok=True)
os.environ["ATLAS_BANCO"] = str(BANCO_TESTE)
# A mesma trava do zap: o numero do dono dispensa confirmacao, entao um teste
# que escorregue numa ferramenta de envio toca o celular dele de verdade.
os.environ.setdefault("ATLAS_SIMULAR", "1")

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
    r5 = f.executar("dados_do_pc", {"parte": "processador"})
    assert "processador" in r5.lower() and "Nao consegui" not in r5, r5
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
    print(f"[ nota ] banco descartavel: {BANCO_TESTE}")
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
