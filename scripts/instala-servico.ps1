# Faz a Atlas ligar junto com o PC -- o "ninguem liga geladeira toda manha".
#
# Cria duas tarefas no Agendador do Windows:
#   Atlas-Cerebro  -> llama-server, no logon, reinicia sozinho se cair
#   Atlas-Voz      -> o loop ouve/pensa/fala (opcional, -ComVoz)
#
# Nao precisa de admin: as tarefas sao do usuario atual.
# Para remover:  .\instala-servico.ps1 -Remover

param(
    [switch]$ComVoz,
    [switch]$Remover
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if ($Remover) {
    foreach ($n in "Atlas-Cerebro", "Atlas-Voz") {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $n -Confirm:$false
            Write-Output "removida: $n"
        }
    }
    return
}

$gatilho = New-ScheduledTaskTrigger -AtLogOn
# Se o processo morrer, o Agendador sobe de novo em 1 min, indefinidamente.
$config = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

$acaoCerebro = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -File `"$root\scripts\servidor.ps1`"" `
    -WorkingDirectory $root

Register-ScheduledTask -TaskName "Atlas-Cerebro" -Trigger $gatilho `
    -Action $acaoCerebro -Settings $config -Force | Out-Null
Write-Output "instalada: Atlas-Cerebro (llama-server no logon)"

if ($ComVoz) {
    $acaoVoz = New-ScheduledTaskAction `
        -Execute "pythonw.exe" `
        -Argument "-m atlas.main" `
        -WorkingDirectory $root
    Register-ScheduledTask -TaskName "Atlas-Voz" -Trigger $gatilho `
        -Action $acaoVoz -Settings $config -Force | Out-Null
    Write-Output "instalada: Atlas-Voz (microfone no logon)"
}

Write-Output ""
Write-Output "Pronto. Testar agora sem reiniciar:"
Write-Output "  Start-ScheduledTask -TaskName Atlas-Cerebro"
