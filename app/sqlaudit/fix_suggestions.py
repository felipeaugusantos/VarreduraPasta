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
from types import SimpleNamespace

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
    undo: str = ""  # comando que volta ao estado lido do banco (vai comentado, na secao DESFAZER)
    undo_note: str = ""  # quando nao ha como desfazer so com SQL: o que fazer

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
    schema: object = field(default=None, repr=False)  # DbSchema lido do banco (so em memoria)

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


_NOW_DEFAULT = re.compile(r"^(current_timestamp|now|localtime|localtimestamp)(\(\d*\))?$", re.IGNORECASE)


def db_column_definition(column):
    """Definicao de uma coluna como ELA ESTA no banco (para desfazer). Levanta FixError se nao der."""
    extra = (column.extra or "").lower()
    if "generated" in extra:
        raise FixError("a coluna e gerada ou tem default por expressao: revise manualmente")
    parts = [quote(column.name), _safe_type(column.column_type)]
    if column.charset:
        parts += ["CHARACTER SET", _word(column.charset, "charset")]
    if column.collation:
        parts += ["COLLATE", _word(column.collation, "colacao")]
    parts.append("NULL" if column.nullable else "NOT NULL")
    if column.default is not None:
        default = str(column.default)
        parts += ["DEFAULT", default if _NOW_DEFAULT.match(default) else literal(default)]
    elif column.nullable:
        parts += ["DEFAULT", "NULL"]
    if "auto_increment" in extra:
        parts.append("AUTO_INCREMENT")
    match = re.search(r"on update (current_timestamp(?:\(\d*\))?|now\(\d*\))", extra)
    if match:
        parts += ["ON UPDATE", match.group(1).upper()]
    if column.comment:
        parts += ["COMMENT", literal(column.comment)]
    return " ".join(parts)


def db_index_definition(index):
    kind = index.index_type.lower() if index.index_type.lower() in ("fulltext", "spatial") else ""
    spec = SimpleNamespace(unique=index.unique, kind=kind, name=index.name, columns=index.columns, prefixes=index.prefixes)
    return index_definition(spec)


def db_foreign_key_definition(fk):
    spec = SimpleNamespace(
        name=fk.name, columns=fk.columns, ref_schema="", ref_table=fk.ref_table, ref_columns=fk.ref_columns,
        on_delete=fk.delete_rule if fk.delete_rule not in ("", "RESTRICT", "NO ACTION") else "",
        on_update=fk.update_rule if fk.update_rule not in ("", "RESTRICT", "NO ACTION") else "",
    )
    return foreign_key_definition(spec)


def _db_table(plan, table):
    schema = plan.schema
    return schema.tables.get(str(table).lower()) if schema is not None else None


def _undo(plan, table, build):
    """(comando, nota): `build(DbTable)` monta o SQL a partir do que o banco tinha; FixError vira nota."""
    db_table = _db_table(plan, table)
    if db_table is None:
        return "", "o esquema original nao foi guardado: restaure do backup"
    try:
        return build(db_table), ""
    except (FixError, KeyError) as error:
        return "", f"nao foi possivel montar o comando de desfazer ({error}): restaure do backup"


def _pk_columns(db_table):
    primary = db_table.indexes.get("primary")
    if primary is None:
        raise FixError("o banco nao tinha chave primaria")
    return primary.columns


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


def build_fix_plan(model, comparison, version="", database="", schema=None):
    """FixPlan de uma versao. `model` e o ExpectedModel e `comparison` o ComparisonResult dele.

    `schema` (DbSchema lido do banco, opcional) permite montar o DESFAZER com a definicao original.
    """
    plan = FixPlan(version, database or comparison.database, schema=schema)
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


