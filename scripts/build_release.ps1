param(
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Invoke-Python {
    & $PythonExe @args
    if ($LASTEXITCODE -ne 0) {
        throw "Python falhou com codigo $LASTEXITCODE`: $($args -join ' ')"
    }
}

Push-Location $root
try {
    $files = @("main.py") + (
        Get-ChildItem -Path app,tests -Filter "*.py" |
            ForEach-Object { $_.FullName }
    )
    Invoke-Python -m py_compile @files
    Invoke-Python -m unittest discover -s tests -t .
    Invoke-Python -c "import app.interface; print('import ok')"
    Invoke-Python -m PyInstaller --noconfirm --clean VarreduraSistema.spec

    $package = Join-Path $root "dist\VarreduraSistema"
    $exe = Join-Path $package "VarreduraSistema.exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "Executavel nao foi gerado."
    }
    if (Test-Path -LiteralPath (Join-Path $package "settings.json")) {
        throw "settings.json nao pode estar no pacote."
    }

    $version = & $PythonExe -c "from app import __version__; print(__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel identificar a versao."
    }
    # versao.txt acompanha o exe: ao publicar o pacote na pasta oficial, os
    # sistemas abertos comparam este numero e avisam que ha versao nova.
    Set-Content -Encoding ascii -LiteralPath (Join-Path $package "versao.txt") -Value $version
    $zip = Join-Path $root "dist\VarreduraSistema-$version.zip"
    Compress-Archive -Path (Join-Path $package "*") -DestinationPath $zip -Force
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $zip
    $hash.Hash | Set-Content -Encoding ascii -LiteralPath "$zip.sha256"
    Write-Output "Pacote: $zip"
    Write-Output "SHA256: $($hash.Hash)"
}
finally {
    Pop-Location
}
