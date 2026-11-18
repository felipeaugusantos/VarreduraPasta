# Changelog

Todas as alteracoes relevantes deste projeto sao documentadas neste arquivo.

O formato e baseado em Keep a Changelog e o projeto segue Versionamento
Semantico (`MAIOR.MENOR.CORRECAO`).

## [2.8.1] - 2026-11-16

### Adicionado
- Tela inicial alinhada ao prototipo web: alternador `Projetos | Banco modelo` com a visao ativa preenchida, cartoes de contagem alinhados a esquerda e sem faixa colorida (o cartao filtrado ganha borda azul), titulo do projeto alinhado a esquerda na tabela e titulos dos grupos de botoes em maiusculas pequenas.
- `Comparar com o banco modelo` mostra a coluna `Controle` (Feito / Pendente / Alterado) lida do registro compartilhado de `Scripts Banco Modelo` (so quando a pasta comparada e a monitorada; leitura sem efeitos colaterais, `ledger_states`). Script marcado como feito que ainda falta no banco fica destacado com aviso. Botao `Marcar OK como feito...` (com confirmacao) grava `feito` no controle so para os scripts OK e pendentes, evitando cruzar as duas listas a mao.
- Integracao continua: job `testes-linux` (py_compile + suite com Tk virtual) alem do job Windows; testes `test_actions` e `test_runtime` independentes de plataforma (suite verde tambem no Linux).
- `scripts/publish_release.ps1`: publica `dist\VarreduraSistema` na pasta oficial conferindo `versao.txt` x `app/__init__.py`, SHA-256 do ZIP e recusando sobrescrever versao mais nova (`-Force`); `versao.txt` vai por ultimo; `-DryRun` so simula; `settings.json` nunca vai.
- Consulta SVN reorganizada: revisao, requisito e `Consultar` (destaque) na mesma linha, caminho base discreto no topo, foco inicial na revisao, mais espaco para resultados e duplo clique copia a revisao.
- Visual da tela inicial mais limpo: janela dimensionada e centralizada para caber na tela (antes cortava o botao Atualizar e o painel de detalhes em telas menores), faixa de destaque no cabecalho, cartoes com legenda discreta e numeros maiores, linhas da tabela mais altas e cabecalhos em cinza.
- `Comparar com o banco modelo` agora fica na tela inicial: alternador `Projetos | Banco modelo` no cabecalho (e `Monitoramento > Comparar com o banco modelo`), sem janelas empilhadas. O botao `Comparar com o banco` do monitor de scripts abre esse painel. O visual ficou mais leve (rotulos sem negrito). A janela propria continua existindo internamente (`open_model_check_window`).
- Senha do banco lembrada (opcional) na Auditoria de Estrutura SQL e em `Comparar com o banco`: o login ganha a caixa `Lembrar a senha neste usuario do Windows` (desligada por padrao). A senha fica criptografada com a DPAPI do Windows, so para o usuario do Windows atual nesta maquina, em `credenciais.dat` (`%LOCALAPPDATA%\VarreduraSistema`), presa a servidor+porta+usuario; nunca em `settings.json`, log, Auditoria, CSV ou relatorio. `Esquecer senha salva` apaga (na Auditoria, em `Avancado`); senha lembrada que o banco recusa e apagada sozinha. `Executar no banco` continua pedindo a senha sempre. Fora do Windows a opcao nao aparece. Use uma conta so com SELECT.
- `Monitoramento > Scripts Banco Modelo > Comparar com o banco`: em 3 passos (1 escolher a pasta de scripts, que fica lembrada; 2 informar servidor e porta, `Conectar e listar bancos...` e escolher o banco modelo na lista; 3 comparar). Le a estrutura do banco modelo (somente leitura; usuario e senha pedidos a cada conexao/comparacao, nunca gravados) e confere CADA script `.sql` da pasta (inclusive subpastas). Mostra `OK` quando tudo o que o script cria ja esta no banco (tabela, campo, indice, chave) ou `Falta rodar` com os itens (ex.: `campo CLIENTES.CLI_FALTA, tabela NOVA`). Cada script e conferido sozinho, sem depender de ordem, base ou fork. O que outro script remove ou renomeia nao e cobrado; diferenca de tipo e so observacao; dados e procedures ficam como "Sem campos para conferir". Tem cartoes de resumo clicaveis (Todos, Falta rodar, OK, Sem campos, Erro), busca, detalhe por script e `Exportar CSV...`. Botao `Gerar plano completo...`: UM `.sql` so, para revisao, com o que falta de TODOS os scripts pendentes na ordem sugerida (secao por script, `depois de:` e a lista no topo). Scripts de conteudo identico (ex.: a copia da pasta de scripts entregues) aparecem como `Copia identica`, valem pelo primeiro e ficam fora da ordem. O script que ele mesmo (re)cria um objeto nao depende de outro que tambem o cria. Botao `Gerar script do que falta...`: para o script selecionado com `Falta rodar`, grava um `.sql` so para revisao com os comandos dos objetos que ainda faltam, na ordem do script (tabelas ja existentes nao sao recriadas; ex.: script aplicado em parte). Dados/procedures do original aparecem comentados na posicao certa; DROP de coluna/tabela sai comentado; o sistema nunca executa nem abre o arquivo. Quando falta algo que OUTRO script da pasta cria, mostra `[criado por SCRIPT_X.sql]`, lista no detalhe `Rode ANTES` e o botao `Ordem sugerida...` monta a ordem para rodar (quem cria vem antes de quem precisa; o CSV traz `rodar_antes` e `ordem_sugerida`). Indice com o mesmo nome e colunas diferentes tambem conta como pendente, e o script que remove e recria um indice precisa deixa-lo no banco. A configuracao (passos 1 e 2) recolhe sozinha ao fim da comparacao, e o andamento mostra cada fase (interpretando scripts, lendo o banco, conferindo).
- Auditoria de Estrutura SQL: campo `Ignorar tabelas com prefixo` (ex.: `CONECTA_`, separado por virgula, salvo nas configuracoes). Essas tabelas saem da comparacao, dos scripts por arquivo, do relatorio e do script corretivo; a contagem de objetos ignorados aparece em Avisos. Serve para tabelas gerenciadas fora dos scripts das versoes (ex.: as do banco modelo).
- Auditoria de Estrutura SQL: botao `Por tabela...` na aba Objetos, com a contagem de objetos por tabela (divergente, ausente, indeterminado, compativel), das mais problematicas para as menos.
- Auditoria de Estrutura SQL: botao `Exportar lista...` na aba Objetos. Grava em CSV (`;`, UTF-8) exatamente os objetos que passam pelos filtros atuais (estado, versao, busca), sem o limite de linhas da tela, tambem ao ver uma auditoria do historico.
- Auditoria de Estrutura SQL: botao `Marcar como conhecida...` (aba Objetos). Seleciona achados Divergente/Ausente ja investigados (Ctrl+A, Ctrl ou Shift), pede o motivo e grava na pasta de controle, valendo para todos os operadores. Ficam ocultos (`Ocultar conhecidas`, ligado por padrao), com a contagem na lista e o motivo nos detalhes; `Desmarcar` desfaz. A chave inclui o estado e as diferencas: se o banco mudar, o achado volta a aparecer.

