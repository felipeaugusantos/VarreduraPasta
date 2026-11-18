"""Confere, script por script, se o banco modelo ja tem o que a pasta de scripts cria.

Pergunta que responde, para CADA script `.sql` da pasta: "tudo o que este script cria
(tabela, campo, indice, chave) ja existe no banco modelo?". O resultado e `OK` ou
`Falta rodar: campo T.C, tabela X...`. Diferente da Auditoria de Estrutura (que
reproduz a ordem dos scripts de uma versao), aqui cada script e conferido sozinho, entao
nao precisa de ordem, base nem fork.

So leitura: o esquema vem de `db_reader` (sessao READ ONLY) e nada dos scripts e executado.
Regras para nao acusar falta que nao existe:
- objeto que OUTRO script (ou o proprio) remove, renomeia ou troca nao e cobrado;
- remocao (`DROP`) so e cobrada se nenhum script recria o objeto;
- diferenca de tipo e so uma observacao: o campo existe, e outro script pode te-lo alterado;
- o que o sistema nao entende (DML, procedures, SQL dinamico) nao entra na conta.
"""

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from app.sqlaudit import db_reader, sql_parse
from app.sqlaudit.file_scanner import SqlFileError, read_sql_text
from app.sqlaudit.type_normalizer import normalize_type

STATUS_OK = "OK"
STATUS_MISSING = "FALTA"
STATUS_NO_STRUCTURE = "SEM_ESTRUTURA"
STATUS_ERROR = "ERRO"
STATUS_LABELS = {
    STATUS_OK: "OK",
    STATUS_MISSING: "Falta rodar",
    STATUS_NO_STRUCTURE: "Sem campos para conferir",
    STATUS_ERROR: "Nao foi possivel ler",
}

KIND_TABLE = "tabela"
KIND_COLUMN = "campo"
KIND_INDEX = "indice"
KIND_FOREIGN = "chave estrangeira"
KIND_PRIMARY = "chave primaria"
KIND_REMOVE_PREFIX = "remover "
SUMMARY_ITEMS = 5


@dataclass(frozen=True)
class Missing:
    kind: str  # tabela | campo | indice | chave estrangeira | chave primaria | remover <tipo>
    table: str
    name: str = ""
    detail: str = ""

    @property
    def text(self):
        target = f"{self.table}.{self.name}" if self.name else self.table
        return f"{self.kind} {target}" + (f" ({self.detail})" if self.detail else "")


@dataclass
class ScriptCheck:
    script: str
    status: str = STATUS_NO_STRUCTURE
    checked: int = 0  # objetos conferidos
    present: int = 0  # ja existem no banco
    missing: list = field(default_factory=list)  # Missing
    notes: list = field(default_factory=list)
    unverifiable: int = 0  # comandos que o sistema nao consegue conferir
    error: str = ""

    @property
    def label(self):
        return STATUS_LABELS[self.status]

    @property
    def detail(self):
        """Coluna de detalhe da tela: so o que importa, sem repetir o resultado."""
        if self.status == STATUS_ERROR:
            return self.error
        if self.status == STATUS_NO_STRUCTURE:
            return "So dados ou comandos que o sistema nao consegue conferir"
        if self.status == STATUS_OK:
            return f"Tudo ja esta no banco ({self.present} conferido(s))"
        shown = [item.text for item in self.missing[:SUMMARY_ITEMS]]
        more = len(self.missing) - len(shown)
        return ", ".join(shown) + (f" (+{more})" if more > 0 else "")

    @property
    def summary(self):
        """Texto curto para a tabela: `OK` ou `Falta rodar: campo T.C, tabela X (+2)`."""
        if self.status == STATUS_ERROR:
            return self.error
        if self.status == STATUS_NO_STRUCTURE:
            return self.label
        if self.status == STATUS_OK:
            return "OK"
        shown = [item.text for item in self.missing[:SUMMARY_ITEMS]]
        more = len(self.missing) - len(shown)
        return "Falta rodar: " + ", ".join(shown) + (f" (+{more})" if more > 0 else "")


@dataclass
class ModelCheckRun:
    database: str
    server_version: str = ""
    tables: int = 0
    results: list = field(default_factory=list)  # ScriptCheck
    cancelled: bool = False

    def counts(self):
        counts = {}
        for item in self.results:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts


class ModelCheckError(Exception):
    """Falha esperada (conexao, parser indisponivel...), com mensagem pronta para o operador."""


