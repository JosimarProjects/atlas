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
    [int]$Ctx = 8192,
    [int]$Gpu = 999,         # todas as camadas na GPU
    [switch]$Rocm,           # usa o build ROCm (nao enumera a placa neste driver)
    [switch]$ComVisao        # carrega o projetor de imagem (+918 MB de VRAM)
)

$ErrorActionPreference = "Stop"
$root  = Split-Path -Parent $PSScriptRoot
$model = Join-Path $root "models\Qwen_Qwen3.5-9B-Q4_K_M.gguf"
$visao = Join-Path $root "models\mmproj-Qwen_Qwen3.5-9B-f16.gguf"

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
if (-not (Test-Path $model))  { throw "modelo nao encontrado em $model" }

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

# Visao DESLIGADA por padrao. O mmproj ocupa 918 MB de VRAM, e com ele o
# Qwen de 6,2 GB mais o cache nao cabem nos 8 GB: o llama.cpp desiste do
# offload e sobe tudo na CPU, a 5,7 tok/s em vez de 43,5. Ler imagem e raro;
# responder rapido e o tempo todo. Para analisar uma imagem, suba uma
# segunda instancia com -ComVisao noutra porta.
if ($ComVisao -and (Test-Path $visao) -and ((Get-Item $visao).Length -gt 800MB)) {
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
if ($temVisao -eq "sim") { $precisa += 950 }

Write-Output "backend: $backend | visao: $temVisao | contexto: $Ctx"
Write-Output "VRAM: $livre MB livres, precisa de ~$precisa MB"

if ($livre -lt $precisa) {
    Write-Warning "NAO CABE NA VRAM. O modelo vai rodar na CPU (~6 tok/s em vez de ~43)."
    Write-Warning "Feche o que estiver usando a placa, ou reduza: -Ctx 4096"
    if ($temVisao -eq "sim") { Write-Warning "Ou tire a visao: nao passe -ComVisao" }
}

& $server @args