### Alterado
- Auditoria de Estrutura SQL, `Script corretivo...`: cada versao agora traz tres secoes. SECAO 1 BACKUP (linha `mysqldump` com servidor/porta e as tabelas alteradas, mais copias `bkp_*` dentro do banco, tudo comentado); SECAO 2 APLICAR (os comandos de antes); SECAO 3 DESFAZER (tudo comentado, em ordem inversa, com a definicao que o banco tinha na hora da analise). Nao usa BEGIN/ROLLBACK porque no MySQL CREATE/ALTER/DROP TABLE fazem COMMIT implicito e nao podem ser desfeitos por transacao; o cabecalho explica e orienta rodar sem `--force`. O esquema lido do banco fica so em memoria (como o modelo esperado), nunca no historico nem nos relatorios.
- Auditoria de Estrutura SQL: tela reorganizada em passos numerados (1 Pasta dos scripts, 2 Servidor MySQL, 3 Versoes a auditar, 4 Analisar). TLS, arquivo da CA e prefixos ignorados ficaram em `Avancado` (abre sozinho se estiverem fora do padrao). O status fica junto do botao Analisar. A secao `Configuracao` (passos 1 a 3) recolhe sozinha ao fim da analise, com um resumo de uma linha, e a lista de resultados ganha a tela. Na aba Objetos, filtros em uma so linha e os botoes (conhecidas, Por tabela, Exportar lista) numa barra abaixo da lista. `Bancos...` virou `Escolher banco...` e `Exportar...` virou `Exportar relatorio...`.

### Corrigido
- Auditoria de Estrutura SQL, falsos alertas que apareciam como divergencia: coluna `AUTO_INCREMENT` sem `NOT NULL` declarado (o MySQL a torna NOT NULL); `DEFAULT 'N '`/`DEFAULT ' '` em CHAR (o MySQL tira os espacos finais; em VARCHAR continua valendo); `DEFAULT` literal em TEXT/BLOB/JSON (o servidor ignora).
- Auditoria de Estrutura SQL: uma procedure (ou SQL dinamico) que faz `ALTER/DROP/RENAME` e cita uma coluna/tabela (ex.: `ALTER_MOTDEL_255`, que muda `MOTDEL` para 255 em varias tabelas) deixava centenas de colunas como DIVERGENTE falso. Agora o que foi definido antes dela e e citado nela vira INDETERMINADO, com a nota do script e da linha e um aviso com a contagem. DML comum e procedures que nao citam o objeto nao escondem nada.
- Auditoria de Estrutura SQL: tamanho com zero a esquerda (`CHAR(02)`, `VARCHAR(003)`) era apontado como divergente de `char(2)`/`varchar(3)`; o MySQL os trata como iguais.
- Auditoria de Estrutura SQL: `ScriptNT.SQL` e `CADNCM_INSERT.SQL` na pasta `SCRIPTS` sao ignorados (nao entram na analise nem nos avisos de "fora do padrao").
- Auditoria de Estrutura SQL: a leitura do banco estourava o tempo (`Lost connection ... read operation timed out`) em bancos grandes. O limite passou de 60 s para 10 min e a consulta das chaves estrangeiras passou a filtrar tambem o esquema em `REFERENTIAL_CONSTRAINTS`.
- Auditoria de Estrutura SQL: `Descobrir versoes` nao fazia nada quando a pasta
  escolhida era a propria `SCRIPTS` (ou a pasta da versao). Agora a pasta `SCRIPTS`,
  a pasta da versao e uma pasta com `.SQL` valem como uma unica versao (ja marcada),
  com o banco sugerido pela pasta ancestral (ex.: `379.48.2.30`) e escolhido depois em
  `Conectar e listar bancos...`. Sem versoes, a mensagem explica o que escolher.

## [2.8.0] - 2026-11-06

### Adicionado
- `Monitoramento > Auditoria de Estrutura SQL`: compara a estrutura de cada banco
  `BANCO_AUTCOM_<versao>` com os scripts da versao e de suas bases (somente leitura,
  senha pedida a cada analise e nunca gravada). Descobre as pastas `SCRIPTS` pela
  raiz, sugere banco e base pelo nome, permite informar o fork, mostra progresso,
  aceita cancelamento, filtra por estado/versao/texto, detalha esperado x encontrado
  com a origem (script e linha), sugere o fork pelo banco e exporta HTML, CSV ou JSON.
  Cada analise grava uma linha na Auditoria.
