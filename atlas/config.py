"""Configuracao central da Atlas.

Tudo que e caminho, porta ou ajuste de comportamento mora aqui, para nenhum
outro modulo precisar adivinhar onde as coisas estao.
"""
from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

MODELOS = RAIZ / "models"
VOZES = RAIZ / "voices"
DADOS = RAIZ / "data"
LOGS = RAIZ / "logs"

for _d in (MODELOS, VOZES, DADOS, LOGS):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- cerebro
LLM_URL = os.environ.get("ATLAS_LLM_URL", "http://127.0.0.1:8080/v1")
LLM_MODELO = "atlas"
LLM_TEMPERATURA = 0.6
LLM_MAX_TOKENS = 700

# ---------------------------------------------------------------- ouvido
# faster-whisper. "small" ja entende portugues bem e cabe na CPU sem roubar
# VRAM do Qwen; "medium" acerta mais, ao custo de ~1s a mais por fala.
STT_MODELO = os.environ.get("ATLAS_STT_MODELO", "small")
STT_IDIOMA = "pt"
STT_COMPUTE = "int8"            # CPU: int8 e o melhor custo/beneficio

AUDIO_TAXA = 16000              # Hz, o que o Whisper espera
AUDIO_BLOCO = 512               # amostras por bloco lido do microfone

# Deteccao de fala: silencio contínuo que encerra a captura.
VAD_SILENCIO_S = 0.9
VAD_FALA_MIN_S = 0.4            # abaixo disso e ruido, nao fala
VAD_LIMIAR = 0.5                # sensibilidade do Silero VAD (0..1)

# ---------------------------------------------------------------- voz
VOZ_MODELO = os.environ.get("ATLAS_VOZ", "pt_BR-faber-medium")
VOZ_VELOCIDADE = 1.0

# ---------------------------------------------------------------- memoria
BANCO = DADOS / "atlas.db"
# Quantas falas ficam na janela literal antes de virarem resumo. E o truque
# do video: em vez do "caderno que engorda", um resumo de tamanho constante.
MEMORIA_JANELA = 8
MEMORIA_RESUMO_MAX = 1200       # caracteres do resumo acumulado

NOME = "Atlas"
DONO = os.environ.get("ATLAS_DONO", "Josimar")

PROMPT_SISTEMA = f"""Voce e a {NOME}, a assistente pessoal do {DONO}, rodando \
localmente no computador dele. Sem internet e sem mensalidade.

Como voce fala:
- Portugues do Brasil, direto e natural, como quem conversa em voz alta.
- Respostas curtas: duas ou tres frases, a nao ser que peçam detalhe.
- Sua resposta vira audio. Nada de markdown, bullet, emoji ou code block.
- Numeros e horas por extenso quando ficar mais natural de ouvir.

Voce tem ferramentas de verdade (lembretes, clima, anotacoes, hora). Use-as em \
vez de inventar. Se nao souber, diga que nao sabe."""
