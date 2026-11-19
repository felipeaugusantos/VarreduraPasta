"""Estado esperado do banco, montado por replay logico dos scripts (sem executar nada).

Aplica em ordem as operacoes interpretadas (`sql_parse`) sobre um modelo de
tabelas, colunas, indices, chave primaria e chaves estrangeiras. Cada definicao
guarda de qual versao/script/linha veio (proveniencia) e se essa origem e certa.
O que o modelo nao consegue seguir (DML, comandos nao interpretados, acoes nao
suportadas) NAO e ignorado: marca a tabela como incompleta ou entra em
`unresolved`, para a comparacao nunca afirmar mais do que sabe.

Cada operacao aplicada guarda o `target` que a verifica: `(tipo, tabela, nome)`
em minusculas. Objetos REMOVIDOS (coluna, indice, chave, tabela) ficam em
`dropped_*`/`absent_tables`: o esperado, nesses casos, e que NAO existam no banco.

Regras de replay (o MySQL falharia nesses casos, entao o modelo mantem o estado):
- CREATE TABLE de tabela que ja existe: conflito (ignorado se IF NOT EXISTS).
- ADD de coluna/indice/chave que ja existe: conflito, mantem a definicao anterior.
- DROP/RENAME/CHANGE de algo que o modelo nao conhece, numa tabela que os scripts
  criaram por inteiro: conflito. Se a tabela veio do banco-base, nao e erro.
- MODIFY/CHANGE substitui a definicao; a anterior fica marcada como substituida.
- Tabela que nenhum script cria veio do banco-base (`origin_unknown`).
- Chave primaria torna as colunas NOT NULL (continua assim mesmo se a chave cair).
"""

import re
from dataclasses import dataclass, field, replace

from app.sqlaudit.file_scanner import read_sql_text, SqlFileError
from app.sqlaudit.sql_parse import (
    KIND_ADD_COLUMN,
    KIND_ADD_FOREIGN_KEY,
    KIND_ADD_INDEX,
    KIND_ADD_PRIMARY_KEY,
    KIND_ALTER_OPTIONS,
    KIND_CHANGE_COLUMN,
    KIND_CONVERT_CHARSET,
    KIND_CREATE_TABLE,
    KIND_DROP_COLUMN,
    KIND_DROP_FOREIGN_KEY,
    KIND_DROP_INDEX,
    KIND_DROP_PRIMARY_KEY,
    KIND_DROP_TABLE,
    KIND_MODIFY_COLUMN,
    KIND_OTHER,
    KIND_RENAME_COLUMN,
    KIND_RENAME_TABLE,
    STATUS_ERROR,
    STATUS_INTERPRETED,
    ForeignKeySpec,
    IndexSpec,
    Operation,
    parse_text,
)

OUTCOME_APPLIED = "aplicada"
OUTCOME_IGNORED = "ignorada"
OUTCOME_CONFLICT = "conflito"
OUTCOME_UNSUPPORTED = "nao_suportada"
OUTCOME_ERROR = "erro"

OBJ_TABLE, OBJ_COLUMN, OBJ_INDEX, OBJ_PRIMARY, OBJ_FOREIGN = "tabela", "coluna", "indice", "chave_primaria", "chave_estrangeira"
PRIMARY_NAME = "primary"
_TEXT_TYPE = re.compile(r"^(char|varchar|tinytext|text|mediumtext|longtext|enum|set)\b")


@dataclass(frozen=True)
class Provenance:
    version: str
    script: str
    number: int | None
    sha256: str
    statement_index: int
    line: int
    kind: str
    certain: bool


@dataclass
class AppliedOperation:
    operation: Operation
    provenance: Provenance
    outcome: str
    note: str = ""
    superseded_by: Provenance | None = None
    target: tuple | None = None  # (tipo, tabela, nome) do objeto que verifica esta operacao


@dataclass
class ColumnState:
    spec: object  # ColumnSpec
    applied: AppliedOperation
    origin_unknown: bool = False  # so conhecemos o MODIFY: a coluna veio do banco-base

    @property
    def provenance(self):
        return self.applied.provenance


@dataclass
class IndexState:
    spec: IndexSpec  # com o nome ja resolvido (automatico quando o script nao deu)
    applied: AppliedOperation
    auto_named: bool = False

    @property
    def provenance(self):
        return self.applied.provenance


