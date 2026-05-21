import re
from typing import List

from .exceptions import (
    ColunasDivergentesError,
    InsertsIncompativeisError,
    SqlNaoSuportadoError,
    SqlParseError,
)
from .models import DeclaracaoInsert


def _remover_comentarios_sql(sql: str) -> str:
    """Remove comentarios --, # e /* */ sem alterar literais SQL."""
    saida = []
    em_aspas_simples = False
    em_aspas_duplas = False
    i = 0

    while i < len(sql):
        ch = sql[i]
        prox = sql[i + 1] if i + 1 < len(sql) else ""

        if ch == "'" and not em_aspas_duplas:
            saida.append(ch)
            if em_aspas_simples and prox == "'":
                saida.append(prox)
                i += 2
                continue
            em_aspas_simples = not em_aspas_simples
            i += 1
            continue

        if ch == '"' and not em_aspas_simples:
            saida.append(ch)
            em_aspas_duplas = not em_aspas_duplas
            i += 1
            continue

        if not em_aspas_simples and not em_aspas_duplas:
            if ch == "-" and prox == "-":
                i += 2
                while i < len(sql) and sql[i] not in "\r\n":
                    i += 1
                continue

            if ch == "#":
                i += 1
                while i < len(sql) and sql[i] not in "\r\n":
                    i += 1
                continue

            if ch == "/" and prox == "*":
                i += 2
                while i + 1 < len(sql) and not (sql[i] == "*" and sql[i + 1] == "/"):
                    i += 1
                i = min(i + 2, len(sql))
                continue

        saida.append(ch)
        i += 1

    return "".join(saida).strip()


def _dividir_por_virgulas_nivel_superior(s: str) -> List[str]:
    saida = []
    buffer = []
    profundidade = 0
    em_aspas_simples = False
    em_aspas_duplas = False
    i = 0

    while i < len(s):
        ch = s[i]

        if ch == "'" and not em_aspas_duplas:
            buffer.append(ch)
            if em_aspas_simples and i + 1 < len(s) and s[i + 1] == "'":
                buffer.append("'")
                i += 2
                continue
            em_aspas_simples = not em_aspas_simples
            i += 1
            continue

        if ch == '"' and not em_aspas_simples:
            buffer.append(ch)
            em_aspas_duplas = not em_aspas_duplas
            i += 1
            continue

        if not em_aspas_simples and not em_aspas_duplas:
            if ch == "(":
                profundidade += 1
            elif ch == ")":
                profundidade = max(0, profundidade - 1)
            elif ch == "," and profundidade == 0:
                saida.append("".join(buffer).strip())
                buffer = []
                i += 1
                continue

        buffer.append(ch)
        i += 1

    if buffer:
        saida.append("".join(buffer).strip())

    return saida


def _dividir_statements_topo(sql: str) -> List[str]:
    partes = []
    buf = []
    em_aspas_simples = False
    em_aspas_duplas = False
    i = 0

    while i < len(sql):
        ch = sql[i]

        if ch == "'" and not em_aspas_duplas:
            buf.append(ch)
            if em_aspas_simples and i + 1 < len(sql) and sql[i + 1] == "'":
                buf.append("'")
                i += 2
                continue
            em_aspas_simples = not em_aspas_simples
            i += 1
            continue

        if ch == '"' and not em_aspas_simples:
            buf.append(ch)
            em_aspas_duplas = not em_aspas_duplas
            i += 1
            continue

        if ch == ";" and not em_aspas_simples and not em_aspas_duplas:
            parte = "".join(buf).strip()
            if parte:
                partes.append(parte)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    parte = "".join(buf).strip()
    if parte:
        partes.append(parte)

    return partes


def _normalizar_token(t: str) -> str:
    return t.strip().strip("`").strip('"').strip("'").lower()


def _contem_palavra_topo(sql: str, palavra: str) -> bool:
    return re.search(rf"\b{re.escape(palavra)}\b", sql, flags=re.IGNORECASE) is not None


def _validar_nao_suportados(statement: str) -> None:
    if re.match(r"^\s*insert\s+ignore\b", statement, flags=re.IGNORECASE):
        raise SqlNaoSuportadoError("Este formato ainda nao e suportado: INSERT IGNORE.")


def _encontrar_fim_grupos_values(resto: str) -> int:
    i = 0
    encontrou = False
    while i < len(resto):
        while i < len(resto) and resto[i].isspace():
            i += 1
        if i >= len(resto) or resto[i] != "(":
            return i

        encontrou = True
        profundidade = 0
        em_aspas_simples = False
        em_aspas_duplas = False
        while i < len(resto):
            ch = resto[i]
            if ch == "'" and not em_aspas_duplas:
                if em_aspas_simples and i + 1 < len(resto) and resto[i + 1] == "'":
                    i += 2
                    continue
                em_aspas_simples = not em_aspas_simples
                i += 1
                continue
            if ch == '"' and not em_aspas_simples:
                em_aspas_duplas = not em_aspas_duplas
                i += 1
                continue
            if not em_aspas_simples and not em_aspas_duplas:
                if ch == "(":
                    profundidade += 1
                elif ch == ")":
                    profundidade -= 1
                    if profundidade == 0:
                        i += 1
                        break
            i += 1
        else:
            raise SqlParseError("Nao encontrei o fechamento de um grupo de VALUES.")

        while i < len(resto) and resto[i].isspace():
            i += 1
        if i < len(resto) and resto[i] == ",":
            i += 1
            continue
        return i
    return i


