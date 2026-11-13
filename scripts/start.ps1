$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Venv ausente. Execute .\scripts\install.ps1'
}
Push-Location -LiteralPath $projectDir
try {
    & $pythonExe (Join-Path $projectDir 'server\main.py')
    $serverExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $serverExitCode
