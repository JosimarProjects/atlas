"""A Atlas aprende sobre o dono sem que ele mande guardar.

Guardar so o que foi pedido explicitamente torna a memoria pobre: o que
importa quase nunca vem anunciado. Ninguem diz "guarde que eu trabalho na
Flipnet" -- diz "preciso resolver isso antes da reuniao da Flipnet", e o fato
esta ali, de passagem.

Entao, depois de cada turno, uma segunda passagem curta olha o que foi dito e
pergunta: apareceu algo durador sobre o dono? A saida e restringida por
gramatica, e o que passa vira fato na memoria de longo prazo.

O DIFICIL E NAO GUARDAR
-----------------------
Um extrator ingenuo vira um acumulador de lixo: guarda "o dono perguntou as
horas", guarda a mesma coisa cinco vezes, guarda o clima de ontem. Tres
defesas:

  1. Instrucao por exemplos, nao por regras. "Guarde X, nao guarde Y" num 9B
     funciona muito pior do que mostrar pares.
  2. Nada de efemero. Fato que caduca em um dia nao e memoria, e conversa.
  3. Deduplicacao semantica antes de gravar: se ja existe algo parecido
     (cosseno >= 0.86), o fato novo e descartado.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from . import lembrancas

# Acima disto, dois fatos dizem a mesma coisa com outras palavras.
PARECIDO_DEMAIS = 0.86

CATEGORIAS = ["pessoa", "trabalho", "preferencia", "rotina", "saude",
              "familia", "projeto", "lugar", "filme", "compra", "geral"]

_INSTRUCAO = """Voce observa uma conversa e extrai fatos DURADOUROS sobre o dono.

Exemplos do que extrair:
"preciso resolver antes da reuniao da Flipnet" -> trabalha na Flipnet
"minha filha faz aniversario em marco" -> filha faz aniversario em marco
"nao consigo tomar leite" -> tem intolerancia a lactose
"odeio acordar cedo" -> prefere nao marcar nada de manha
"moro em Ribeirao Pires" -> mora em Ribeirao Pires
"meu PC tem uma RX 7600 de 8GB" -> o PC dele tem uma RX 7600 de 8GB
"to estudando espanhol nas tercas" -> estuda espanhol as tercas
"uso Windows e WSL" -> trabalha no Windows com WSL
"meu carro e um Onix 2019" -> tem um Onix 2019
"sou alergico a dipirona" -> e alergico a dipirona

Exemplos do que NAO extrair:
"que horas sao?" -> nada
"vai chover amanha?" -> nada (efemero)
"me explica quantizacao" -> nada
"obrigado" -> nada

Escreva SEMPRE na terceira pessoa, como ficha sobre ele: "tem um Onix 2019",
nunca "meu carro e um Onix". A memoria e lida meses depois, fora do contexto
da frase original -- "meu PC" nao diz de quem.

Teste: o fato continuaria verdadeiro daqui a um mes? Se nao, nao extraia.
Vale qualquer assunto -- pessoa, trabalho, equipamento, rotina, gosto, saude.
Responda apenas o JSON. Sem fatos, devolva lista vazia."""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fatos": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "assunto": {"type": "string"},
                    "texto": {"type": "string"},
                    "motivo": {"type": "string"},
                    "categoria": {"type": "string", "enum": CATEGORIAS},
                },
                "required": ["assunto", "texto", "categoria"],
            },
        }
    },
    "required": ["fatos"],
}


def _ja_sabe(assunto: str, texto: str) -> bool:
    """Ha algo semanticamente equivalente guardado?"""
    novo = lembrancas.embutir(f"{assunto}. {texto}")
    if not novo:
        # Sem embeddings, cai para comparacao textual simples -- pior, mas
        # melhor do que duplicar tudo.
        alvo = f"{assunto} {texto}".lower()
        return any(alvo == f"{f['assunto']} {f['texto']}".lower()
                   for f in lembrancas.listar(limite=200))
    con = lembrancas.abrir()
    for l in con.execute("SELECT vetor FROM fatos WHERE vetor IS NOT NULL"):
        if lembrancas._cosseno(novo, lembrancas._desempacotar(l["vetor"])) >= PARECIDO_DEMAIS:
            return True
    return False


def extrair(pergunta: str, resposta: str, origem: str = "") -> list[str]:
    """Olha um turno e grava o que achou. Devolve o que guardou."""
    from . import cerebro

    conversa = f"Dono: {pergunta}\nAtlas: {resposta}"
    msgs = [{"role": "system", "content": _INSTRUCAO},
            {"role": "user", "content": conversa}]
    try:
        # Temperatura baixa e sem raciocinio: e extracao, nao redacao.
        bruto = cerebro._chamar(msgs, max_tokens=400, pensar=False,
                                schema=_SCHEMA, temperatura=0.1)
        fatos = (json.loads(bruto) or {}).get("fatos") or []
    except Exception:
        return []

    guardados = []
    for f in fatos[:3]:
        assunto = (f.get("assunto") or "").strip()
        texto = (f.get("texto") or "").strip()
        if len(assunto) < 3 or len(texto) < 3:
            continue
        if _ja_sabe(assunto, texto):
            continue
        lembrancas.guardar(assunto, texto, (f.get("motivo") or "").strip(),
                           f.get("categoria") or "geral", origem)
        guardados.append(f"{assunto}: {texto}")
    return guardados


def extrair_em_segundo_plano(pergunta: str, resposta: str,
                             origem: str = "") -> None:
    """Dispara a extracao sem segurar a resposta ao dono.

    Custa uma geracao a mais por turno. Sincrono, somaria ~2 s a cada
    resposta; numa thread, o dono nao espera por nada.
    """
    threading.Thread(target=lambda: extrair(pergunta, resposta, origem),
                     daemon=True).start()
