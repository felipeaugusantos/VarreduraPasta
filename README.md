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
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_release.ps1
```

O script valida o codigo e os testes, gera a pasta portatil, o ZIP de
distribuicao e o arquivo SHA-256.

## Implantacao em producao

O pacote e portatil: copie a pasta completa e execute o `.exe`, sem instalador
ou permissao administrativa. Configuracoes e dados de uso ficam no perfil:

```text
%LOCALAPPDATA%\VarreduraSistema
```

Arquivos antigos encontrados ao lado do executavel sao migrados automaticamente.

## Configuracao

As configuracoes ficam em
`%LOCALAPPDATA%\VarreduraSistema\settings.json`:

- `Configuracoes > Diretorio-base`: pasta analisada (padrao `C:\VERSOES_FECHADAS`)
  e destinos adicionais de copia na rede. Por padrao, a busca de destino considera
  `\\citel-fileserve\Modem\VERSOES\379_Versoes\379.48.02\379.48.2.30` e
  `\\citel-fileserve\Modem\_VERSOES_INDIVIDUAIS`.
- `Configuracoes > Padrao de Arquivos`: seleciona uma pasta modelo Local e uma
  Cloud; os arquivos encontrados nelas viram o padrao essencial aplicado em
  todas as versoes (gravado no perfil local da aplicacao).
- `Configuracoes > Pastas Ignoradas`: pastas excluidas da varredura.
- `Jenkins > Configuracoes`: cadastra URL, usuario e senha do Jenkins local
  (padrao `http://localhost:8080`) e permite testar conexao. A senha fica no
  Gerenciador de Credenciais do Windows, nunca no `settings.json`. Enderecos
  nao locais exigem HTTPS quando houver autenticacao.
- `Jenkins > Status dos Jobs`: consulta o ultimo build de cada job e permite
  abrir o build selecionado no navegador.
- `Auditoria > Logs`: consulta os registros mensais em tabela a partir de
  `%LOCALAPPDATA%\VarreduraSistema\logs\fechamentos-AAAA-MM.log`.
- `Monitoramento > Scripts Banco Modelo`: verifica scripts novos em
  `\\citel-fileserve\Modem\WEBSERVICE_CITEL\Scripts`.
- `Ajuda > Manual de Utilizacao`: mostra um guia rapido de uso dentro do sistema.
- `Ajuda > Sobre`: mostra versao, data de geracao do executavel, criador e setor.

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
- O monitoramento de scripts verifica a pasta uma vez ao dia ao abrir o sistema
  e tambem permite verificacao manual pelo menu. Scripts novos ficam pendentes
  ate o operador clicar em `Marcar como feito`.
- A Consulta SVN aceita Enter, mostra a faixa pesquisada e pode ser cancelada.
  Marque `Buscar todas as ocorrencias` quando quiser continuar depois do
  primeiro bloco com resultados.

## Aprendizado local

O sistema inicia um perfil estatistico local de projetos validados em
`%LOCALAPPDATA%\VarreduraSistema\learning.db`. Nenhum dado e enviado para API ou
internet, e o recurso nunca executa acoes automaticamente. Quando houver
historico suficiente, a tela `Detalhes` mostra uma sugestao explicavel sobre
tamanhos fora do padrao observado. Consulte
`docs/PLANO_IA_LOCAL.md` para o plano de evolucao.

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
  `comandosCMD\_FechamentoArquivos.bat` do projeto em segundo plano, sem
  janela, e grava o padrao de arquivos aprendido. Habilitado conforme o tipo do
  projeto (Local sem `_CLOUD`; Cloud com `_CLOUD`). Aceita varios projetos
  selecionados do mesmo tipo: os BATs rodam ate 2 por vez e o restante fica na
  fila. A coluna `Fechamento` mostra a situacao e o tempo de cada um; a saida do
  BAT fica em `logs\fechamentos_bat`.
- `Validar Fechamento`: quando a fila termina a tabela e atualizada; use este
  botao para registrar na Auditoria a versao/status final do projeto.
- `Limpar Pasta`: remove os arquivos e subpastas da raiz do projeto selecionado,
  preservando sempre `comandosCMD`, apos confirmacao do operador. A limpeza roda
  em segundo plano para manter a tela responsiva.
- `Copiar Local`: permitido quando `Autcom.exe` esta abaixo de 100 MB.
- `Copiar Cloud`: permitido quando `Autcom.exe` tem pelo menos 150 MB.
- `Testar Autcom`: para uma versao como `379.48.2.30.74.111.181`, sincroniza
  os ZIPs em `C:\VERSOES_FECHADAS\PastaTesteCopia_74_111\exe`, executa
  `CopiaArquivos.bat`, abre o `Autcom.exe` da raiz e preenche o login de teste
  com usuario `998`, senha `998` e empresa `001`. O resultado diferencia
  abertura autenticada, falha e verificacao inconclusiva.
- A copia procura no destino de rede uma pasta com o mesmo nome do projeto,
  pede confirmacao mostrando origem e destino e envia somente os arquivos
  `.zip` da pasta do projeto.
- Depois da copia concluida e verificada, somente os ZIPs copiados sao
  removidos da origem; os demais itens sao mantidos e listados em um aviso.
- Quando existe mais de uma pasta de destino com o mesmo nome do projeto, o
  sistema mostra uma tela de escolha antes da confirmacao da copia.
- Fechamentos e copias sao bloqueados quando existem ZIPs com nome diferente do
  arquivo interno.
- Todas as acoes geram registro mensal em
  `%LOCALAPPDATA%\VarreduraSistema\logs\fechamentos-AAAA-MM.log`.
- ZIPs invalidos ou corrompidos bloqueiam a copia. Todos os ZIPs sao validados
  por SHA-256 em uma area temporaria do destino antes da substituicao final.

## Checklist de seguranca antes da copia

- Confirme se o projeto selecionado e Local ou Cloud.
- Verifique se o status esta `OK`.
- Confira `FileVersion`, `ProductVersion` e `Autcom MB`.
- Confirme se a tela de validacao mostra origem e destino corretos.
- Nao continue se houver destino duplicado, ZIP incorreto ou versao divergente.
- Apos a acao, consulte `Auditoria > Logs` para confirmar o resultado.
