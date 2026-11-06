"""Auditoria de estrutura SQL: compara scripts `.SQL` com o banco (somente leitura).

Nada neste pacote executa SQL dos arquivos. Fase 1: leitura dos arquivos
(`file_scanner`), divisao em comandos com posicao (`sql_split`) e
interpretacao dos comandos (`sql_parse`). O que nao for interpretado com
seguranca nunca e tratado como validado.
"""
