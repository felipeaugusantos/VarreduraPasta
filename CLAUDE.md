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

O build tambem grava `versao.txt` (so o numero) dentro de `dist\VarreduraSistema`.
Para publicar, copie o conteudo dessa pasta para `\\citel-fileserve\Modem\SISTEMA_COPIA`
(o `versao.txt` vai junto): os sistemas abertos avisam "Versao X disponivel"
(`app/update_check.py`). Nao da para sobrescrever o exe/`_internal` enquanto
algum operador estiver com o sistema aberto a partir dessa pasta.

O icone (`assets/app-icon.png` e `.ico`) e gerado por
`.\.venv\Scripts\python.exe scripts\generate_icon.py` (usa o Pillow da .venv,
so para gerar o arquivo; o sistema continua so com biblioteca padrao).

Nao distribuir `settings.json` junto do pacote. Dados persistentes ficam em
`%LOCALAPPDATA%\VarreduraSistema`; o pacote pode ser executado sem instalador.

## Estrutura principal

| Arquivo | Responsabilidade |
|---|---|
| `main.py` | Entrada da aplicacao. Instancia `VersionScannerApp`. |
| `app/interface.py` | UI Tkinter, menus, telas, botoes e orquestracao. |
| `app/theme.py` | Paleta, fontes e estilos ttk (tema `clam`), aplicados no Tk principal. |
| `app/scanner.py` | Varredura dos projetos e montagem dos resultados. |
| `app/version_reader.py` | Leitura de `FileVersion`/`ProductVersion` via WinAPI. |
| `app/actions.py` | Fechamento, copia, limpeza de pasta e validacoes operacionais. |
| `app/closing_runner.py` | Fila de fechamentos em segundo plano (sem Tkinter). |
| `app/zip_memory.py` | Maior total de ZIPs ja gerado por pasta (`zip_memory.json`). |
| `app/project_lock.py` | Trava por projeto entre operadores do TS (`.varredura.lock` na pasta). |
| `app/error_handler.py` | Erros inesperados: `logs\erros.log`, Auditoria e mensagem na tela. |
| `app/shared_audit.py` | Copia da Auditoria na pasta de controle (fila em segundo plano). |
| `app/update_check.py` | Aviso de versao nova (le `versao.txt` da pasta oficial do exe). |
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
  `PastaTesteCopia_<parte5>_<parte6>`. O login de teste (padrao `998`/`998`/`001`
  em `app/config.py`) e configuravel por operador em `settings.json`
  (`autcom_test_login`, tela Configuracoes); so letras e numeros, digitados com
  `VkKeyScanW` (respeita Shift). Nunca gravar a senha em log.
- A copia envia somente arquivos `.zip`.
- A copia nao envia `comandosCMD`.
- A limpeza pos-copia remove da origem somente os ZIPs copiados (e nao
  alterados depois da copia); o resto e mantido e listado ao operador.
- `Limpar Pasta` (manual) remove tudo da raiz, preservando `comandosCMD`.
- `AutBan.exe`, `libAutban.dll` e `libAutban.exe` sao alternativas do grupo Autban.
- `Auttin.exe`, `libAuttin.dll` e `libAuttin.exe` sao alternativas do grupo Auttin.
- `libFuncoes.dll` e opcional.
- Pastas de teste podem ser ignoradas pela tela de configuracao.
- Copias especificas tem nome com 5 partes (ex.: `379.48.2.40.5`): FileVersion
  = 4 primeiras partes (`379.48.02.40`) e ProductVersion = 4 ultimas
  (`48.02.40.05`). Nao usam o padrao geral nem os grupos fixos: exigem so o
  Autcom ate o primeiro fechamento, que aprende os `.exe`/`.dll` da propria
  copia (`is_specific_copy` em `app/pattern.py`).
- Fechamento concluido com menos ZIPs que o maior total ja registrado para a
  mesma pasta (nome completo) deixa a linha amarela (`zip_below`).
- Os testes usam uma pasta de dados temporaria (`tests/__init__.py`); nunca
  devem gravar em `%LOCALAPPDATA%\VarreduraSistema`.
- Fechamento, copia, limpeza manual e teste do Autcom criam a trava
  `.varredura.lock` na pasta do projeto (criacao atomica, renovada a cada 30 s,
  expira apos 3 min sem renovacao). Outro operador do TS ve "Em uso" na coluna
  Fechamento e fica bloqueado. A limpeza nunca apaga o arquivo de trava.
