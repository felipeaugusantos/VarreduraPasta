# Plano de aprendizado local

## Objetivo

Adicionar recomendacoes e deteccao de anomalias sem API, internet, instalador
ou permissao administrativa. O recurso usa apenas `sqlite3`, incorporado ao
Python e ao executavel.

## Principios

- Nenhum dado sai do Terminal Server.
- O sistema apenas recomenda; nunca executa BAT, copia ou limpa automaticamente.
- Regras obrigatorias de versao, tamanho, ZIP e hash sempre prevalecem.
- Toda recomendacao deve informar motivo, amostragem e confianca.
- O operador podera consultar e apagar o historico.

## Primeira etapa implementada

O modulo `app.learning` cria `%LOCALAPPDATA%\VarreduraSistema\learning.db` e
registra somente projetos com status `OK`. Observacoes identicas nao sao
duplicadas. A primeira heuristica detecta tamanho do Autcom fora da faixa
historica da mesma familia Local/Cloud depois de pelo menos tres amostras. O
insight aparece em `Detalhes`, com motivo e confianca, sem bloquear operacoes.

## Evolucao proposta

1. Registrar se o operador considerou a sugestao util ou incorreta.
2. Aprender quantidade habitual de ZIPs e arquivos essenciais.
3. Estimar duracao de copia, varredura e pesquisa SVN.
4. Sugerir destinos frequentes sem seleciona-los automaticamente.
5. Criar uma tela para historico, privacidade, exportacao e exclusao.

## Fora de escopo

Modelos de linguagem, Ollama, servicos Windows, bibliotecas externas e qualquer
automacao autonoma de operacoes destrutivas.