# -- coleta global: o que algum script remove / recria / define --

def _key(table, name=""):
    return (str(table).lower(), str(name or "").lower())


@dataclass
class _Global:
    dropped_tables: set = field(default_factory=set)
    dropped_columns: set = field(default_factory=set)
    dropped_indexes: set = field(default_factory=set)
    dropped_foreign: set = field(default_factory=set)
    dropped_primary: set = field(default_factory=set)
    created_tables: set = field(default_factory=set)
    created_columns: set = field(default_factory=set)
    created_indexes: set = field(default_factory=set)
    created_foreign: set = field(default_factory=set)
    column_types: dict = field(default_factory=dict)  # (tabela, coluna) -> {tipos normalizados}


def _remember_type(info, table, spec):
    normalized = normalize_type(spec.data_type)
    if normalized.recognized:
        info.column_types.setdefault(_key(table, spec.name), set()).add(normalized.text)


def _collect(parsed):
    """`parsed`: lista de (script, [Operation]). Junta o que qualquer script remove, cria ou define."""
    info = _Global()
    for _script, operations in parsed:
        for op in operations:
            if not op.interpreted:
                continue
            kind, table = op.kind, op.table
            if kind == sql_parse.KIND_CREATE_TABLE:
                info.created_tables.add(_key(table))
                for spec in op.columns:
                    info.created_columns.add(_key(table, spec.name))
                    _remember_type(info, table, spec)
                for index in op.indexes:
                    if index.name:
                        info.created_indexes.add(_key(table, index.name))
                for foreign in op.foreign_keys:
                    if foreign.name:
                        info.created_foreign.add(_key(table, foreign.name))
            elif kind in (sql_parse.KIND_ADD_COLUMN, sql_parse.KIND_MODIFY_COLUMN) and op.column is not None:
                info.created_columns.add(_key(table, op.column.name))
                _remember_type(info, table, op.column)
            elif kind == sql_parse.KIND_CHANGE_COLUMN and op.column is not None:
                info.dropped_columns.add(_key(table, op.name))
                info.created_columns.add(_key(table, op.column.name))
                _remember_type(info, table, op.column)
            elif kind == sql_parse.KIND_DROP_COLUMN:
                info.dropped_columns.add(_key(table, op.name))
            elif kind == sql_parse.KIND_RENAME_COLUMN:
                info.dropped_columns.add(_key(table, op.name))
                info.created_columns.add(_key(table, op.new_name))
            elif kind == sql_parse.KIND_DROP_TABLE:
                info.dropped_tables.add(_key(table))
            elif kind == sql_parse.KIND_RENAME_TABLE:
                info.dropped_tables.add(_key(table))
                info.created_tables.add(_key(op.new_name))
            elif kind == sql_parse.KIND_ADD_INDEX and op.index is not None and op.index.name:
                info.created_indexes.add(_key(table, op.index.name))
            elif kind == sql_parse.KIND_DROP_INDEX:
                info.dropped_indexes.add(_key(table, op.name))
            elif kind == sql_parse.KIND_ADD_FOREIGN_KEY and op.foreign_key is not None and op.foreign_key.name:
                info.created_foreign.add(_key(table, op.foreign_key.name))
            elif kind == sql_parse.KIND_DROP_FOREIGN_KEY:
                info.dropped_foreign.add(_key(table, op.name))
            elif kind == sql_parse.KIND_DROP_PRIMARY_KEY:
                info.dropped_primary.add(_key(table))
    return info


# -- conferencia de um script --

def _lower_tuple(values):
    return tuple(str(v).lower() for v in values)


def _find_index(db_table, spec):
    if spec.name:
        return db_table.indexes.get(spec.name.lower())
    wanted = _lower_tuple(spec.columns)
    for index in db_table.indexes.values():
        if index.name.lower() != "primary" and _lower_tuple(index.columns) == wanted and index.unique == bool(spec.unique):
            return index
    return None


def _find_foreign(db_table, spec):
    if spec.name:
        return db_table.foreign_keys.get(spec.name.lower())
    wanted = _lower_tuple(spec.columns)
    for foreign in db_table.foreign_keys.values():
        if _lower_tuple(foreign.columns) == wanted and foreign.ref_table.lower() == spec.ref_table.lower():
            return foreign
    return None


