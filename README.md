# Sistema de Acompanhamento de Copia

Aplicacao Python (Tkinter, apenas biblioteca padrao) que varre o diretorio de
versoes fechadas, valida as versoes dos executaveis contra o padrao do nome da
pasta e executa as acoes de fechamento (BAT) e copia para o servidor de rede.

## Executar

```powershell
python main.py
```

## Testes

```powershell
python -m unittest discover -s tests -t .
```

## Build (executavel)

```powershell
pyinstaller VarreduraSistema.spec
```

## Implantacao em producao

Instale a pasta completa do executavel em um local com permissao de escrita,
por exemplo:

```text
C:\VarreduraSistema
```

Evite instalar em `C:\Program Files`, pois o aplicativo grava `settings.json`,
`project_pattern.json` e `logs\fechamentos-AAAA-MM.log` ao lado do executavel.
Sem permissao de escrita, configuracoes e auditoria podem falhar.

Antes de distribuir o pacote, exclua `settings.json` da pasta enviada ou revise
seu conteudo. O sistema cria esse arquivo automaticamente com os valores padrao
quando ele nao existe; distribuir o arquivo do ambiente de desenvolvimento pode
levar caminhos locais para outra maquina.

## Configuracao

Tudo fica em `settings.json`, ao lado do executavel (criado automaticamente):

- `Configuracoes > Diretorio-base`: pasta analisada (padrao `C:\VERSOES_FECHADAS`)
  e destinos adicionais de copia na rede. Por padrao, a busca de destino considera
  `\\citel-fileserve\Modem\VERSOES\379_Versoes\379.48.02\379.48.2.30` e
  `\\citel-fileserve\Modem\_VERSOES_INDIVIDUAIS`.
- `Configuracoes > Padrao de Arquivos`: seleciona uma pasta modelo Local e uma
  Cloud; os arquivos encontrados nelas viram o padrao essencial aplicado em
  todas as versoes (gravado em `project_pattern.json`).
- `Configuracoes > Pastas Ignoradas`: pastas excluidas da varredura.
- `Auditoria > Logs`: consulta os registros mensais em `logs/fechamentos-AAAA-MM.log`.

## Uso

- Clique duas vezes em um projeto (ou use `Abrir Pasta`) para abrir a pasta no
  Explorer.
- `Detalhes` mostra todos os grupos essenciais do projeto com arquivo
  encontrado, origem (pasta ou zip), versoes, tamanho e status individual;
  linhas com pendencia ficam destacadas em vermelho.
- A interface atualiza automaticamente a cada 5 minutos; `Atualizar` força nova
  varredura.
- A primeira varredura apos abrir o sistema pode levar mais tempo quando houver
  ZIPs grandes. As varreduras seguintes na mesma execucao tendem a ser mais
  rapidas por uso de cache em memoria.

## Regras de validacao

- Exemplo de pasta: `379.48.2.30.74.150.90`
  - `FileVersion` esperado: `48.02.30.74` (partes 2 a 5 do nome)
  - `ProductVersion` esperado: `02.30.74.150` (partes 3 a 6 do nome)
- Grupos essenciais padrao (aceitam alternativas `.exe`/`.dll`):
  - `Autcom.exe`
  - `AutcomTinta.exe` ou `AutcomTinta.dll`
  - `libAutban.dll`, `libAutban.exe` ou `AutBan.exe`
  - `libAuttin.dll`, `libAuttin.exe` ou `Auttin.exe`
- As colunas `FileVersion`, `ProductVersion` e `Autcom MB` usam o `Autcom.exe`
  solto; quando ele nao existe, usam o `Autcom.exe` dentro de `autcom.zip`.
- Pastas com sufixo `_CLOUD` sao projetos Cloud; as demais sao Local.

## Acoes

- `Fechamento Local` / `Fechamento Cloud`: executa
  `comandosCMD\_FechamentoArquivos.bat` do projeto em um novo console e grava o
  padrao de arquivos aprendido. Habilitado conforme o tipo do projeto
  (Local sem `_CLOUD`; Cloud com `_CLOUD`).
- `Validar Fechamento`: apos o BAT terminar, clique em `Atualizar` e use este
  botao para registrar na Auditoria a versao/status final do projeto.
- `Copiar Local`: permitido quando `Autcom.exe` esta abaixo de 100 MB.
- `Copiar Cloud`: permitido quando `Autcom.exe` esta acima de 200 MB.
- A copia procura no destino de rede uma pasta com o mesmo nome do projeto,
  pede confirmacao mostrando origem e destino e envia somente os arquivos
  `.zip` da pasta do projeto.
- Depois da copia concluida e verificada, a pasta de origem no Diretorio-base e
  limpa automaticamente, preservando apenas `comandosCMD`.
- Quando existe mais de uma pasta de destino com o mesmo nome do projeto, o
  sistema mostra uma tela de escolha antes da confirmacao da copia.
- Fechamentos e copias sao bloqueados quando existem ZIPs com nome diferente do
  arquivo interno.
- Todas as acoes geram registro mensal em `logs/fechamentos-AAAA-MM.log`.

## Checklist de seguranca antes da copia

- Confirme se o projeto selecionado e Local ou Cloud.
- Verifique se o status esta `OK`.
- Confira `FileVersion`, `ProductVersion` e `Autcom MB`.
- Confirme se a tela de validacao mostra origem e destino corretos.
- Nao continue se houver destino duplicado, ZIP incorreto ou versao divergente.
- Apos a acao, consulte `Auditoria > Logs` para confirmar o resultado.
