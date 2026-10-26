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

function New-PackageZip {
    # Compress-Archive falha quando antivirus/indexador esta com um arquivo aberto
    # (visto em _tcl_data	zdata). Aqui cada arquivo e aberto permitindo
    # compartilhamento e o ZIP inteiro e refeito algumas vezes se ainda falhar.
    param([string]$Source, [string]$Destination)

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $sourceRoot = (Resolve-Path -LiteralPath $Source).Path.TrimEnd("\")
    $attempts = 5
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        if (Test-Path -LiteralPath $Destination) {
            Remove-Item -LiteralPath $Destination -Force
        }
        try {
            $archive = [System.IO.Compression.ZipFile]::Open($Destination, "Create")
            try {
                foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -Recurse -File) {
                    $entryName = $file.FullName.Substring($sourceRoot.Length + 1).Replace("\", "/")
                    $entry = $archive.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
                    $input = [System.IO.File]::Open(
                        $file.FullName,
                        [System.IO.FileMode]::Open,
                        [System.IO.FileAccess]::Read,
                        [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
                    try {
                        $output = $entry.Open()
                        try { $input.CopyTo($output) } finally { $output.Dispose() }
                    }
                    finally { $input.Dispose() }
                }
            }
            finally { $archive.Dispose() }
            return
        }
        catch {
            if ($attempt -eq $attempts) { throw }
            Write-Warning "Falha ao gerar o ZIP (tentativa $attempt de $attempts): $($_.Exception.Message)"
            Start-Sleep -Seconds 3
        }
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
    # Iniciador: abre o sistema de uma copia local, sem travar a pasta de rede.
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Iniciar_VarreduraSistema.bat") -Destination $package -Force
    $zip = Join-Path $root "dist\VarreduraSistema-$version.zip"
    New-PackageZip -Source $package -Destination $zip
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $zip
    $hash.Hash | Set-Content -Encoding ascii -LiteralPath "$zip.sha256"
    Write-Output "Pacote: $zip"
    Write-Output "SHA256: $($hash.Hash)"
}
finally {
    Pop-Location
}