- Auditoria de Estrutura SQL: aba `Historico`. Cada analise fica salva (SQLite local,
  ultimas 60, com o SHA-256 dos scripts) e um resumo por versao vai para a pasta de
  controle compartilhada (so acrescimo). Permite ver uma auditoria antiga e comparar
  duas (objetos que pioraram, melhoraram, surgiram ou sumiram; scripts alterados,
  adicionados ou removidos pelo SHA-256). `Salvar e registrar para todos` grava o
  fork da versao para todos os operadores, e a descoberta aplica o fork registrado
  por outra pessoa quando voce ainda nao informou o seu.

- Auditoria de Estrutura SQL: o que e entendido e comparado cresceu. `DROP COLUMN`,
  `CHANGE COLUMN`, `RENAME COLUMN`, `RENAME TO`/`RENAME TABLE`, `DROP TABLE`, indices
  (`ADD/DROP INDEX|KEY|UNIQUE`, `CREATE/DROP INDEX`, prefixo, FULLTEXT/SPATIAL),
  chave primaria, chaves estrangeiras (colunas, referencia, `ON DELETE/UPDATE`),
  opcoes de tabela (`ENGINE`, `CHARSET`, `COLLATE`) e `CONVERT TO CHARACTER SET`.
  Objetos removidos sao esperados ausentes; indice e FK sem nome sao achados pelas
  colunas; a chave primaria passa a explicar o `NOT NULL` das colunas. Continuam
  nao suportados, com o motivo: CHECK, particoes, indice funcional, `MATCH` em FK,
  triggers, procedures e DML.

- Auditoria de Estrutura SQL: `Script corretivo...` gera um `.sql` para REVISAO com o
  que falta para o banco chegar ao estado esperado (colunas, indices, chave primaria,
  chaves estrangeiras, tabelas, opcoes). Nunca e executado pelo sistema. So usa
  achados firmes; comandos de risco alto e todo DROP de coluna/tabela saem
  comentados, assim como o `USE`; cada comando traz motivo, risco e script/linha de
  origem. O que e indeterminado ou nao suportado nao gera comando.
- Auditoria de Estrutura SQL: `Conectar e listar bancos...` (tambem com Enter em
  servidor/porta). Pede usuario e senha, lista os bancos que o usuario enxerga no
  servidor (sem os internos do MySQL) e abre a tela de escolha do banco de cada
  versao, com filtro e a indicacao "existe/nao existe no servidor". O banco sugerido
  pelo nome da pasta e conferido contra o servidor (ignora maiusculas); o que nao
  existe aparece como "Banco nao encontrado". Analisar um banco fora da lista pede
  confirmacao. O campo Banco da edicao da versao passa a ser uma lista.

### Corrigido
- Registro compartilhado da Auditoria de Estrutura SQL (`auditoria_estrutura_sql.log`):
  no Windows, `open("a")` nao e atomico e dois operadores gravando juntos podiam
  sobrescrever a linha um do outro (o CI perdeu 2 a 5 de 100 linhas). Agora a gravacao
  usa bloqueio de intervalo de bytes (`msvcrt.locking`/`flock`) em area bem alem dos
  dados, entao nao bloqueia quem le e vale entre processos e maquinas.
- `requirements.txt` e o workflow de CI fixavam `pymysql==2.2.8`, versao que nao
  existe (o `2.2.8` e o `pymysql.__version__` de compatibilidade com MySQLdb): o
  `pip install` falhava e o CI ficou vermelho desde a v2.7.0. Agora `pymysql==1.2.3`.

### Interno
- Auditoria de estrutura SQL, fase 1 (`app/sqlaudit/`, ainda sem tela): le os
  `.SQL` de uma pasta (SHA-256, ordem por `SCRIPT_<n>`, avisos de lacuna e
  repeticao), divide em comandos com linha e interpreta `CREATE TABLE` e
  `ALTER TABLE ... ADD/MODIFY COLUMN`. Somente leitura: nada e executado. O que
  nao e entendido com seguranca fica `nao_suportado` ou `erro`, nunca validado.
  Nova dependencia: `sqlglot` (embutido no exe).
- Auditoria de estrutura SQL, fase 2 (ainda sem tela): descobre as pastas
  `<versao>\SCRIPTS` e sugere o banco (`BANCO_AUTCOM_379_48_02_30_...`), monta a
  arvore de versoes (a base e sugerida pelo nome e o operador pode mudar) com o
  ponto de fork, e monta o estado esperado por replay logico dos scripts da base
  (ate o fork) e da versao. Sem fork informado, o herdado fica marcado como
  incerto. Comandos que o modelo nao segue (DROP, CHANGE, DML...) deixam a tabela
  marcada como incompleta, nunca ignorados. Config `sqlaudit` em `settings.json`
  (sem credenciais).
- Auditoria de estrutura SQL, fase 3 (ainda sem tela): `db_reader` le tabelas e
  colunas do `information_schema` em sessao somente leitura (so consultas fixas;
  nada do texto dos scripts chega ao banco), `comparator` classifica cada comando,
  objeto, arquivo e versao (COMPATIVEL, AUSENTE, DIVERGENTE, SUBSTITUIDO,
  NAO_SUPORTADO, INDETERMINADO, ERRO) normalizando tipos equivalentes entre
  versoes do MySQL, e `fork_hint` sugere o ponto de fork da versao filha pelo
  banco dela. Origem incerta, tabela com DROP/RENAME/CHANGE e tipo desconhecido
  nunca viram AUSENTE/DIVERGENTE: ficam INDETERMINADO.

## [2.7.0] - 2026-10-26

