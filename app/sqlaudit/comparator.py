"""Compara o estado esperado (scripts) com a estrutura real do banco.

Estados (ver `classificacao` no plano da auditoria):
  COMPATIVEL     o encontrado bate com o esperado (ou e equivalente no MySQL)
  AUSENTE        esperado e inexistente, com origem certa e modelo confiavel
  DIVERGENTE     existe com definicao diferente
  SUBSTITUIDO    o comando foi sobrescrito por um script posterior
  NAO_SUPORTADO  o comando nao foi interpretado com seguranca
  INDETERMINADO  faltam dados para concluir (fork, tabela incompleta, tipo desconhecido)
  ERRO           falha de leitura, interpretacao ou consulta

COMPATIVEL significa "o banco esta compativel com o script", nunca "o script foi
executado": a mesma estrutura pode ter vindo de outro caminho.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.sqlaudit.expected_model import (
    OUTCOME_CONFLICT,
    OUTCOME_ERROR,
    OUTCOME_IGNORED,
    OUTCOME_UNSUPPORTED,
)
from app.sqlaudit.type_normalizer import normalize_type

COMPATIVEL = "COMPATIVEL"
AUSENTE = "AUSENTE"
DIVERGENTE = "DIVERGENTE"
SUBSTITUIDO = "SUBSTITUIDO"
NAO_SUPORTADO = "NAO_SUPORTADO"
INDETERMINADO = "INDETERMINADO"
ERRO = "ERRO"
# Do pior para o melhor: e o estado de um arquivo/versao com varios resultados.
PRECEDENCE = (ERRO, DIVERGENTE, AUSENTE, NAO_SUPORTADO, INDETERMINADO, SUBSTITUIDO, COMPATIVEL)

DISCLAIMER = (
    "COMPATIVEL indica que a estrutura do banco e compativel com os scripts; "
    "nao comprova que algum script foi executado."
)
_TRUE_WORDS = {"TRUE": "1", "FALSE": "0"}
_NOW_WORDS = ("CURRENT_TIMESTAMP", "NOW", "LOCALTIME", "LOCALTIMESTAMP")
_GENERATED = re.compile(r"\b(virtual|stored) generated\b", re.IGNORECASE)


class ComparisonError(Exception):
    """A comparacao nao faz sentido (ex.: schema de outro banco)."""


def worst(statuses):
    statuses = set(statuses)
    for status in PRECEDENCE:
        if status in statuses:
            return status
    return COMPATIVEL


@dataclass(frozen=True)
class Difference:
    attribute: str
    expected: str
    found: str


@dataclass
class ObjectResult:
    kind: str  # "tabela" ou "coluna"
    table: str
    column: str | None
    status: str
    differences: tuple = ()
    notes: tuple = ()
    provenance: object = None
    expected_absent: bool = False  # objeto removido por um script: o esperado e NAO existir


@dataclass
class OperationResult:
    applied: object  # AppliedOperation
    status: str
    detail: str = ""
    effect_missing: bool = False  # DROP/RENAME cujo efeito nao aparece no banco (objeto ainda existe)


@dataclass
class FileSummary:
    version: str
    script: str
    number: int | None
    status: str
    counts: dict = field(default_factory=dict)

    @property
    def partial(self):
        return COMPATIVEL in self.counts and (AUSENTE in self.counts or DIVERGENTE in self.counts)


@dataclass
class ComparisonResult:
    target: str
    database: str
    server_version: str
    objects: list = field(default_factory=list)
    operations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def object_counts(self):
        counts = {}
        for item in self.objects:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def operation_counts(self):
        counts = {}
        for item in self.operations:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def files(self):
        grouped = {}
        for item in self.operations:
            provenance = item.applied.provenance
            key = (provenance.version, provenance.script)
            summary = grouped.setdefault(
                key, FileSummary(provenance.version, provenance.script, provenance.number, COMPATIVEL)
            )
            summary.counts[item.status] = summary.counts.get(item.status, 0) + 1
        for summary in grouped.values():
            summary.status = worst(summary.counts)
        return sorted(grouped.values(), key=lambda s: (s.version, s.number if s.number is not None else 10**12, s.script))


# -- comparacao de atributos --

def _expected_default(raw):
    """(tipo, valor) do DEFAULT do script: ('null',None) | ('text',s) | ('number',Decimal) | ('now',None) | ('unknown',None)."""
    text = str(raw).strip()
    upper = text.upper()
    if upper == "NULL":
        return "null", None
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return "text", text[1:-1].replace("''", "'").replace("\\'", "'")
    if upper in _TRUE_WORDS:
        return "number", Decimal(_TRUE_WORDS[upper])
    if re.fullmatch(r"[+-]?\d+(\.\d+)?", text):
        return "number", Decimal(text)
    if re.sub(r"\(\d*\)$", "", upper) in _NOW_WORDS or upper.rstrip("()") in _NOW_WORDS:
        return "now", None
    return "unknown", None


def _default_difference(spec, db_column, type_text):
    """(Difference|None, inconclusive_note|None)."""
    found = db_column.default
    if not spec.has_default:
        legacy_timestamp = type_text.startswith(("timestamp", "datetime"))
        generated = "generated" in db_column.extra.lower()
        if found is not None and not legacy_timestamp and not generated and not spec.auto_increment:
            return Difference("default", "(sem default)", found), None
        return None, None
    kind, value = _expected_default(spec.default)
    if kind == "unknown":
        return None, f"default '{spec.default}' nao comparavel"
    if kind == "null":
        return (None, None) if found is None else (Difference("default", "NULL", found), None)
    if found is None:
        return Difference("default", str(spec.default), "NULL"), None
    if kind == "now":
        same = found.upper().startswith(("CURRENT_TIMESTAMP", "NOW", "LOCALTIME"))
        return (None, None) if same else (Difference("default", "CURRENT_TIMESTAMP", found), None)
    if kind == "number":
        try:
            same = Decimal(found) == value
        except InvalidOperation:
            same = False
        return (None, None) if same else (Difference("default", str(value), found), None)
    return (None, None) if found == value else (Difference("default", value, found), None)


def compare_column(spec, db_column, table_state):
    """(status, differences, notes) so pelo conteudo, antes de considerar a origem."""
    differences, notes = [], []
    expected_type = normalize_type(spec.data_type)
    found_type = normalize_type(db_column.column_type)
    if expected_type.recognized and found_type.recognized:
        if expected_type.text != found_type.text:
            differences.append(Difference("tipo", expected_type.text, found_type.text))
    elif expected_type.text != found_type.text:
        notes.append(f"tipo nao reconhecido para comparar ({spec.data_type} x {db_column.column_type})")

    if spec.nullable is False and db_column.nullable:
        differences.append(Difference("nulabilidade", "NOT NULL", "NULL"))
    elif spec.nullable is True and not db_column.nullable:
        differences.append(Difference("nulabilidade", "NULL", "NOT NULL"))
    elif spec.nullable is None and spec.name.lower() in table_state.implicit_not_null:
        if db_column.nullable:  # a chave primaria torna a coluna NOT NULL
            differences.append(Difference("nulabilidade", "NOT NULL (chave primaria)", "NULL"))
    elif spec.nullable is None and not db_column.nullable:
        # NOT NULL sem o script declarar: pode vir de TIMESTAMP legado ou de restricao nao comparada
        if not (table_state.unchecked_elements or expected_type.text.startswith("timestamp")):
            differences.append(Difference("nulabilidade", "NULL (nao declarado)", "NOT NULL"))
        else:
            notes.append("NOT NULL no banco sem declaracao no script (TIMESTAMP ou restricao nao comparada): nao conclusivo")

    difference, note = _default_difference(spec, db_column, expected_type.text)
    if difference:
        differences.append(difference)
    if note:
        notes.append(note)

    found_auto = "auto_increment" in db_column.extra.lower()
    if spec.auto_increment != found_auto:
        differences.append(Difference("auto_increment", str(spec.auto_increment), str(found_auto)))
    found_on_update = "on update" in db_column.extra.lower()
    if (spec.on_update is not None) != found_on_update:
        differences.append(Difference("on_update", str(spec.on_update is not None), str(found_on_update)))
    if _GENERATED.search(db_column.extra):
        differences.append(Difference("coluna_gerada", "nao", db_column.extra))
    if spec.charset is not None and (db_column.charset or "").lower() != spec.charset:
        differences.append(Difference("charset", spec.charset, db_column.charset or "-"))
    if spec.collation is not None and (db_column.collation or "").lower() != spec.collation:
        differences.append(Difference("collation", spec.collation, db_column.collation or "-"))
    expected_comment = spec.comment or ""
    if expected_comment != db_column.comment:
        differences.append(Difference("comentario", expected_comment or "(sem comentario)", db_column.comment or "(sem comentario)"))

    if differences:
        return DIVERGENTE, tuple(differences), tuple(notes)
    return (INDETERMINADO if notes else COMPATIVEL), (), tuple(notes)


def _table_options_differences(state, db_table):
    differences = []
    options = state.options
    if "engine" in options and (db_table.engine or "").lower() != options["engine"]:
        differences.append(Difference("engine", options["engine"], db_table.engine or "-"))
    if "collation" in options and (db_table.collation or "").lower() != options["collation"]:
        differences.append(Difference("collation_tabela", options["collation"], db_table.collation or "-"))
    elif "charset" in options and "collation" not in options:
        if not (db_table.collation or "").lower().startswith(options["charset"] + "_"):
            differences.append(Difference("charset_tabela", options["charset"], db_table.collation or "-"))
    return differences


def _uncertain(status, provenance, incomplete, notes):
    """Rebaixa DIVERGENTE/AUSENTE para INDETERMINADO quando a origem nao e confiavel."""
    notes = list(notes)
    if status in (DIVERGENTE, AUSENTE):
        if provenance is not None and not provenance.certain:
            notes.append("origem incerta: ponto de fork nao informado ou script sem ordem")
            status = INDETERMINADO
        elif incomplete:
            notes.append("a tabela teve acao que o modelo nao seguiu (veja Avisos): o esperado pode ter mudado")
            status = INDETERMINADO
    return status, tuple(notes)


# -- indices, chave primaria e chaves estrangeiras --

def _lower(names):
    return tuple(name.lower() for name in names)


def _rule(value):
    value = (value or "").upper()
    return "RESTRICT" if value in ("", "NO ACTION", "RESTRICT") else value  # equivalentes no InnoDB


def compare_index(spec, db_index, check_unique=True):
    differences = []
    if _lower(spec.columns) != _lower(db_index.columns):
        differences.append(Difference("colunas", ", ".join(spec.columns), ", ".join(db_index.columns)))
    if check_unique and spec.unique != db_index.unique:
        differences.append(Difference("unico", str(spec.unique), str(db_index.unique)))
    found_kind = {"FULLTEXT": "fulltext", "SPATIAL": "spatial"}.get(db_index.index_type, "")
    if check_unique and spec.kind != found_kind:
        differences.append(Difference("tipo_indice", spec.kind or "normal", found_kind or "normal"))
    if len(spec.prefixes) == len(db_index.prefixes) == len(spec.columns) and _lower(spec.columns) == _lower(db_index.columns):
        for column, expected, found in zip(spec.columns, spec.prefixes, db_index.prefixes):
            if expected != found:
                differences.append(Difference(f"prefixo de {column}", str(expected or "inteira"), str(found or "inteira")))
    return differences


def compare_foreign_key(spec, db_fk):
    differences = []
    if _lower(spec.columns) != _lower(db_fk.columns):
        differences.append(Difference("colunas", ", ".join(spec.columns), ", ".join(db_fk.columns)))
    if spec.ref_table.lower() != db_fk.ref_table.lower() or _lower(spec.ref_columns) != _lower(db_fk.ref_columns):
        differences.append(
            Difference("referencia", f"{spec.ref_table}({', '.join(spec.ref_columns)})", f"{db_fk.ref_table}({', '.join(db_fk.ref_columns)})")
        )
    if _rule(spec.on_delete) != _rule(db_fk.delete_rule):
        differences.append(Difference("on_delete", _rule(spec.on_delete), _rule(db_fk.delete_rule)))
    if _rule(spec.on_update) != _rule(db_fk.update_rule):
        differences.append(Difference("on_update", _rule(spec.on_update), _rule(db_fk.update_rule)))
    return differences


def _find_index(state, db_table):
    """Indice do banco que corresponde ao esperado (por nome; se o nome era automatico, por colunas)."""
    spec = state.spec
    found = db_table.indexes.get(spec.name.lower())
    if found is None and state.auto_named:
        found = next(
            (index for key, index in db_table.indexes.items() if key != "primary" and _lower(index.columns) == _lower(spec.columns) and index.unique == spec.unique),
            None,
        )
    return found


def _find_foreign_key(state, db_table):
    spec = state.spec
    found = db_table.foreign_keys.get(spec.name.lower())
    if found is None and state.auto_named:
        found = next(
            (
                fk for fk in db_table.foreign_keys.values()
                if _lower(fk.columns) == _lower(spec.columns) and fk.ref_table.lower() == spec.ref_table.lower() and _lower(fk.ref_columns) == _lower(spec.ref_columns)
            ),
            None,
        )
    return found


# -- comparacao principal --

def compare(model, schema, expected_database=None):
    """Compara `ExpectedModel` com `DbSchema`. Nunca altera nenhum dos dois."""
    if expected_database and schema.database.lower() != expected_database.lower():
        raise ComparisonError(f"O schema lido e '{schema.database}', mas a auditoria era do banco '{expected_database}'.")
    result = ComparisonResult(model.target, schema.database, schema.server_version)
    if model.unresolved:
        result.warnings.append(
            f"{len(model.unresolved)} comando(s) inteiro(s) nao foram interpretados e podem alterar o resultado."
        )
    if not schema.tables:
        result.warnings.append(
            "Nenhuma tabela visivel no banco: banco vazio ou usuario sem permissao de leitura nas tabelas. "
            "Nada foi comparado."
        )

    for key, state in model.tables.items():
        _compare_table(result, state, schema.tables.get(key), schema)
    for key, dropped in model.absent_tables.items():
        _compare_dropped_table(result, dropped, schema.tables.get(key), schema)
    _operation_results(result, model)
    return result


def _compare_dropped_table(result, dropped, db_table, schema):
    if not schema.tables:
        status, differences, notes = ERRO, (), ["sem tabelas visiveis no banco"]
    elif db_table is not None:
        status, differences, notes = DIVERGENTE, (Difference("existe", "nao (removida)", "sim"),), []
    else:
        status, differences, notes = COMPATIVEL, (), []
    notes = [f"esperado ausente: removida por {dropped.provenance.script}, linha {dropped.provenance.line}", *notes]
    status, notes = _uncertain(status, dropped.provenance, False, notes)
    result.objects.append(ObjectResult("tabela", dropped.name, None, status, differences, tuple(notes), dropped.provenance, True))


def _table_provenance(state):
    if state.created_by is not None:
        return state.created_by.provenance
    return next((column.provenance for column in state.columns.values()), None)


def _emit(result, kind, table, name, status, differences, notes, provenance, incomplete, expected_absent=False):
    status, notes = _uncertain(status, provenance, incomplete, notes)
    result.objects.append(
        ObjectResult(kind, table, name, status, tuple(differences), tuple(notes), provenance, expected_absent)
    )


def _compare_table(result, state, db_table, schema):
    provenance = _table_provenance(state)
    notes = []
    if state.unchecked_elements:
        notes.append(f"{len(state.unchecked_elements)} restricao(oes)/opcao(oes) (CHECK, ROW_FORMAT...) nao comparada(s)")
    if state.incomplete:
        notes.append(f"{len(state.incomplete)} acao(oes) que o modelo nao seguiu")
    if state.origin_unknown:
        notes.append("nenhum script cria esta tabela: ela veio do banco-base")
    incomplete = bool(state.incomplete)

    if not schema.tables:
        status, differences = ERRO, ()
        notes.append("sem tabelas visiveis no banco")
    elif db_table is None:
        status, notes_t = _uncertain(AUSENTE, provenance, incomplete, notes)
        notes, differences = list(notes_t), ()
    else:
        if schema.lower_case_table_names == 0 and db_table.name != state.name:
            notes.append(f"nome difere em maiusculas/minusculas ({state.name} x {db_table.name})")
        differences = tuple(_table_options_differences(state, db_table))
        status = DIVERGENTE if differences else COMPATIVEL
        status, notes_t = _uncertain(status, provenance, incomplete, notes)
        notes = list(notes_t)
    result.objects.append(ObjectResult("tabela", state.name, None, status, differences, tuple(notes), provenance))
    table_status = status

    def missing(what):
        """Estado de um objeto esperado quando a tabela nao existe ou nao e legivel."""
        if table_status == ERRO:
            return ERRO, ["sem tabelas visiveis no banco"]
        return AUSENTE, [f"tabela ausente no banco ({what})"] if what else ["tabela ausente no banco"]

    # colunas
    for column_key, column in state.columns.items():
        column_notes = []
        if table_status == ERRO or db_table is None:
            column_status, column_notes = missing("")
            differences = ()
        else:
            db_column = db_table.columns.get(column_key)
            if db_column is None:
                column_status, differences = AUSENTE, ()
            else:
                column_status, differences, column_notes = compare_column(column.spec, db_column, state)
        if column.origin_unknown:
            column_notes = [*column_notes, "coluna so conhecida pelo MODIFY/CHANGE: veio do banco-base"]
        _emit(result, "coluna", state.name, column.spec.name, column_status, differences, column_notes, column.provenance, incomplete)

    # indices
    matched = set()
    for index_key, index in state.indexes.items():
        found = None
        if table_status == ERRO or db_table is None:
            status, notes_i = missing("")
            differences = ()
        else:
            found = _find_index(index, db_table)
            if found is None:
                status, differences, notes_i = AUSENTE, (), []
            else:
                matched.add(found.name.lower())
                differences = compare_index(index.spec, found)
                status, notes_i = (DIVERGENTE if differences else COMPATIVEL), []
        if index.auto_named:
            notes_i = [*notes_i, "nome automatico do MySQL: procurado pelas colunas"]
        _emit(result, "indice", state.name, index.spec.name, status, differences, notes_i, index.provenance, incomplete)

    # chave primaria
    if state.primary_key is not None:
        pk = state.primary_key
        if table_status == ERRO or db_table is None:
            status, notes_p = missing("")
            differences = ()
        else:
            found = db_table.indexes.get("primary")
            matched.add("primary")
            if found is None:
                status, differences, notes_p = AUSENTE, (), []
            else:
                differences = compare_index(pk.spec, found, check_unique=False)
                status, notes_p = (DIVERGENTE if differences else COMPATIVEL), []
        _emit(result, "chave_primaria", state.name, "PRIMARY", status, differences, notes_p, pk.provenance, incomplete)

    # chaves estrangeiras
    matched_fks = set()
    for fk_key, fk in state.foreign_keys.items():
        if table_status == ERRO or db_table is None:
            status, notes_f = missing("")
            differences = ()
        else:
            found = _find_foreign_key(fk, db_table)
            if found is None:
                status, differences, notes_f = AUSENTE, (), []
            else:
                matched_fks.add(found.name.lower())
                differences = compare_foreign_key(fk.spec, found)
                status, notes_f = (DIVERGENTE if differences else COMPATIVEL), []
        if fk.auto_named:
            notes_f = [*notes_f, "nome automatico do MySQL: procurada pelas colunas e referencia"]
        _emit(result, "chave_estrangeira", state.name, fk.spec.name, status, differences, notes_f, fk.provenance, incomplete)

    # removidos: o esperado e nao existir
    def dropped(kind, name, state_item, present):
        if table_status == ERRO:
            status, differences, extra = ERRO, (), ["sem tabelas visiveis no banco"]
        elif db_table is None:
            status, differences, extra = INDETERMINADO, (), ["tabela ausente no banco"]
        elif present:
            status, differences, extra = DIVERGENTE, (Difference("existe", "nao (removido)", "sim"),), []
        else:
            status, differences, extra = COMPATIVEL, (), []
        notes_d = [f"esperado ausente: removido por {state_item.provenance.script}, linha {state_item.provenance.line}", *extra]
        _emit(result, kind, state.name, name, status, differences, notes_d, state_item.provenance, incomplete, expected_absent=True)

    for key, item in state.dropped_columns.items():
        dropped("coluna", item.name, item, db_table is not None and key in db_table.columns)
    for key, item in state.dropped_indexes.items():
        dropped("indice", item.name, item, db_table is not None and key in db_table.indexes)
    if state.dropped_primary_key is not None:
        dropped("chave_primaria", "PRIMARY", state.dropped_primary_key, db_table is not None and "primary" in db_table.indexes)
    for key, item in state.dropped_foreign_keys.items():
        dropped("chave_estrangeira", item.name, item, db_table is not None and key in db_table.foreign_keys)

    # so no banco: em tabela conhecida por inteiro, nenhum script explica
    if db_table is not None and state.fully_known:
        for column_key, db_column in db_table.columns.items():
            if column_key not in state.columns and column_key not in state.dropped_columns:
                result.objects.append(ObjectResult("coluna", state.name, db_column.name, INDETERMINADO, (), ("existe no banco e nenhum script a cria",), None))
        fk_columns = [_lower(fk.columns) for fk in db_table.foreign_keys.values()]
        for key, index in db_table.indexes.items():
            if key in matched or key in state.dropped_indexes or (key == "primary" and state.dropped_primary_key is not None):
                continue
            if key in db_table.foreign_keys or any(_lower(index.columns) == columns[: len(index.columns)] for columns in fk_columns):
                continue  # indice criado automaticamente para uma chave estrangeira
            kind = "chave_primaria" if key == "primary" else "indice"
            result.objects.append(ObjectResult(kind, state.name, index.name, INDETERMINADO, (), ("existe no banco e nenhum script o cria",), None))
        for key, fk in db_table.foreign_keys.items():
            if key not in matched_fks and key not in state.dropped_foreign_keys:
                result.objects.append(ObjectResult("chave_estrangeira", state.name, fk.name, INDETERMINADO, (), ("existe no banco e nenhum script a cria",), None))


def _operation_results(result, model):
    by_key = {(item.kind, item.table.lower(), (item.column or "").lower()): item for item in result.objects}
    for applied in model.applied:
        if applied.outcome == OUTCOME_UNSUPPORTED:
            result.operations.append(OperationResult(applied, NAO_SUPORTADO, applied.note))
        elif applied.outcome == OUTCOME_ERROR:
            result.operations.append(OperationResult(applied, ERRO, applied.note))
        elif applied.outcome in (OUTCOME_IGNORED, OUTCOME_CONFLICT):
            result.operations.append(OperationResult(applied, INDETERMINADO, applied.note))
        elif applied.superseded_by is not None:
            later = applied.superseded_by
            result.operations.append(
                OperationResult(applied, SUBSTITUIDO, f"substituido por {later.script} (linha {later.line})")
            )
        else:
            target = by_key.get(applied.target) if applied.target else None
            if target is None:
                result.operations.append(OperationResult(applied, INDETERMINADO, "objeto sem resultado"))
            else:
                result.operations.append(
                    OperationResult(
                        applied, target.status, "; ".join(target.notes),
                        effect_missing=target.expected_absent and target.status == DIVERGENTE,
                    )
                )
