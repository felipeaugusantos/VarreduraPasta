# Ajuste de Insert

Ferramenta desktop em Python/Tkinter para ajustar comandos `INSERT` e `REPLACE`.

Versao atual: `1.2.1` (`stable`) - release `2026-05-21`.

O aplicativo permite colar ou abrir um SQL, visualizar campos e valores em tabela, editar valores, excluir colunas ou linhas, gerar um novo SQL, copiar para o clipboard e salvar o resultado em `.sql`.

## Formatos Suportados

```sql
INSERT INTO tabela (col1, col2) VALUES (1, 'texto');
```

```sql
INSERT INTO tabela VALUES (1, 'texto');
```

```sql
REPLACE INTO tabela (col1, col2) VALUES (1, 'texto');
```

```sql
INSERT INTO tabela (col1, col2) VALUES (1, 'a'), (2, 'b');
```

```sql
INSERT INTO tabela (col1, col2) VALUES (1, 'a');
INSERT INTO tabela (col1, col2) VALUES (2, 'b');
```

Tambem sao suportados comentarios SQL fora de strings:

```sql
-- comentario
/* comentario */
# comentario MySQL
```

Strings com virgula, ponto e virgula, parenteses e aspas escapadas sao preservadas:

```sql
INSERT INTO t (texto) VALUES ('O''Reilly, valor (teste); ok');
```

## Formatos Nao Suportados

Estes formatos sao bloqueados com mensagem clara:

- `INSERT IGNORE`
- `INSERT ... SET`
- `INSERT ... SELECT`
- `ON DUPLICATE KEY UPDATE`
- comandos diferentes de `INSERT` ou `REPLACE`
- texto residual apos os grupos de `VALUES`
- mistura de `INSERT` e `REPLACE` no mesmo processamento
- multiplos statements com tabelas ou colunas diferentes

## Edicao de Valores

Ao alterar valores pela interface:

- campo vazio vira `NULL`
- `NULL` vira `NULL`
- numeros ficam sem aspas
- texto comum recebe aspas simples
- aspas internas sao escapadas

Para inserir uma expressao SQL sem aspas, use o prefixo `=`:

```text
=NOW()          -> NOW()
=CURRENT_DATE  -> CURRENT_DATE
=UUID()         -> UUID()
=TRUE           -> TRUE
=FALSE          -> FALSE
```

Sem o `=`, o valor e tratado como texto:

```text
NOW() -> 'NOW()'
```

## Como Executar

```powershell
python principal.py
```

Ou usando a virtualenv do projeto:

```powershell
.\.venv\Scripts\python.exe principal.py
```

## Como Rodar Testes

Instale as dependencias de desenvolvimento quando necessario:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

```powershell
pytest
```

Ou:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Como Gerar o Executavel

```powershell
.\build.ps1
```

O executavel sera gerado em:

```text
dist\AjusteInsert.exe
```

## Como Verificar o Executavel com SHA256

Depois de gerar o executavel, gere o arquivo de hash da release:

```powershell
.\gerar_hash_release.ps1
```

O comando cria:

```text
SHA256SUMS.txt
releases\v1.2.1\SHA256SUMS.txt
```

Para conferir manualmente o hash do executavel:

```powershell
Get-FileHash -Algorithm SHA256 .\dist\AjusteInsert.exe
```

Compare o valor com o conteudo de `releases\v1.2.1\SHA256SUMS.txt`.

## Estrutura

```text
app/
  main.py
  core/
    exceptions.py
    models.py
    parser_insert.py
    sql_formatador.py
  ui/
    dialogs.py
    main_window.py
    styles.py
tests/
```

## Aviso de Seguranca

Revise sempre o SQL gerado antes de executar em producao. A ferramenta ajuda a transformar e validar comandos, mas a responsabilidade final pela execucao no banco continua sendo do operador.