### Adicionado
- Scripts do banco modelo: `Executar no banco` roda no MySQL os scripts
  selecionados, em ordem, parando no primeiro erro. Usuario e senha sao
  digitados a cada execucao (a senha nunca e gravada nem vai para log); o que
  roda com sucesso vira `executado` no controle compartilhado na hora. Cada
  script e uma transacao (DML desfeito em erro; DDL o MySQL confirma sozinho).
  Servidor, porta e banco em `Banco...` (com teste de conexao). Usa o PyMySQL,
  embutido no exe (unica dependencia alem da biblioteca padrao).

## [2.6.8] - 2026-10-26

### Adicionado
- Scripts do banco modelo: um script ja marcado como feito que for alterado
  depois (data do arquivo) volta a ficar pendente, destacado como `Alterado
  depois de marcado`, e o aviso diferencia novos de alterados. Marcar de novo
  vale para todos os operadores. Registros antigos, sem a data gravada, nunca
  contam como alterados.
- Scripts do banco modelo: a pasta de scripts agora e configuravel em
  `Configuracoes > Pasta de scripts` (vazio volta ao padrao).
- Jenkins: a tela de status mostra tambem os jobs dentro de pastas (ate 3
  niveis, como `Pasta/job`).
- Jenkins: botao `Ver Log` mostra o final do log do ultimo build (onde a falha
  aparece), com opcao de copiar.
- Jenkins: botao `Disparar Build` (com confirmacao) inicia um build do job; vai
  para a Auditoria. Jobs com parametros nao sao suportados.
- Jenkins: o cabecalho mostra `Jenkins: N job(s) com falha` (consulta a cada 5
  min); clicar abre a tela de status.

## [2.6.7] - 2026-10-26

### Adicionado
- Ao detectar uma versao nova publicada, o sistema pergunta (uma vez por versao)
  se o operador quer fechar o programa para abrir a versao nova; ao confirmar,
  roda o `Iniciar_VarreduraSistema.bat` da pasta oficial, que copia e abre a
  versao nova. O clique no aviso do cabecalho repete a pergunta. Com
  fechamentos na fila/em andamento, a atualizacao e adiada.

### Corrigido
- O build nao falha mais ao gerar o ZIP quando antivirus/indexador esta com um
  arquivo do pacote aberto: o ZIP e feito com leitura compartilhada e refeito
  algumas vezes antes de desistir.

## [2.6.6] - 2026-10-26

### Adicionado
- `Iniciar_VarreduraSistema.bat` no pacote: copia a versao publicada para
  `%LOCALAPPDATA%\VarreduraSistema\app\<versao>` e abre o sistema dali. A
  pasta de rede deixa de ficar travada, entao uma versao nova pode ser publicada
  com operadores usando o sistema (quem esta na antiga segue nela ate fechar).
  Copias locais antigas que nao estao em uso sao apagadas.

## [2.6.5] - 2026-10-26

### Adicionado
- Cache em disco das versoes lidas dentro dos ZIPs (`zip_versions.json`): ao
  reabrir o sistema, ZIPs que nao mudaram (data/tamanho) nao sao extraidos de
  novo, o que acelera a primeira varredura de cada operador do TS. Leituras com
  erro nao sao guardadas.
- Limpeza automatica dos logs de BAT (`logsechamentos_bat`) com mais de 90
  dias, feita em segundo plano ao abrir o sistema. A Auditoria e o `erros.log`
  nao sao apagados.

### Alterado
- Com varios projetos selecionados, o botao de fechamento so fica desabilitado
  quando todos estao em uso por outro operador; os livres seguem para a fila e
  os demais sao recusados com o nome de quem esta usando.
- O login de teste do Autcom (usuario, senha e empresa) deixa de ser fixo no
  codigo: cada operador pode configurar em `Configuracoes > Teste do Autcom`
  (senha mascarada, so letras e numeros, campo vazio volta ao padrao
  `998`/`998`/`001`). A validacao acontece antes de gravar qualquer
  configuracao.

### Corrigido
- O login automatico digitava cada caractere pelo codigo bruto da tecla, o que
  so funcionava para numeros: letras saiam trocadas (`senha1` virava `5,811`,
  maiusculas viravam minusculas). Agora a digitacao usa o mapa do teclado
  (`VkKeyScanW`) e respeita Shift.

## [2.6.4] - 2026-10-26

### Alterado
- `Testar Autcom` fecha o Autcom ao final do teste, em qualquer resultado:
  primeiro pede para as janelas fecharem e, se nao sair em 8 s, encerra o
  processo e seus filhos. A mensagem informa como foi fechado. Antes o Autcom
  ficava aberto e travava os arquivos da pasta de teste para o teste seguinte.

### Adicionado
- Botao `Cancelar Fechamento`: tira da fila ou encerra o BAT em andamento (com
  todos os processos que ele abriu), solta a trava do projeto e registra
  `cancelado` na Auditoria. A coluna mostra `Cancelado em N%`; os arquivos ja
  compactados ficam e o projeto precisa ser fechado de novo.
- Auditoria compartilhada: toda linha de Auditoria tambem e acrescentada a
  `auditoria-AAAA-MM.log` na pasta de controle. A gravacao e em segundo plano
  (fila limitada; se a rede cair, as linhas aguardam e sao gravadas depois), entao
  a tela nunca espera a rede. Em `Auditoria`, o seletor `Origem` alterna entre
  `Meus registros` e `Todos os operadores` (leitura em thread, sem auto-atualizar).
- Aviso de versao nova: o build gera `versao.txt` dentro do pacote; ao abrir (e
  a cada hora) o sistema le o `versao.txt` de `\\citel-fileserve\Modem\SISTEMA_COPIA`
  (configuravel em `update_directory` no `settings.json`) e, se a versao
  publicada for mais nova que a do exe em uso, mostra "▲ Versao X disponivel" no
  cabecalho (clique mostra onde esta). Falha ao ler a pasta e silenciosa.

### Corrigido
- `Validar Fechamento` agora tambem confere a trava de outro operador (antes so
  os botoes ficavam desabilitados).
