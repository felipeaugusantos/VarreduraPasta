"""Monta, PARA REVISAO HUMANA, o `.sql` com so o que FALTA rodar de um script no banco modelo.

Caso tipico: o script foi aplicado em parte (as tabelas ja existem, so faltam alguns `ALTER`).
Rodar o arquivo original inteiro para no primeiro `CREATE TABLE` de tabela existente; este
arquivo traz so os comandos dos objetos que a comparacao achou faltando, na ordem do script.

NUNCA executa nada: so produz texto. Mesmas regras do script corretivo:
- remocao de coluna/tabela sai COMENTADA; o resto sai ativo, um comando por objeto;
- comandos do script que o sistema nao confere (dados, procedures) aparecem COMENTADOS,
  na posicao original, para o operador decidir se tambem precisam rodar;
- o `USE <banco>` vem comentado; nada de senha, nada do modelo interno.
"""

import os
from datetime import datetime
from pathlib import Path

from app.sqlaudit import sql_parse
from app.sqlaudit.fix_suggestions import (
    FixError,
    _columns,
    _word,
    alter,
    column_definition,
    foreign_key_definition,
    index_definition,
    quote,
)
from app.sqlaudit.model_check import (
    KIND_COLUMN,
    KIND_FOREIGN,
    KIND_INDEX,
    KIND_PRIMARY,
    KIND_REMOVE_PREFIX,
    KIND_TABLE,
    STATUS_MISSING,
    ScriptCheck,
)

HEADER = """-- ======================================================================
-- SO O QUE FALTA RODAR, GERADO PARA REVISAO. NAO EXECUTE SEM REVISAR.
--
-- Gerado pela comparacao com o banco modelo. O sistema NAO executou nada.
--   * Rode UM comando por vez, na ordem, ou com o cliente parando no primeiro erro.
--   * Remocao de coluna/tabela esta COMENTADA: so roda se voce descomentar.
--   * Comandos do script que o sistema nao confere (dados, procedures...) aparecem
--     COMENTADOS na posicao original: descomente os que tambem precisarem rodar.
--   * Antes de alterar tabela grande, faca backup e teste numa copia.
-- ======================================================================
"""
PREVIEW_CHARS = 400
UNCHECKED_KINDS = (sql_parse.KIND_ALTER_OPTIONS, sql_parse.KIND_CONVERT_CHARSET, sql_parse.KIND_OTHER)


def _commented(text):
    return "\n".join("-- " + line if line else "--" for line in str(text).splitlines())


def _line(op):
    statement = getattr(op, "statement", None)
    return statement.line_start if statement is not None else 10**9


def _options(op):
    parts = []
    values = {name: value for name, value in op.options}
    if values.get("engine"):
        parts.append("ENGINE=" + _word(values["engine"], "engine"))
    if values.get("charset"):
        parts.append("DEFAULT CHARSET=" + _word(values["charset"], "charset"))
    if values.get("collation"):
        parts.append("COLLATE=" + _word(values["collation"], "colacao"))
    return " ".join(parts)


def create_table_from_operation(op):
    """`CREATE TABLE` de uma operacao `create_table` do analisador (definicao completa do script)."""
    lines = [column_definition(column) for column in op.columns]
    if op.primary_key:
        lines.append(f"PRIMARY KEY {_columns(op.primary_key)}")
    for index in op.indexes:
        kind = "UNIQUE KEY" if index.unique else {"fulltext": "FULLTEXT KEY", "spatial": "SPATIAL KEY"}.get(index.kind, "KEY")
        label = f"{quote(index.name)} " if index.name else ""
        lines.append(f"{kind} {label}{_columns(index.columns, index.prefixes)}")
    for foreign in op.foreign_keys:
        lines.append(foreign_key_definition(foreign))
    body = ",\n  ".join(lines)
    options = _options(op)
    return f"CREATE TABLE {quote(op.table)} (\n  {body}\n)" + (f" {options}" if options else "") + ";"


