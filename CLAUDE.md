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
(`app/update_check.py`), perguntam se querem fechar o sistema e, ao confirmar,
rodam o iniciador da pasta oficial para abrir a versao nova. Os operadores devem abrir o sistema por `Iniciar_VarreduraSistema.bat`
(vai no pacote, `scripts\Iniciar_VarreduraSistema.bat`): ele copia a versao para
`%LOCALAPPDATA%\VarreduraSistema\app\<versao>` e roda dali, entao a pasta de rede
nao fica travada e pode ser sobrescrita a qualquer hora. Abrir o
`VarreduraSistema.exe` direto da rede volta a travar a pasta.

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
| `app/version_cache.py` | Cache em disco das versoes lidas dos ZIPs (`zip_versions.json`). |
| `app/log_cleanup.py` | Apaga logs de BAT com mais de 90 dias. |
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
| `app/model_db.py` | Execucao de scripts SQL no MySQL do banco modelo (PyMySQL), divisor de comandos. |
| `app/script_runner.py` | Roda scripts no banco e registra `executado` no controle compartilhado. |
| `app/sqlaudit/` | Auditoria de estrutura SQL (so leitura, sem Tkinter): `file_scanner` (`.SQL` + SHA-256, ordem por `SCRIPT_<n>`), `sql_split` (comandos com linha), `sql_parse` (sqlglot; `CREATE TABLE`, `ALTER TABLE` com `ADD/MODIFY/CHANGE/DROP/RENAME COLUMN`, `RENAME`, indices, PK, FK, opcoes e `CONVERT TO CHARACTER SET`; `CREATE/DROP INDEX`, `DROP/RENAME TABLE`), `version_discovery` (pastas `<versao>\SCRIPTS` e banco sugerido), `version_tree` (base/fork de cada versao e scripts efetivos), `expected_model` (replay logico com proveniencia; objetos removidos ficam como "esperado ausente"), `type_normalizer`, `db_reader` (UNICO modulo que fala com o MySQL: so SELECT/SET SESSION fixos, sessao READ ONLY), `comparator` (COMPATIVEL/AUSENTE/DIVERGENTE/SUBSTITUIDO/NAO_SUPORTADO/INDETERMINADO/ERRO), `fork_hint`, `audit_runner` (orquestra a auditoria por versao, com cancelamento), `report` (CSV/JSON/HTML e linha de Auditoria), `fix_suggestions` (script corretivo SO PARA REVISAO: texto, nunca executado), `audit_store` (historico local em SQLite `sqlaudit.db` e comparacao entre auditorias) e `shared_log` (`auditoria_estrutura_sql.log` na pasta de controle: resumos e forks declarados). Config em `settings.json` (`sqlaudit`). Nunca executa SQL dos scripts (`tests/test_sqlaudit_isolation.py`). O usuario do banco precisa de SELECT nas tabelas: sem isso o `information_schema` as esconde. |
| `app/sqlaudit_window.py` | Janela `Monitoramento > Auditoria de Estrutura SQL` (so desenho; logica em `app/sqlaudit/`). |
| `app/model_db_dialogs.py` | Janelas de login e configuracao do banco modelo. |
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
contador `Scripts pendentes`. Os eventos `base` e `feito` gravam a data do arquivo
(`(mod=<segundos>)` no detalhe): se o arquivo for alterado depois (mais de 2 s), o
script volta a pendente como `Alterado depois de marcado`; registros sem a data
nunca contam como alterados. A pasta de scripts e configuravel em Configuracoes
(`script_directory`). Logica em `app/script_monitor.py` (sem Tkinter).

`Executar no banco` (`app/model_db.py`, `app/script_runner.py`) roda no MySQL os
scripts selecionados, em ordem e parando no primeiro erro, com usuario/senha
digitados a cada execucao (nunca gravados nem logados). Servidor/porta/banco ficam
em `settings.json` (`model_db`). Cada script e uma transacao: DML e desfeito em
erro, DDL nao. Sucesso grava `executado` no controle compartilhado na hora. O
PyMySQL e o `sqlglot` (so leitura, em `app/sqlaudit/`) sao as unicas dependencias
fora da biblioteca padrao (`requirements.txt`, `hiddenimports` no spec).

### Auditoria de Estrutura SQL

