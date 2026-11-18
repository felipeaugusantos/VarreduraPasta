<#
Publica o pacote gerado por build_release.ps1 na pasta oficial de rede.

Uso:
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\publish_release.ps1
  ... -File .\scripts\publish_release.ps1 -DryRun     (so mostra o que faria)

Checagens antes de copiar:
  - dist\VarreduraSistema\versao.txt existe e bate com app\__init__.py;
  - o ZIP versionado confere com o .sha256;
  - a versao da pasta oficial nao e MAIS NOVA que a que sera publicada (exceto com -Force);
  - settings.json nunca vai junto.
O versao.txt e copiado por ultimo: os sistemas abertos so avisam "Versao X disponivel"
depois que todos os arquivos ja estao la.
#>
param(
    [string]$Destination = "\\citel-fileserve\Modem\SISTEMA_COPIA",
    [string]$Source = (Join-Path $PSScriptRoot "..\dist\VarreduraSistema"),
    [switch]$DryRun,
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Source = (Resolve-Path $Source).Path

function Parse-Version([string]$text) { [version]($text.Trim()) }

$codeVersion = ((Get-Content (Join-Path $root "app\__init__.py") -Raw) | Select-String '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$packageVersion = (Get-Content (Join-Path $Source "versao.txt") -Raw).Trim()
if ($packageVersion -ne $codeVersion) {
    throw "versao.txt do pacote ($packageVersion) difere de app\__init__.py ($codeVersion). Gere o build de novo."
}

$zip = Get-ChildItem (Join-Path $root "dist") -Filter "*$codeVersion*.zip" | Select-Object -First 1
if ($zip) {
    $hashFile = "$($zip.FullName).sha256"
    if (Test-Path $hashFile) {
        $expected = ((Get-Content $hashFile -Raw).Trim() -split '\s+')[0]
        $actual = (Get-FileHash $zip.FullName -Algorithm SHA256).Hash
        if ($expected -ne $actual) { throw "SHA-256 do ZIP nao confere: $($zip.Name)" }
        Write-Host "SHA-256 do ZIP confere."
    }
}

$officialFile = Join-Path $Destination "versao.txt"
if ((Test-Path $officialFile) -and -not $Force) {
    $official = Parse-Version (Get-Content $officialFile -Raw)
    if ($official -gt (Parse-Version $packageVersion)) {
        throw "A pasta oficial ja tem a versao $official, mais nova que $packageVersion. Use -Force se for proposital."
    }
    Write-Host "Versao oficial atual: $official -> nova: $packageVersion"
}

$files = Get-ChildItem $Source -Recurse -File | Where-Object { $_.Name -ne "settings.json" -and $_.Name -ne "versao.txt" }
Write-Host "$($files.Count) arquivo(s) para copiar para $Destination"
if ($DryRun) { Write-Host "DryRun: nada foi copiado."; return }

foreach ($file in $files) {
    $relative = $file.FullName.Substring($Source.Length).TrimStart('\')
    $target = Join-Path $Destination $relative
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    Copy-Item $file.FullName $target -Force
}
Copy-Item (Join-Path $Source "versao.txt") $officialFile -Force
Write-Host "Publicado: versao $packageVersion."