class _Checker:
    def __init__(self, script, schema, info, database):
        self.result = ScriptCheck(script)
        self.schema = schema
        self.info = info
        self.database = database.lower()
        self._missing_tables = set()
        self._seen = set()

    # registro
    def _miss(self, kind, table, name="", detail=""):
        item = Missing(kind, table, name, detail)
        marker = (kind, table.lower(), name.lower())
        if marker not in self._seen:
            self._seen.add(marker)
            self.result.missing.append(item)

    def _ok(self):
        self.result.checked += 1
        self.result.present += 1

    def _checked_missing(self, kind, table, name="", detail=""):
        self.result.checked += 1
        self._miss(kind, table, name, detail)

    def _superseded(self, kind, table, name=""):
        """Objeto que outro script remove/renomeia: a ausencia e esperada e o script nao esta pendente."""
        marker = ("superseded", kind, table.lower(), name.lower())
        if marker in self._seen:
            return
        self._seen.add(marker)
        self._ok()
        target = f"{table}.{name}" if name else table
        self.result.notes.append(f"{kind} {target}: removido por outro script")

    def _outside_database(self, op):
        return bool(op.schema) and op.schema.lower() != self.database

    def _table(self, op):
        """DbTable da operacao, ou None (e registra a tabela como faltando, uma vez)."""
        key = op.table.lower()
        db_table = self.schema.tables.get(key)
        if db_table is not None:
            return db_table
        if key in self.info.dropped_tables:
            self._superseded(KIND_TABLE, op.table)
            return None  # removida por algum script: a ausencia e esperada
        if key not in self._missing_tables:
            self._missing_tables.add(key)
            self._checked_missing(KIND_TABLE, op.table)
        return None

    # conferencias por tipo de comando
    def _column(self, op, spec, db_table):
        key = _key(op.table, spec.name)
        db_column = db_table.columns.get(spec.name.lower())
        if db_column is None:
            if key in self.info.dropped_columns:
                self._superseded(KIND_COLUMN, op.table, spec.name)
                return
            self._checked_missing(KIND_COLUMN, op.table, spec.name)
            return
        self._ok()
        expected, found = normalize_type(spec.data_type), normalize_type(db_column.column_type)
        if expected.recognized and found.recognized and expected.text != found.text:
            if len(self.info.column_types.get(key, ())) <= 1:  # senao outro script o altera: nao e do script
                self.result.notes.append(f"{op.table}.{spec.name}: tipo no script {expected.text}, no banco {found.text}")

    def _create_table(self, op):
        db_table = self._table(op)
        if db_table is None:
            return
        self._ok()
        for spec in op.columns:
            self._column(op, spec, db_table)
        for spec in op.indexes:
            self._index(op, spec, db_table)
        for spec in op.foreign_keys:
            self._foreign(op, spec, db_table)
        if op.primary_key:
            self._primary(op, db_table)

    def _index(self, op, spec, db_table):
        found = _find_index(db_table, spec)
        if found is None and spec.name and _key(op.table, spec.name) in self.info.dropped_indexes:
            self._superseded(KIND_INDEX, op.table, spec.name)
            return
        if found is None:
            self._checked_missing(KIND_INDEX, op.table, spec.name or ",".join(spec.columns))
        else:
            self._ok()

    def _foreign(self, op, spec, db_table):
        found = _find_foreign(db_table, spec)
        if found is None and spec.name and _key(op.table, spec.name) in self.info.dropped_foreign:
            self._superseded(KIND_FOREIGN, op.table, spec.name)
            return
        if found is None:
            self._checked_missing(KIND_FOREIGN, op.table, spec.name or ",".join(spec.columns))
        else:
            self._ok()

    def _primary(self, op, db_table):
        if "primary" not in db_table.indexes and _key(op.table) in self.info.dropped_primary:
            self._superseded(KIND_PRIMARY, op.table)
            return
        if "primary" not in db_table.indexes:
            self._checked_missing(KIND_PRIMARY, op.table)
        else:
            self._ok()

    def _expect_absent(self, kind, table, name, exists, recreated):
        self.result.checked += 1
        if exists and not recreated:
            self._miss(KIND_REMOVE_PREFIX + kind, table, name)
        else:
            self.result.present += 1

    def apply(self, op):
        if not op.interpreted:
            self.result.unverifiable += 1
            return
        kind = op.kind
        if self._outside_database(op):
            self.result.unverifiable += 1
            return
        if kind == sql_parse.KIND_CREATE_TABLE:
            self._create_table(op)
        elif kind in (sql_parse.KIND_ADD_COLUMN, sql_parse.KIND_MODIFY_COLUMN, sql_parse.KIND_CHANGE_COLUMN):
            db_table = self._table(op)
            if db_table is not None and op.column is not None:
                self._column(op, op.column, db_table)
        elif kind == sql_parse.KIND_RENAME_COLUMN:
            db_table = self._table(op)
            if db_table is not None:
                self._column(op, SimpleNamespace(name=op.new_name, data_type=""), db_table)
        elif kind == sql_parse.KIND_ADD_INDEX and op.index is not None:
            db_table = self._table(op)
            if db_table is not None:
                self._index(op, op.index, db_table)
        elif kind == sql_parse.KIND_ADD_FOREIGN_KEY and op.foreign_key is not None:
            db_table = self._table(op)
            if db_table is not None:
                self._foreign(op, op.foreign_key, db_table)
        elif kind == sql_parse.KIND_ADD_PRIMARY_KEY:
            db_table = self._table(op)
            if db_table is not None:
                self._primary(op, db_table)
        elif kind == sql_parse.KIND_DROP_COLUMN:
            db_table = self.schema.tables.get(op.table.lower())
            if db_table is not None:
                key = _key(op.table, op.name)
                self._expect_absent(KIND_COLUMN, op.table, op.name, op.name.lower() in db_table.columns, key in self.info.created_columns)
        elif kind == sql_parse.KIND_DROP_INDEX:
            db_table = self.schema.tables.get(op.table.lower())
            if db_table is not None:
                key = _key(op.table, op.name)
                self._expect_absent(KIND_INDEX, op.table, op.name, op.name.lower() in db_table.indexes, key in self.info.created_indexes)
        elif kind == sql_parse.KIND_DROP_FOREIGN_KEY:
            db_table = self.schema.tables.get(op.table.lower())
            if db_table is not None:
                key = _key(op.table, op.name)
                self._expect_absent(KIND_FOREIGN, op.table, op.name, op.name.lower() in db_table.foreign_keys, key in self.info.created_foreign)
        elif kind == sql_parse.KIND_DROP_TABLE:
            self._expect_absent(KIND_TABLE, op.table, "", op.table.lower() in self.schema.tables, _key(op.table) in self.info.created_tables)
        else:  # alter_options, convert_charset, rename_table...: nao ha o que conferir com seguranca
            self.result.unverifiable += 1

    def finish(self):
        result = self.result
        if result.missing:
            result.status = STATUS_MISSING
        elif result.checked:
            result.status = STATUS_OK
        else:
            result.status = STATUS_NO_STRUCTURE
        return result


