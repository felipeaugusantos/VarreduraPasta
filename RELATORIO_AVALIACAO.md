# Relatório de Avaliação — Sistema de Acompanhamento de Cópia

**Última atualização:** 10/06/2026 — Revalidação dos ajustes de proteção contra ZIPs renomeados
**Escopo:** análise completa do código-fonte (`main.py` e módulos `app/`).

---

## Revalidação 10/06 (tarde) — Ajustes do usuário: proteção contra ZIPs com nome incorreto

**Contexto:** incidente real no projeto `379.48.2.30.74.111.172` — 156 zips de componentes
ficaram nomeados `autcom (N).zip` após o Fechamento Local das 15:19. Os ajustes analisados
nesta revalidação adicionam detecção e bloqueio para esse cenário.

### O que foi adicionado (validado, funcionando)

| Ajuste | Onde | Avaliação |
|---|---|---|
| `validate_zip_names()`: detecta zip com um único arquivo interno cujo nome não bate com o nome do zip (normalizando o sufixo ` (N)`), informando o nome correto esperado | `scanner.py` | ✅ Correto. Teste real: **156 erros detectados na pasta do incidente, 0 no projeto saudável 111.173** |
| Status do projeto passa a incluir `Nome de ZIP incorreto: ...` (com limite de 3 no resumo) e novo campo `ProjectResult.zip_name_errors` | `scanner.py` | ✅ Correto, propagado para a UI |
| `_block_on_zip_name_errors()`: **bloqueia** Fechamento Local/Cloud e Copiar Local/Cloud quando há zips com nome errado, com diálogo explicativo e registro no log | `actions.py` | ✅ Correto — impede que o problema se propague para a rede |
| `_merge_required_core_groups()`: os 4 grupos essenciais (Autcom, AutcomTinta, Autban, Auttin) passam a ser **sempre** exigidos, mesmo quando o padrão aprendido não os contém | `scanner.py` | ✅ Correção importante — antes um padrão aprendido podia omitir o Autcom e o projeto aparecia OK |
| `_short_status` reordenado: pendência de ZIP agora classifica antes de versão incorreta | `interface.py` | ✅ OK (mudança de comportamento intencional: projeto com ambos os problemas aparece como "Pendência ZIP") |
| 5 testes novos (merge de grupos essenciais, status com erro de nome, 3 cenários de `validate_zip_names`) | `tests/test_scanner.py` | ✅ Suíte completa: **46 testes, todos passando** |

### Observações (não aplicadas — para decisão)

1. **Organização dos testes:** 4 testes de `_version_errors` (`test_wrong_file_version_is_reported`,
   `test_wrong_product_version_is_reported`, `test_checks_without_version_validation_are_skipped`,
   `test_non_ok_checks_are_skipped`) ficaram dentro da classe `RequiredCoreGroupsTests` em vez de
   `VersionErrorsTests`. Continuam rodando — é só organização.
2. **Zip numerado com conteúdo "certo" passa na validação, mas é invisível para o scanner:**
   `autcom (1).zip` contendo `autcom.exe` é considerado OK pela `validate_zip_names` (há teste
   afirmando isso), porém o índice de zips (`_build_zip_index`/`_find_zip_file`) procura pelo stem
   exato `autcom` — esse zip não seria encontrado na checagem de versão. Vale decidir: ou acusar
   todo zip numerado, ou normalizar o stem também no índice do scanner.
3. **Custo por varredura:** `validate_zip_names` abre todos os zips de todos os projetos a cada
   varredura (atualização automática de 5 min), sem cache. A leitura é só do diretório central do
   zip (rápida), mas em pastas grandes/rede adiciona I/O. Um cache por (mtime, tamanho), como o de
   versões, eliminaria o custo.
4. **Bloqueio roda na thread da UI:** `_block_on_zip_name_errors` valida sincronamente no clique
   do botão. Em pasta com centenas de zips em rede pode haver um congelamento breve.
5. **Severidade visual:** erro de nome de ZIP aparece como "Pendência ZIP" (amarelo/warning),
   mas a ação correspondente é bloqueada — talvez mereça vermelho/error para chamar atenção.
6. **Versionamento:** os ajustes ainda não estão commitados nem registrados no CHANGELOG
   (sugestão: versão 1.2.0 — "Detecção e bloqueio de ZIPs com nome incorreto").

---

## 1. Visão geral

O sistema é um utilitário interno em Python/Tkinter que varre o diretório de versões fechadas, valida versões de executáveis (FileVersion/ProductVersion) contra o padrão do nome da pasta e executa ações de fechamento (BAT) e cópia para servidor de rede.

