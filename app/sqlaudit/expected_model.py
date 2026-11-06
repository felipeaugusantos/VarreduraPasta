"""Estado esperado do banco, montado por replay logico dos scripts (sem executar nada).

Aplica em ordem as operacoes interpretadas (`sql_parse`) sobre um modelo de
tabelas/colunas. Cada definicao guarda de qual versao/script/linha veio
(proveniencia) e se essa origem e certa. O que o modelo nao consegue seguir
(DROP, CHANGE, DML, comandos nao interpretados) NAO e ignorado: marca a tabela
como incompleta ou entra em `unresolved`, para a comparacao nunca afirmar
mais do que sabe.

Regras de replay (o MySQL falharia nesses casos, entao o modelo mantem o estado):
- CREATE TABLE de tabela que ja existe: conflito (ignorado se IF NOT EXISTS).
- ADD COLUMN de coluna que ja existe: conflito, mantem a definicao anterior.
- MODIFY COLUMN substitui a definicao; a anterior fica marcada como substituida.
- MODIFY/ADD numa tabela que os scripts nunca criaram: a tabela veio do banco-base
  (`origin_unknown`), nao e erro.
"""

from dataclasses import dataclass, field

from app.sqlaudit.file_scanner import read_sql_text, SqlFileError
from app.sqlaudit.sql_parse import (
    KIND_ADD_COLUMN,
    KIND_CREATE_TABLE,
    KIND_MODIFY_COLUMN,
    KIND_OTHER,
    STATUS_ERROR,
    STATUS_INTERPRETED,
    Operation,
    parse_text,
)

OUTCOME_APPLIED = "aplicada"
OUTCOME_IGNORED = "ignorada"
OUTCOME_CONFLICT = "conflito"
OUTCOME_UNSUPPORTED = "nao_suportada"
OUTCOME_ERROR = "erro"


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


@dataclass
class ColumnState:
    spec: object  # ColumnSpec
    applied: AppliedOperation
    origin_unknown: bool = False  # so conhecemos o MODIFY: a coluna veio do banco-base

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
    incomplete: list = field(default_factory=list)  # AppliedOperation que o modelo nao seguiu
    unchecked_elements: list = field(default_factory=list)  # chaves/indices/FK do CREATE TABLE

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
    applied: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)  # comandos inteiros que o modelo nao seguiu
    scripts_loaded: int = 0

    def apply(self, operation, provenance):
        if operation.status != STATUS_INTERPRETED:
            return self._apply_unsupported(operation, provenance)
        handler = {
            KIND_CREATE_TABLE: self._create_table,
            KIND_ADD_COLUMN: self._add_column,
            KIND_MODIFY_COLUMN: self._modify_column,
        }[operation.kind]
        return handler(operation, provenance)

    # -- auxiliares --
    def _record(self, operation, provenance, outcome, note=""):
        entry = AppliedOperation(operation, provenance, outcome, note)
        self.applied.append(entry)
        return entry

    def _finding(self, kind, message, provenance):
        self.findings.append(Finding(kind, message, provenance))

    def _table(self, operation, create=False):
        key = operation.table.lower()
        table = self.tables.get(key)
        if table is None:
            table = TableState(operation.table, operation.schema, origin_unknown=not create)
            self.tables[key] = table
        return table

    # -- operacoes --
    def _create_table(self, operation, provenance):
        key = operation.table.lower()
        existing = self.tables.get(key)
        if existing is not None:
            if operation.if_not_exists:
                return self._record(operation, provenance, OUTCOME_IGNORED, "tabela ja existia (IF NOT EXISTS)")
            self._finding("tabela_repetida", f"CREATE TABLE {operation.table}: a tabela ja existe no modelo.", provenance)
            return self._record(operation, provenance, OUTCOME_CONFLICT, "tabela ja existia")
        table = self._table(operation, create=True)
        entry = self._record(operation, provenance, OUTCOME_APPLIED)
        table.created_by = entry
        table.options = dict(operation.options)
        for spec in operation.columns:
            column_entry = self._record(
                Operation(
                    kind=KIND_CREATE_TABLE,
                    status=STATUS_INTERPRETED,
                    table=operation.table,
                    schema=operation.schema,
                    column=spec,
                    statement=operation.statement,
                ),
                provenance,
                OUTCOME_APPLIED,
                "coluna do CREATE TABLE",
            )
            table.columns[spec.name.lower()] = ColumnState(spec, column_entry)
        return entry

    def _add_column(self, operation, provenance):
        table = self._table(operation)
        key = operation.column.name.lower()
        if key in table.columns:
            self._finding(
                "coluna_repetida",
                f"ADD COLUMN {operation.table}.{operation.column.name}: a coluna ja existe no modelo.",
                provenance,
            )
            return self._record(operation, provenance, OUTCOME_CONFLICT, "coluna ja existia")
        entry = self._record(operation, provenance, OUTCOME_APPLIED)
        table.columns[key] = ColumnState(operation.column, entry)
        return entry

    def _modify_column(self, operation, provenance):
        table = self._table(operation)
        key = operation.column.name.lower()
        current = table.columns.get(key)
        if current is None and table.fully_known:
            self._finding(
                "coluna_inexistente",
                f"MODIFY COLUMN {operation.table}.{operation.column.name}: a coluna nao existe no modelo.",
                provenance,
            )
            return self._record(operation, provenance, OUTCOME_CONFLICT, "coluna inexistente")
        entry = self._record(operation, provenance, OUTCOME_APPLIED)
        if current is not None:
            current.applied.superseded_by = provenance
        table.columns[key] = ColumnState(operation.column, entry, origin_unknown=current is None)
        return entry

    def _apply_unsupported(self, operation, provenance):
        outcome = OUTCOME_ERROR if operation.status == STATUS_ERROR else OUTCOME_UNSUPPORTED
        entry = self._record(operation, provenance, outcome, operation.reason)
        if not operation.table:
            self.unresolved.append(entry)
            return entry
        table = self._table(operation)
        if operation.kind == KIND_CREATE_TABLE:
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
