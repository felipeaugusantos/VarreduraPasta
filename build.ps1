$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Python da .venv nao encontrado em: $python"
}

& $python -m pytest
& $python -m PyInstaller AjusteInsert.spec --noconfirm

Write-Host ""
Write-Host "Build concluido: dist\AjusteInsert.exe"