@dataclass
class ForeignKeyState:
    spec: ForeignKeySpec
    applied: AppliedOperation
    auto_named: bool = False

    @property
    def provenance(self):
        return self.applied.provenance


@dataclass
class DroppedState:
    """Objeto que os scripts removeram: o esperado e nao existir no banco."""

    kind: str
    name: str
    applied: AppliedOperation
    origin_unknown: bool = False  # nunca o vimos ser criado (veio do banco-base)

    @property
    def provenance(self):
        return self.applied.provenance


@dataclass
class TableState:
    name: str
    schema: str = ""
    columns: dict = field(default_factory=dict)  # nome minusculo -> ColumnState
    options: dict = field(default_factory=dict)
    created_by: AppliedOperation | None = None
    origin_unknown: bool = False  # nenhum script cria a tabela: veio do banco-base
    incomplete: list = field(default_factory=list)  # acoes que podem ter mudado colunas e o modelo nao seguiu
    unchecked_elements: list = field(default_factory=list)  # restricoes/opcoes que o modelo nao compara
    indexes: dict = field(default_factory=dict)  # nome minusculo -> IndexState
    primary_key: IndexState | None = None
    foreign_keys: dict = field(default_factory=dict)  # nome minusculo -> ForeignKeyState
    implicit_not_null: set = field(default_factory=set)  # colunas (minusculas) NOT NULL por causa da PK
    dropped_columns: dict = field(default_factory=dict)
    dropped_indexes: dict = field(default_factory=dict)
    dropped_primary_key: DroppedState | None = None
    dropped_foreign_keys: dict = field(default_factory=dict)

    @property
    def fully_known(self):
        return self.created_by is not None and not self.incomplete


@dataclass(frozen=True)
class Finding:
    kind: str
    message: str
    provenance: Provenance


