"""O que a Atlas sabe sobre a maquina onde ela mora.

Perguntada "qual o modelo deste computador?", a Atlas respondia que nao tinha
acesso aos dados do sistema. Era verdade -- nenhuma ferramenta olhava para o
proprio PC --, mas era uma verdade constrangedora: ela roda DENTRO da maquina
do dono. O dado estava a uma chamada de distancia.

No Windows tudo vem de uma unica chamada ao CIM (o WMI moderno). Abrir um
PowerShell custa perto de um segundo; pedir processador, memoria e disco em
tres chamadas somaria tres segundos a uma resposta falada. Fora do Windows,
cai para /proc e platform -- menos detalhe, mas nunca mentira.

O retrato fica em cache por um minuto: ninguem troca de processador no meio da
conversa, e a parte que de fato muda (memoria livre, espaco em disco) nao
envelhece em sessenta segundos a ponto de enganar.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import unicodedata

VALIDADE_S = 60

_cache: dict | None = None
_cache_em = 0.0


# O ErrorActionPreference silencioso e de proposito: numa VM sem placa de
# video dedicada, num PC sem disco C:, cada Get-CimInstance que falha deve
# deixar o campo vazio -- nao derrubar o retrato inteiro.
_POWERSHELL = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$cs  = Get-CimInstance Win32_ComputerSystem
$csp = Get-CimInstance Win32_ComputerSystemProduct
$so  = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$c   = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"

# AdapterRAM e uint32 e satura em 4 GB: numa placa de 8 GB ele mente por
# metade. A VRAM de verdade esta no registro, em qwMemorySize, com 64 bits.
$classe = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
$gpus = @()
foreach ($g in Get-CimInstance Win32_VideoController) {
  $vram = $null
  foreach ($k in Get-ChildItem $classe) {
    $p = Get-ItemProperty $k.PSPath
    if ($p.DriverDesc -eq $g.Name -and $p.'HardwareInformation.qwMemorySize') {
      $vram = [int64]$p.'HardwareInformation.qwMemorySize'
    }
  }
  $gpus += [pscustomobject]@{ nome = $g.Name; vram = $vram }
}

$boot = $null
if ($so.LastBootUpTime) { $boot = $so.LastBootUpTime.ToString('dd/MM/yyyy HH:mm') }

[pscustomobject]@{
  fabricante   = $cs.Manufacturer
  modelo       = $cs.Model
  comercial    = $csp.Name
  sistema      = $so.Caption
  versao       = $so.Version
  processador  = $cpu.Name
  nucleos      = $cpu.NumberOfCores
  threads      = $cpu.NumberOfLogicalProcessors
  ram          = $cs.TotalPhysicalMemory
  ram_livre    = $so.FreePhysicalMemory * 1KB
  disco        = $c.Size
  disco_livre  = $c.FreeSpace
  ligado_desde = $boot
  gpus         = @($gpus)
} | ConvertTo-Json -Compress -Depth 3
"""


def _windows() -> dict:
    saida = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    d = json.loads(saida.stdout.strip())
    gpus = d.get("gpus") or []
    # ConvertTo-Json do PowerShell 5.1 desembrulha array de um elemento so:
    # com uma placa de video, `gpus` chega como objeto, nao como lista.
    d["gpus"] = [gpus] if isinstance(gpus, dict) else gpus
    return d


def _posix() -> dict:
    d: dict = {"sistema": f"{platform.system()} {platform.release()}",
               "gpus": [], "threads": os.cpu_count()}
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for linha in f:
                if linha.startswith("model name"):
                    d["processador"] = linha.split(":", 1)[1].strip()
                    break
    except OSError:
        d["processador"] = platform.processor()
    try:
        with open("/proc/meminfo", encoding="utf-8", errors="replace") as f:
            for linha in f:
                if linha.startswith("MemTotal:"):
                    d["ram"] = int(linha.split()[1]) * 1024
                elif linha.startswith("MemAvailable:"):
                    d["ram_livre"] = int(linha.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    for campo, chave in (("product_name", "comercial"), ("sys_vendor", "fabricante")):
        try:
            with open(f"/sys/class/dmi/id/{campo}", encoding="utf-8") as f:
                d[chave] = f.read().strip()
        except OSError:
            pass
    try:
        uso = shutil.disk_usage("/")
        d["disco"], d["disco_livre"] = uso.total, uso.free
    except OSError:
        pass
    return d


def _enxugar(d: dict) -> dict:
    """Tira o espaco que o fabricante deixou no nome.

    O CIM devolve "AMD Ryzen 5 8500G w/ Radeon 740M Graphics      " com seis
    espacos no fim -- que o Piper le como uma pausa no meio da frase.
    """
    return {k: (" ".join(v.split()) if isinstance(v, str) else v)
            for k, v in d.items()}


def ler(forcar: bool = False) -> dict:
    """Retrato da maquina. Levanta OSError se nem isso der para saber."""
    global _cache, _cache_em
    if _cache is not None and not forcar and time.time() - _cache_em < VALIDADE_S:
        return _cache
    try:
        d = _windows() if platform.system() == "Windows" else _posix()
        d = _enxugar(d)
        d["gpus"] = [_enxugar(g) for g in d.get("gpus") or []]
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
        raise OSError(f"{type(e).__name__}: {e}") from e
    _cache, _cache_em = d, time.time()
    return d


# ------------------------------------------------------------------- frases
def _tamanho(valor) -> str:
    """Bytes em GB -- ou em MB quando GB arredondaria para zero.

    Com a RAM apertada, "0 GB livres agora" soa a maquina morta; "400 MB
    livres agora" e a mesma verdade dita de um jeito que se entende.
    """
    try:
        b = float(valor)
    except (TypeError, ValueError):
        return ""
    if b < 512 * 1024 ** 2:
        return f"{b / 1024 ** 2:.0f} MB"
    return f"{b / 1024 ** 3:.0f} GB"


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t)
                   if unicodedata.category(c) != "Mn").lower()


