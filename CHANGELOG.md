# Changelog

Todas as alterações relevantes deste projeto são documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o projeto segue [Versionamento Semântico](https://semver.org/lang/pt-BR/)
(`MAIOR.MENOR.CORREÇÃO`).

## [1.1.0] - 2026-06-10

### Corrigido
- Tela Detalhes passou a mostrar **todos** os grupos essenciais com status
  individual (antes escondia os arquivos com versão divergente, aparecendo
  vazia justamente quando havia problema).
- Tela Detalhes não trava mais a interface: a leitura das versões (incluindo
  extração de zips grandes) roda em segundo plano com indicador de
  carregamento.
- Lista de Pastas Ignoradas vazia passou a ser respeitada (antes, salvar a
  lista vazia fazia os padrões voltarem na próxima leitura).
- Chamadas ao Tkinter a partir de threads secundárias protegidas em todos os
  fluxos (fechamento, busca de destino e cópia), eliminando risco de crash
  intermitente.

### Adicionado
- Suíte de testes automatizados (`tests/`, 41 testes via `unittest`):
  `python -m unittest discover -s tests -t .`
- Verificação pós-cópia: os tamanhos dos arquivos no destino são conferidos ao
  final; divergências geram erro detalhado listando os arquivos.
- Destino de cópia configurável pela tela `Configurações` (persistido em
  `settings.json`; antes era fixo no código).
- Janela de busca na rede mostra a pasta que está sendo varrida; erros de
  acesso durante a busca são registrados no log.
- Destaque por cor na tela Detalhes (verde OK, vermelho com pendência).
- Número de versão do aplicativo exibido no título da janela.

### Alterado
- `settings.json` e `project_pattern.json` gravados de forma atômica
  (arquivo temporário + substituição), evitando corrupção se o app fechar
  durante a gravação.
- Cache de versões de ZIP reestruturado para uma entrada por arquivo
  (substituída quando o zip muda), eliminando crescimento de memória sem
  limite em uso contínuo.
- Aprendizado de padrão no fechamento ficou mais completo (inclui arquivos sem
  validação de versão) e mais rápido (não extrai zips desnecessariamente).
- Janelas de progresso protegidas contra fechamento acidental durante
  operações.
- README reescrito refletindo o comportamento atual (configuração, regras,
  ações, testes e build).

## [1.0.0] - 2026-06-08

### Adicionado
- Varredura do diretório-base com validação de `FileVersion` e
  `ProductVersion` dos executáveis contra o padrão do nome da pasta.
- Leitura de versão de executáveis soltos e dentro de `.zip`
  (via WinAPI/ctypes, sem dependências externas).
- Interface Tkinter com filtros, busca, ordenação por coluna e atualização
  automática a cada 5 minutos.
- Ações de Fechamento Local/Cloud (execução de BAT) e Copiar Local/Cloud
  (cópia para servidor de rede) com regras de habilitação por tipo de projeto
  e tamanho do `Autcom.exe`.
- Padrão de arquivos essenciais aprendido a partir de pastas modelo
  (`project_pattern.json`).
- Pastas ignoradas configuráveis e log de auditoria (`logs/fechamentos.log`)
  com tela de consulta.
- Build de executável via PyInstaller (`VarreduraSistema.spec`).