def _statement_for(item):
    """(texto, comentado, avisos) para um item que falta. Levanta FixError se nao der com seguranca."""
    op, spec, kind = item.op, item.spec, item.kind
    table = item.table
    if kind == KIND_TABLE:
        if op is None or op.kind != sql_parse.KIND_CREATE_TABLE:
            raise FixError(
                "a tabela nao existe e este script nao a cria"
                + (f": falta rodar {', '.join(item.providers[:2])}" if item.providers else "")
            )
        return create_table_from_operation(op), False, []
    if kind == KIND_COLUMN:
        if op is None or op.kind not in (sql_parse.KIND_CREATE_TABLE, sql_parse.KIND_ADD_COLUMN):
            raise FixError(
                f"a coluna {item.name} nao existe e este comando nao a cria (e MODIFY/CHANGE)"
                + (f": falta rodar {', '.join(item.providers[:2])}" if item.providers else "")
            )
        place = ""
        if spec.position == "first":
            place = " FIRST"
        elif spec.position and spec.position.startswith("after:"):
            place = " AFTER " + quote(spec.position[6:])
        return alter(table, f"ADD COLUMN {column_definition(spec)}{place}"), False, []
    if kind == KIND_INDEX:
        if spec is None:
            raise FixError("indice sem definicao no script")
        if item.detail.startswith("no banco:"):  # existe com outras colunas: recria
            return alter(table, f"DROP INDEX {quote(spec.name)}, ADD " + index_definition(spec)), False, [
                "recria o indice: " + item.detail
            ]
        warnings = ["falha se ja houver valores repetidos"] if spec.unique else []
        return alter(table, "ADD " + index_definition(spec)), False, warnings
    if kind == KIND_FOREIGN:
        if spec is None:
            raise FixError("chave estrangeira sem definicao no script")
        return alter(table, "ADD " + foreign_key_definition(spec)), False, ["falha se houver linhas que violem a referencia"]
    if kind == KIND_PRIMARY:
        columns = op.primary_key if op is not None else ()
        if not columns:
            raise FixError("chave primaria sem colunas no script")
        return alter(table, f"ADD PRIMARY KEY {_columns(columns)}"), False, ["falha se houver NULL ou valores repetidos"]
    if kind == KIND_REMOVE_PREFIX + KIND_INDEX:
        return alter(table, f"DROP INDEX {quote(item.name)}"), False, []
    if kind == KIND_REMOVE_PREFIX + KIND_FOREIGN:
        return alter(table, f"DROP FOREIGN KEY {quote(item.name)}"), False, []
    if kind == KIND_REMOVE_PREFIX + KIND_COLUMN:
        return alter(table, f"DROP COLUMN {quote(item.name)}"), True, ["APAGA a coluna e os dados dela"]
    if kind == KIND_REMOVE_PREFIX + KIND_TABLE:
        return f"DROP TABLE {quote(table)};", True, ["APAGA a tabela e todos os dados"]
    raise FixError("tipo de item sem comando automatico")


def _body_lines(check):
    """Linhas dos comandos que faltam de `check`, na ordem do script (com os nao conferidos comentados)."""
    entries = []  # (linha, ordem, [linhas de texto])
    shown_statements = set()
    for order, item in enumerate(check.missing):
        lines = [f"-- [{item.kind} {item.table}" + (f".{item.name}" if item.name else "") + "]"]
        try:
            statement, commented, warnings = _statement_for(item)
        except FixError as error:
            lines.append(_commented(f"NAO GERADO: {error}"))
        else:
            lines.extend(f"--   cuidado: {warning}" for warning in warnings)
            if commented:
                lines.append("--   COMENTADO: descomente apenas depois de revisar")
                lines.append(_commented(statement))
            else:
                lines.append(statement)
        entries.append((_line(item.op), order, lines + [""]))
        if item.op is not None and getattr(item.op, "statement", None) is not None:
            shown_statements.add(item.op.statement.index)
    # comandos que o sistema nao confere, na posicao original
    seen = set()
    for order, op in enumerate(check.operations, start=len(check.missing)):
        statement = getattr(op, "statement", None)
        if statement is None or statement.index in seen or statement.index in shown_statements:
            continue
        if op.interpreted and op.kind not in UNCHECKED_KINDS:
            continue
        seen.add(statement.index)
        text = " ".join(statement.text.split())
        preview = text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "..."
        entries.append(
            (statement.line_start, order, [f"-- [nao conferido pelo sistema | linha {statement.line_start} do original]", _commented(preview), ""])
        )
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    lines = []
    for _line_number, _order, entry_lines in entries:
        lines.extend(entry_lines)
    return lines


