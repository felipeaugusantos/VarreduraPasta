# Apresentacao - Sistema de Acompanhamento de Copia

## Slide 1 - Titulo

**Sistema de Acompanhamento de Copia**

Validacao, fechamento, copia e auditoria de versoes fechadas.

## Slide 2 - Problema

- Validacao manual de arquivos pode gerar erro.
- FileVersion e ProductVersion precisam bater com o nome da pasta.
- Projetos Local e Cloud possuem regras diferentes.
- Copias para destino errado podem impactar producao.
- Sem auditoria padronizada, fica dificil rastrear o que aconteceu.

## Slide 3 - Objetivo da ferramenta

- Analisar automaticamente as pastas de versoes.
- Identificar projetos Local e Cloud.
- Validar arquivos essenciais.
- Validar FileVersion e ProductVersion.
- Validar ZIPs e tamanho do Autcom.
- Executar fechamento e copia com travas.
- Registrar tudo em Auditoria.

## Slide 4 - Como a versao esperada e calculada

Exemplo:

```text
379.48.2.30.74.150.90
```

Resultado:

```text
FileVersion: 48.02.30.74
ProductVersion: 02.30.74.150
```

O sufixo `_CLOUD` e ignorado no calculo.

## Slide 5 - Arquivos essenciais

Grupos principais:

- Autcom.
- AutcomTinta.
- Autban.
- Auttin.

Alternativas aceitas:

- `libAutban.dll`, `libAutban.exe` ou `AutBan.exe`.
- `libAuttin.dll`, `libAuttin.exe` ou `Auttin.exe`.

O sistema tambem pode aprender padroes especificos por projeto.

## Slide 6 - Regras Local e Cloud

Local:

- Pasta sem `_CLOUD`.
- Fechamento Local habilitado.
- Copiar Local permitido somente com Autcom abaixo de 100 MB.

Cloud:

- Pasta com `_CLOUD`.
- Fechamento Cloud habilitado.
- Copiar Cloud permitido somente com Autcom acima de 200 MB.

## Slide 7 - Travas de seguranca

Antes da copia, o sistema valida:

- Tipo do projeto.
- Nome exato do destino.
- Destino dentro da raiz configurada.
- FileVersion e ProductVersion.
- Autcom MB.
- Status do projeto.
- Nome dos ZIPs.
- Duplicidade de destino na rede.

Se algo falhar, o botao `Continuar` fica bloqueado.

## Slide 8 - Protecao contra ZIP incorreto

Caso bloqueado:

```text
autcom (1).zip contem AutBan.exe; esperado AutBan.zip
```

Essa trava evita que ZIPs gerados ou copiados com nome errado sejam enviados para producao.

## Slide 9 - Auditoria

Todas as acoes criticas geram log:

```text
usuario
projeto
tipo
origem
destino
bat
fileversion
productversion
autcom_mb
motivo
```

Resultados:

- iniciado.
- processo_aberto.
- concluido.
- bloqueado.
- cancelado.
- erro.

## Slide 10 - Fluxo de uso

1. Abrir o sistema.
2. Confirmar Diretorio-base.
3. Atualizar lista.
4. Selecionar projeto.
5. Conferir status.
6. Abrir Detalhes se necessario.
7. Executar fechamento ou copia.
8. Conferir Auditoria.

## Slide 11 - Como agir em caso de erro

- Verificar o Status na tela principal.
- Abrir Detalhes.
- Conferir a tela de validacao.
- Consultar Auditoria.
- Enviar o arquivo mensal `logs\fechamentos-AAAA-MM.log` para suporte.

## Slide 12 - Recomendacao para producao

- Primeira execucao acompanhada.
- Testar 1 projeto Local OK.
- Testar 1 projeto Cloud OK.
- Conferir Auditoria apos cada acao.
- Depois liberar uso recorrente.

## Slide 13 - Beneficios

- Reducao de erro manual.
- Mais seguranca antes da copia.
- Rastreabilidade por usuario e projeto.
- Padronizacao da validacao.
- Apoio rapido para suporte.

## Slide 14 - Proximos passos

- Validar primeira execucao real em rede.
- Avaliar log centralizado em pasta de rede.
- Criar botao de exportacao de Auditoria.
- Evoluir a Auditoria para tela em formato de tabela.