def _item(plan, obj, label, statement, reason, risk, bucket, destructive=False, warnings=(), undo="", undo_note=""):
    return FixItem(
        plan.version, plan.database, obj.table, label, statement, reason, risk, destructive, bucket,
        _origin(obj), tuple(warnings), _order(obj), undo, undo_note,
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
        items = [_item(plan, obj, label, create_table_statement(state), "tabela ausente no banco", RISK_LOW, BUCKET_CREATE_TABLE, warnings=warnings,
                       undo=f"DROP TABLE {quote(state.name)};")]
        for fk in state.foreign_keys.values():  # depois que todas as tabelas existirem
            items.append(
                _item(plan, obj, f"chave estrangeira {state.name}.{fk.spec.name}",
                      alter(state.name, "ADD " + foreign_key_definition(fk.spec, named=not fk.auto_named)),
                      "chave estrangeira da tabela ausente", RISK_MEDIUM, BUCKET_FOREIGN,
                      warnings=["falha se ja houver linhas que violem a referencia"],
                      undo=(alter(state.name, f"DROP FOREIGN KEY {quote(fk.spec.name)}") if not fk.auto_named else ""),
                      undo_note=("" if not fk.auto_named else "a tabela inteira some no DROP TABLE acima"))
            )
        return items
    items = []
    for difference in obj.differences:
        if difference.attribute == "engine":
            undo, note = _undo(plan, state.name, lambda t, d=difference: alter(state.name, "ENGINE=" + _word(d.found, "engine")))
            items.append(_item(plan, obj, label, alter(state.name, "ENGINE=" + _word(difference.expected, "engine")), _differences(obj), RISK_HIGH, BUCKET_OPTIONS,
                               warnings=["trocar o engine reconstroi a tabela e pode demorar"], undo=undo, undo_note=note))
        elif difference.attribute == "charset_tabela":
            undo, note = _undo(plan, state.name, lambda t, d=difference: alter(state.name, "DEFAULT CHARACTER SET " + _word(d.found, "charset")))
            items.append(_item(plan, obj, label, alter(state.name, "DEFAULT CHARACTER SET " + _word(difference.expected, "charset")), _differences(obj), RISK_MEDIUM, BUCKET_OPTIONS,
                               warnings=["muda so o padrao da tabela; as colunas existentes nao sao convertidas"], undo=undo, undo_note=note))
        elif difference.attribute == "collation_tabela":
            undo, note = _undo(plan, state.name, lambda t, d=difference: alter(state.name, "DEFAULT COLLATE " + _word(d.found, "colacao")))
            items.append(_item(plan, obj, label, alter(state.name, "DEFAULT COLLATE " + _word(difference.expected, "colacao")), _differences(obj), RISK_MEDIUM, BUCKET_OPTIONS,
                               warnings=["muda so o padrao da tabela; as colunas existentes nao sao convertidas"], undo=undo, undo_note=note))
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
        return [_item(plan, obj, label, alter(state.name, f"ADD COLUMN {definition}{place}"), "coluna ausente no banco", risk, BUCKET_COLUMN, warnings=warnings,
                      undo=alter(state.name, f"DROP COLUMN {quote(spec.name)}"))]
    attributes = {d.attribute for d in obj.differences}
    if "coluna_gerada" in attributes:
        raise FixError("o banco tem coluna gerada que o script nao tem: revise manualmente")
    warnings = []
    risk = RISK_MEDIUM
    if attributes & _HIGH_RISK_DIFFERENCES:
        risk, warnings = RISK_HIGH, ["mudar tipo/charset/colacao converte os dados e pode truncar ou falhar"]
    elif any(d.attribute == "nulabilidade" and d.expected.startswith("NOT NULL") for d in obj.differences):
        risk, warnings = RISK_HIGH, ["falha se existirem linhas com NULL nesta coluna"]
    def back(db_table):
        original = db_table.columns[(obj.column or "").lower()]
        return alter(state.name, f"MODIFY COLUMN {db_column_definition(original)}")

    undo, note = _undo(plan, state.name, back)
    return [_item(plan, obj, label, alter(state.name, f"MODIFY COLUMN {definition}"), _differences(obj), risk, BUCKET_COLUMN, warnings=warnings, undo=undo, undo_note=note)]


def _index(model, plan, obj, label):
    state = _state(model, obj)
    index = state.indexes.get((obj.column or "").lower())
    if index is None:
        raise FixError("o indice nao esta no modelo")
    warnings = ["falha se ja houver valores repetidos"] if index.spec.unique else []
    if obj.status == AUSENTE:
        named = not index.auto_named
        return [_item(plan, obj, label, alter(state.name, "ADD " + index_definition(index.spec, name=named)), "indice ausente no banco", RISK_MEDIUM if index.spec.unique else RISK_LOW, BUCKET_INDEX, warnings=warnings,
                      undo=(alter(state.name, f"DROP INDEX {quote(index.spec.name)}") if named else ""),
                      undo_note=("" if named else "indice de nome automatico: veja o nome criado com SHOW INDEX e remova-o"))]
    if index.auto_named:
        raise FixError("o indice tem nome automatico: o nome real no banco nao e conhecido, revise manualmente")
    clause = f"DROP INDEX {quote(index.spec.name)}, ADD " + index_definition(index.spec)
    undo, note = _undo(
        plan, state.name,
        lambda t: alter(state.name, f"DROP INDEX {quote(index.spec.name)}, ADD " + db_index_definition(t.indexes[index.spec.name.lower()])),
    )
    return [_item(plan, obj, label, alter(state.name, clause), _differences(obj), RISK_MEDIUM, BUCKET_INDEX, warnings=warnings, undo=undo, undo_note=note)]


def _primary(model, plan, obj, label):
    state = _state(model, obj)
    if state.primary_key is None:
        raise FixError("a chave primaria nao esta no modelo")
    columns = _columns(state.primary_key.spec.columns)
    if obj.status == AUSENTE:
        return [_item(plan, obj, label, alter(state.name, f"ADD PRIMARY KEY {columns}"), "chave primaria ausente no banco", RISK_MEDIUM, BUCKET_PRIMARY,
                      warnings=["falha se houver NULL ou valores repetidos nas colunas"],
                      undo=alter(state.name, "DROP PRIMARY KEY"))]
    undo, note = _undo(plan, state.name, lambda t: alter(state.name, f"DROP PRIMARY KEY, ADD PRIMARY KEY {_columns(_pk_columns(t))}"))
    return [_item(plan, obj, label, alter(state.name, f"DROP PRIMARY KEY, ADD PRIMARY KEY {columns}"), _differences(obj), RISK_HIGH, BUCKET_PRIMARY,
                  warnings=["trocar a chave primaria reconstroi a tabela e pode falhar"], undo=undo, undo_note=note)]


def _foreign(model, plan, obj, label):
    state = _state(model, obj)
    fk = state.foreign_keys.get((obj.column or "").lower())
    if fk is None:
        raise FixError("a chave estrangeira nao esta no modelo")
    warnings = ["falha se ja houver linhas que violem a referencia"]
    if obj.status == AUSENTE:
        named = not fk.auto_named
        return [_item(plan, obj, label, alter(state.name, "ADD " + foreign_key_definition(fk.spec, named=named)), "chave estrangeira ausente no banco", RISK_MEDIUM, BUCKET_FOREIGN, warnings=warnings,
                      undo=(alter(state.name, f"DROP FOREIGN KEY {quote(fk.spec.name)}") if named else ""),
                      undo_note=("" if named else "chave de nome automatico: veja o nome criado com SHOW CREATE TABLE e remova-a"))]
    if fk.auto_named:
        raise FixError("a chave tem nome automatico: o nome real no banco nao e conhecido, revise manualmente")
    clause = f"DROP FOREIGN KEY {quote(fk.spec.name)}, ADD " + foreign_key_definition(fk.spec)
    undo, note = _undo(
        plan, state.name,
        lambda t: alter(state.name, f"DROP FOREIGN KEY {quote(fk.spec.name)}, ADD " + db_foreign_key_definition(t.foreign_keys[fk.spec.name.lower()])),
    )
    return [_item(plan, obj, label, alter(state.name, clause), _differences(obj), RISK_MEDIUM, BUCKET_FOREIGN, warnings=warnings, undo=undo, undo_note=note)]


def _removed(model, plan, obj, label):
    """Objeto que os scripts removeram e que ainda existe no banco."""
    why = (obj.notes[0] if obj.notes else "removido por um script") + "; ainda existe no banco"
    name = obj.column or ""
    if obj.kind == OBJ_TABLE:
        return [_item(plan, obj, label, f"DROP TABLE {quote(obj.table)};", why, RISK_HIGH, BUCKET_DROP_TABLE, destructive=True, warnings=["APAGA a tabela e todos os dados"],
                      undo_note="so o BACKUP recupera a tabela e os dados (restaure o dump ou a copia bkp_*)")]
    if obj.kind == OBJ_COLUMN:
        undo, note = _undo(plan, obj.table, lambda t: alter(obj.table, f"ADD COLUMN {db_column_definition(t.columns[name.lower()])}"))
        note = (note + "; " if note else "") + "os DADOS da coluna so voltam pelo BACKUP"
        return [_item(plan, obj, label, alter(obj.table, f"DROP COLUMN {quote(name)}"), why, RISK_HIGH, BUCKET_DROP_COLUMN, destructive=True, warnings=["APAGA a coluna e os dados dela"], undo=undo, undo_note=note)]
    if obj.kind == OBJ_INDEX:
        undo, note = _undo(plan, obj.table, lambda t: alter(obj.table, "ADD " + db_index_definition(t.indexes[name.lower()])))
        return [_item(plan, obj, label, alter(obj.table, f"DROP INDEX {quote(name)}"), why, RISK_MEDIUM, BUCKET_INDEX, undo=undo, undo_note=note)]
    if obj.kind == OBJ_PRIMARY:
        undo, note = _undo(plan, obj.table, lambda t: alter(obj.table, f"ADD PRIMARY KEY {_columns(_pk_columns(t))}"))
        return [_item(plan, obj, label, alter(obj.table, "DROP PRIMARY KEY"), why, RISK_HIGH, BUCKET_PRIMARY, warnings=["pode falhar se a coluna for AUTO_INCREMENT ou usada por chave estrangeira"], undo=undo, undo_note=note)]
    if obj.kind == OBJ_FOREIGN:
        undo, note = _undo(plan, obj.table, lambda t: alter(obj.table, "ADD " + db_foreign_key_definition(t.foreign_keys[name.lower()])))
        return [_item(plan, obj, label, alter(obj.table, f"DROP FOREIGN KEY {quote(name)}"), why, RISK_MEDIUM, BUCKET_DROP_FK, undo=undo, undo_note=note)]
    raise FixError("tipo de objeto removido sem correcao automatica")


# -- arquivo --

HEADER = """-- ======================================================================
-- SCRIPT CORRETIVO GERADO PARA REVISAO. NAO EXECUTE SEM REVISAR.
--
-- Gerado pela Auditoria de Estrutura SQL a partir da COMPARACAO entre os scripts
-- e o banco. O sistema NAO executou nada e NAO altera o banco.
--   * Revise cada comando e rode primeiro em homologacao (copia do banco).
--   * Comandos de risco ALTO e todo DROP de coluna/tabela estao COMENTADOS:
--     so rodam se voce descomentar de proposito.
--   * Achados INDETERMINADOS ou nao suportados NAO geram comando.
--   * Compatibilidade estrutural nao prova que um script foi executado.
--
-- SOBRE BEGIN / ROLLBACK
--   No MySQL, CREATE/ALTER/DROP TABLE fazem COMMIT IMPLICITO: nao existe transacao
--   que desfaca esses comandos. Por isso, em vez de BEGIN/ROLLBACK, cada versao traz:
--     SECAO 1 - BACKUP   (faca ANTES de aplicar)
--     SECAO 2 - APLICAR  (os comandos corretivos)
--     SECAO 3 - DESFAZER (tudo comentado; volta a estrutura ao que o banco tinha)
--   Rode com `mysql ... < arquivo.sql` SEM --force: o cliente PARA no primeiro erro.
--   O DESFAZER restaura a ESTRUTURA. Dados apagados ou truncados so o BACKUP recupera.
-- ======================================================================
"""

_SHELL_WORD = re.compile(r"^[A-Za-z0-9_.$-]+$")
BACKUP_PREFIX = "bkp"


def _commented(text):
    return "\n".join("-- " + line if line else "--" for line in text.splitlines())


def _shell_word(value, placeholder):
    return str(value) if _SHELL_WORD.match(str(value or "")) else placeholder


def backup_table_name(table, now):
    """Nome da copia dentro do banco (no maximo 64 caracteres, limite do MySQL)."""
    return f"{BACKUP_PREFIX}_{now:%Y%m%d}_{table}"[:64]


def _backup_tables(plan):
    """Tabelas que ja existem e serao alteradas (as criadas do zero nao precisam de backup)."""
    created = {item.table.lower() for item in plan.items if item.bucket == BUCKET_CREATE_TABLE}
    seen, tables = set(), []
    for item in plan.items:
        key = item.table.lower()
        if key in created or key in seen:
            continue
        seen.add(key)
        tables.append(item.table)
    return sorted(tables, key=str.lower)


def _backup_section(plan, now, server):
    tables = _backup_tables(plan)
    lines = [
        "-- ======================================================================",
        f"-- SECAO 1 - BACKUP ({len(tables)} tabela(s) alterada(s)): faca ANTES de aplicar",
        "-- ======================================================================",
    ]
    if not tables:
        lines += ["-- Nenhuma tabela existente sera alterada (so criacoes): nada a copiar.", ""]
        return lines
    host = _shell_word(server[0] if server else "", "<SERVIDOR>")
    port = _shell_word(server[1] if server else "", "<PORTA>")
    database = _shell_word(plan.database, "<BANCO>")
    names = " ".join(_shell_word(t, "<TABELA>") for t in tables)
    lines += [
        "-- a) Copia completa pelo mysqldump (linha de comando do Windows/Linux, nao e SQL).",
        "--    O usuario precisa de SELECT e LOCK TABLES; o -p sem valor faz o mysqldump perguntar no terminal:",
        f"--    mysqldump -h {host} -P {port} -u <USUARIO> -p --single-transaction {database} {names} > backup_{database}_{now:%Y%m%d-%H%M}.sql",
        "--",
        "-- b) Copia dentro do proprio banco (opcional; so para tabelas pequenas).",
        "--    Descomente para criar as copias; apague as bkp_* depois de validar.",
    ]
    for table in tables:
        copy = backup_table_name(table, now)
        lines.append(_commented(f"CREATE TABLE {quote(copy)} LIKE {quote(table)};"))
        lines.append(_commented(f"INSERT INTO {quote(copy)} SELECT * FROM {quote(table)};"))
    lines.append("")
    return lines


def _undo_section(plan):
    lines = [
        "-- ======================================================================",
        "-- SECAO 3 - DESFAZER (tudo comentado): so use se precisar voltar atras",
        "-- Ordem inversa da SECAO 2. Restaura a ESTRUTURA lida do banco; dados so pelo BACKUP.",
        "-- ======================================================================",
    ]
    wrote = False
    for item in reversed(plan.items):
        if not (item.undo or item.undo_note):
            continue
        wrote = True
        lines.append(f"-- desfazer: {item.label}")
        if item.undo:
            lines.append(_commented(item.undo))
        if item.undo_note:
            lines.append(f"--   obs: {item.undo_note}")
    if not wrote:
        lines.append("-- Nada a desfazer.")
    lines.append("")
    return lines


def render_script(plans, user="", now=None, server=None):
    """Texto do arquivo .sql com todas as versoes. `plans`: lista de FixPlan; `server`: (host, porta)."""
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
        lines += _backup_section(plan, now, server)
        lines += [
            "-- ======================================================================",
            "-- SECAO 2 - APLICAR",
            "-- ======================================================================",
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
        lines += _undo_section(plan)
    return "\n".join(lines).rstrip() + "\n"


def write_script(plans, path, user="", now=None, server=None):
    """Grava o .sql de forma atomica. Levanta OSError/ValueError."""
    path = Path(path)
    if path.suffix.lower() != ".sql":
        raise ValueError("Use a extensao .sql.")
    text = render_script(plans, user, now, server)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return text
