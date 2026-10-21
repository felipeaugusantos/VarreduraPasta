# CLAUDE.md

Este arquivo orienta o Claude Code ao trabalhar neste repositorio.

## Projeto

Sistema Python/Tkinter para Windows que acompanha copias/fechamentos de versoes.
O sistema varre pastas de projetos, valida `FileVersion` e `ProductVersion` de
executaveis/DLLs, executa BATs de fechamento, copia arquivos ZIP para destinos
de rede e registra auditoria das operacoes.

Versao atual no codigo: `app/__init__.py`.

## Como executar

```powershell
.\.venv\Scripts\python.exe main.py
```

O projeto usa biblioteca padrao do Python para a aplicacao. O PyInstaller fica
no ambiente virtual apenas para gerar o executavel.

## Validacao obrigatoria

Antes de considerar uma alteracao pronta, rodar:

```powershell
$files = @('main.py') + (Get-ChildItem -Path app,tests -Filter '*.py' | ForEach-Object { $_.FullName })
.\.venv\Scripts\python.exe -m py_compile @files
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
.\.venv\Scripts\python.exe -c "import app.interface; print('ok')"
```

## Build do executavel

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -PythonExe .\.venv\Scripts\python.exe
```

O executavel final fica em:

```text
dist\VarreduraSistema\VarreduraSistema.exe
```

O script tambem gera o ZIP versionado e seu arquivo `.sha256`.

Nao distribuir `settings.json` junto do pacote. Dados persistentes ficam em
`%LOCALAPPDATA%\VarreduraSistema`; o pacote pode ser executado sem instalador.

## Estrutura principal

| Arquivo | Responsabilidade |
|---|---|
| `main.py` | Entrada da aplicacao. Instancia `VersionScannerApp`. |
| `app/interface.py` | UI Tkinter, menus, telas, botoes e orquestracao. |
| `app/scanner.py` | Varredura dos projetos e montagem dos resultados. |
| `app/version_reader.py` | Leitura de `FileVersion`/`ProductVersion` via WinAPI. |
| `app/actions.py` | Fechamento, copia, limpeza de pasta e validacoes operacionais. |
| `app/closing_runner.py` | Fila de fechamentos em segundo plano (sem Tkinter). |
| `app/autcom_test.py` | Preparo da pasta de teste e validacao da abertura do Autcom. |
| `app/pattern.py` | Padrao de arquivos por projeto. |
| `app/settings.py` | Persistencia em `settings.json`. |
| `app/audit.py` | Leitura/filtro dos logs para tela de Auditoria. |
| `app/logging_utils.py` | Escrita dos logs mensais. |
| `app/script_monitor.py` | Monitoramento diario de scripts de banco modelo. |
| `app/jenkins.py` | Integracao Jenkins local via HTTP/Basic Auth. |
| `app/svn_search.py` | Consulta SVN por revisao/requisito. |
| `app/runtime.py` | Caminhos em dev/PyInstaller e data de geracao do exe. |
| `app/config.py` | Constantes padrao e regras de negocio. |

## Regras de negocio importantes

- Diretorio-base padrao: `C:\VERSOES_FECHADAS`.
- Varredura automatica a cada 5 minutos.
- Projetos com `_CLOUD` sao Cloud; sem `_CLOUD` sao Local.
- `Fechamento Local` so fica disponivel para projeto Local.
- `Fechamento Cloud` so fica disponivel para projeto Cloud.
- `Copiar Local`: `Autcom` precisa estar abaixo de 100 MB.
- `Copiar Cloud`: `Autcom` precisa ter pelo menos 150 MB.
- `Testar Autcom` mapeia as partes 5 e 6 da versao para
  `PastaTesteCopia_<parte5>_<parte6>`.
- A copia envia somente arquivos `.zip`.
- A copia nao envia `comandosCMD`.
- A limpeza pos-copia remove da origem somente os ZIPs copiados (e nao
  alterados depois da copia); o resto e mantido e listado ao operador.
- `Limpar Pasta` (manual) remove tudo da raiz, preservando `comandosCMD`.
- `AutBan.exe`, `libAutban.dll` e `libAutban.exe` sao alternativas do grupo Autban.
- `Auttin.exe`, `libAuttin.dll` e `libAuttin.exe` sao alternativas do grupo Auttin.
- `libFuncoes.dll` e opcional.
- Pastas de teste podem ser ignoradas pela tela de configuracao.

## Caminhos de rede conhecidos

Destinos/bases de copia:

```text
\\citel-fileserve\Modem\VERSOES\379_Versoes\379.48.02\379.48.2.30
\\citel-fileserve\Modem\_VERSOES_INDIVIDUAIS
```

Monitoramento de scripts:

```text
\\citel-fileserve\Modem\WEBSERVICE_CITEL\Scripts
```

O caminho base SVN deve ser configurado pelo usuario em:

```text
Configuracoes > Diretorio-base > Caminho base SVN
```

## Fluxos principais

### Varredura

`interface.py` inicia uma thread que chama `scan_projects()`.
Os resultados alimentam a tabela principal sem travar a UI.

### Fechamento

Os botoes de fechamento validam os projetos selecionados (aceita varios do
mesmo tipo) e os colocam na fila de `app/closing_runner.py`. O BAT
`comandosCMD\_FechamentoArquivos.bat` roda oculto, ate 2 por vez, com saida em
`logs\fechamentos_bat`. A coluna `Fechamento` da tabela consulta a fila a cada
segundo. O sistema grava auditoria e aprende o padrao de arquivos do projeto.
Enquanto o projeto estiver na fila ou em andamento, copia, limpeza, teste do
Autcom e validacao ficam bloqueados (`_block_if_closing_active` em
`app/actions.py`, alem dos botoes desabilitados na interface).

### Copia

A copia procura o destino nas raizes configuradas, mostra origem/destino antes
de continuar, copia ZIPs, valida a copia e remove da origem somente os ZIPs
copiados. Arquivos criticos como `autcom.zip` sao verificados com SHA-256.

### Auditoria

Logs ficam na pasta `logs` ao lado do executavel. A tela `Auditoria > Logs`
mostra os registros em tabela e permite filtros.

### Monitoramento de scripts

`Monitoramento > Scripts Banco Modelo` verifica uma vez ao dia se existem novos
scripts na pasta configurada. Scripts novos ficam pendentes ate o operador clicar
em `Marcar como feito`.

### Consulta SVN

`Monitoramento > Consulta SVN` busca por revisao, requisito ou ambos.
Preferencialmente usa o comando `svn log`. Se o comando `svn` nao existir e o
caminho base for uma pasta local, busca o requisito em nomes/conteudo de arquivos.

### Jenkins

Menu `Jenkins` permite configurar URL, usuario e senha, abrir o painel local e
ver status dos jobs. A senha nao deve ser escrita em logs.

Padrao esperado:

```text
http://localhost:8080
```

## Cuidados ao alterar

- Nao alterar regras de tamanho Local/Cloud sem pedido explicito.
- Nao hardcodar senha do Jenkins.
- Nao incluir `settings.json` no pacote distribuido.
- Nao bloquear a thread principal do Tkinter com operacoes de rede, copia,
  varredura, limpeza ou SVN. Usar thread + queue/polling.
- Nao chamar metodos Tkinter diretamente de thread secundaria.
- Preservar logs e mensagens em portugues.
- Usar apenas biblioteca padrao para novas funcionalidades do sistema.
- Ao editar manualmente, preferir mudancas pequenas e cobertas por testes.

## Git

Branch atual usada no projeto:

```text
claude/validate-project-mgifU
```

Repositorio remoto:

```text
https://github.com/felipeaugusantos/VarreduraPasta.git
```

Observacao: historicamente o `master` remoto pode ter conteudo diferente. Evitar
force push ou substituicao de `master` sem confirmacao explicita do usuario.
