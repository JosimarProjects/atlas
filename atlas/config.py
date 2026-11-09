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
# 0.6 com top_p/top_k, que e o recomendado pelo Qwen para modo de
# raciocinio. A mistura de espanhol que me levou a baixar para 0.2 era
# sintoma do template quebrado, nao da temperatura -- e temperatura baixa
# em modo de raciocinio causa loop infinito.
LLM_TEMPERATURA = 0.6
LLM_TOP_P = 0.95
LLM_TOP_K = 20
# O Qwen3.5 raciocina antes de responder, e esse raciocinio consome o mesmo
# orcamento: uma pergunta de conhecimento gasta ~950 tokens so pensando.
# Com 2000 ainda havia resposta vazia; 4000 da folga. Nao desligue o
# pensamento: sem ele o modelo para de chamar ferramentas e passa a
# "simular" o que faria.
LLM_MAX_TOKENS = 4000

# Servidor de embeddings (EmbeddingGemma 2), separado do cerebro. E ele que
# da memoria semantica: "Duna 3, ficcao cientifica" vira um vetor, e
# "quais filmes eu mandei guardar?" acha esse vetor mesmo sem a palavra
# "filme" aparecer em lugar nenhum.
EMBED_URL = os.environ.get("ATLAS_EMBED_URL", "http://127.0.0.1:8083/v1")

# Quantos fatos de longo prazo entram no contexto a cada turno. Poucos para
# nao afogar o prompt; o suficiente para a Atlas lembrar do que importa.
MEMORIA_FATOS_NO_CONTEXTO = 5

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
# Conta Google usada pelas ferramentas de e-mail e agenda.
EMAIL_DONO = os.environ.get(
    "ATLAS_EMAIL", "josimar.negocioslmtda@gmail.com")

# Prompt curto de proposito. Com uma lista longa de regras de estilo -- ainda
# mais regras negativas ("nada de markdown, bullet, emoji") -- este 9B passa a
# repetir o proprio prompt em loop dentro dos argumentos da ferramenta, ate
# estourar o JSON e derrubar a requisicao. Duas frases funcionam; dez nao.
PROMPT_SISTEMA = f"""Voce e a {NOME}, assistente de voz do {DONO}, rodando no \
computador dele. Escreva SEMPRE em portugues do Brasil, nunca em outro idioma. \
Texto corrido curto, no maximo tres frases. Use as ferramentas para obter \
dados reais. Responda direto ao que foi pedido, sem sermao e sem avisos \
desnecessarios: o {DONO} e adulto e a maquina e dele."""
