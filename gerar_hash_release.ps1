$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$exePath = Join-Path $projectRoot "dist\AjusteInsert.exe"
$versionPath = Join-Path $projectRoot "app\version.py"

if (-not (Test-Path $exePath)) {
    Write-Error "Executavel nao encontrado em: $exePath"
}

if (-not (Test-Path $versionPath)) {
    Write-Error "Arquivo de versao nao encontrado em: $versionPath"
}

$versionContent = Get-Content -Raw -Path $versionPath

function Get-PythonConst {
    param(
        [string]$Content,
        [string]$Name
    )
    $pattern = "(?m)^$Name\s*=\s*`"([^`"]+)`""
    $match = [regex]::Match($Content, $pattern)
    if (-not $match.Success) {
        Write-Error "Constante $Name nao encontrada em app/version.py"
    }
    return $match.Groups[1].Value
}

$appName = Get-PythonConst -Content $versionContent -Name "APP_NAME"
$appVersion = Get-PythonConst -Content $versionContent -Name "APP_VERSION"
$releaseDate = Get-PythonConst -Content $versionContent -Name "APP_RELEASE_DATE"
$exeName = Split-Path -Leaf $exePath
$releaseDir = Join-Path $projectRoot "releases\v$appVersion"
$releaseExePath = Join-Path $releaseDir $exeName
$outputPath = Join-Path $releaseDir "SHA256SUMS.txt"
$rootOutputPath = Join-Path $projectRoot "SHA256SUMS.txt"

New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
Copy-Item -Path $exePath -Destination $releaseExePath -Force

$hash = Get-FileHash -Algorithm SHA256 -Path $releaseExePath

$lines = @(
    "# $appName",
    "# Versao: $appVersion",
    "# Data da release: $releaseDate",
    "# Algoritmo: SHA256",
    "$($hash.Hash.ToLowerInvariant())  $exeName"
)

$lines | Set-Content -Path $outputPath -Encoding UTF8
$lines | Set-Content -Path $rootOutputPath -Encoding UTF8

Write-Host "Executavel da release: $releaseExePath"
Write-Host "SHA256 gerado em: $outputPath"
Write-Host "SHA256 atual da raiz: $rootOutputPath"
Write-Host "$($hash.Hash.ToLowerInvariant())  $exeName"