def check_operations(parsed, schema, database):
    """Confere `parsed` (lista de (script, [Operation])) contra `schema` (DbSchema). Lista de ScriptCheck."""
    info = _collect(parsed)
    results = []
    for script, operations in parsed:
        checker = _Checker(script, schema, info, database)
        for op in operations:
            checker.apply(op)
        results.append(checker.finish())
    return results


def sql_script_names(directory):
    """Nomes relativos (com subpastas) dos `.sql` da pasta, ordenados."""
    directory = Path(directory)
    names = []
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".sql":
            names.append(str(path.relative_to(directory)))
    return sorted(names, key=str.lower)


def parse_scripts(directory, names, cancel_event=None, progress=None):
    """Interpreta os scripts. Devolve (parsed, falhas): falhas = {nome: mensagem}."""
    if not sql_parse.is_available():
        raise ModelCheckError("A biblioteca sqlglot nao foi encontrada: nao da para interpretar os scripts.")
    parsed, failures = [], {}
    total = len(names)
    for index, name in enumerate(names, 1):
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            text, _encoding = read_sql_text(Path(directory) / name)
            parsed.append((name, sql_parse.parse_text(text)))
        except SqlFileError as error:
            failures[name] = str(error)
        if progress is not None:
            progress(index, total)
    return parsed, failures


def _scrub(text, password):
    text = str(text)
    return text.replace(password, "***") if password else text


