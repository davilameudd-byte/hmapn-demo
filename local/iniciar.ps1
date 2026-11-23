$ErrorActionPreference = 'Stop'
$serverFile = Join-Path $PSScriptRoot 'servidor.py'
$repoDir = Split-Path $PSScriptRoot -Parent
$settingsFile = Join-Path $PSScriptRoot 'ajustes-locais.json'
if (-not $env:HMAPN_DATA_DIR) { $env:HMAPN_DATA_DIR = Join-Path $repoDir 'runtime' }
if (Test-Path -LiteralPath $settingsFile) {
    $savedSettings = Get-Content -LiteralPath $settingsFile -Raw | ConvertFrom-Json
    if (-not $env:HMAPN_INPUT_DIR -and $savedSettings.pasta_relatorios) { $env:HMAPN_INPUT_DIR = $savedSettings.pasta_relatorios }
    if (-not $env:HMAPN_PORT -and $savedSettings.porta) { $env:HMAPN_PORT = [string]$savedSettings.porta }
}
$serverPort = if ($env:HMAPN_PORT) { [int]$env:HMAPN_PORT } else { 8765 }
if ($serverPort -lt 1024 -or $serverPort -gt 65535) { throw 'Escolha uma porta entre 1024 e 65535.' }
$address = 'http://127.0.0.1:' + $serverPort
$pidFile = Join-Path $env:HMAPN_DATA_DIR 'servidor-processo.json'
New-Item -ItemType Directory -Path $env:HMAPN_DATA_DIR -Force | Out-Null
function Get-LocalHealth {
    try { return Invoke-RestMethod ($address + '/api/health') -TimeoutSec 2 } catch { return $null }
}
try {
    $runningHealth = Get-LocalHealth
    if ($runningHealth -and $runningHealth.mode -eq 'local' -and (Test-Path -LiteralPath $pidFile)) {
        $savedProcess = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
        $runningProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$savedProcess.pid) -ErrorAction SilentlyContinue
        if ($runningProcess -and $runningProcess.CommandLine.Contains($serverFile)) {
            Start-Process $address
            exit 0
        }
    }
    if ($runningHealth) { throw ('A porta ' + $serverPort + ' ja esta em uso. Encerre o outro painel ou escolha outra porta em ajustes-locais.json.') }
    $pythonExe = $null
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $resolvedPython = & $pyLauncher.Source -3 -c 'import sys; print(sys.executable) if sys.version_info >= (3,10) else sys.exit(1)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolvedPython -and (Test-Path -LiteralPath $resolvedPython.Trim())) { $pythonExe = $resolvedPython.Trim() }
    }
    if (-not $pythonExe) {
        $candidates = @((Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source))
        $candidates += @(Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python*\python.exe') -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
        foreach ($candidate in $candidates) {
            if (-not $candidate -or $candidate -like '*WindowsApps*') { continue }
            & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $pythonExe = $candidate; break }
        }
    }
    if (-not $pythonExe) { throw 'Python 3.10 ou superior nao encontrado. Ele executa o servidor e o banco SQLite, sem pacotes extras. Instale em https://www.python.org/downloads/windows/.' }
    $process = Start-Process -FilePath $pythonExe -ArgumentList @('-u', ('"' + $serverFile + '"')) -WorkingDirectory $repoDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $env:HMAPN_DATA_DIR 'inicializacao.log') -RedirectStandardError (Join-Path $env:HMAPN_DATA_DIR 'erros.log') -PassThru
    @{pid=$process.Id; server=$serverFile; port=$serverPort} | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8
    $ready = $false
    for ($attempt=0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        $health = Get-LocalHealth
        if ($health -and $health.mode -eq 'local') { $ready=$true; break }
        if ($process.HasExited) { break }
    }
    if (-not $ready) { throw ('O servico nao iniciou. Consulte ' + (Join-Path $env:HMAPN_DATA_DIR 'erros.log')) }
    Start-Process $address
    Write-Host 'Painel iniciado. Para encerrar, use ENCERRAR_PAINEL.bat.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