@dataclass
class ExpectedModel:
    target: str
    tables: dict = field(default_factory=dict)  # nome minusculo -> TableState
    absent_tables: dict = field(default_factory=dict)  # nome minusculo -> DroppedState
    applied: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)  # comandos inteiros que o modelo nao seguiu
    scripts_loaded: int = 0

    def apply(self, operation, provenance):
        if operation.status != STATUS_INTERPRETED:
            return self._apply_unsupported(operation, provenance)
        if operation.kind not in (KIND_CREATE_TABLE, KIND_DROP_TABLE) and operation.table.lower() in self.absent_tables:
            # o MySQL daria "tabela nao existe": nao inventa uma tabela do banco-base
            return self._conflict(
                operation, provenance, "tabela_inexistente",
                f"{operation.kind} em {operation.table}: a tabela foi removida ou renomeada antes, no modelo.", "tabela removida",
            )
        handler = {
            KIND_CREATE_TABLE: self._create_table,
            KIND_ADD_COLUMN: self._add_column,
            KIND_MODIFY_COLUMN: self._modify_column,
            KIND_CHANGE_COLUMN: self._change_column,
            KIND_DROP_COLUMN: self._drop_column,
            KIND_RENAME_COLUMN: self._rename_column,
            KIND_DROP_TABLE: self._drop_table,
            KIND_RENAME_TABLE: self._rename_table,
            KIND_ADD_INDEX: self._add_index,
            KIND_DROP_INDEX: self._drop_index,
            KIND_ADD_PRIMARY_KEY: self._add_primary_key,
            KIND_DROP_PRIMARY_KEY: self._drop_primary_key,
            KIND_ADD_FOREIGN_KEY: self._add_foreign_key,
            KIND_DROP_FOREIGN_KEY: self._drop_foreign_key,
            KIND_ALTER_OPTIONS: self._alter_options,
            KIND_CONVERT_CHARSET: self._convert_charset,
        }[operation.kind]
        return handler(operation, provenance)

    # -- auxiliares --
    def _record(self, operation, provenance, outcome, note="", target=None):
        entry = AppliedOperation(operation, provenance, outcome, note, target=target)
        self.applied.append(entry)
        return entry

    def _finding(self, kind, message, provenance):
        self.findings.append(Finding(kind, message, provenance))

    def _conflict(self, operation, provenance, kind, message, note):
        self._finding(kind, message, provenance)
        return self._record(operation, provenance, OUTCOME_CONFLICT, note)

    def _table(self, operation, create=False):
        key = operation.table.lower()
        table = self.tables.get(key)
        if table is None:
            table = TableState(operation.table, operation.schema, origin_unknown=not create)
            self.tables[key] = table
        return table

    @staticmethod
    def _supersede(entry, provenance):
        if entry is not None and entry.superseded_by is None:
            entry.superseded_by = provenance

    @staticmethod
    def _target(kind, table, name=""):
        return (kind, table.name.lower(), name.lower())

    # -- nomes automaticos do MySQL --
    @staticmethod
    def _auto_index_name(table, columns):
        used = set(table.indexes) | {PRIMARY_NAME}
        base = columns[0]
        if base.lower() not in used:
            return base
        number = 2
        while f"{base}_{number}".lower() in used:
            number += 1
        return f"{base}_{number}"

    @staticmethod
    def _auto_fk_name(table):
        number = len(table.foreign_keys) + len(table.dropped_foreign_keys) + 1
        while f"{table.name}_ibfk_{number}".lower() in table.foreign_keys:
            number += 1
        return f"{table.name}_ibfk_{number}"

    # -- registro de indices/chaves (usado por CREATE, ADD COLUMN e ADD INDEX) --
    def _register_index(self, table, spec, operation, provenance, entry=None, note="", source="indice"):
        auto = not spec.name
        name = spec.name or self._auto_index_name(table, spec.columns)
        if name.lower() in table.indexes:
            self._finding("indice_repetido", f"Indice {name} de {table.name} ja existe no modelo.", provenance)
            return None
        spec = replace(spec, name=name)
        entry = entry or self._record(operation, provenance, OUTCOME_APPLIED, note, self._target(OBJ_INDEX, table, name))
        table.indexes[name.lower()] = IndexState(spec, entry, auto_named=auto)
        dropped = table.dropped_indexes.pop(name.lower(), None)
        if dropped is not None:
            self._supersede(dropped.applied, provenance)
        return entry

    def _register_primary_key(self, table, columns, operation, provenance, entry=None, note=""):
        if table.primary_key is not None:
            self._finding("chave_primaria_repetida", f"{table.name} ja tem chave primaria no modelo.", provenance)
            return False
        entry = entry or self._record(operation, provenance, OUTCOME_APPLIED, note, self._target(OBJ_PRIMARY, table, PRIMARY_NAME))
        table.primary_key = IndexState(IndexSpec(PRIMARY_NAME.upper(), tuple(columns), unique=True), entry)
        table.implicit_not_null.update(column.lower() for column in columns)
        if table.dropped_primary_key is not None:
            self._supersede(table.dropped_primary_key.applied, provenance)
            table.dropped_primary_key = None
        return True

    def _register_foreign_key(self, table, spec, operation, provenance, entry=None, note=""):
        auto = not spec.name
        name = spec.name or self._auto_fk_name(table)
        if name.lower() in table.foreign_keys:
            self._finding("chave_estrangeira_repetida", f"Chave estrangeira {name} de {table.name} ja existe no modelo.", provenance)
            return False
        entry = entry or self._record(operation, provenance, OUTCOME_APPLIED, note, self._target(OBJ_FOREIGN, table, name))
        table.foreign_keys[name.lower()] = ForeignKeyState(replace(spec, name=name), entry, auto_named=auto)
        dropped = table.dropped_foreign_keys.pop(name.lower(), None)
        if dropped is not None:
            self._supersede(dropped.applied, provenance)
        return True

    def _inline_keys(self, table, spec, operation, provenance, entry):
        """PRIMARY KEY / UNIQUE escritos na propria definicao da coluna."""
        if spec.primary_key:
            self._register_primary_key(table, (spec.name,), operation, provenance, note="PRIMARY KEY da coluna")
        if spec.unique:
            self._register_index(table, IndexSpec(None, (spec.name,), (None,), unique=True), operation, provenance, note="UNIQUE da coluna")

    def _forget_not_null(self, table, spec):
        """MODIFY/CHANGE sem NOT NULL volta a coluna a aceitar NULL, salvo se ela e da PK."""
        key = spec.name.lower()
        in_pk = table.primary_key is not None and key in {c.lower() for c in table.primary_key.spec.columns}
        if not in_pk:
            table.implicit_not_null.discard(key)

    # -- operacoes sobre tabelas --
    def _create_table(self, operation, provenance):
        key = operation.table.lower()
        existing = self.tables.get(key)
        if existing is not None:
            if operation.if_not_exists:
                return self._record(operation, provenance, OUTCOME_IGNORED, "tabela ja existia (IF NOT EXISTS)")
            return self._conflict(operation, provenance, "tabela_repetida", f"CREATE TABLE {operation.table}: a tabela ja existe no modelo.", "tabela ja existia")
        table = self._table(operation, create=True)
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_TABLE, table))
        table.created_by = entry
        table.options = dict(operation.options)
        dropped = self.absent_tables.pop(key, None)
        if dropped is not None:
            self._supersede(dropped.applied, provenance)
        for spec in operation.columns:
            column_entry = self._record(
                Operation(
                    kind=KIND_CREATE_TABLE, status=STATUS_INTERPRETED, table=operation.table, schema=operation.schema,
                    column=spec, statement=operation.statement,
                ),
                provenance,
                OUTCOME_APPLIED,
                "coluna do CREATE TABLE",
                self._target(OBJ_COLUMN, table, spec.name),
            )
            table.columns[spec.name.lower()] = ColumnState(spec, column_entry)
            self._inline_keys(table, spec, operation, provenance, column_entry)
        if operation.primary_key:
            self._register_primary_key(table, operation.primary_key, operation, provenance, note="PRIMARY KEY do CREATE TABLE")
        for spec in operation.indexes:
            self._register_index(table, spec, operation, provenance, note="indice do CREATE TABLE")
        for spec in operation.foreign_keys:
            self._register_foreign_key(table, spec, operation, provenance, note="chave estrangeira do CREATE TABLE")
        return entry

    def _drop_table(self, operation, provenance):
        key = operation.table.lower()
        known = key in self.tables
        if not known and key in self.absent_tables and not operation.if_exists:
            return self._conflict(operation, provenance, "tabela_inexistente", f"DROP TABLE {operation.table}: a tabela ja foi removida no modelo.", "tabela ja removida")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=(OBJ_TABLE, key, ""))
        table = self.tables.pop(key, None)
        if table is not None:  # tudo o que era dela deixa de valer
            for other in self.applied:
                if other is not entry and other.target and other.target[1] == key:
                    self._supersede(other, provenance)
        self.absent_tables[key] = DroppedState(OBJ_TABLE, operation.table, entry, origin_unknown=table is None or table.origin_unknown)
        return entry

    def _rename_table(self, operation, provenance):
        old_key, new_key = operation.table.lower(), operation.new_name.lower()
        table = self.tables.get(old_key)
        if new_key in self.tables:
            return self._conflict(operation, provenance, "tabela_repetida", f"RENAME TO {operation.new_name}: ja existe uma tabela com esse nome.", "destino ja existia")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=(OBJ_TABLE, new_key, ""))
        if table is None:  # tabela do banco-base: so sabemos o nome novo
            table = TableState(operation.new_name, operation.schema, origin_unknown=True)
            table.incomplete.append(entry)  # as colunas dela nao sao conhecidas
            self.tables[new_key] = table
        else:
            del self.tables[old_key]
            table.name = operation.new_name
            self.tables[new_key] = table
            for other in self.applied:
                if other is not entry and other.target and other.target[1] == old_key:
                    other.target = (other.target[0], new_key, other.target[2])
        gone = self.absent_tables.pop(new_key, None)
        if gone is not None:  # o nome novo estava removido: a renomeacao o recria
            self._supersede(gone.applied, provenance)
        self.absent_tables[old_key] = DroppedState(OBJ_TABLE, operation.table, entry, origin_unknown=table.origin_unknown)
        return entry

    def _alter_options(self, operation, provenance):
        table = self._table(operation)
        table.options.update(dict(operation.options))
        return self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_TABLE, table))

    def _convert_charset(self, operation, provenance):
        table = self._table(operation)
        options = dict(operation.options)
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_TABLE, table))
        table.options["charset"] = options["charset"]
        if "collation" in options:
            table.options["collation"] = options["collation"]
        else:  # a colacao passa a ser a padrao do novo charset, que o modelo nao conhece
            table.options.pop("collation", None)
        for state in table.columns.values():
            if _TEXT_TYPE.match(state.spec.data_type):
                state.spec = replace(state.spec, charset=options["charset"], collation=options.get("collation"))
        if not table.fully_known and table.created_by is None:
            table.incomplete.append(entry)  # colunas do banco-base tambem foram convertidas
        return entry

    # -- colunas --
    def _add_column(self, operation, provenance):
        table = self._table(operation)
        key = operation.column.name.lower()
        if key in table.columns:
            return self._conflict(operation, provenance, "coluna_repetida", f"ADD COLUMN {operation.table}.{operation.column.name}: a coluna ja existe no modelo.", "coluna ja existia")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_COLUMN, table, operation.column.name))
        table.columns[key] = ColumnState(operation.column, entry)
        dropped = table.dropped_columns.pop(key, None)
        if dropped is not None:
            self._supersede(dropped.applied, provenance)
        self._inline_keys(table, operation.column, operation, provenance, entry)
        return entry

    def _modify_column(self, operation, provenance):
        table = self._table(operation)
        key = operation.column.name.lower()
        current = table.columns.get(key)
        if current is None and table.fully_known:
            return self._conflict(operation, provenance, "coluna_inexistente", f"MODIFY COLUMN {operation.table}.{operation.column.name}: a coluna nao existe no modelo.", "coluna inexistente")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_COLUMN, table, operation.column.name))
        if current is not None:
            self._supersede(current.applied, provenance)
        table.columns[key] = ColumnState(operation.column, entry, origin_unknown=current is None)
        if operation.column.nullable is not False:
            self._forget_not_null(table, operation.column)
        self._inline_keys(table, operation.column, operation, provenance, entry)
        return entry

    def _rename_in_keys(self, table, old, new):
        old_key = old.lower()
        swap = lambda columns: tuple(new if column.lower() == old_key else column for column in columns)
        for state in table.indexes.values():
            state.spec = replace(state.spec, columns=swap(state.spec.columns))
        if table.primary_key is not None:
            table.primary_key.spec = replace(table.primary_key.spec, columns=swap(table.primary_key.spec.columns))
        for state in table.foreign_keys.values():
            state.spec = replace(state.spec, columns=swap(state.spec.columns))
        if old_key in table.implicit_not_null:
            table.implicit_not_null.discard(old_key)
            table.implicit_not_null.add(new.lower())

    def _change_column(self, operation, provenance):
        table = self._table(operation)
        old_key, new = operation.name.lower(), operation.column
        current = table.columns.get(old_key)
        if current is None and table.fully_known:
            return self._conflict(operation, provenance, "coluna_inexistente", f"CHANGE COLUMN {operation.table}.{operation.name}: a coluna nao existe no modelo.", "coluna inexistente")
        if new.name.lower() != old_key and new.name.lower() in table.columns:
            return self._conflict(operation, provenance, "coluna_repetida", f"CHANGE COLUMN {operation.table}.{operation.name} -> {new.name}: o nome novo ja existe.", "nome novo ja existia")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_COLUMN, table, new.name))
        if current is not None:
            self._supersede(current.applied, provenance)
            del table.columns[old_key]
        table.columns[new.name.lower()] = ColumnState(new, entry, origin_unknown=current is None)
        if new.name.lower() != old_key:
            self._rename_in_keys(table, operation.name, new.name)
            table.dropped_columns[old_key] = DroppedState(OBJ_COLUMN, operation.name, entry, origin_unknown=current is None)
            dropped_new = table.dropped_columns.pop(new.name.lower(), None)
            if dropped_new is not None:
                self._supersede(dropped_new.applied, provenance)
        if new.nullable is not False:
            self._forget_not_null(table, new)
        self._inline_keys(table, new, operation, provenance, entry)
        return entry

    def _rename_column(self, operation, provenance):
        table = self._table(operation)
        old_key, new_name = operation.name.lower(), operation.new_name
        current = table.columns.get(old_key)
        if current is None and table.fully_known:
            return self._conflict(operation, provenance, "coluna_inexistente", f"RENAME COLUMN {operation.table}.{operation.name}: a coluna nao existe no modelo.", "coluna inexistente")
        if new_name.lower() in table.columns:
            return self._conflict(operation, provenance, "coluna_repetida", f"RENAME COLUMN {operation.table}.{operation.name} -> {new_name}: o nome novo ja existe.", "nome novo ja existia")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_COLUMN, table, new_name))
        if current is not None:
            del table.columns[old_key]
            current.spec = replace(current.spec, name=new_name)
            table.columns[new_name.lower()] = current
            if current.applied.target is not None:  # a definicao da coluna passa a valer para o nome novo
                current.applied.target = self._target(OBJ_COLUMN, table, new_name)
            self._rename_in_keys(table, operation.name, new_name)
        table.dropped_columns[old_key] = DroppedState(OBJ_COLUMN, operation.name, entry, origin_unknown=current is None)
        dropped_new = table.dropped_columns.pop(new_name.lower(), None)
        if dropped_new is not None:
            self._supersede(dropped_new.applied, provenance)
        return entry

    def _drop_column(self, operation, provenance):
        table = self._table(operation)
        key = operation.name.lower()
        current = table.columns.get(key)
        if current is None and (table.fully_known or key in table.dropped_columns):
            return self._conflict(operation, provenance, "coluna_inexistente", f"DROP COLUMN {operation.table}.{operation.name}: a coluna nao existe no modelo.", "coluna inexistente")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_COLUMN, table, operation.name))
        if current is not None:
            self._supersede(current.applied, provenance)
            del table.columns[key]
        table.dropped_columns[key] = DroppedState(OBJ_COLUMN, operation.name, entry, origin_unknown=current is None)
        self._remove_column_from_keys(table, key, provenance)
        table.implicit_not_null.discard(key)
        return entry

    def _remove_column_from_keys(self, table, key, provenance):
        """O MySQL tira a coluna dos indices e chaves; o que ficar sem colunas some."""
        for name, state in list(table.indexes.items()):
            kept = [i for i, column in enumerate(state.spec.columns) if column.lower() != key]
            if len(kept) == len(state.spec.columns):
                continue
            if not kept:
                self._supersede(state.applied, provenance)
                del table.indexes[name]
            else:
                prefixes = state.spec.prefixes
                state.spec = replace(
                    state.spec,
                    columns=tuple(state.spec.columns[i] for i in kept),
                    prefixes=tuple(prefixes[i] for i in kept) if len(prefixes) == len(state.spec.columns) else (),
                )
        pk = table.primary_key
        if pk is not None:
            columns = tuple(c for c in pk.spec.columns if c.lower() != key)
            if columns != pk.spec.columns:
                if columns:
                    pk.spec = replace(pk.spec, columns=columns)
                else:
                    table.primary_key = None

    # -- indices e chaves --
    def _add_index(self, operation, provenance):
        table = self._table(operation)
        spec = operation.index
        name = (spec.name or "").lower()
        if name and name in table.indexes:
            return self._conflict(operation, provenance, "indice_repetido", f"Indice {spec.name} de {operation.table} ja existe no modelo.", "indice ja existia")
        return self._register_index(table, spec, operation, provenance)

    def _drop_index(self, operation, provenance):
        table = self._table(operation)
        key = operation.name.lower()
        if key == PRIMARY_NAME:
            return self._drop_primary_key(operation, provenance)
        current = table.indexes.get(key)
        if current is None and (table.fully_known or key in table.dropped_indexes):
            return self._conflict(operation, provenance, "indice_inexistente", f"DROP INDEX {operation.name} de {operation.table}: o indice nao existe no modelo.", "indice inexistente")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_INDEX, table, operation.name))
        if current is not None:
            self._supersede(current.applied, provenance)
            del table.indexes[key]
        table.dropped_indexes[key] = DroppedState(OBJ_INDEX, operation.name, entry, origin_unknown=current is None)
        return entry

    def _add_primary_key(self, operation, provenance):
        table = self._table(operation)
        if table.primary_key is not None:
            return self._conflict(operation, provenance, "chave_primaria_repetida", f"{operation.table} ja tem chave primaria no modelo.", "chave primaria ja existia")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_PRIMARY, table, PRIMARY_NAME))
        self._register_primary_key(table, operation.primary_key, operation, provenance, entry=entry)
        return entry

    def _drop_primary_key(self, operation, provenance):
        table = self._table(operation)
        current = table.primary_key
        if current is None and (table.fully_known or table.dropped_primary_key is not None):
            return self._conflict(operation, provenance, "chave_primaria_inexistente", f"DROP PRIMARY KEY de {operation.table}: nao ha chave primaria no modelo.", "chave primaria inexistente")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_PRIMARY, table, PRIMARY_NAME))
        if current is not None:
            self._supersede(current.applied, provenance)
            table.primary_key = None
        table.dropped_primary_key = DroppedState(OBJ_PRIMARY, "PRIMARY", entry, origin_unknown=current is None)
        return entry

    def _add_foreign_key(self, operation, provenance):
        table = self._table(operation)
        spec = operation.foreign_key
        if spec.name and spec.name.lower() in table.foreign_keys:
            return self._conflict(operation, provenance, "chave_estrangeira_repetida", f"Chave estrangeira {spec.name} de {operation.table} ja existe no modelo.", "chave estrangeira ja existia")
        name = spec.name or self._auto_fk_name(table)
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_FOREIGN, table, name))
        self._register_foreign_key(table, spec, operation, provenance, entry=entry)
        return entry

    def _drop_foreign_key(self, operation, provenance):
        table = self._table(operation)
        key = operation.name.lower()
        current = table.foreign_keys.get(key)
        if current is None and (table.fully_known or key in table.dropped_foreign_keys):
            return self._conflict(operation, provenance, "chave_estrangeira_inexistente", f"DROP FOREIGN KEY {operation.name} de {operation.table}: nao existe no modelo.", "chave estrangeira inexistente")
        entry = self._record(operation, provenance, OUTCOME_APPLIED, target=self._target(OBJ_FOREIGN, table, operation.name))
        if current is not None:
            self._supersede(current.applied, provenance)
            del table.foreign_keys[key]
        table.dropped_foreign_keys[key] = DroppedState(OBJ_FOREIGN, operation.name, entry, origin_unknown=current is None)
        return entry

    def _apply_unsupported(self, operation, provenance):
        outcome = OUTCOME_ERROR if operation.status == STATUS_ERROR else OUTCOME_UNSUPPORTED
        entry = self._record(operation, provenance, outcome, operation.reason)
        if not operation.table:
            self.unresolved.append(entry)
            return entry
        table = self._table(operation)
        if operation.kind == KIND_CREATE_TABLE or not operation.affects_columns:
            table.unchecked_elements.append(entry)
        else:
            table.incomplete.append(entry)
        return entry

    # -- consultas --
    def counts(self):
        counts = {}
        for entry in self.applied:
            counts[entry.outcome] = counts.get(entry.outcome, 0) + 1
        return counts