- Erros inesperados no exe (que roda sem console) sumiam sem deixar rastro: o
  botao "nao fazia nada". Agora todo erro nao tratado em tela, em thread ou na
  abertura do sistema e gravado com detalhes em `logs\erros.log`, gera uma
  linha `Sistema erro_inesperado` na Auditoria e mostra uma mensagem com o
  caminho do log (o mesmo erro repetido so abre mensagem a cada 30 s).

## [2.6.3] - 2026-10-26

### Alterado
- Cartoes do cabecalho: mostram "…/analisando…" ate a primeira varredura (em
  vez de zeros) e mantem os numeros durante as varreduras seguintes; zero nos
  cartoes de alerta fica em cinza; cada cartao tem uma linha de detalhe (ex.:
  "62% do total", "1 versao • 1 ausente • 1 ZIP", "1 rodando • 1 na fila").
- Clicar num cartao filtra a tabela (OK, Com pendencia, Em fechamento, Alerta
  de ZIPs) e o cartao ativo fica destacado; clicar de novo volta para Todos.
  Filtros "Em fechamento" e "Alerta de ZIPs" tambem no combo de filtro.
- Icone com cantos transparentes (o PNG original era RGB e mostrava um
  quadrado branco na barra de tarefas escura) e versao simplificada (pasta +
  lupa) em 16 e 24 px, onde o desenho completo ficava ilegivel. Gerado por
  `scripts/generate_icon.py`.

### Adicionado
- Copiar Local/Cloud sem destino encontrado oferece criar a pasta em
  `_VERSOES_INDIVIDUAIS`: subpasta da versao (ProductVersion com hifen na
  ultima parte, ex.: `02.30.74-150`, criada se faltar) e pasta do projeto
  (`<projeto>_LOCAL` no Local). Mostra o caminho e so cria apos confirmacao;
  auditado como `destino_criado`. Copias especificas mantem o aviso.

### Alterado
- Jenkins - Status dos Jobs: situacao em portugues com simbolo e cor
  (Falhou, Em andamento, Instavel, Cancelado, Sucesso, Sem build), falhas e
  builds em andamento no topo, andamento estimado (`Em andamento 45%`) e resumo
  com total de falhas.
- A tela de status atualiza sozinha a cada 15 s enquanto aberta; erro na
  consulta automatica aparece so na tela, sem janela de aviso.

### Corrigido
- Um unico ZIP ilegivel (formato nao suportado como Deflate64, senha, ou ainda
  sendo gravado pelo BAT) derrubava a varredura inteira e esvaziava a tabela.
  Agora vira pendencia "ZIP ilegivel"/"ZIP incompleto" so daquele projeto, e um
  erro inesperado em um projeto nao interrompe os demais.
- Dois operadores do TS podiam mexer no mesmo projeto ao mesmo tempo (ex.:
  copiar durante o fechamento do outro). Nova trava compartilhada por projeto
  (`.varredura.lock`) em fechamento, copia, limpeza manual e teste do Autcom;
  a tabela mostra "Em uso: <acao> (<operador>)" e bloqueia as acoes.
- Auditoria do Jenkins mostrava apenas "Jenkins" na coluna Acao; agora mostra a
  acao (ex.: `Jenkins teste_conexao`) e o resultado. Consultas de status nao
  sao mais gravadas na Auditoria.

## [2.6.2] - 2026-10-22

### Alterado
- Scripts Banco Modelo com controle compartilhado entre operadores do TS:
  registro `scripts_banco_modelo.log` na pasta de controle (padrao
  `\\citel-fileserve\Modem\_CONTROLE_VARREDURA`, configuravel em
  Configuracoes). Um script marcado como feito some da lista de todos.
- Marcacao por script (selecao multipla), com confirmacao, aviso quando outro
  operador ja marcou e Auditoria com os nomes dos scripts e o usuario.
- Verificacao a cada 30 minutos; se a pasta estiver inacessivel, nova tentativa
  em 15 minutos (antes esperava o dia seguinte).
- Tela nova com data de cada script, abrir o script com duplo clique e
  historico dos ultimos registros de todos os operadores.
- Contador `Scripts pendentes` no cabecalho (clique abre a tela).
- Abrir a tela nao grava mais "verificado" na Auditoria; a lista completa de
  scripts conhecidos saiu do `settings.json` de cada operador.

## [2.6.1] - 2026-10-22

### Corrigido
- Copias especificas (nome com 5 partes, ex.: `379.48.2.40.5`) apareciam como
  "Arquivo ausente" mesmo completas: eram cobradas pelo padrao geral da copia
  completa (138 grupos) e pelos grupos fixos. Agora exigem so o Autcom ate o
  primeiro fechamento, que aprende os arquivos da propria copia; dali em diante
  alertam se algum deles sumir.
- Versoes esperadas das copias especificas passam a ser lidas do nome
  (`379.48.02.40` / `48.02.40.05`), sem a pendencia "Nome da pasta fora do padrao".
- Janelas secundarias crescem ate caber o conteudo com o tema novo (botoes de
  Copiar, Status dos Jobs e Consulta SVN ficavam cortados).

## [2.6.0] - 2026-10-21

### Alterado
- Redesenho da tela principal (somente biblioteca padrao, tema ttk `clam` em
  `app/theme.py`): cabecalho com versao e diretorio-base, contadores (analisados,
  OK, com pendencia, em fechamento, alerta de ZIPs), tabela com linhas mais
  altas e status com simbolos, painel de detalhes do projeto selecionado (versao
  errada em vermelho com a esperada abaixo), botoes agrupados em Fechamento,
  Copia e Projeto, e barra de status sem corte.
- Colunas "File esperado" e "Product esperado" sairam da tabela; a versao
  divergente e marcada com `✗` e o esperado aparece no painel.

## [2.5.0] - 2026-10-21