- Erro nao tratado (tela, thread ou abertura) vai para
  `%LOCALAPPDATA%\VarreduraSistema\logs\erros.log` com traceback, versao e
  usuario, gera `Sistema erro_inesperado` na Auditoria e mostra mensagem
  (`app/error_handler.py`, instalado no inicio de `VersionScannerApp`).
  Primeiro lugar a olhar quando um operador relatar que "nada aconteceu".
- Um ZIP ilegivel (formato nao suportado, senha, gravacao em andamento) vira
  pendencia so daquele projeto; erro inesperado num projeto nao interrompe a
  varredura dos demais (`_failed_project_result` em `app/scanner.py`).

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
`Cancelar Fechamento` (`cancelar_fechamentos` em `app/actions.py`): na fila o job
sai na hora; rodando, `cancel_event` faz `run_closing_bat` encerrar o BAT e seus
filhos (`taskkill /T`) em ate 1 s. Termina como `Cancelado em N%`, solta a trava
e audita `cancelado`; o projeto fica incompleto e precisa ser fechado de novo.

### Copia

A copia procura o destino nas raizes configuradas, mostra origem/destino antes
de continuar, copia ZIPs, valida a copia e remove da origem somente os ZIPs
copiados. Se nenhum destino existir, oferece criar (com confirmacao) em
`_VERSOES_INDIVIDUAIS\<ProductVersion com hifen na ultima parte>\<projeto>`,
com `_LOCAL` na copia Local (ex.: `02.30.74-111\379.48.2.30.74.111.191_LOCAL`);
copias especificas (5 partes) nao tem regra e continuam so com o aviso
(`new_destination_for` em `app/actions.py`). Arquivos criticos como `autcom.zip` sao verificados com SHA-256.

### Auditoria

Logs locais ficam em `%LOCALAPPDATA%\VarreduraSistema\logs` (um por operador do
TS). Toda linha de `write_log` tambem vai para `auditoria-AAAA-MM.log` na pasta
de controle compartilhada, por uma fila em segundo plano (`app/shared_audit.py`:
fila limitada, nova tentativa a cada 30 s se a rede falhar; nunca bloqueia a
interface). A tela `Auditoria > Logs` mostra os registros em tabela, com filtros
e o seletor `Origem` (`Meus registros` / `Todos os operadores`); a origem
compartilhada e lida em thread e nao atualiza sozinha. Os testes desligam a
gravacao compartilhada (`VARREDURA_SEM_AUDITORIA_COMPARTILHADA`).

### Monitoramento de scripts

`Monitoramento > Scripts Banco Modelo` verifica a pasta de scripts a cada 30
minutos (15 minutos se der erro). O que ja foi feito fica num registro
compartilhado (`scripts_banco_modelo.log` na pasta de controle, padrao
`\\citel-fileserve\Modem\_CONTROLE_VARREDURA`, configuravel), porque cada
operador do TS tem seu proprio `%LOCALAPPDATA%`. Cada evento e uma linha
acrescentada (`data | usuario | script | evento | detalhe`), nunca reescrita.
Pendente = script da pasta sem evento `base`, `feito` ou `executado`. O operador
marca scripts selecionados como feito; vale para todos. O cabecalho mostra o
contador `Scripts pendentes`. Logica em `app/script_monitor.py` (sem Tkinter).

### Consulta SVN

`Monitoramento > Consulta SVN` busca por revisao, requisito ou ambos.
Preferencialmente usa o comando `svn log`. Se o comando `svn` nao existir e o
caminho base for uma pasta local, busca o requisito em nomes/conteudo de arquivos.

### Jenkins

Menu `Jenkins` permite configurar URL, usuario e senha, abrir o painel local e
ver status dos jobs. A senha nao deve ser escrita em logs.

A tela de status mostra a situacao em portugues com simbolo (`job_label` em
`app/jenkins.py`), falhas e builds em andamento primeiro
(`sort_jobs_for_display`) e o andamento estimado pelo `estimatedDuration` do
Jenkins. Atualiza sozinha a cada 15 s enquanto aberta; consultas de status nao
vao para a Auditoria (so configuracao, teste de conexao e abrir build/painel).
Linhas de Auditoria do Jenkins seguem `Jenkins <acao> <resultado> | campo=valor`.

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
