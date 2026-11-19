"""Auditoria de estrutura SQL: compara scripts `.SQL` com o banco (somente leitura).

Nada neste pacote executa SQL dos arquivos. Leitura dos arquivos (`file_scanner`),
divisao em comandos com posicao (`sql_split`), interpretacao (`sql_parse`), modelo
esperado por replay (`expected_model`), leitura do banco (`db_reader`, o unico que
fala com o MySQL) e comparacao (`comparator`). O que nao for interpretado com
seguranca nunca e tratado como validado.
"""