| Aspecto | Avaliação |
|---|---|
| Arquitetura / organização | ✅ Muito boa |
| Threading (varredura, detalhes, ações) | ✅ Corrigido |
| Tela Detalhes | ✅ Corrigida (todos os arquivos, destaque por cor, carregamento em 2º plano) |
| Persistência (settings e padrões) | ✅ Escrita atômica |
| Verificação pós-cópia | ✅ Implementada |
| Configurabilidade | ✅ Diretório-base e destino de cópia configuráveis pela UI |
| Testes automatizados | ✅ 41 testes (unittest, stdlib) — todos passando |
| Documentação (README) | ✅ Atualizada |

---

## 2. Pontos fortes

1. **Separação clara de responsabilidades** — cada módulo com papel único (`scanner`, `interface`, `actions`, `pattern`, `settings`, `version_reader`, `audit`, `runtime`).
2. **Trabalho pesado fora da thread da UI** — varredura, enriquecimento de versões na tela Detalhes e ações de fechamento/cópia rodam em threads, com proteção contra resultados obsoletos (`scan_generation`) e contra janelas destruídas.
3. **Leitura de versão via ctypes/WinAPI** — rápida, sem dependências externas; o projeto inteiro (app + testes) roda só com a stdlib, facilitando o build PyInstaller.
4. **Cache de versões em ZIP autolimitado** — uma entrada por arquivo, invalidada e substituída quando o zip muda (`mtime` + tamanho); sem crescimento de memória em uso contínuo.
5. **Guard-rails nas ações perigosas** — validação dupla nos botões, confirmação com origem/destino antes de copiar, verificação de tamanhos pós-cópia, log de auditoria com tela de consulta, janelas de progresso protegidas contra fechamento acidental.
6. **Persistência robusta** — `settings.json` e `project_pattern.json` gravados de forma atômica (arquivo temporário + `replace`), com fallback para defaults quando corrompidos.
7. **Cobertura de testes da lógica de negócio** — parsing de versão do nome da pasta, montagem de status, chaves/normalização de padrões, persistência de configurações e formatação de versão.

## 3. Histórico de correções aplicadas

| # | Item do relatório original | Status |
|---|---|---|
| 1 | Tela Detalhes escondia arquivos com versão errada | ✅ Mostra todos os grupos com status individual e destaque em vermelho |
| 2 | Tela Detalhes travava a UI | ✅ Enriquecimento em thread com indicador de carregamento |
| 3 | Impossível esvaziar lista de pastas ignoradas | ✅ Lista vazia é respeitada |
| 4 | Tkinter chamado de threads secundárias | ✅ Fluxos protegidos (fechamento, busca e cópia) |
| 5 | Zero testes automatizados | ✅ `tests/` com 41 testes (`python -m unittest discover -s tests -t .`) |
| 6 | Busca na rede sem feedback e com erros engolidos | ✅ Janela mostra a pasta sendo varrida; erros de acesso vão para o log |
| 7 | Cópia sem verificação de integridade | ✅ Conferência de tamanhos no destino; divergências geram erro detalhado |
| 8 | Cache de ZIP crescia sem limite | ✅ Cache autolimitado (substituição in-place) |
| 9 | Destino de cópia hardcoded | ✅ Configurável em `Configurações` (persistido em `settings.json`) |
| 10 | README desatualizado | ✅ Reescrito (configuração, uso, regras, ações, testes, build) |
| 11 | `os_walk` com import interno | ✅ Removido; `os.walk` direto |

## 4. Sugestões futuras (opcionais)

- **Limites de 100/200 MB configuráveis** — hoje seguem fixos em `config.py` (regra de negócio estável; mover para `settings.json` se passar a variar).
- **Hash (ex.: SHA-256) na verificação pós-cópia** — a conferência atual é por tamanho; hash dá garantia mais forte ao custo de reler os arquivos pela rede.
- **Botão Cancelar na busca/cópia de rede** — as threads atuais não são canceláveis; exigiria sinalização cooperativa no worker.
- **CI simples (GitHub Actions)** — rodar a suíte de testes a cada push.

---

## Histórico

- **10/06/2026 12h** — Análise original: 6 pontos fortes, 11 pontos de melhoria.
- **10/06/2026 14h** — Revalidação: escrita atômica do padrão, aprendizado de padrão melhorado, thread-safety parcial.
- **10/06/2026 (tarde)** — Rodada de profissionalização: todos os 11 itens resolvidos; suíte com 41 testes passando.