def list_model_databases(host, port, username, password, connector=None, tls=db_reader.TLS_ENCRYPT, ca_file=""):
    """(ok, bancos, mensagem): conecta so ao servidor e lista os bancos que o usuario enxerga."""
    try:
        with db_reader.connect_readonly(
            host, port, "", username, password, tls=tls, ca_file=ca_file, connector=connector, require_database=False
        ) as connection:
            names = connection.list_databases()
    except db_reader.AuditDbError as error:
        return False, [], _scrub(error, password)
    if not names:
        return False, [], (
            "Conectado, mas nenhum banco visivel para este usuario. Confira se ele tem SELECT no banco modelo."
        )
    return True, names, f"Conectado: {len(names)} banco(s) visivel(is) para o usuario."


def run_model_check(
    directory, names, host, port, database, username, password,
    connector=None, cancel_event=None, progress=None, tls=db_reader.TLS_ENCRYPT, ca_file="", on_phase=None,
):
    """Le o esquema do banco modelo (so leitura) e confere os scripts. Levanta ModelCheckError.

    `progress(feitos, total)` conta os scripts interpretados; `on_phase(texto)` avisa as fases longas.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ModelCheckError(f"Pasta de scripts nao encontrada: {directory}")
    names = list(names) if names is not None else sql_script_names(directory)
    if not names:
        raise ModelCheckError("A pasta nao tem nenhum script .sql para conferir.")
    parsed, failures = parse_scripts(directory, names, cancel_event, progress)
    run = ModelCheckRun(database=database)
    if cancel_event is not None and cancel_event.is_set():
        run.cancelled = True
        return run
    if on_phase is not None:
        on_phase("reading_database")
    try:
        with db_reader.connect_readonly(
            host, port, database, username, password, tls=tls, ca_file=ca_file, connector=connector
        ) as connection:
            schema = connection.read_schema(database, cancel_event)
    except db_reader.AuditDbError as error:
        raise ModelCheckError(str(error)) from error
    if cancel_event is not None and cancel_event.is_set():
        run.cancelled = True
        return run
    if not schema.tables:
        raise ModelCheckError(
            "Nenhuma tabela visivel no banco modelo: banco vazio ou usuario sem permissao de leitura nas tabelas."
        )
    run.server_version = schema.server_version
    run.tables = len(schema.tables)
    if on_phase is not None:
        on_phase("comparing")
    by_name = {item.script: item for item in check_operations(parsed, schema, database)}
    for name in names:
        if name in failures:
            run.results.append(ScriptCheck(name, STATUS_ERROR, error=failures[name]))
        elif name in by_name:
            run.results.append(by_name[name])
    return run


# -- saida: CSV e linha de Auditoria --

CSV_COLUMNS = ("script", "resultado", "faltando", "observacoes", "conferidos", "ja_no_banco", "nao_conferidos")


def _safe_cell(value):
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def build_csv_rows(run):
    rows = []
    for item in run.results:
        rows.append(
            [
                _safe_cell(value)
                for value in (
                    item.script,
                    item.label,
                    "; ".join(missing.text for missing in item.missing) or (item.error if item.status == STATUS_ERROR else ""),
                    "; ".join(item.notes),
                    item.checked,
                    item.present,
                    item.unverifiable,
                )
            ]
        )
    return rows


def write_results_csv(run, path):
    """CSV (`;`, UTF-8 com BOM) gravado de forma atomica. Levanta OSError/ValueError."""
    import csv
    import os

    path = Path(path)
    if path.suffix.lower() != ".csv":
        raise ValueError("Use a extensao .csv.")
    temp = path.with_name(f".{path.name}.tmp")
    try:
        with temp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(CSV_COLUMNS)
            writer.writerows(build_csv_rows(run))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return len(run.results)


def summary_text(run):
    counts = run.counts()
    parts = [f"{len(run.results)} script(s)"]
    for status in (STATUS_OK, STATUS_MISSING, STATUS_NO_STRUCTURE, STATUS_ERROR):
        if counts.get(status):
            parts.append(f"{counts[status]} {STATUS_LABELS[status].lower()}")
    return ": ".join([parts[0], ", ".join(parts[1:])]) if len(parts) > 1 else parts[0]


def audit_line(user, directory, run, result, reason=None):
    """Linha para o `write_log` (Auditoria): sem credenciais e sem SQL."""
    clean = lambda value: str(value).replace("|", "/").replace("\n", " ")
    return (
        f"Banco modelo comparacao {result} | usuario={clean(user)} | projeto= | tipo= | origem={clean(directory)} | "
        f"destino={clean(run.database)} | bat= | fileversion= | productversion= | autcom_mb= | "
        f"motivo={clean(reason if reason is not None else summary_text(run))}"
    )