def build_missing_script(check, database="", user="", now=None):
    """Texto do `.sql` com so o que falta rodar de `check` (um ScriptCheck com status FALTA)."""
    if not isinstance(check, ScriptCheck) or check.status != STATUS_MISSING:
        raise ValueError("so ha o que gerar para um script com resultado 'Falta rodar'.")
    now = now or datetime.now()
    out = [HEADER, f"-- Script de origem: {check.script}", f"-- Gerado em {now:%d/%m/%Y %H:%M}" + (f" por {user}" if user else "")]
    if database:
        out.append(_commented(f"USE {quote(database)};"))
    if check.depends_on:
        out.append("--")
        out.append("-- ANTES deste arquivo, rode os scripts que criam o que ele usa: " + ", ".join(check.depends_on))
    out.append("")
    out.extend(_body_lines(check))
    return "\n".join(out).rstrip() + "\n"


PLAN_HEADER = """-- ======================================================================
-- PLANO DO QUE FALTA RODAR NO BANCO MODELO, GERADO PARA REVISAO.
-- NAO EXECUTE SEM REVISAR.
--
-- Todos os scripts com resultado "Falta rodar", na ORDEM SUGERIDA: quem cria
-- um objeto vem antes de quem o usa; fora isso vale a ordem numerica do nome.
-- Cada secao traz so os comandos dos objetos que ainda faltam.
--   * Rode UM comando por vez, na ordem, ou com o cliente parando no primeiro erro.
--   * Remocao de coluna/tabela esta COMENTADA: so roda se voce descomentar.
--   * Comandos que o sistema nao confere (dados, procedures...) aparecem COMENTADOS
--     na posicao original: descomente os que tambem precisarem rodar.
--   * Depois de rodar, compare de novo: o resultado deve virar OK.
--   * Faca backup antes e teste numa copia do banco.
-- ======================================================================
"""


def build_plan_script(results, database="", user="", now=None):
    """Um `.sql` so, com o que falta de TODOS os scripts pendentes, na ordem sugerida."""
    from app.sqlaudit.model_check import run_order

    order = run_order(results)
    if not order:
        raise ValueError("nenhum script falta rodar: nada a gerar.")
    now = now or datetime.now()
    by_name = {item.script: item for item in results}
    out = [PLAN_HEADER, f"-- Gerado em {now:%d/%m/%Y %H:%M}" + (f" por {user}" if user else "")]
    if database:
        out.append(_commented(f"USE {quote(database)};"))
    out.append(f"-- {len(order)} script(s), na ordem:")
    for number, (name, dependencies) in enumerate(order, 1):
        out.append(f"--   {number}. {name}" + (f"  (depois de: {', '.join(dependencies)})" if dependencies else ""))
    out.append("")
    for number, (name, dependencies) in enumerate(order, 1):
        out += [
            "-- ----------------------------------------------------------------------",
            f"-- {number}/{len(order)}  {name}",
        ]
        if dependencies:
            out.append("-- depois de: " + ", ".join(dependencies))
        out += ["-- ----------------------------------------------------------------------", ""]
        out.extend(_body_lines(by_name[name]))
    return "\n".join(out).rstrip() + "\n"


def write_plan_script(results, path, database="", user="", now=None):
    """Grava o plano de forma atomica. Levanta OSError/ValueError."""
    path = Path(path)
    if path.suffix.lower() != ".sql":
        raise ValueError("Use a extensao .sql.")
    text = build_plan_script(results, database, user, now)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return text


def write_missing_script(check, path, database="", user="", now=None):
    """Grava o `.sql` de forma atomica. Levanta OSError/ValueError."""
    path = Path(path)
    if path.suffix.lower() != ".sql":
        raise ValueError("Use a extensao .sql.")
    text = build_missing_script(check, database, user, now)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return text