### Adicionado
- Memoria de ZIPs por pasta: cada fechamento concluido registra o total de
  ZIPs gerados e o maior total ja visto (`zip_memory.json`). Se um fechamento
  gerar menos ZIPs que o maximo, a linha fica amarela, a coluna mostra
  `(149/150 ZIPs)` e a dica alerta. Auditado como `concluido_abaixo_maximo`.

### Corrigido
- Testes passam a usar uma pasta de dados temporaria e nao gravam mais em
  `%LOCALAPPDATA%\VarreduraSistema`.

## [2.4.1] - 2026-10-21

### Alterado
- Coluna `Fechamento` mostra o percentual ja compactado durante o fechamento
  (`Em andamento 45%`), calculado pelo tamanho dos `.exe`, `.dll` e `.bpl`
  que o BAT ja processou. O tempo aparece apenas no `Concluido`; erros mostram
  ate onde o fechamento chegou (`Erro em 80%`).

## [2.4.0] - 2026-10-21

### Adicionado
- Fechamento Local/Cloud roda o BAT em segundo plano, sem janela de console e
  sem bloquear o sistema. A saida de cada BAT fica em `logs\fechamentos_bat`.
- Coluna `Fechamento` na tabela principal com a situacao (`Na fila`,
  `Em andamento`, `Concluido`, `Erro`) e o tempo decorrido.
- Selecao de varios projetos do mesmo tipo para fechamento em lote, com ate 2
  BATs simultaneos e o restante em fila.
- BAT que passar de 60 minutos e encerrado e registrado como erro.
- Ao sair com fechamentos ativos, o sistema pede confirmacao.
- Enquanto o fechamento de um projeto esta na fila ou em andamento, Copiar,
  Limpar Pasta, Testar Autcom e Validar Fechamento ficam bloqueados para ele
  (botoes desabilitados e verificacao tambem na acao, auditada como
  `bloqueado`). A copia revalida logo antes de iniciar.

### Corrigido
- BAT de fechamento roda em console proprio oculto: o `timeout` do laco de
  espera voltou a esperar 1 s (sem console ele falhava na hora, gerando laco
  ocupado e lentidao no PC). O BAT roda com prioridade abaixo do normal.
- Resultado do fechamento segue o criterio do BAT (nenhum `.exe`, `.dll` ou
  `.bpl` pendente na pasta), pois o `_FechamentoArquivos.bat` sai com codigo 1
  ao concluir. O codigo do BAT fica na Auditoria como `codigo_bat`.
- Testes nao gravam mais registros na Auditoria real.
- Limpeza da origem apos a copia remove somente os ZIPs copiados e que nao
  mudaram desde a copia. Itens nao copiados sao mantidos, listados em aviso e
  registrados na Auditoria (`mantidos_na_origem`).
- Falhas inesperadas durante fechamento, copia ou limpeza manual nao deixam
  mais a janela de progresso travada; o erro e exibido ao operador.

## [2.3.1] - 2026-07-28

### Corrigido
- Identificacao da tela de login deixou de depender exclusivamente da classe
  Delphi `TParentForm` e passou a aceitar a janela personalizada visivel.
- Deteccao da tela principal agora aceita variacoes de classe, sem confundir
  dialogs, janelas utilitarias ou mensagens com uma autenticacao concluida.

## [2.3.0] - 2026-07-28

### Adicionado
- Teste do Autcom preenche automaticamente usuario `998`, senha `998` e
  empresa `001` na tela de login.
- Resultado `OK` agora exige que a tela principal do Autcom fique visivel,
  responsiva e estavel depois da autenticacao.

## [2.2.0] - 2026-07-28

### Adicionado
- Acao `Testar Autcom` prepara a `PastaTesteCopia` correspondente a versao,
  executa `CopiaArquivos.bat` e valida a abertura do `Autcom.exe`.
- Validacao identifica encerramento prematuro, janela travada e janela visivel,
  responsiva e estavel, sem bibliotecas externas.
- ZIPs da pasta de teste sao sincronizados transacionalmente com SHA-256.

## [2.1.1] - 2026-07-27

### Alterado
- Copiar Cloud agora considera `Autcom.exe` valido a partir de 150 MB.

## [2.1.0] - 2026-07-27

### Adicionado
- Consulta SVN recebeu barra de progresso, tempo decorrido, cancelamento real,
  busca por Enter e modos de primeira ou todas as ocorrencias.
- Pesquisa SVN por requisito agora percorre o historico em blocos adaptativos.
- Aprendizado estatistico local com SQLite registra projetos validos sem API,
  internet, instalador ou permissao administrativa.
- Plano de evolucao da IA local documentado em `docs/PLANO_IA_LOCAL.md`.
- CI passou a validar tambem a geracao do executavel.

### Alterado
- Configuracoes, padroes, logs e aprendizado agora ficam em
  `%LOCALAPPDATA%\VarreduraSistema`, com migracao dos arquivos antigos.
- Build inclui `project_pattern.json` automaticamente quando ele esta presente.

### Corrigido
- Versoes obrigatorias ausentes agora geram pendencia.
- Baseline vazio do monitor de scripts agora detecta o primeiro arquivo criado.
- Rollback incompleto preserva backups e manifesto para recuperacao manual.
- JSON corrompido e preservado como `.corrupt.bak`.
- Escritas de logs e padroes foram serializadas.
- Processo SVN interrompido pelo Windows repete a faixa com bloco menor.

## [2.0.3] - 2026-07-14

### Adicionado
- Validacao continua no GitHub Actions para compilacao, testes e importacao da
  interface em Windows.
- Senha do Jenkins passou a ser armazenada no Gerenciador de Credenciais do
  Windows; autenticacao remota por HTTP sem TLS agora e bloqueada.
- Copia passou a usar area temporaria transacional e SHA-256 para todos os ZIPs.
- Regras de apresentacao da tabela principal foram extraidas da interface para
  um modulo independente e testavel.
