# Liga a Atlas inteira, na ordem que garante o cerebro na VRAM.
#
# A ordem importa. A VRAM livre oscila com o navegador e o compositor do
# Windows, e o llama.cpp, quando nao cabe, nao avisa: ele sobe o modelo na
# CPU e segue respondendo a 6 tok/s em vez de 43. Por isso o cerebro entra
# PRIMEIRO, enquanto a placa esta mais vazia, e a memoria depois -- na CPU,
# onde nao disputa nada.
#
#   .\scripts\ligar.ps1            # cerebro + memoria
#   .\scripts\ligar.ps1 -ComVisao  # tambem le imagens (+950 MB de VRAM)
#   .\scripts\ligar.ps1 -Parar     # desliga tudo

param(
    [switch]$ComVisao,
    [switch]$Parar,
    [int]$Ctx = 8192
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot

function Esperar($url, $segundos = 180) {
    for ($i = 0; $i -lt $segundos; $i++) {
        try {
            Invoke-WebRequest -Uri $url -TimeoutSec 2 -UseBasicParsing | Out-Null
            return $true
        } catch { Start-Sleep -Seconds 1 }
    }
    return $false
}

Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
if ($Parar) { Write-Output "Atlas desligada."; return }

# --- 1. cerebro, na GPU ------------------------------------------------
$argsCerebro = @("-NoProfile", "-File", "$root\scripts\servidor.ps1", "-Ctx", $Ctx)
if ($ComVisao) { $argsCerebro += "-ComVisao" }
Start-Process powershell -ArgumentList $argsCerebro `
    -RedirectStandardOutput "$root\logs\servidor.log" `
    -RedirectStandardError "$root\logs\servidor.err" -WindowStyle Hidden

if (-not (Esperar "http://127.0.0.1:8080/v1/models")) {
    Write-Error "O cerebro nao subiu. Veja logs\servidor.log"
    return
}

# --- 2. memoria, na CPU ------------------------------------------------
Start-Process powershell -ArgumentList @("-NoProfile", "-File", "$root\scripts\servidor-memoria.ps1") `
    -RedirectStandardOutput "$root\logs\embed.log" `
    -RedirectStandardError "$root\logs\embed.err" -WindowStyle Hidden

if (-not (Esperar "http://127.0.0.1:8083/v1/models")) {
    Write-Warning "A memoria semantica nao subiu. A Atlas funciona, mas so com busca por palavra-chave."
}

# --- 3. conferir que o cerebro ficou mesmo na placa --------------------
# Nao usamos Get-Counter aqui: ele leva dezenas de segundos nesta maquina.
# O proprio servidor ja compara VRAM livre contra o necessario na subida, e
# o llama.cpp registra "failed to fit" quando desiste do offload -- ler o
# log e instantaneo e diz a mesma coisa.
$log = Get-Content "$root\logs\servidor.log" -Raw -ErrorAction SilentlyContinue
$naCpu = $log -match "failed to fit"

Write-Output ""
Write-Output "Atlas no ar."
Write-Output "  cerebro : http://127.0.0.1:8080"
Write-Output "  memoria : http://127.0.0.1:8083  (CPU)"
if ($naCpu) {
    Write-Warning "O cerebro NAO coube na placa e subiu na CPU -- ~7x mais devagar."
    Write-Warning "Feche o que usa a GPU e rode de novo, ou use: -Ctx 4096"
} else {
    Write-Output "  (cerebro na GPU)"
}
Write-Output ""
Write-Output "  python -m atlas.main --texto"
