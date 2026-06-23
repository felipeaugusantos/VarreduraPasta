# Changelog

Todas as alteracoes relevantes deste projeto sao documentadas neste arquivo.

O formato e baseado em Keep a Changelog e o projeto segue Versionamento
Semantico (`MAIOR.MENOR.CORRECAO`).

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
