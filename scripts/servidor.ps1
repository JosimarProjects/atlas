# Sobe o cerebro da Atlas: llama-server com o Qwen3.5-9B na GPU.
# Expoe uma API compativel com OpenAI em http://127.0.0.1:8080/v1
#
# TEMPLATE: usa o chat template CORRIGIDO, nao o que vem no GGUF.
# O template oficial do Qwen duplica as tags <think> no historico; o modelo
# entao abre um <think> dentro do proprio <think>, reinicia o raciocinio em
# loop e nunca produz resposta (content vazio, finish_reason=length). Pelo
# caminho ele inventa fatos e palavras. Com o template corrigido a mesma
# pergunta na mesma GPU passa a ser respondida certo. Veja o README.
param(
    [int]$Port = 8080,
    # 8k, nao 16k. Com a visao carregada (918 MB) o cache de 16k nao cabia
    # nos 8 GB e o llama.cpp abortava o offload -- subia tudo na CPU, a 5,9
    # tok/s em vez de 43. E 8k basta: quem carrega o historico longo agora e
    # a memoria de longo prazo, que busca e injeta so o que importa.
    # 0 = escolhe sozinho pelo tamanho do modelo (veja abaixo).
    [int]$Ctx = 0,
    [int]$Gpu = 999,         # todas as camadas na GPU
    [switch]$Rocm,           # usa o build ROCm (nao enumera a placa neste driver)
    [switch]$ComVisao,       # carrega o projetor de imagem
    # Qual cerebro local subir. O 9B responde melhor; o 4B responde rapido e
    # deixa a placa com folga -- com ele a visao cabe junto e o contexto pode
    # dobrar. Os dois sao Qwen3.5, entao o template corrigido, as ferramentas
    # e a chave de pensamento valem para os dois sem mudar nada.
    [ValidateSet("9b", "4b")]
    [string]$Modelo = "9b"
)

$ErrorActionPreference = "Stop"
$root  = Split-Path -Parent $PSScriptRoot
$rotulo = $Modelo.ToUpper()

# Contexto por tamanho de modelo. O 9B fica em 8k porque e o que sobra na
# placa depois dos 6,2 GB de pesos. O 4B ocupa 2,8 GB e permite 64k -- que
# nao e luxo: o OpenClaw, apontado para este mesmo servidor, monta um prompt
# de abertura (instrucoes do workspace + esquemas de ~70 plugins + memoria)
# que NAO cabe em 16k por slot. Com 8k ele respondia "context overflow"
# antes de chegar na primeira palavra.
if ($Ctx -eq 0) {
    $Ctx = if ($rotulo -eq "4B") { 65536 } else { 8192 }
}
$model = Join-Path $root "models\Qwen_Qwen3.5-$rotulo-Q4_K_M.gguf"
$visao = Join-Path $root "models\mmproj-Qwen_Qwen3.5-$rotulo-f16.gguf"

# --- escolhe o backend -------------------------------------------------
# Vulkan e o padrao e funciona. Cheguei a acusar o Vulkan de corromper a
# saida, mas o culpado era o chat template do Qwen; com o template corrigido
# a mesma GPU responde certo. O build ROCm fica disponivel atras de -Rocm,
# mas no driver Adrenalin consumidor ele nao enumera a placa
# (--list-devices devolve "(none)") e o llama-server cai para a CPU.
$vk = Join-Path $root "engine\llama\llama-server.exe"
$rocmExe = Get-ChildItem (Join-Path $root "engine\rocm") -Recurse -Filter "llama-server.exe" `
        -ErrorAction SilentlyContinue | Select-Object -First 1

if ($Rocm -and $rocmExe) {
    $server = $rocmExe.FullName
    $backend = "ROCm"
} else {
    $server = $vk
    $backend = "Vulkan"
}

if (-not (Test-Path $server)) { throw "llama-server nao encontrado em $server" }
if (-not (Test-Path $model)) {
    throw "modelo $rotulo nao encontrado em $model`nBaixe com: bash scripts/baixa-modelo.sh $Modelo"
}

$args = @(
    "--model", $model
    "--alias", "atlas"
    "--host", "127.0.0.1", "--port", $Port
    "--ctx-size", $Ctx
    "--n-gpu-layers", $Gpu
    "--jinja"
    "--chat-template-file", (Join-Path $root "templates\qwen3.5-fixed.jinja")
    "--reasoning-format", "deepseek"
    "--parallel", "2"
    "--metrics"
)

# Visao DESLIGADA por padrao no 9B. O mmproj dele ocupa 918 MB de VRAM, e
# com ele o Qwen de 6,2 GB mais o cache nao cabem nos 8 GB: o llama.cpp
# desiste do offload e sobe tudo na CPU, a 5,7 tok/s em vez de 43,5. Ler
# imagem e raro; responder rapido e o tempo todo.
#
# No 4B a conta muda: 2,8 GB de modelo + 640 MB de projetor deixam a placa
# com folga, entao -ComVisao cabe junto sem derrubar nada para a CPU.
#
# O corte de tamanho e proporcional ao proprio arquivo esperado, nao um
# numero fixo: o de 800 MB era do 9B e rejeitaria calado o projetor do 4B,
# que e menor de fabrica. So serve para pegar download pela metade.
$minProj = if ($rotulo -eq "4B") { 500MB } else { 800MB }
if ($ComVisao -and (Test-Path $visao) -and ((Get-Item $visao).Length -gt $minProj)) {
    $args += @("--mmproj", $visao)
    $temVisao = "sim"
} else {
    $temVisao = "nao"
}

# Quanto sobra na placa AGORA. A VRAM livre oscila com o navegador, o
# compositor do Windows e qualquer outro processo grafico -- por isso o
# mesmo comando que funcionou ontem pode derrubar o modelo para a CPU hoje,
# sem erro visivel, so ficando 7x mais lento. Entao conferimos antes.
$livre = 0
$saida = & $server --list-devices 2>&1 | Out-String
if ($saida -match '(\d+)\s*MiB free') { $livre = [int]$matches[1] }

$precisa = [math]::Round(((Get-Item $model).Length / 1MB) + ($Ctx / 1024.0 * 40) + 600)
if ($temVisao -eq "sim") {
    $precisa += [math]::Round((Get-Item $visao).Length / 1MB) + 60
}

# Quem esta no ar fica registrado em disco: a interface le daqui para mostrar
# "Local 4B" ou "Local 9B" no selo da resposta. Sem isso o dono troca de
# modelo e nao tem como saber qual respondeu -- foi exatamente o que
# aconteceu na troca entre Local e Claude.
$rotuloArq = Join-Path $root "logs\modelo.txt"
Set-Content -Path $rotuloArq -Value $rotulo -Encoding ascii

Write-Output "modelo: Qwen3.5-$rotulo | backend: $backend | visao: $temVisao | contexto: $Ctx"
Write-Output "VRAM: $livre MB livres, precisa de ~$precisa MB"

if ($livre -lt $precisa) {
    Write-Warning "NAO CABE NA VRAM. O modelo vai rodar na CPU (~6 tok/s em vez de ~43)."
    Write-Warning "Feche o que estiver usando a placa, ou reduza: -Ctx 4096"
    if ($temVisao -eq "sim") { Write-Warning "Ou tire a visao: nao passe -ComVisao" }
}

& $server @args