`Monitoramento > Auditoria de Estrutura SQL` compara a estrutura do banco de cada
versao (`BANCO_AUTCOM_<...>`, sugerido pelo nome da pasta e editavel) com o que os
scripts `<raiz>\<versao>\SCRIPTS\SCRIPT_<n>.SQL` deveriam ter produzido. Somente
leitura: nada dos scripts e executado e a sessao do MySQL e `READ ONLY` (usuario so
com SELECT). Cada versao deriva de uma base (sugerida pelo nome, editavel) e tem um
ponto de fork (maior `SCRIPT_<n>` da base ja presente no dump); sem fork o herdado
fica INDETERMINADO. Servidor/porta/usuario/TLS e a arvore ficam em `settings.json`
(`sqlaudit`); a senha e pedida a cada analise e nunca e gravada, logada ou exportada.
`Conectar e listar bancos...` (ou Enter em servidor/porta) pede usuario e senha, conecta
so ao servidor e lista os bancos que o usuario ENXERGA (`information_schema.SCHEMATA`;
bancos internos do MySQL ficam de fora) e abre a tela de escolha do banco de cada versao:
casa o banco sugerido com a lista (ignora maiusculas), mostra "existe/nao existe no
servidor" e deixa escolher ou digitar. Banco que nao esta na lista aparece como `Banco
nao encontrado` na tabela e em Avisos, e analisar um banco fora da lista pede
confirmacao. A lista fica so em memoria; a senha continua sendo pedida a cada operacao.
Descoberta, teste de conexao, analise e exportacao rodam em thread + fila; cada
analise gera uma linha `Auditoria SQL concluida|cancelada` na Auditoria. COMPATIVEL
nao prova que o script foi executado.

O que a auditoria compara: tabelas (engine, charset, colacao), colunas (tipo, NULL,
DEFAULT, AUTO_INCREMENT, ON UPDATE, charset, colacao, comentario), indices (colunas,
unicidade, prefixo, FULLTEXT/SPATIAL), chave primaria e chaves estrangeiras (colunas,
referencia, ON DELETE/UPDATE). Objetos removidos por script (DROP, rename, CHANGE) sao
esperados AUSENTES do banco; se ainda existem, DIVERGENTE. Indice/FK sem nome sao
achados pelas colunas (o MySQL escolhe o nome). Continuam NAO SUPORTADOS, com o
motivo, e nunca viram validado: CHECK, particoes, `ROW_FORMAT`/`COMMENT` de tabela,
indice funcional, `MATCH` em FK, colunas geradas/invisiveis, triggers, procedures,
views, DML e SQL dinamico. `ALGORITHM=`, `LOCK=` e `AUTO_INCREMENT=N` sao ignorados de
proposito (nao sao estrutura). Uma acao que o modelo nao segue (ex.: `ALTER COLUMN ...
SET DEFAULT`) deixa so aquela tabela INDETERMINADA.

Script corretivo: `Script corretivo...` (depois de uma analise) grava um `.sql` PARA
REVISAO; o sistema nunca o executa nem o abre (`tests/test_sqlaudit_isolation.py`). So
parte de achados firmes (AUSENTE/DIVERGENTE com origem certa em tabela conhecida);
INDETERMINADO, NAO SUPORTADO e ERRO nao geram comando, so sao contados. Saem
COMENTADOS os comandos de risco alto (mudar tipo/charset/NOT NULL, chave primaria,
engine) e todo DROP de coluna/tabela; o `USE <banco>` tambem vem comentado. Cada
comando traz motivo, diferenca, risco e script/linha de origem. Indice ou FK de nome
automatico que diverge nao e corrigido (o nome real no banco nao e conhecido). O SQL
gerado e relido pelo parser do proprio sistema nos testes. O modelo esperado fica
so em memoria (`VersionAudit.model`), nunca no historico nem nos relatorios. Cada
geracao grava `Auditoria SQL script_corretivo` na Auditoria.

Historico: cada analise e gravada em `%LOCALAPPDATA%\VarreduraSistema\sqlaudit.db`
(SQLite, so o seu; ultimas 60; guarda resultado por objeto e o SHA-256 dos scripts
usados, nunca senha) e, se concluida, um resumo por versao vai para
`auditoria_estrutura_sql.log` na pasta de controle (so acrescimo, nunca reescrito). A
aba `Historico` lista Meus registros ou Todos os operadores, mostra uma auditoria
antiga e compara duas (piorou/melhorou/novo/removido e scripts alterados pelo
SHA-256, nunca pela data do arquivo). `Salvar e registrar para todos` na edicao da
versao grava o evento `fork` (base e ultimo `SCRIPT_<n>` herdado) no mesmo arquivo; na
descoberta, o fork registrado por outro operador vale quando o operador ainda nao
informou o seu (o local vence e o conflito aparece em Avisos). Falha no historico ou
na pasta de controle nunca perde o resultado da analise.

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
Jenkins. Jobs dentro de pastas aparecem como `Pasta/job`. `Ver Log` mostra o fim do
log do ultimo build; `Disparar Build` (confirmacao, Auditoria `disparar_build`)
usa o endpoint `/build` com o crumb do Jenkins (sem parametros). Os links vindos
da API sao refeitos sobre a URL configurada, para as credenciais nunca irem a
outro host. O cabecalho mostra `Jenkins: N job(s) com falha`, consultado a cada
5 min em segundo plano. A tela atualiza sozinha a cada 15 s enquanto aberta; consultas de status nao
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
- Usar apenas biblioteca padrao para novas funcionalidades do sistema (unica
  excecoes autorizadas: PyMySQL, para o banco modelo, e sqlglot, para interpretar scripts na `app/sqlaudit/`).
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