# A escolha da parte e por palavra solta porque a pergunta chega falada e o
# roteador repassa o que ouviu: "placa de video", "quanto de ram", "ssd".
_PARTES = {
    "processador": ("processador", "cpu", "nucleo", "nucleos", "clock"),
    "memoria": ("memoria", "ram"),
    "video": ("video", "gpu", "placa", "grafica", "vram"),
    "disco": ("disco", "hd", "ssd", "armazenamento", "espaco"),
    "sistema": ("sistema", "windows", "so", "versao", "linux"),
    "modelo": ("modelo", "fabricante", "marca", "notebook", "pc", "computador",
               "maquina", "hardware"),
}


def _qual_parte(pedido: str) -> str:
    palavras = set(_sem_acento(pedido).replace(",", " ").split())
    for parte, chaves in _PARTES.items():
        if palavras & set(chaves):
            return parte
    return "tudo"


def _frases(d: dict) -> dict[str, str]:
    f: dict[str, str] = {}

    nome = " ".join(x for x in (d.get("fabricante"), d.get("comercial")) if x)
    modelo = d.get("modelo")
    if modelo and modelo not in nome:
        nome = f"{nome} (modelo {modelo})" if nome else f"modelo {modelo}"
    if nome:
        f["modelo"] = f"Este computador e um {nome}."

    sistema = " ".join(x for x in (d.get("sistema"), d.get("versao")) if x)
    if sistema:
        desde = f", ligado desde {d['ligado_desde']}" if d.get("ligado_desde") else ""
        f["sistema"] = f"O sistema e o {sistema}{desde}."

    if d.get("processador"):
        partes = [f"O processador e um {d['processador']}"]
        if d.get("nucleos"):
            partes.append(f"com {d['nucleos']} nucleos")
        if d.get("threads"):
            partes.append(f"{d['threads']} threads")
        f["processador"] = ", ".join(partes) + "."

    if _tamanho(d.get("ram")):
        texto = f"Tem {_tamanho(d['ram'])} de memoria RAM"
        if _tamanho(d.get("ram_livre")):
            texto += f", {_tamanho(d['ram_livre'])} livres agora"
        f["memoria"] = texto + "."

    placas = []
    for g in d.get("gpus") or []:
        nome_gpu = g.get("nome")
        if not nome_gpu:
            continue
        vram = _tamanho(g.get("vram"))
        placas.append(f"{nome_gpu} de {vram}" if vram else nome_gpu)
    if placas:
        f["video"] = "A placa de video e " + " e ".join(placas) + "."

    if _tamanho(d.get("disco")):
        texto = f"O disco principal tem {_tamanho(d['disco'])}"
        if _tamanho(d.get("disco_livre")):
            texto += f", com {_tamanho(d['disco_livre'])} livres"
        f["disco"] = texto + "."

    return f


def resumo(parte: str = "") -> str:
    """O retrato em portugues falavel. `parte` filtra; vazio traz tudo."""
    try:
        d = ler()
    except OSError as e:
        # Falhar dizendo o que falhou. O silencio e que era o problema
        # original: ela dizia "nao tenho acesso" sem nunca ter tentado.
        return f"Nao consegui ler os dados desta maquina ({e})."

    f = _frases(d)
    if not f:
        return "Nao consegui ler os dados desta maquina."

    escolhida = _qual_parte(parte)
    if escolhida != "tudo" and escolhida in f:
        return f[escolhida]
    # Ordem de leitura: o que a maquina e, depois do que ela e feita.
    return " ".join(f[k] for k in ("modelo", "sistema", "processador",
                                   "memoria", "video", "disco") if k in f)
