param(
    [ValidateSet('whisper', 'parakeet')]
    [string]$AsrBackend,
    # Habilita deliberadamente registros privados de texto e WAV para comparação.
    [switch]$AsrDiagnostics
)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Venv ausente. Execute .\scripts\install.ps1'
}
$overrides = @{}
if ($AsrBackend) { $overrides['ASR_BACKEND'] = $AsrBackend }
if ($AsrDiagnostics) {
    $overrides['ASR_DIAGNOSTICS_ENABLED'] = 'true'
    $overrides['ASR_SAVE_AUDIO'] = 'true'
    $overrides['LOG_TRANSCRIPTS'] = 'true'
}
$previous = @{}
foreach ($key in $overrides.Keys) {
    $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    [Environment]::SetEnvironmentVariable($key, $overrides[$key], 'Process')
}
Push-Location -LiteralPath $projectDir
try {
    & $pythonExe (Join-Path $projectDir 'server\main.py')
    $serverExitCode = $LASTEXITCODE
} finally {
    Pop-Location
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
}
exit $serverExitCode