- Resultado da Consulta SVN passou a ser exibido em tabela por revisao, com
  detalhe completo da revisao selecionada.
- Tela de Consulta SVN recebeu visual mais profissional, data resumida,
  detalhe estruturado, barras de rolagem e botoes para copiar revisao/caminho.
- Tabela da Consulta SVN agora separa o caminho alterado em colunas `Versao`
  e `Caminho`.
- Coluna `Versao` da Consulta SVN agora identifica a pasta de versao do projeto
  dentro do caminho, mesmo quando o item alterado e um arquivo abaixo dela.

### Corrigido
- ZIP invalido ou corrompido agora bloqueia a operacao de copia.
- Monitoramento de scripts nao acessa mais o Tkinter pela thread secundaria.
- Escritas concorrentes no arquivo de configuracao agora sao serializadas.

## [2.0.2] - 2026-07-14

### Adicionado
- Novo submenu `Monitoramento > Consulta SVN` permite buscar por revisao,
  requisito ou ambos usando o caminho base SVN configurado.
- Tela de configuracoes passou a salvar o caminho base SVN em `settings.json`.

## [2.0.1] - 2026-06-23

### Adicionado
- Menu `Jenkins > Status dos Jobs` consulta os jobs do Jenkins local e mostra
  ultimo build, status, duracao, data e link para abrir o build no navegador.

## [2.0.0] - 2026-06-23

### Corrigido
- `Limpar Pasta` agora executa em segundo plano com janela de progresso,
  evitando que o sistema pareca travado durante limpezas demoradas.

## [1.2.14] - 2026-06-23

### Adicionado
- Novo menu `Jenkins` permite configurar URL, usuario e senha do Jenkins local,
  abrir o painel no navegador e testar conexao sem registrar a senha na Auditoria.

## [1.2.13] - 2026-06-23

### Adicionado
- Novo botao `Limpar Pasta` remove os arquivos da raiz do projeto selecionado,
  preserva `comandosCMD` e registra a acao na Auditoria.

## [1.2.12] - 2026-06-18

### Alterado
- Copiar Cloud agora considera `Autcom.exe` valido a partir de 180 MB.

## [1.2.11] - 2026-06-18

### Adicionado
- Monitoramento de scripts agora mantem scripts novos como pendentes ate o
  operador clicar em `Marcar como feito`.

## [1.2.10] - 2026-06-17

### Adicionado
- Novo menu `Ajuda` com `Manual de Utilizacao` e `Sobre`.
- Tela `Sobre` exibe versao do sistema, data de geracao do executavel,
  criador e setor responsavel.

## [1.2.9] - 2026-06-17

### Adicionado
- Novo menu `Monitoramento > Scripts Banco Modelo` verifica uma vez ao dia a
  pasta `\\citel-fileserve\Modem\WEBSERVICE_CITEL\Scripts` e avisa quando
  encontrar scripts novos.
- Estado do monitoramento de scripts e salvo em `settings.json`, evitando
  alertas repetidos no mesmo dia.

## [1.2.8] - 2026-06-17

### Alterado
- Tela de Auditoria passou a exibir os logs em tabela com colunas para data,
  acao, resultado, usuario, projeto, tipo, destino, versoes, tamanho e motivo.

## [1.2.7] - 2026-06-17

### Adicionado
- Tela de configuracoes agora exibe os destinos padrao de copia e permite
  cadastrar multiplos destinos adicionais sem novo build.
- Quando a busca encontra mais de um destino para o mesmo projeto, o operador
  pode escolher o caminho correto antes da confirmacao da copia.
- Novo botao `Validar Fechamento` registra na Auditoria a versao/status atual
  do projeto apos o operador atualizar a tela.

## [1.2.6] - 2026-06-16

### Alterado
- Busca de destino para Copiar Local e Copiar Cloud agora considera as raizes
  `\\citel-fileserve\Modem\VERSOES\379_Versoes\379.48.02\379.48.2.30` e
  `\\citel-fileserve\Modem\_VERSOES_INDIVIDUAIS`.

## [1.2.5] - 2026-06-16

### Adicionado
- Apos Copiar Local ou Copiar Cloud concluir com destino verificado, o caminho
  do destino e copiado automaticamente para a area de transferencia e exibido
  na mensagem final.

## [1.2.4] - 2026-06-11

### Alterado
- Copiar Local agora tambem aceita destino de rede com sufixo `_LOCAL`, mantendo
  a validacao de seguranca antes da copia.

## [1.2.3] - 2026-06-11

### Corrigido
- Limpeza da origem apos copia verificada agora faz retentativas antes de
  registrar `erro_limpeza`, reduzindo falhas temporarias por arquivo `.zip`
  ainda travado por outro processo.

## [1.2.2] - 2026-06-11

### Alterado
- A copia envia somente arquivos `.zip` da pasta do projeto para o destino,
  sem copiar `comandosCMD` ou executaveis soltos.
- Apos copia concluida e verificada com sucesso, a pasta de origem no
  Diretorio-base e limpa automaticamente, preservando apenas `comandosCMD`.
- Tela de confirmacao da copia passou a informar que somente `.zip` sera enviado
  e que a origem sera limpa apos a validacao.
- Auditoria da copia concluida registra quantos itens foram removidos da origem
  e que `comandosCMD` foi preservada.
- Falha na limpeza da origem agora e auditada como `erro_limpeza`, deixando
  claro que a copia ja foi concluida e verificada no destino.

## [1.2.1] - 2026-06-11

### Corrigido
- Fluxos de fechamento e copia deixaram de chamar `after()` do Tkinter a partir
  de threads secundarias; agora usam `queue.Queue` com polling pela thread da
  interface.
- Verificacao pos-copia passou a calcular SHA-256 dos arquivos criticos
  `Autcom.exe` e `autcom.zip`, detectando divergencia mesmo quando o tamanho do
  arquivo e igual.
