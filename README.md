# Atlas

Assistente de voz que roda **inteiramente no seu PC**. Sem internet, sem mensalidade, sem API de terceiros.

```
ouve  →  pensa  →  fala
```

| Peça | O que é | Onde roda |
|---|---|---|
| **Ouvido** | faster-whisper `small` + Silero VAD | CPU |
| **Cérebro** | Qwen3.5-9B Q4_K_M via llama.cpp (Vulkan) | GPU |
| **Voz** | Piper TTS (`pt_BR-faber-medium`) | CPU |
| **Memória** | SQLite + resumo de tamanho constante | disco |

A divisão é de propósito: os 8 GB de VRAM são **todos do Qwen**. Whisper e Piper ficam na CPU para não disputar espaço com o cérebro.

---

## Por que esse desenho

Esse projeto nasceu de [um reel da @juliacardoso.dev](https://www.instagram.com/reel/DfA4yvgyjlH/), que montou a mesma coisa numa RX 580 de 2017 — e explicou os truques que fazem caber. Os três que importam:

**1. Quantização — como um modelo de 18 GB cabe numa placa de 8 GB**

O Qwen3.5-9B em BF16 ocupa 18,4 GB. Quantizar é arredondar os pesos: em vez de guardar `3,14159265`, guarda `3,14`. Cada parâmetro perde precisão, o modelo cai para **5,4 GB** — menos de um terço — e a inteligência quase não muda. O `Q4_K_M` ainda é esperto no arredondamento: as camadas mais sensíveis ficam com mais casas decimais.

**2. Memória que não engorda**

O jeito ingênuo guarda a conversa inteira, palavra por palavra. O contexto cresce sem parar e cada resposta fica mais cara. Aqui ([`atlas/memoria.py`](atlas/memoria.py)) as últimas 8 falas ficam literais e o resto vira um **resumo de tamanho constante**. Lembrar para de ficar mais caro com o tempo, e dá para manter várias conversas abertas.

**3. Ferramentas de verdade, não encenação**

Quando a Atlas diz que criou um lembrete, [uma linha entrou no SQLite](atlas/ferramentas.py). Ela não finge. São 8 ferramentas: lembretes (criar/listar/concluir), notas, hora, clima e relatório do dia.

---

## Instalação

**Requisitos:** Windows 10/11 ou Linux, Python 3.10+, uma GPU com 8 GB (ou 16 GB de RAM para rodar em CPU, bem mais devagar).

```bash
git clone https://github.com/JosimarProjects/atlas.git
cd atlas
pip install -r requirements.txt
```

### 1. O motor

Baixe um build do [llama.cpp](https://github.com/ggml-org/llama.cpp/releases) e extraia em `engine/llama/`. Escolha a variante conforme a GPU:

| GPU | Build |
|---|---|
| AMD (qualquer) | `bin-win-vulkan-x64` |
| NVIDIA | `bin-win-cuda-x64` |
| Intel Arc | `bin-win-sycl-x64` |

Confirme que sua placa aparece:

```bash
engine/llama/llama-server.exe --list-devices
```

> **Vulkan em vez de ROCm, de propósito.** O ROCm é chato de instalar no Windows e cobre poucas placas. O Vulkan roda em qualquer GPU moderna e a diferença de velocidade é pequena em inferência, que é limitada por banda de memória.

### 2. O modelo e a voz

```bash
bash scripts/baixa-modelo.sh    # Qwen3.5-9B Q4_K_M — 5,6 GB
bash scripts/baixa-voz.sh       # Piper pt_BR — 63 MB
```

O download do modelo usa `aria2` em 16 conexões e **retoma de onde parou** se cair.

### 3. Rodar

```bash
# terminal 1 — o cérebro
powershell -File scripts/servidor.ps1

# terminal 2 — a assistente
python -m atlas.main              # voz
python -m atlas.main --texto      # teclado, para testar sem áudio
python -m atlas.main --diga "que horas são?"
```

### 4. Ligar junto com o PC

```powershell
.\scripts\instala-servico.ps1            # só o cérebro
.\scripts\instala-servico.ps1 -ComVoz    # cérebro + microfone
.\scripts\instala-servico.ps1 -Remover
```

Registra tarefas no Agendador do Windows que sobem no logon e **reiniciam sozinhas** se o processo cair. Não precisa de admin.

---

## A API

O `llama-server` expõe uma API **compatível com OpenAI** em `http://127.0.0.1:8080/v1`. Qualquer coisa que fale com a OpenAI funciona trocando a base URL:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="nao-precisa")
r = client.chat.completions.create(
    model="atlas",
    messages=[{"role": "user", "content": "me explica quantização"}],
)
print(r.choices[0].message.content)
```

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"atlas","messages":[{"role":"user","content":"oi"}]}'
```

Dá para registrar como um modelo num gateway tipo LiteLLM e usar nos seus projetos com custo zero.

---

## Estrutura

```
atlas/
├── atlas/
│   ├── config.py        # caminhos, portas, prompt do sistema
│   ├── memoria.py       # SQLite + resumo de tamanho constante
│   ├── ferramentas.py   # 8 ferramentas + esquemas de tool calling
│   ├── cerebro.py       # cliente HTTP do llama-server, loop de ferramentas
│   ├── ouvido.py        # microfone + VAD + faster-whisper
│   ├── voz.py           # Piper TTS
│   └── main.py          # loop ouve → pensa → fala
└── scripts/
    ├── servidor.ps1          # sobe o llama-server na GPU certa
    ├── baixa-modelo.sh       # Qwen3.5-9B, 16 conexões, retomável
    ├── baixa-voz.sh          # voz PT-BR do Piper
    └── instala-servico.ps1   # Agendador do Windows
```

---

## Ajustes

Tudo em [`atlas/config.py`](atlas/config.py), ou por variável de ambiente:

| Variável | Padrão | Para quê |
|---|---|---|
| `ATLAS_LLM_URL` | `http://127.0.0.1:8080/v1` | apontar para outro servidor |
| `ATLAS_STT_MODELO` | `small` | `medium` acerta mais, ~1s mais lento |
| `ATLAS_VOZ` | `pt_BR-faber-medium` | `pt_BR-edresson-low` é feminina |
| `ATLAS_DONO` | `Josimar` | como ela te chama |

**Contexto vs. VRAM.** `servidor.ps1` usa 16k de contexto com cache KV em `q8_0`. Se faltar VRAM, baixe `-Ctx`. Se sobrar, suba — o Qwen3.5 aguenta 262k.

**Microfone.** A Atlas se silencia enquanto fala, senão ela se escuta e responde à própria voz. Se ela cortar você no meio da frase, aumente `VAD_SILENCIO_S`; se esperar demais, diminua.

---

## Problemas conhecidos

**A GPU errada é escolhida.** Em máquinas com GPU dedicada + integrada, alguns runtimes pegam a integrada (lenta, usa RAM compartilhada). Confirme com `--list-devices` e fixe em `servidor.ps1` com `--device`.

**Download de 5,6 GB caindo.** O `baixa-modelo.sh` retoma sozinho — é só rodar de novo.

**`piper` não instala.** Em Python 3.13 algumas rodas ainda não existem. Use 3.10–3.12.

---

## Licença

MIT. O Qwen3.5-9B é Apache 2.0; as vozes do Piper seguem cada uma sua licença.
