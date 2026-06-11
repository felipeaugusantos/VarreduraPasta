# Manual Operacional - Sistema de Acompanhamento de Copia

## 1. Objetivo

O Sistema de Acompanhamento de Copia auxilia na validacao das versoes fechadas antes do fechamento e da copia para a rede.

A ferramenta analisa as pastas de projetos, identifica se o projeto e Local ou Cloud, valida arquivos essenciais, confere FileVersion e ProductVersion, verifica o tamanho do Autcom, aponta pendencias de ZIP e registra as acoes em Auditoria.

## 2. Como abrir o sistema

Para uso em producao, execute:

```text
VarreduraSistema.exe
```

O executavel fica dentro da pasta:

```text
dist\VarreduraSistema
```

Importante: use a pasta inteira `VarreduraSistema`, pois ela contem arquivos internos, configuracoes e logs.

Instale em uma pasta com permissao de escrita, por exemplo:

```text
C:\VarreduraSistema
```

Evite `C:\Program Files`, pois o sistema precisa gravar `settings.json`, `project_pattern.json` e arquivos em `logs`.

Antes de distribuir o pacote para outra maquina, exclua `settings.json` da pasta enviada ou revise seu conteudo. Se o arquivo nao existir, o sistema cria um novo com os valores padrao.

## 3. Configuracoes

As configuracoes ficam no menu `Configuracoes`.

### 3.1 Diretorio-base

Informe a pasta onde ficam as versoes fechadas.

Padrao:

```text
C:\VERSOES_FECHADAS
```

O sistema analisa as subpastas dentro desse diretorio.

### 3.2 Destino de copia

Informe a pasta raiz da rede onde a copia deve localizar o projeto de destino.

Padrao:

```text
\\citel-fileserve\Modem\_VERSOES_INDIVIDUAIS
```

Ao copiar, o sistema procura dentro dessa raiz uma pasta com o mesmo nome do projeto selecionado.

### 3.3 Pastas ignoradas

Use essa tela para cadastrar pastas que nao devem aparecer na varredura.

Exemplos:

```text
pastaTeste
PastaTesteCopia_72
PastaTesteCopia_74
```

Pastas ignoradas nao entram na tela principal, mesmo que tenham arquivos `.exe`, `.dll` ou `.zip`.

### 3.4 Padrao de arquivos

Use essa tela para gravar o padrao de arquivos essenciais com base em projetos modelo Local e Cloud.

O sistema tambem aprende padroes especificos quando o fechamento e executado, usando os arquivos validos do projeto selecionado.

## 4. Tela principal

A tela principal apresenta os projetos encontrados no Diretorio-base.

Observacao: a primeira varredura apos abrir o sistema pode ser mais lenta quando existirem ZIPs grandes. Isso e esperado, pois o sistema precisa ler informacoes dos arquivos. As proximas varreduras na mesma execucao tendem a ser mais rapidas por uso de cache em memoria.

### Colunas

- `Projeto`: nome da pasta analisada.
- `Tipo`: Local ou Cloud.
- `FileVersion`: versao encontrada no Autcom.
- `ProductVersion`: product version encontrada no Autcom.
- `File esperado`: versao esperada calculada pelo nome da pasta.
- `Product esperado`: product version esperada calculada pelo nome da pasta.
- `Autcom MB`: tamanho do Autcom em MB.
- `Origem`: indica se o Autcom veio do EXE ou ZIP.
- `Status`: resultado final da validacao.

### Cores

- Verde: projeto OK.
- Vermelho: erro critico, como arquivo ausente, versao incorreta ou pendencia de ZIP.
- Amarelo: pendencia de atencao.

## 5. Regras de versao

O sistema calcula as versoes esperadas pelo nome da pasta.

Exemplo:

```text
379.48.2.30.74.150.90
```

Resultado esperado:

```text
FileVersion: 48.02.30.74
ProductVersion: 02.30.74.150
```

Projetos com `_CLOUD` seguem a mesma regra, ignorando o sufixo para calcular a versao.

## 6. Arquivos essenciais

Os grupos principais validados sao:

```text
Autcom.exe
AutcomTinta.exe ou AutcomTinta.dll
libAutban.dll, libAutban.exe ou AutBan.exe
libAuttin.dll, libAuttin.exe ou Auttin.exe
```

