param(
    [ValidateSet('auto','text','endpoint','local','esp32')]
    [Alias('Profile')]
    [string]$DependencyProfile = 'auto',
    [string]$PythonCommand = 'python'
)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectDir '.venv\Scripts\python.exe'
Push-Location -LiteralPath $projectDir
try {
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        & $PythonCommand -m venv (Join-Path $projectDir '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Não foi possível criar o venv.' }
    }
    & $pythonExe -m pip install -r requirements-core.txt
    if ($LASTEXITCODE -ne 0) { throw 'Falha na instalação das dependências básicas.' }
    $envFile = Join-Path $projectDir '.env'
    if (-not (Test-Path -LiteralPath $envFile)) {
        Copy-Item -LiteralPath (Join-Path $projectDir '.env.example') -Destination $envFile
        Write-Host '.env criado do exemplo. Configure antes de iniciar.'
    }
    $manifests = & $pythonExe (Join-Path $PSScriptRoot 'install_profile.py') $DependencyProfile
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao selecionar capacidades.' }
    foreach ($manifest in $manifests) {
        & $pythonExe -m pip install -r $manifest
        if ($LASTEXITCODE -ne 0) { throw 'Falha na instalação de uma capacidade.' }
    }
    Write-Host 'Dependências instaladas. Perfil não altera .env, modelos ou prompts existentes.'
    Write-Host 'Confira a configuração; início manual: .\scripts\start.ps1'
} finally {
    Pop-Location
}
# Sem instalação de serviços externos, download de modelos, startup ou testes.
