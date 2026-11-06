"""Gera, PARA REVISAO HUMANA, o SQL que levaria o banco ao estado esperado pelos scripts.

NUNCA executa nada: so produz texto. A seguranca esta no desenho:
- so parte de achados firmes (AUSENTE/DIVERGENTE com origem certa); o que e
  INDETERMINADO, NAO SUPORTADO ou ERRO nao gera comando, so e contado;
- comando de risco ALTO (mudar tipo/charset/NOT NULL, mexer em chave primaria ou
  engine) e todo DROP de coluna/tabela saem COMENTADOS: so rodam se alguem
  descomentar de proposito;
- cada comando traz o motivo, a diferenca encontrada e o script/linha de origem;
- o `USE <banco>` vem comentado, para o arquivo nao rodar no banco errado.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.sqlaudit.comparator import AUSENTE, DIVERGENTE
from app.sqlaudit.expected_model import OBJ_COLUMN, OBJ_FOREIGN, OBJ_INDEX, OBJ_PRIMARY, OBJ_TABLE

RISK_LOW, RISK_MEDIUM, RISK_HIGH = "baixo", "medio", "alto"
(
    BUCKET_CREATE_TABLE,
    BUCKET_DROP_FK,
    BUCKET_COLUMN,
    BUCKET_DROP_COLUMN,
    BUCKET_INDEX,
    BUCKET_PRIMARY,
    BUCKET_FOREIGN,
    BUCKET_OPTIONS,
    BUCKET_DROP_TABLE,
) = range(9)
_WORD = re.compile(r"^[A-Za-z0-9_]+$")
_TYPE_UNSAFE = re.compile(r"[\r\n]|;|--|/\*")
_HIGH_RISK_DIFFERENCES = {"tipo", "charset", "collation"}


@dataclass(frozen=True)
class FixItem:
    version: str
    database: str
    table: str
    label: str  # ex.: "coluna CLIENTES.CLI_EMAIL"
    statement: str
    reason: str
    risk: str
    destructive: bool
    bucket: int
    source: str = ""  # "SCRIPT_x.SQL linha n"
    warnings: tuple = ()
    order: tuple = (10**12, 0)  # (numero do script, linha): mantem a ordem original dentro do grupo

    @property
    def commented(self):
        """Sai comentado: precisa ser descomentado de proposito."""
        return self.destructive or self.risk == RISK_HIGH


@dataclass
class FixPlan:
    version: str
    database: str
    items: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # (rotulo, motivo): achado firme que nao deu para corrigir
    not_handled: dict = field(default_factory=dict)  # estado -> quantidade (INDETERMINADO, ERRO...)
    notes: list = field(default_factory=list)

    @property
    def commented_count(self):
        return sum(1 for item in self.items if item.commented)


class FixError(Exception):
    """Nao foi possivel montar um comando com seguranca."""


# -- renderizacao --

def quote(name):
    return "`" + str(name).replace("`", "``") + "`"


def literal(text):
    return "'" + str(text).replace("\\", "\\\\").replace("'", "''") + "'"


def _word(value, what):
    if not _WORD.match(str(value)):
        raise FixError(f"{what} '{value}' nao e um identificador simples")
    return str(value)


def _safe_type(data_type):
    if not data_type or _TYPE_UNSAFE.search(data_type):
        raise FixError(f"tipo '{data_type}' nao pode ser reescrito com seguranca")
    return data_type


def column_definition(spec, implicit_not_null=False):
    parts = [quote(spec.name), _safe_type(spec.data_type)]
    if spec.charset:
        parts += ["CHARACTER SET", _word(spec.charset, "charset")]
    if spec.collation:
        parts += ["COLLATE", _word(spec.collation, "colacao")]
    if spec.nullable is False or (spec.nullable is None and implicit_not_null):
        parts.append("NOT NULL")
    elif spec.nullable is True:
        parts.append("NULL")
    if spec.has_default:
        if _TYPE_UNSAFE.search(str(spec.default)):
            raise FixError("DEFAULT nao pode ser reescrito com seguranca")
        parts += ["DEFAULT", str(spec.default)]
    if spec.auto_increment:
        parts.append("AUTO_INCREMENT")
    if spec.on_update:
        if _TYPE_UNSAFE.search(str(spec.on_update)):
            raise FixError("ON UPDATE nao pode ser reescrito com seguranca")
        parts += ["ON UPDATE", str(spec.on_update)]
    if spec.comment is not None:
        parts += ["COMMENT", literal(spec.comment)]
    return " ".join(parts)


def _columns(columns, prefixes=()):
    items = []
    for position, column in enumerate(columns):
        prefix = prefixes[position] if position < len(prefixes) else None
        items.append(quote(column) + (f"({prefix})" if prefix else ""))
    return "(" + ", ".join(items) + ")"


def index_definition(spec, name=True):
    kind = "UNIQUE " if spec.unique else {"fulltext": "FULLTEXT ", "spatial": "SPATIAL "}.get(spec.kind, "")
    label = (quote(spec.name) + " ") if (name and spec.name) else ""
    return f"{kind}INDEX {label}{_columns(spec.columns, spec.prefixes)}"


def foreign_key_definition(spec, named=True):
    target = (quote(spec.ref_schema) + ".") if spec.ref_schema else ""
    text = (f"CONSTRAINT {quote(spec.name)} " if named and spec.name else "") + (
        f"FOREIGN KEY {_columns(spec.columns)} REFERENCES {target}{quote(spec.ref_table)} {_columns(spec.ref_columns)}"
    )
    if spec.on_delete:
        text += f" ON DELETE {spec.on_delete}"
    if spec.on_update:
        text += f" ON UPDATE {spec.on_update}"
    return text


def _table_options(options):
    parts = []
    if options.get("engine"):
        parts.append("ENGINE=" + _word(options["engine"], "engine"))
    if options.get("charset"):
        parts.append("DEFAULT CHARSET=" + _word(options["charset"], "charset"))
    if options.get("collation"):
        parts.append("COLLATE=" + _word(options["collation"], "colacao"))
    return " ".join(parts)


def create_table_statement(state):
    lines = [column_definition(c.spec, c.spec.name.lower() in state.implicit_not_null) for c in state.columns.values()]
    if state.primary_key is not None:
        lines.append(f"PRIMARY KEY {_columns(state.primary_key.spec.columns)}")
    for index in state.indexes.values():
        kind = "UNIQUE KEY" if index.spec.unique else {"fulltext": "FULLTEXT KEY", "spatial": "SPATIAL KEY"}.get(index.spec.kind, "KEY")
        label = f"{quote(index.spec.name)} " if index.spec.name else ""
        lines.append(f"{kind} {label}{_columns(index.spec.columns, index.spec.prefixes)}")
    body = ",\n  ".join(lines)
    options = _table_options(state.options)
    return f"CREATE TABLE {quote(state.name)} (\n  {body}\n)" + (f" {options}" if options else "") + ";"


def alter(table, clause):
    return f"ALTER TABLE {quote(table)} {clause};"


# -- plano --

def _origin(obj):
    provenance = obj.provenance
    return f"{provenance.script} linha {provenance.line}" if provenance else ""


def _order(obj):
    provenance = obj.provenance
    if provenance is None or provenance.number is None:
        return (10**12, 0)
    return (provenance.number, provenance.line)


def _differences(obj):
    return "; ".join(f"{d.attribute}: esperado {d.expected}, encontrado {d.found}" for d in obj.differences)


def build_fix_plan(model, comparison, version="", database=""):
    """FixPlan de uma versao. `model` e o ExpectedModel e `comparison` o ComparisonResult dele."""
    plan = FixPlan(version, database or comparison.database)
    if model.unresolved:
        plan.notes.append(
            f"{len(model.unresolved)} comando(s) dos scripts nao foram interpretados (DML, SQL dinamico...): "
            "o que eles fazem NAO esta neste arquivo."
        )
    missing_tables = {o.table.lower() for o in comparison.objects if o.kind == OBJ_TABLE and o.status == AUSENTE and not o.expected_absent}
    for obj in comparison.objects:
        if obj.status not in (AUSENTE, DIVERGENTE):
            if obj.status not in ("COMPATIVEL", "SUBSTITUIDO"):
                plan.not_handled[obj.status] = plan.not_handled.get(obj.status, 0) + 1
            continue
        if obj.kind != OBJ_TABLE and not obj.expected_absent and obj.table.lower() in missing_tables:
            continue  # o CREATE TABLE ja traz isto
        label = f"{obj.kind.replace('_', ' ')} {obj.table}" + (f".{obj.column}" if obj.column else "")
        try:
            items = _fix_for(model, plan, obj, label)
        except FixError as error:
            plan.skipped.append((label, str(error)))
            continue
        plan.items.extend(items)
    plan.items.sort(key=lambda item: (item.bucket, item.table.lower(), item.order))
    return plan


def _item(plan, obj, label, statement, reason, risk, bucket, destructive=False, warnings=()):
    return FixItem(
        plan.version, plan.database, obj.table, label, statement, reason, risk, destructive, bucket,
        _origin(obj), tuple(warnings), _order(obj),
    )


def _fix_for(model, plan, obj, label):
    handler = _removed if obj.expected_absent else {
        OBJ_TABLE: _table, OBJ_COLUMN: _column, OBJ_INDEX: _index, OBJ_PRIMARY: _primary, OBJ_FOREIGN: _foreign,
    }.get(obj.kind)
    if handler is None:
        raise FixError("tipo de objeto sem correcao automatica")
    return handler(model, plan, obj, label)


def _state(model, obj):
    state = model.tables.get(obj.table.lower())
    if state is None:
        raise FixError("a tabela nao esta no modelo")
    return state


def _table(model, plan, obj, label):
    state = _state(model, obj)
    if obj.status == AUSENTE:
        if not state.fully_known or state.origin_unknown:
            raise FixError("a estrutura da tabela nao e conhecida por inteiro (ela veio do banco-base)")
        warnings = []
        if state.unchecked_elements:
            warnings.append("CHECK e opcoes que a auditoria nao compara NAO foram incluidos")
        items = [_item(plan, obj, label, create_table_statement(state), "tabela ausente no banco", RISK_LOW, BUCKET_CREATE_TABLE, warnings=warnings)]
        for fk in state.foreign_keys.values():  # depois que todas as tabelas existirem
            items.append(
                _item(plan, obj, f"chave estrangeira {state.name}.{fk.spec.name}",
                      alter(state.name, "ADD " + foreign_key_definition(fk.spec, named=not fk.auto_named)),
                      "chave estrangeira da tabela ausente", RISK_MEDIUM, BUCKET_FOREIGN,
                      warnings=["falha se ja houver linhas que violem a referencia"])
            )
        return items
    items = []
    for difference in obj.differences:
        if difference.attribute == "engine":
            items.append(_item(plan, obj, label, alter(state.name, "ENGINE=" + _word(difference.expected, "engine")), _differences(obj), RISK_HIGH, BUCKET_OPTIONS,
                               warnings=["trocar o engine reconstroi a tabela e pode demorar"]))
        elif difference.attribute == "charset_tabela":
            items.append(_item(plan, obj, label, alter(state.name, "DEFAULT CHARACTER SET " + _word(difference.expected, "charset")), _differences(obj), RISK_MEDIUM, BUCKET_OPTIONS,
                               warnings=["muda so o padrao da tabela; as colunas existentes nao sao convertidas"]))
        elif difference.attribute == "collation_tabela":
            items.append(_item(plan, obj, label, alter(state.name, "DEFAULT COLLATE " + _word(difference.expected, "colacao")), _differences(obj), RISK_MEDIUM, BUCKET_OPTIONS,
                               warnings=["muda so o padrao da tabela; as colunas existentes nao sao convertidas"]))
    if not items:
        raise FixError("diferenca de tabela sem correcao automatica")
    return items


def _column(model, plan, obj, label):
    state = _state(model, obj)
    column = state.columns.get((obj.column or "").lower())
    if column is None:
        raise FixError("a coluna nao esta no modelo")
    spec = column.spec
    definition = column_definition(spec, spec.name.lower() in state.implicit_not_null)
    if obj.status == AUSENTE:
        place = ""
        if spec.position == "first":
            place = " FIRST"
        elif spec.position and spec.position.startswith("after:") and spec.position[6:].lower() in state.columns:
            place = " AFTER " + quote(spec.position[6:])
        not_null = spec.nullable is False or spec.name.lower() in state.implicit_not_null
        risk = RISK_MEDIUM if not_null and not spec.has_default else RISK_LOW
        warnings = ["NOT NULL sem DEFAULT: as linhas existentes recebem o valor padrao do tipo"] if risk == RISK_MEDIUM else []
        return [_item(plan, obj, label, alter(state.name, f"ADD COLUMN {definition}{place}"), "coluna ausente no banco", risk, BUCKET_COLUMN, warnings=warnings)]
    attributes = {d.attribute for d in obj.differences}
    if "coluna_gerada" in attributes:
        raise FixError("o banco tem coluna gerada que o script nao tem: revise manualmente")
    warnings = []
    risk = RISK_MEDIUM
    if attributes & _HIGH_RISK_DIFFERENCES:
        risk, warnings = RISK_HIGH, ["mudar tipo/charset/colacao converte os dados e pode truncar ou falhar"]
    elif any(d.attribute == "nulabilidade" and d.expected.startswith("NOT NULL") for d in obj.differences):
        risk, warnings = RISK_HIGH, ["falha se existirem linhas com NULL nesta coluna"]
    return [_item(plan, obj, label, alter(state.name, f"MODIFY COLUMN {definition}"), _differences(obj), risk, BUCKET_COLUMN, warnings=warnings)]


def _index(model, plan, obj, label):
    state = _state(model, obj)
    index = state.indexes.get((obj.column or "").lower())
    if index is None:
        raise FixError("o indice nao esta no modelo")
    warnings = ["falha se ja houver valores repetidos"] if index.spec.unique else []
    if obj.status == AUSENTE:
        return [_item(plan, obj, label, alter(state.name, "ADD " + index_definition(index.spec, name=not index.auto_named)), "indice ausente no banco", RISK_MEDIUM if index.spec.unique else RISK_LOW, BUCKET_INDEX, warnings=warnings)]
    if index.auto_named:
        raise FixError("o indice tem nome automatico: o nome real no banco nao e conhecido, revise manualmente")
    clause = f"DROP INDEX {quote(index.spec.name)}, ADD " + index_definition(index.spec)
    return [_item(plan, obj, label, alter(state.name, clause), _differences(obj), RISK_MEDIUM, BUCKET_INDEX, warnings=warnings)]


def _primary(model, plan, obj, label):
    state = _state(model, obj)
    if state.primary_key is None:
        raise FixError("a chave primaria nao esta no modelo")
    columns = _columns(state.primary_key.spec.columns)
    if obj.status == AUSENTE:
        return [_item(plan, obj, label, alter(state.name, f"ADD PRIMARY KEY {columns}"), "chave primaria ausente no banco", RISK_MEDIUM, BUCKET_PRIMARY,
                      warnings=["falha se houver NULL ou valores repetidos nas colunas"])]
    return [_item(plan, obj, label, alter(state.name, f"DROP PRIMARY KEY, ADD PRIMARY KEY {columns}"), _differences(obj), RISK_HIGH, BUCKET_PRIMARY,
                  warnings=["trocar a chave primaria reconstroi a tabela e pode falhar"])]


def _foreign(model, plan, obj, label):
    state = _state(model, obj)
    fk = state.foreign_keys.get((obj.column or "").lower())
    if fk is None:
        raise FixError("a chave estrangeira nao esta no modelo")
    warnings = ["falha se ja houver linhas que violem a referencia"]
    if obj.status == AUSENTE:
        return [_item(plan, obj, label, alter(state.name, "ADD " + foreign_key_definition(fk.spec, named=not fk.auto_named)), "chave estrangeira ausente no banco", RISK_MEDIUM, BUCKET_FOREIGN, warnings=warnings)]
    if fk.auto_named:
        raise FixError("a chave tem nome automatico: o nome real no banco nao e conhecido, revise manualmente")
    clause = f"DROP FOREIGN KEY {quote(fk.spec.name)}, ADD " + foreign_key_definition(fk.spec)
    return [_item(plan, obj, label, alter(state.name, clause), _differences(obj), RISK_MEDIUM, BUCKET_FOREIGN, warnings=warnings)]


def _removed(model, plan, obj, label):
    """Objeto que os scripts removeram e que ainda existe no banco."""
    why = (obj.notes[0] if obj.notes else "removido por um script") + "; ainda existe no banco"
    name = obj.column or ""
    if obj.kind == OBJ_TABLE:
        return [_item(plan, obj, label, f"DROP TABLE {quote(obj.table)};", why, RISK_HIGH, BUCKET_DROP_TABLE, destructive=True, warnings=["APAGA a tabela e todos os dados"])]
    if obj.kind == OBJ_COLUMN:
        return [_item(plan, obj, label, alter(obj.table, f"DROP COLUMN {quote(name)}"), why, RISK_HIGH, BUCKET_DROP_COLUMN, destructive=True, warnings=["APAGA a coluna e os dados dela"])]
    if obj.kind == OBJ_INDEX:
        return [_item(plan, obj, label, alter(obj.table, f"DROP INDEX {quote(name)}"), why, RISK_MEDIUM, BUCKET_INDEX)]
    if obj.kind == OBJ_PRIMARY:
        return [_item(plan, obj, label, alter(obj.table, "DROP PRIMARY KEY"), why, RISK_HIGH, BUCKET_PRIMARY, warnings=["pode falhar se a coluna for AUTO_INCREMENT ou usada por chave estrangeira"])]
    if obj.kind == OBJ_FOREIGN:
        return [_item(plan, obj, label, alter(obj.table, f"DROP FOREIGN KEY {quote(name)}"), why, RISK_MEDIUM, BUCKET_DROP_FK)]
    raise FixError("tipo de objeto removido sem correcao automatica")


# -- arquivo --

HEADER = """-- ======================================================================
-- SCRIPT CORRETIVO GERADO PARA REVISAO. NAO EXECUTE SEM REVISAR.
--
-- Gerado pela Auditoria de Estrutura SQL a partir da COMPARACAO entre os scripts
-- e o banco. O sistema NAO executou nada e NAO altera o banco.
--   * Faca BACKUP antes. Revise cada comando e rode primeiro em homologacao.
--   * Comandos de risco ALTO e todo DROP de coluna/tabela estao COMENTADOS:
--     so rodam se voce descomentar de proposito.
--   * Achados INDETERMINADOS ou nao suportados NAO geram comando.
--   * Compatibilidade estrutural nao prova que um script foi executado.
-- ======================================================================
"""


def _commented(text):
    return "\n".join("-- " + line if line else "--" for line in text.splitlines())


def render_script(plans, user="", now=None):
    """Texto do arquivo .sql com todas as versoes. `plans`: lista de FixPlan."""
    now = now or datetime.now()
    lines = [HEADER, f"-- Gerado em {now:%d/%m/%Y %H:%M}" + (f" por {user}" if user else ""), ""]
    for plan in plans:
        lines += [
            "-- ----------------------------------------------------------------------",
            f"-- Versao {plan.version} | banco {plan.database}",
            f"-- {len(plan.items)} comando(s), {plan.commented_count} comentado(s) para revisao",
            "-- ----------------------------------------------------------------------",
            _commented(f"USE {quote(plan.database)};"),
            "",
        ]
        for note in plan.notes:
            lines.append(_commented("ATENCAO: " + note))
        if plan.not_handled:
            counts = ", ".join(f"{count} {status.lower()}" for status, count in sorted(plan.not_handled.items()))
            lines.append(f"-- Sem comando (nao e seguro corrigir automaticamente): {counts}.")
        for label, reason in plan.skipped:
            lines.append(_commented(f"NAO GERADO ({label}): {reason}"))
        if plan.notes or plan.not_handled or plan.skipped:
            lines.append("")
        for item in plan.items:
            lines.append(f"-- [{item.risk.upper()}{' | DESTRUTIVO' if item.destructive else ''}] {item.label}")
            lines.append(f"--   motivo: {item.reason}" + (f" (origem: {item.source})" if item.source else ""))
            for warning in item.warnings:
                lines.append(f"--   cuidado: {warning}")
            if item.commented:
                lines.append("--   COMENTADO: descomente apenas depois de revisar")
                lines.append(_commented(item.statement))
            else:
                lines.append(item.statement)
            lines.append("")
        if not plan.items:
            lines += ["-- Nenhum comando para esta versao.", ""]
    return "\n".join(lines).rstrip() + "\n"


def write_script(plans, path, user="", now=None):
    """Grava o .sql de forma atomica. Levanta OSError/ValueError."""
    path = Path(path)
    if path.suffix.lower() != ".sql":
        raise ValueError("Use a extensao .sql.")
    text = render_script(plans, user, now)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return text