- Auditoria da copia concluida passou a registrar os hashes SHA-256 calculados
  para arquivos criticos.
- Logging foi movido para `app/logging_utils.py`, mantendo rotacao mensal sem
  acoplar a tela de Auditoria ao modulo de acoes.
- Documentacao de implantacao reforca que `settings.json` do ambiente de
  desenvolvimento nao deve ser distribuido sem revisao.

### Alterado
- Versao do aplicativo atualizada para `1.2.1`.
- Suite de testes ampliada para cobrir verificacao de hash de arquivos criticos.

## [1.2.0] - 2026-06-10

### Adicionado
- Deteccao de ZIPs com nome diferente do arquivo interno, incluindo arquivos
  numerados pelo Windows como `autcom (1).zip`.
- Bloqueio de Fechamento Local/Cloud e Copiar Local/Cloud quando houver ZIPs
  com nome incorreto, com mensagem explicativa e registro em log.
- Auditoria padronizada para fechamento e copia, registrando usuario, projeto,
  tipo, origem, destino, BAT, versoes, tamanho do Autcom, resultado e motivo.
- Auditoria dos bloqueios por tipo no fechamento (Local em projeto Cloud e
  Cloud em projeto Local).
- Tela de validacao de seguranca antes da copia, com botao `Continuar`
  bloqueado quando houver divergencia.
- Bloqueio de copia quando a busca na rede encontra mais de uma pasta de
  destino com o mesmo nome do projeto.
- Contagem de arquivos copiados e verificados no registro de conclusao da
  auditoria.
- Logs rotacionados por mes no formato `logs/fechamentos-AAAA-MM.log`.
- Documentacao operacional em `docs/MANUAL_OPERACIONAL.md` e roteiro de
  apresentacao em `docs/APRESENTACAO.md`.
- Cache da validacao de nomes de ZIP por pasta, invalidado por nome, tamanho e
  data de modificacao dos arquivos.
- Os grupos essenciais (`Autcom`, `AutcomTinta`, `Autban`, `Auttin`) agora sao
  sempre exigidos, mesmo quando o padrao aprendido por projeto estiver
  incompleto.

### Alterado
- Pendencia de nome de ZIP passou a ser tratada visualmente como erro critico
  na tela principal.
- Tela de Auditoria passou a atualizar automaticamente, manter filtros e abrir
  por padrao o log mensal mais recente.
- `LOGS_DIRECTORY` foi centralizado em `runtime.py`, evitando dependencia da
  tela de Auditoria no modulo de acoes.
- README atualizado com checklist de seguranca, bloqueio de destino duplicado,
  rotacao mensal de logs e orientacao de instalacao em pasta com permissao de
  escrita.
- Suite de testes ampliada para 58 testes, incluindo regressao de caixa preta.

## [1.1.0] - 2026-06-10

### Corrigido
- Tela Detalhes passou a mostrar todos os grupos essenciais com status
  individual (antes escondia os arquivos com versao divergente, aparecendo
  vazia justamente quando havia problema).
- Tela Detalhes nao trava mais a interface: a leitura das versoes, incluindo
  extracao de zips grandes, roda em segundo plano com indicador de carregamento.
- Lista de Pastas Ignoradas vazia passou a ser respeitada (antes, salvar a lista
  vazia fazia os padroes voltarem na proxima leitura).
- Chamadas ao Tkinter a partir de threads secundarias protegidas em todos os
  fluxos (fechamento, busca de destino e copia), eliminando risco de crash
  intermitente.

### Adicionado
- Suite de testes automatizados (`tests/`, 41 testes via `unittest`):
  `python -m unittest discover -s tests -t .`
- Verificacao pos-copia: os tamanhos dos arquivos no destino sao conferidos ao
  final; divergencias geram erro detalhado listando os arquivos.
- Destino de copia configuravel pela tela `Configuracoes` (persistido em
  `settings.json`; antes era fixo no codigo).
- Janela de busca na rede mostra a pasta que esta sendo varrida; erros de
  acesso durante a busca sao registrados no log.
- Destaque por cor na tela Detalhes (verde OK, vermelho com pendencia).
- Numero de versao do aplicativo exibido no titulo da janela.

### Alterado
- `settings.json` e `project_pattern.json` gravados de forma atomica (arquivo
  temporario + substituicao), evitando corrupcao se o app fechar durante a
  gravacao.
- Cache de versoes de ZIP reestruturado para uma entrada por arquivo
  (substituida quando o zip muda), eliminando crescimento de memoria sem limite
  em uso continuo.
- Aprendizado de padrao no fechamento ficou mais completo (inclui arquivos sem
  validacao de versao) e mais rapido (nao extrai zips desnecessariamente).
- Janelas de progresso protegidas contra fechamento acidental durante
  operacoes.
- README reescrito refletindo o comportamento atual (configuracao, regras,
  acoes, testes e build).

## [1.0.0] - 2026-06-08

### Adicionado
- Varredura do diretorio-base com validacao de `FileVersion` e
  `ProductVersion` dos executaveis contra o padrao do nome da pasta.
- Leitura de versao de executaveis soltos e dentro de `.zip` (via WinAPI/ctypes,
  sem dependencias externas).
- Interface Tkinter com filtros, busca, ordenacao por coluna e atualizacao
  automatica a cada 5 minutos.
- Acoes de Fechamento Local/Cloud (execucao de BAT) e Copiar Local/Cloud
  (copia para servidor de rede) com regras de habilitacao por tipo de projeto e
  tamanho do `Autcom.exe`.
- Padrao de arquivos essenciais aprendido a partir de pastas modelo
  (`project_pattern.json`).
- Pastas ignoradas configuraveis e log de auditoria mensal
  (`logs/fechamentos-AAAA-MM.log`) com tela de consulta.
- Build de executavel via PyInstaller (`VarreduraSistema.spec`).
