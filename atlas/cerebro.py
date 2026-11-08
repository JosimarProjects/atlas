"""O cerebro: conversa com o Qwen3.5-9B servido pelo llama-server.

Fala HTTP puro com a API compativel com OpenAI -- sem SDK, para a Atlas nao
depender de nada que possa quebrar numa atualizacao.

POR QUE NAO USAMOS TOOL CALLING NATIVO
--------------------------------------
O Qwen3.5 emite tool calls num formato XML-ish proprio. O parser do llama.cpp
b12211 ainda nao da conta: ou vaza o fechamento da tag para dentro do valor
("Teste a Atlas as 9h</ texto" foi parar no banco), ou produz JSON invalido e o
servidor devolve HTTP 500 antes de a resposta chegar aqui.

Entao a decisao de usar ferramenta e tomada numa chamada separada, com
`response_format: json_schema`. O llama.cpp converte o schema numa gramatica
BNF e restringe a geracao token a token -- a saida e JSON valido por
construcao, nao por sorte. Custa uma chamada curta a mais por turno e, em
troca, nao depende do parser de tool call amadurecer.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Iterator

from . import config, ferramentas
from .memoria import Memoria

MAX_VOLTAS_FERRAMENTA = 3

# O Qwen3.5 raciocina dentro de <think>...</think>. Mantemos o raciocinio
# ligado na resposta final (sem ele o modelo perde muita qualidade), mas o
# bloco nunca pode vazar para a voz.
_PENSAMENTO = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ORFAO = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)


def _limpar(texto: str) -> str:
    texto = _PENSAMENTO.sub("", texto)
    if "</think>" in texto.lower():
        texto = _ORFAO.sub("", texto)
    return texto.strip()


# O Qwen e um modelo chines e, com prompt de sistema curto, as vezes responde
# em mandarim -- principalmente quando a pergunta tem nome proprio. Instrucao
# no prompt nao resolve sozinha; detectamos e refazemos.
_CJK = re.compile(r"[　-鿿＀-￯]")


def _derivou_idioma(texto: str) -> bool:
    return bool(_CJK.search(texto))


_NUMEROS = re.compile(r"-?\d+(?:[.,]\d+)?")


def _fiel_aos_dados(resposta: str, dados: str) -> bool:
    """A resposta preservou os numeros que a ferramenta devolveu?

    Sem esta trava o modelo reescreve o resultado e inventa: perguntado sobre
    Curitiba, respondeu "aproximadamente 30 graus, com leve brisa" sem que
    numero nenhum tivesse vindo da API. Se os numeros da ferramenta nao
    aparecem na resposta, a resposta nao vale.
    """
    esperados = {n.replace(",", ".") for n in _NUMEROS.findall(dados)}
    if not esperados:
        return True
    presentes = {n.replace(",", ".") for n in _NUMEROS.findall(resposta)}
    return esperados.issubset(presentes)


class CerebroIndisponivel(RuntimeError):
    """O llama-server nao respondeu."""


def _post(caminho: str, corpo: dict[str, Any], timeout: int = 240) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{config.LLM_URL}{caminho}",
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:300]
        raise CerebroIndisponivel(f"llama-server devolveu {e.code}: {detalhe}") from e
    except urllib.error.URLError as e:
        raise CerebroIndisponivel(
            f"llama-server nao respondeu em {config.LLM_URL}: {e}"
        ) from e


def no_ar() -> bool:
    try:
        with urllib.request.urlopen(f"{config.LLM_URL}/models", timeout=5):
            return True
    except Exception:
        return False


def _chamar(
    mensagens: list[dict[str, Any]],
    *,
    max_tokens: int | None = None,
    pensar: bool = True,
    schema: dict[str, Any] | None = None,
    temperatura: float | None = None,
) -> str:
    corpo: dict[str, Any] = {
        "model": config.LLM_MODELO,
        "messages": mensagens,
        "temperature": config.LLM_TEMPERATURA if temperatura is None else temperatura,
        "top_p": config.LLM_TOP_P,
        "top_k": config.LLM_TOP_K,
        "max_tokens": max_tokens or config.LLM_MAX_TOKENS,
    }
    if not pensar:
        corpo["chat_template_kwargs"] = {"enable_thinking": False}
    if schema is not None:
        corpo["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "decisao", "strict": True, "schema": schema},
        }
    resposta = _post("/chat/completions", corpo)
    return resposta["choices"][0]["message"].get("content") or ""


# ------------------------------------------------------------------ roteador
# Pergunta curta que comeca por conectivo ou pronome nao se explica sozinha:
# "e em Curitiba?", "e amanha?", "esse ai". So nesses casos vale arrastar a
# conversa anterior para o roteador.
_CONTINUACAO = re.compile(
    r"^\s*(e|ou|entao|tambem|ai|la|ele|ela|isso|esse|essa|aquele|aquela|"
    r"nele|nela|dele|dela|o mesmo|a mesma)\b",
    re.IGNORECASE,
)


def _parece_continuacao(pergunta: str) -> bool:
    p = pergunta.strip()
    return bool(_CONTINUACAO.match(p)) or len(p.split()) <= 3


def _schema_decisao() -> dict[str, Any]:
    nomes = [e["function"]["name"] for e in ferramentas.ESQUEMAS]
    return {
        "type": "object",
        "properties": {
            "ferramenta": {"type": "string", "enum": nomes + ["nenhuma"]},
            "argumentos": {"type": "object"},
        },
        "required": ["ferramenta", "argumentos"],
    }


def _catalogo() -> str:
    linhas = []
    for e in ferramentas.ESQUEMAS:
        f = e["function"]
        params = ", ".join(f["parameters"]["properties"]) or "sem argumentos"
        linhas.append(f"- {f['name']}({params}): {f['description']}")
    return "\n".join(linhas)


def _decidir(pergunta: str, memoria: Memoria,
             observado: str = "") -> tuple[str, dict[str, Any]]:
    """Escolhe uma ferramenta (ou nenhuma). Saida garantida pela gramatica."""
    # Instrucao curta DE PROPOSITO. Tentei acrescentar regras ("use a busca
    # quando a resposta depender de fato do mundo", "nao use ferramenta para
    # matematica") e a precisao CAIU: com as regras, "qual a temperatura
    # hoje?" e "quem ganhou o Oscar 2026?" iam para 'nenhuma'; sem elas, as
    # duas roteiam certo. O que funciona neste 9B e por em palavra-chave na
    # descricao de cada ferramenta, nao em prosa na instrucao.
    instrucao = (
        "Escolha a ferramenta que responde a mensagem do usuario.\n"
        f"{_catalogo()}\n"
        "Se nenhuma for necessaria, responda com ferramenta igual a 'nenhuma'.\n"
        "Responda apenas o JSON."
    )
    # A janela anterior entra so quando a pergunta nao se sustenta sozinha
    # ("e em Curitiba?"). Passar contexto sempre desviava o roteador: com uma
    # conversa sobre Dom Pedro II na memoria, "qual a temperatura em Ribeirao
    # Pires?" era roteada para 'nenhuma'.
    msgs: list[dict[str, Any]] = [{"role": "system", "content": instrucao}]
    if _parece_continuacao(pergunta):
        msgs.extend(memoria.janela()[-2:])
    conteudo = pergunta
    if observado:
        # Segunda volta: o modelo ve o que a ferramenta anterior trouxe e
        # decide se precisa de mais uma (tipicamente abrir uma das paginas).
        conteudo = (pergunta + "\n\nJa obtive isto:\n" + observado[:1500] +
                    "\n\nFalta alguma ferramenta para responder? Se ja da para "
                    "responder, escolha 'nenhuma'.")
    msgs.append({"role": "user", "content": conteudo})
    try:
        # Sem raciocinio: a gramatica ja garante a forma, e pensar aqui so
        # gastaria tokens antes de um JSON de uma linha.
        # Temperatura baixa aqui, nao a 0.6 da conversa: escolher ferramenta e
        # uma decisao, nao redacao, e com 0.6 o mesmo caso ora acertava ora
        # nao. O alerta do Qwen sobre loop com temperatura baixa vale para o
        # modo de raciocinio, que esta desligado neste passo.
        bruto = _chamar(msgs, max_tokens=300, pensar=False,
                        schema=_schema_decisao(), temperatura=0.1)
        d = json.loads(bruto)
        nome = d.get("ferramenta", "nenhuma")
        args = d.get("argumentos") or {}
        return (nome, args) if isinstance(args, dict) else (nome, {})
    except (json.JSONDecodeError, KeyError, CerebroIndisponivel):
        return "nenhuma", {}   # na duvida, responde sem ferramenta


# ------------------------------------------------------------------- resposta
def responder(pergunta: str, memoria: Memoria) -> str:
    memoria.registrar("user", pergunta)

    # Encadear so vale depois de uma busca: "quais filmes estao em cartaz?"
    # traz links para o Ingresso e o AdoroCinema, nunca a lista -- a resposta
    # util exige abrir uma das paginas. As outras ferramentas ja devolvem o
    # dado final, e uma volta extra so daria ao modelo mais chance de errar.
    observacoes: list[str] = []
    observado = ""
    for _ in range(MAX_VOLTAS_FERRAMENTA):
        nome, args = _decidir(pergunta, memoria, observado)
        if nome == "nenhuma":
            break
        resultado = ferramentas.executar(nome, args)
        observacoes.append(f"{nome} -> {resultado}")
        if nome != "buscar_na_web":
            break
        observado = resultado

    mensagens = memoria.contexto()
    if observacoes:
        # O template do Qwen3.5 exige que TODO system venha no inicio e levanta
        # "System message must be at the beginning" se houver outro depois --
        # por isso o resultado entra concatenado no system que ja existe.
        mensagens[0] = {
            "role": "system",
            "content": mensagens[0]["content"]
            + "\n\nDados ja obtidos pelas ferramentas (use-os, nao invente):\n"
            + "\n".join(observacoes),
        }

    texto = _limpar(_chamar(mensagens))

    if observacoes:
        # Resultado de ferramenta ja vem como frase falavel em portugues. Se a
        # reescrita do modelo perdeu os numeros ou trocou de idioma, ela nao
        # vale: entregamos o dado cru, que e sempre verdadeiro.
        dados = observacoes[-1].split(" -> ", 1)[-1]
        # A trava dos numeros so vale para resultado curto e factual (clima,
        # hora): ali todo numero importa. Texto de web traz dezenas de numeros
        # incidentais -- exigir todos reprovava qualquer resumo e despejava a
        # pagina crua na voz do usuario.
        exigir_numeros = len(dados) < 400
        reprovado = (not texto or _derivou_idioma(texto)
                     or (exigir_numeros and not _fiel_aos_dados(texto, dados)))
        if reprovado:
            memoria.registrar("assistant", dados)
            return dados
        memoria.registrar("assistant", texto)
        _talvez_resumir(memoria)
        return texto

    if _derivou_idioma(texto):
        # Refaz uma vez, com a lingua cobrada no proprio turno do usuario --
        # ali o modelo obedece mais do que a uma regra la no system.
        reforco = mensagens + [{
            "role": "user",
            "content": "Reescreva a resposta anterior inteiramente em portugues do Brasil.",
        }]
        segunda = _limpar(_chamar(reforco))
        texto = segunda if segunda and not _derivou_idioma(segunda) else ""

    if not texto and observacoes:
        # O modelo gastou o orcamento pensando: entrega o dado cru em vez de
        # ficar mudo -- vale mais que silencio numa assistente de voz.
        texto = observacoes[-1].split(" -> ", 1)[-1]

    memoria.registrar("assistant", texto)
    _talvez_resumir(memoria)
    return texto


def _talvez_resumir(memoria: Memoria) -> None:
    if not memoria.precisa_resumir():
        return
    antigas = memoria.falas_para_resumir()
    if not antigas:
        return
    transcricao = "\n".join(f"{m['role']}: {m['content']}" for m in antigas)
    pedido = [
        {"role": "system", "content":
            "Resuma a conversa em ate 5 linhas, em portugues. Guarde fatos sobre "
            "o usuario, decisoes e pendencias."},
        {"role": "user", "content":
            (f"Resumo anterior:\n{memoria.resumo}\n\n" if memoria.resumo else "")
            + f"Novas falas:\n{transcricao}"},
    ]
    try:
        novo = _limpar(_chamar(pedido, max_tokens=800))
        if novo:
            memoria.resumo = novo
    except CerebroIndisponivel:
        pass  # resumir e otimizacao; nunca derrubar a conversa por causa disso


def responder_em_pedacos(pergunta: str, memoria: Memoria) -> Iterator[str]:
    """Fatia a resposta em frases para a fala comecar antes do fim."""
    texto = responder(pergunta, memoria)
    atual = ""
    for ch in texto:
        atual += ch
        if ch in ".!?" and len(atual.strip()) > 25:
            yield atual.strip()
            atual = ""
    if atual.strip():
        yield atual.strip()
