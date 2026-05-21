# Changelog

## 1.2.1 - 2026-05-21

### Adicionado

- Publicacao de releases em pastas versionadas em `releases/vX.Y.Z`.
- Preservacao do artefato congelado `v1.2.0` em `releases/v1.2.0`.

### Alterado

- Versao da aplicacao atualizada para `1.2.1`.
- Script de hash passa a copiar o executavel atual para a pasta da versao antes de gerar o manifesto.

### Corrigido

- Evita sobrescrever artefatos de release anteriores durante a preparacao de uma nova versao.

### Seguranca

- Manifesto SHA256 passa a ser gerado junto ao executavel versionado, facilitando verificacao do artefato distribuido.

### Build

- `gerar_hash_release.ps1` agora cria/usa `releases/v1.2.1` para a release atual.

## 1.2.0 - 2026-05-21

### Adicionado

- Estrutura modular em `app/core` e `app/ui`.
- Metadados centralizados de versão em `app/version.py`.
- Suporte a comentários MySQL com `#`.
- Suporte a expressão SQL sem aspas usando prefixo `=`.
- Histórico de desfazer/refazer para alterações de dados.
- Botão para salvar SQL gerado em arquivo `.sql`.
- Testes automatizados com `pytest`.

### Alterado

- Parser SQL ficou mais restritivo e valida somente formatos suportados.
- Leitura de arquivos tenta múltiplos encodings: `utf-8-sig`, `utf-8`, `cp1252`, `latin1`.
- Tela Sobre passa a exibir versão, build, data da release e autor.
- Build passa a executar `pytest` antes de gerar o executável.

### Corrigido

- Correção do índice visual no editor inline da Treeview.
- Bloqueio de geração quando há linhas sem valores compatíveis com as colunas.
- Preservação de strings contendo vírgula, ponto e vírgula, comentários falsos e parênteses.
- Validação de índice antes de alteração de linha específica.

### Segurança

- Bloqueio explícito de `INSERT IGNORE`, `INSERT ... SET`, `INSERT ... SELECT` e `ON DUPLICATE KEY UPDATE`.
- Bloqueio de texto residual após os grupos `VALUES`.
- Mensagens de erro mais claras para SQL não suportado.
- Saída SQL antiga é limpa quando a geração é bloqueada ou o estado é alterado.

### Build

- Ícone incluído no executável.
- Script `build.ps1` para rodar testes e gerar `dist/AjusteInsert.exe`.
- Script `gerar_hash_release.ps1` para gerar `SHA256SUMS.txt`.