def _extrair_grupos_values(resto: str) -> tuple[list[str], str]:
    fim = _encontrar_fim_grupos_values(resto)
    area_values = resto[:fim].strip()
    residual = resto[fim:].strip()
    if not area_values:
        raise SqlParseError("Nao encontrei grupos de VALUES no INSERT/REPLACE.")
    grupos = _dividir_por_virgulas_nivel_superior(area_values)
    if not grupos or any(not g.startswith("(") or not g.endswith(")") for g in grupos):
        raise SqlParseError("Nao encontrei grupos de VALUES validos no INSERT/REPLACE.")
    return grupos, residual


def _validar_residual(residual: str) -> None:
    if not residual:
        return
    if re.search(r"\bon\s+duplicate\s+key\s+update\b", residual, flags=re.IGNORECASE):
        raise SqlNaoSuportadoError("Este formato ainda nao e suportado: ON DUPLICATE KEY UPDATE.")
    raise SqlNaoSuportadoError(
        f"Texto residual apos VALUES ainda nao e suportado: {residual[:80]}"
    )


def parsear_insert_unico(sql: str) -> DeclaracaoInsert:
    statement = _remover_comentarios_sql(sql).rstrip(";").strip()
    if not statement:
        raise SqlParseError("Nao encontrei nenhum INSERT ou REPLACE.")
    if not re.match(r"^\s*(insert|replace)\b", statement, flags=re.IGNORECASE):
        raise SqlNaoSuportadoError("Somente comandos INSERT ou REPLACE sao suportados.")
    _validar_nao_suportados(statement)

    m = re.match(
        r"^\s*(insert|replace)\s+into\s+([^\s(]+)\s*(\((.*?)\))?\s+values\s*(.+)$",
        statement,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        if re.search(r"^\s*(insert|replace)\s+into\b.*\bset\b", statement, flags=re.IGNORECASE | re.DOTALL):
            raise SqlNaoSuportadoError("Este formato ainda nao e suportado: INSERT ... SET.")
        if re.search(r"^\s*(insert|replace)\s+into\b.*\bselect\b", statement, flags=re.IGNORECASE | re.DOTALL):
            raise SqlNaoSuportadoError("Este formato ainda nao e suportado: INSERT ... SELECT.")
        if _contem_palavra_topo(statement, "values"):
            raise SqlParseError("Nao consegui reconhecer um INSERT ou REPLACE valido.")
        raise SqlNaoSuportadoError("Este formato ainda nao e suportado: INSERT sem VALUES.")

    tipo_comando = m.group(1).upper()
    tabela = m.group(2).strip()
    grupo_colunas = m.group(4)
    resto_valores = m.group(5).strip()
    tinha_colunas = grupo_colunas is not None

    colunas = None
    if grupo_colunas is not None:
        colunas = [
            c.strip() for c in _dividir_por_virgulas_nivel_superior(grupo_colunas)
            if c.strip()
        ]

    grupos, residual = _extrair_grupos_values(resto_valores)
    _validar_residual(residual)

    linhas = []
    for g in grupos:
        interno = g[1:-1].strip()
        valores = _dividir_por_virgulas_nivel_superior(interno) if interno else []
        linhas.append(valores)

    if colunas is not None:
        colunas_norm = [_normalizar_token(c) for c in colunas]
        linhas = [
            ln for ln in linhas
            if [_normalizar_token(v) for v in ln] != colunas_norm
        ]

    return DeclaracaoInsert(
        tabela=tabela,
        colunas=colunas,
        linhas=linhas,
        tinha_colunas=tinha_colunas,
        quantidade_inserts_origem=1,
        tipo_comando=tipo_comando,
    )


def parsear_insert(sql: str) -> DeclaracaoInsert:
    sql_limpo = _remover_comentarios_sql(sql)
    statements = _dividir_statements_topo(sql_limpo)

    if not statements:
        raise SqlParseError("Nao encontrei nenhum INSERT ou REPLACE.")

    declaracoes = [parsear_insert_unico(st) for st in statements]

    tabela_base = declaracoes[0].tabela
    colunas_base = declaracoes[0].colunas
    tinha_colunas_base = declaracoes[0].tinha_colunas
    tipo_comando_base = declaracoes[0].tipo_comando

    def colunas_iguais(c1, c2):
        if c1 is None and c2 is None:
            return True
        if (c1 is None) != (c2 is None):
            return False
        return [_normalizar_token(x) for x in c1] == [_normalizar_token(x) for x in c2]

    for d in declaracoes[1:]:
        if d.tipo_comando != tipo_comando_base:
            raise InsertsIncompativeisError(
                "Foram colados INSERTs e REPLACEs misturados. Processe separado."
            )
        if _normalizar_token(d.tabela) != _normalizar_token(tabela_base):
            raise InsertsIncompativeisError(
                "Foram colados INSERTs de tabelas diferentes. Processe separado."
            )
        if not colunas_iguais(colunas_base, d.colunas):
            raise ColunasDivergentesError(
                "Foram colados INSERTs com colunas diferentes. Processe separado."
            )

    linhas = []
    for d in declaracoes:
        linhas.extend(d.linhas)

    return DeclaracaoInsert(
        tabela=tabela_base,
        colunas=colunas_base,
        linhas=linhas,
        tinha_colunas=tinha_colunas_base,
        quantidade_inserts_origem=len(statements),
        tipo_comando=tipo_comando_base,
    )
