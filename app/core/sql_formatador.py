import re


def eh_numero_sql(texto: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d+(\.\d+)?([eE][+-]?\d+)?", texto))


def normalizar_valor_editado(bruto: str) -> str:
    texto = bruto.strip()
    if texto == "" or texto.upper() == "NULL":
        return "NULL"
    if texto.startswith("="):
        expressao = texto[1:].strip()
        return expressao or "NULL"
    if len(texto) >= 2 and texto[0] == "'" and texto[-1] == "'":
        return texto
    if eh_numero_sql(texto):
        return texto
    texto_escapado = texto.replace("'", "''")
    return f"'{texto_escapado}'"
