@echo off
setlocal EnableExtensions
rem Iniciador do Sistema de Acompanhamento de Copia.
rem Abre o sistema a partir de uma copia local (por usuario), nao da pasta de
rem rede. Assim a pasta de rede nunca fica travada e uma versao nova pode ser
rem publicada com operadores usando o sistema. Quem estiver na versao antiga
rem segue nela ate fechar; na proxima abertura entra na versao publicada.

set "ORIGEM=%~dp0"
set "VER="
if exist "%ORIGEM%versao.txt" set /p VER=<"%ORIGEM%versao.txt"
if not defined VER goto :direto

set "BASE=%LOCALAPPDATA%\VarreduraSistema\app"
set "DESTINO=%BASE%\%VER%"
if exist "%DESTINO%\VarreduraSistema.exe" goto :limpeza

rem Copia para uma pasta temporaria e so entao renomeia: uma copia interrompida
rem nunca e usada como se estivesse completa.
if exist "%DESTINO%.tmp" rd /s /q "%DESTINO%.tmp"
robocopy "%ORIGEM%." "%DESTINO%.tmp" /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP /XF *.bat >nul
if errorlevel 8 goto :direto
if not exist "%DESTINO%\VarreduraSistema.exe" ren "%DESTINO%.tmp" "%VER%"
if not exist "%DESTINO%\VarreduraSistema.exe" goto :direto

:limpeza
rem Apaga copias locais de versoes antigas que nao estao em uso. Executavel em
rem uso nao abre para escrita; nesse caso a pasta e mantida.
for /d %%D in ("%BASE%\*") do call :limpar "%%~fD"
start "" /D "%DESTINO%" "%DESTINO%\VarreduraSistema.exe"
exit /b 0

:direto
rem Sem versao.txt ou sem conseguir copiar: abre direto da pasta de origem.
start "" /D "%ORIGEM%" "%ORIGEM%VarreduraSistema.exe"
exit /b 0

:limpar
if /i "%~1"=="%DESTINO%" exit /b 0
if not exist "%~1\VarreduraSistema.exe" exit /b 0
type nul 2>nul >>"%~1\VarreduraSistema.exe" || exit /b 0
rd /s /q "%~1" 2>nul
exit /b 0
