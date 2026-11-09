# Abre um endereco publico para a Atlas, via Cloudflare Tunnel.
#
# Por que tunel e nao abrir porta no roteador: nada de port forwarding, nada
# de IP fixo, nada de certificado para gerar -- e a sua maquina que sai para
# a Cloudflare, entao funciona atras de CGNAT e do firewall do provedor. O
# endereco sai pronto em HTTPS.
#
# O tunel rapido ("quick tunnel") nao precisa de conta. Em troca, o endereco
# muda a cada vez que voce liga.
#
#   .\scripts\publicar-fora.ps1          # endereco publico, COM senha
#   .\scripts\publicar-fora.ps1 -Aberto  # sem senha (nao recomendado)

param(
    [int]$Porta = 8800,
    [switch]$Aberto
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$cf   = Join-Path $root "engine\cloudflared.exe"

if (-not (Test-Path $cf)) {
    throw "cloudflared nao encontrado. Baixe em engine\cloudflared.exe"
}

# O servidor web precisa estar no ar ANTES do tunel, senao a Cloudflare
# publica um endereco que responde 502.
try {
    Invoke-WebRequest "http://127.0.0.1:$Porta/api/estado" -TimeoutSec 3 -UseBasicParsing | Out-Null
} catch {
    Write-Warning "A Atlas web nao esta no ar na porta $Porta."
    Write-Warning "Suba antes:  python -m atlas.web --porta $Porta" + $(if (-not $Aberto) { " --senha" })
    return
}

if (-not $Aberto) {
    Write-Output "Lembre-se: o endereco publico so e seguro com token."
    Write-Output "Se subiu a web sem --senha, qualquer um que receba o link entra."
    Write-Output ""
}

Write-Output "Abrindo o tunel... o endereco aparece abaixo em alguns segundos."
Write-Output "Ctrl+C encerra e o endereco deixa de existir."
Write-Output ""

& $cf tunnel --url "http://127.0.0.1:$Porta" --no-autoupdate
