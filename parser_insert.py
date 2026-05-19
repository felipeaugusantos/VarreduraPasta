import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DeclaracaoInsert:
    tabela: str
    colunas: Optional[List[str]]          # None se não havia colunas explícitas
    linhas: List[List[str]]              # registros
    tinha_colunas: bool                  # se o SQL original tinha "(col1, col2...)"
    quantidade_inserts_origem: int = 1   # quantos INSERTs foram colados na origem
    tipo_comando: str = "INSERT"         # "INSERT" ou "REPLACE"

    def remover_colunas_por_indices(self, indices: List[int]):
        """Remove colunas (campo + valor) em todas as linhas."""
        if not indices:
            return
        indices = sorted(set(indices), reverse=True)

        if self.colunas is not None:
            for i in indices:
                if 0 <= i < len(self.colunas):
                    self.colunas.pop(i)

        for linha in self.linhas:
            for i in indices:
                if 0 <= i < len(linha):
                    linha.pop(i)

    def remover_linha(self, indice_linha: int):
        """Remove uma linha inteira."""
        if 0 <= indice_linha < len(self.linhas):
            self.linhas.pop(indice_linha)

    def _parte_colunas(self) -> str:
        if self.tinha_colunas and self.colunas is not None:
            return f" ({', '.join(self.colunas)})"
        return ""

    def para_sql(self) -> str:
        """Gera um INSERT/REPLACE único (multi-row)."""
        parte_colunas = self._parte_colunas()
        grupos_valores = [f"({', '.join(linha)})" for linha in self.linhas]
        parte_valores = ", ".join(grupos_valores) if grupos_valores else "()"
        return f"{self.tipo_comando} INTO {self.tabela}{parte_colunas} VALUES {parte_valores};"

    def para_sql_multiplos(self) -> str:
        """Gera um INSERT/REPLACE por linha."""
        parte_colunas = self._parte_colunas()
        partes = [
            f"{self.tipo_comando} INTO {self.tabela}{parte_colunas} VALUES ({', '.join(linha)});"
            for linha in self.linhas
        ]
        return "\n".join(partes)


# ==========================
# Helpers de parsing SQL
# ==========================

def _remover_comentarios_sql(sql: str) -> str:
    """Remove comentários -- e /* */."""
    sql = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql.strip()


def _dividir_por_virgulas_nivel_superior(s: str) -> List[str]:
    """Divide por vírgula ignorando strings e parênteses internos."""
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

            if ch == "," and profundidade == 0:
                saida.append("".join(buffer).strip())
                buffer = []
                i += 1
                continue

        buffer.append(ch)
        i += 1

    if buffer:
        saida.append("".join(buffer).strip())

    return saida


def _extrair_grupos_parenteses(s: str) -> List[str]:
    """Extrai grupos ( ... ) no nível 0."""
    grupos = []
    profundidade = 0
    inicio = None
    em_aspas_simples = False
    em_aspas_duplas = False
    i = 0

    while i < len(s):
        ch = s[i]

        if ch == "'" and not em_aspas_duplas:
            if em_aspas_simples and i + 1 < len(s) and s[i + 1] == "'":
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
                if profundidade == 0:
                    inicio = i
                profundidade += 1
            elif ch == ")":
                profundidade -= 1
                if profundidade == 0 and inicio is not None:
                    grupos.append(s[inicio:i + 1])
                    inicio = None

        i += 1

    return grupos


def _dividir_statements_topo(sql: str) -> List[str]:
    """Divide por ';' ignorando ';' dentro de aspas."""
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


# ==========================
# Parsing principal
# ==========================

def parsear_insert_unico(sql: str) -> DeclaracaoInsert:
    """Parseia um único INSERT."""
    sql = _remover_comentarios_sql(sql)
    sql = sql.rstrip(";").strip()

    m = re.search(
        r"(insert|replace)\s+into\s+([^\s(]+)\s*(\((.*?)\))?\s*values\s*(.+)$",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        raise ValueError("Não consegui reconhecer um INSERT ou REPLACE válido.")

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

    grupos = _extrair_grupos_parenteses(resto_valores)
    if not grupos:
        raise ValueError("Não encontrei grupos de VALUES no INSERT/REPLACE.")

    linhas = []
    for g in grupos:
        interno = g.strip()[1:-1].strip()
        valores = _dividir_por_virgulas_nivel_superior(interno) if interno else []
        linhas.append(valores)

    # Remove linhas lixo iguais às colunas
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
    """
    Parseia 1 ou vários INSERTs colados.
    - Se forem da mesma tabela e colunas, junta as linhas.
    - Se não forem, levanta erro.
    """
    sql_limpo = _remover_comentarios_sql(sql)
    statements = _dividir_statements_topo(sql_limpo)

    if not statements:
        raise ValueError("Não encontrei nenhum INSERT ou REPLACE.")

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
        if _normalizar_token(d.tabela) != _normalizar_token(tabela_base):
            raise ValueError(
                "Foram colados INSERTs de tabelas diferentes. Processe separado."
            )
        if not colunas_iguais(colunas_base, d.colunas):
            raise ValueError(
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