Dependendo do padrao aprendido por projeto, outros arquivos tambem podem ser considerados essenciais.

## 7. Status mais comuns

### OK

O projeto passou nas validacoes principais.

### Versao incorreta

FileVersion ou ProductVersion nao bate com o esperado.

### Arquivo ausente

Um arquivo essencial nao foi encontrado na pasta ou no ZIP correspondente.

### Pendencia ZIP

Existe problema relacionado a ZIP.

### Nome de ZIP incorreto

O ZIP tem nome diferente do arquivo interno.

Exemplo bloqueado:

```text
autcom (1).zip contem AutBan.exe; esperado AutBan.zip
```

Esse caso deve ser corrigido antes de fechamento ou copia.

## 8. Acoes

### 8.1 Fechamento Local

Disponivel apenas para projeto Local, ou seja, pasta sem `_CLOUD`.

Ao executar, o sistema entra na pasta:

```text
<Projeto>\comandosCMD
```

E executa:

```text
_FechamentoArquivos.bat
```

### 8.2 Fechamento Cloud

Disponivel apenas para projeto Cloud, ou seja, pasta com `_CLOUD`.

Executa o mesmo BAT:

```text
<Projeto>\comandosCMD\_FechamentoArquivos.bat
```

### 8.3 Copiar Local

Permitido quando:

- O projeto e Local.
- O Autcom esta abaixo de 100 MB.
- O projeto esta OK.
- Nao existem ZIPs com nome incorreto.
- Existe exatamente um destino com o mesmo nome na rede.

### 8.4 Copiar Cloud

Permitido quando:

- O projeto e Cloud.
- O Autcom esta acima de 200 MB.
- O projeto esta OK.
- Nao existem ZIPs com nome incorreto.
- Existe exatamente um destino com o mesmo nome na rede.

## 9. Validacao antes da copia

Antes de copiar, o sistema mostra uma tela de confirmacao com origem, destino e validacoes.

O botao `Continuar` so fica habilitado se todas as validacoes estiverem OK.

Validacoes realizadas:

- Tipo do projeto.
- Nome do destino.
- Destino dentro da raiz configurada.
- FileVersion.
- ProductVersion.
- Autcom MB.
- Limite Local ou Cloud.
- Status do projeto.
- Nome dos ZIPs.

Se houver qualquer bloqueio, revise o motivo apresentado na tela e na Auditoria.

## 10. Auditoria

A Auditoria fica em:

```text
Auditoria > Logs
```

Os arquivos de log sao mensais:

```text
logs\fechamentos-AAAA-MM.log
```

A tela atualiza automaticamente e mostra os registros mais recentes no topo.

### Campos registrados

Cada acao critica segue o padrao:

```text
[data hora] Acao resultado | usuario=... | projeto=... | tipo=... | origem=... | destino=... | bat=... | fileversion=... | productversion=... | autcom_mb=... | motivo=...
```

Resultados comuns:

```text
iniciado
processo_aberto
padrao_gravado
padrao_nao_gravado
bloqueado
cancelado
concluido
erro
```

## 11. Como enviar evidencias para suporte

Quando houver duvida ou erro, envie:

1. Nome do projeto.
2. Print da tela principal.
3. Print da tela de validacao, se for copia.
4. Arquivo:

```text
logs\fechamentos-AAAA-MM.log
```

## 12. Checklist antes de operar em producao

Antes de executar fechamento ou copia:

- Confirme se o Diretorio-base esta correto.
- Confirme se o destino de copia esta correto.
- Atualize a tela.
- Selecione o projeto correto.
- Verifique se o status esta OK.
- Confirme se o tipo e Local ou Cloud.
- Confira FileVersion e ProductVersion.
- Confira o tamanho do Autcom.
- Abra Detalhes se houver duvida.
- Apos executar, confira a Auditoria.

## 13. Recomendacao de liberacao

Para primeira execucao em producao, recomenda-se:

1. Testar um projeto Local OK.
2. Conferir Auditoria.
3. Testar um projeto Cloud OK.
4. Conferir Auditoria.
5. Somente depois liberar uso recorrente.
