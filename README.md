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

## Configuracao

Tudo fica em `settings.json`, ao lado do executavel (criado automaticamente):

- `Configuracoes > Diretorio-base`: pasta analisada (padrao `C:\VERSOES_FECHADAS`)
  e destino de copia na rede (padrao `\\citel-fileserve\Modem\_VERSOES_INDIVIDUAIS`).
- `Configuracoes > Padrao de Arquivos`: seleciona uma pasta modelo Local e uma
  Cloud; os arquivos encontrados nelas viram o padrao essencial aplicado em
  todas as versoes (gravado em `project_pattern.json`).
- `Configuracoes > Pastas Ignoradas`: pastas excluidas da varredura.
- `Auditoria > Logs`: consulta os registros de `logs/fechamentos.log`.

## Uso

- Clique duas vezes em um projeto (ou use `Abrir Pasta`) para abrir a pasta no
  Explorer.
- `Detalhes` mostra todos os grupos essenciais do projeto com arquivo
  encontrado, origem (pasta ou zip), versoes, tamanho e status individual;
  linhas com pendencia ficam destacadas em vermelho.
- A interface atualiza automaticamente a cada 5 minutos; `Atualizar` força nova
  varredura.

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
- `Copiar Local`: permitido quando `Autcom.exe` esta abaixo de 100 MB.
- `Copiar Cloud`: permitido quando `Autcom.exe` esta acima de 200 MB.
- A copia procura no destino de rede uma pasta com o mesmo nome do projeto,
  pede confirmacao mostrando origem e destino, sobrescreve arquivos de mesmo
  nome e confere os tamanhos no destino ao final.
- Todas as acoes geram registro em `logs/fechamentos.log`.