@dataclass(frozen=True)
class ScriptOperations:
    operations: tuple = ()
    encoding: str = ""
    error: str = ""


def load_script_operations(script_file):
    """Le e interpreta um arquivo. Falha de leitura vira `error`, nunca excecao."""
    try:
        text, encoding = read_sql_text(script_file.path)
    except SqlFileError as error:
        return ScriptOperations(error=str(error))
    return ScriptOperations(tuple(parse_text(text)), encoding)


def build_expected_model(effective, loader=load_script_operations, cache=None, cancel_event=None, progress=None):
    """Replay dos `EffectiveScripts` (ver version_tree) em ordem.

    `cache` (dict sha256 -> ScriptOperations) evita reinterpretar o mesmo
    conteudo copiado em varias versoes. Para no meio se `cancel_event` for acionado.
    """
    model = ExpectedModel(target=effective.target)
    cache = {} if cache is None else cache
    for position, item in enumerate(effective.scripts, start=1):
        if cancel_event is not None and cancel_event.is_set():
            break
        script = item.script
        loaded = cache.get(script.sha256)
        if loaded is None:
            loaded = loader(script)
            cache[script.sha256] = loaded
        base = dict(version=item.version, script=script.relative, number=script.number, sha256=script.sha256)
        if loaded.error:
            failure = Operation(kind=KIND_OTHER, status=STATUS_ERROR, reason=loaded.error)
            model.apply(failure, Provenance(statement_index=0, line=0, kind=KIND_OTHER, certain=item.certain, **base))
        for operation in loaded.operations:
            statement = operation.statement
            model.apply(
                operation,
                Provenance(
                    statement_index=statement.index if statement else 0,
                    line=statement.line_start if statement else 0,
                    kind=operation.kind,
                    certain=item.certain,
                    **base,
                ),
            )
        model.scripts_loaded += 1
        if progress is not None:
            progress(position, len(effective.scripts))
    return model
