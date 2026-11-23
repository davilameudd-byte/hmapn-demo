$ErrorActionPreference = 'Stop'
$dataDir = if ($env:HMAPN_DATA_DIR) { $env:HMAPN_DATA_DIR } else { Join-Path (Split-Path $PSScriptRoot -Parent) 'runtime' }
$pidFile = Join-Path $dataDir 'servidor-processo.json'
try {
    if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'Nenhum servidor iniciado por este atalho.'; exit 0 }
    $savedProcess = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $serverFile = Join-Path $PSScriptRoot 'servidor.py'
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$savedProcess.pid) -ErrorAction SilentlyContinue
    if (-not $process) { Write-Host 'O painel ja esta encerrado.'; exit 0 }
    if ($savedProcess.server -ne $serverFile -or -not $process.CommandLine.Contains($serverFile)) { throw 'O processo nao corresponde a este painel. Nada foi encerrado.' }
    $address = 'http://127.0.0.1:' + [int]$savedProcess.port
    $health = Invoke-RestMethod ($address + '/api/health') -TimeoutSec 3
    Invoke-RestMethod ($address + '/api/shutdown') -Method Post -Headers @{'X-HMAPN-Token'=$health.csrfToken} -TimeoutSec 10 | Out-Null
    Wait-Process -Id ([int]$savedProcess.pid) -Timeout 20 -ErrorAction SilentlyContinue
    if (Get-Process -Id ([int]$savedProcess.pid) -ErrorAction SilentlyContinue) { throw 'O servidor ainda esta encerrando. Aguarde antes de copiar o banco.' }
    Write-Host 'Painel encerrado. Agora pode copiar a pasta de dados para backup.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
