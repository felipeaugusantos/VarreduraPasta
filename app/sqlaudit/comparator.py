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


@dataclass
class OperationResult:
    applied: object  # AppliedOperation
    status: str
    detail: str = ""


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
    elif spec.nullable is None and not db_column.nullable:
        # NOT NULL sem o script declarar: pode vir de PRIMARY KEY, TIMESTAMP legado etc.
        if not (spec.primary_key or table_state.unchecked_elements or expected_type.text.startswith("timestamp")):
            differences.append(Difference("nulabilidade", "NULL (nao declarado)", "NOT NULL"))
        else:
            notes.append("NOT NULL no banco sem declaracao no script (chave/PK ou TIMESTAMP): nao conclusivo")

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
            notes.append("a tabela teve acao que o modelo nao seguiu (DROP/CHANGE/renomear): o esperado pode ter mudado")
            status = INDETERMINADO
    return status, tuple(notes)


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
    _operation_results(result, model)
    return result


def _table_provenance(state):
    if state.created_by is not None:
        return state.created_by.provenance
    return next((column.provenance for column in state.columns.values()), None)


def _compare_table(result, state, db_table, schema):
    provenance = _table_provenance(state)
    notes = []
    if state.unchecked_elements:
        notes.append(f"{len(state.unchecked_elements)} elemento(s) (chaves/indices/FK/opcoes) nao comparados nesta fase")
    if state.incomplete:
        notes.append(f"{len(state.incomplete)} acao(oes) que o modelo nao seguiu (DROP COLUMN, CHANGE...)")
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

    for column_key, column in state.columns.items():
        column_notes = []
        if table_status == ERRO:
            column_status, differences, column_notes = ERRO, (), ["sem tabelas visiveis no banco"]
        elif db_table is None:
            column_status, differences = AUSENTE, ()
            column_notes = ["tabela ausente no banco"]
        else:
            db_column = db_table.columns.get(column_key)
            if db_column is None:
                column_status, differences = AUSENTE, ()
            else:
                column_status, differences, column_notes = compare_column(column.spec, db_column, state)
        if column.origin_unknown:
            column_notes = [*column_notes, "coluna so conhecida pelo MODIFY: veio do banco-base"]
        column_status, column_notes = _uncertain(column_status, column.provenance, incomplete, column_notes)
        result.objects.append(
            ObjectResult("coluna", state.name, column.spec.name, column_status, tuple(differences), tuple(column_notes), column.provenance)
        )

    if db_table is not None and state.fully_known:
        for column_key, db_column in db_table.columns.items():
            if column_key not in state.columns:
                result.objects.append(
                    ObjectResult(
                        "coluna", state.name, db_column.name, INDETERMINADO, (),
                        ("existe no banco e nenhum script a cria",), None,
                    )
                )


def _operation_results(result, model):
    by_key = {(item.table.lower(), (item.column or "").lower()): item for item in result.objects}
    for applied in model.applied:
        operation = applied.operation
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
            column_name = operation.column.name.lower() if operation.column is not None else ""
            target = by_key.get((operation.table.lower(), column_name))
            if target is None:
                result.operations.append(OperationResult(applied, INDETERMINADO, "objeto sem resultado"))
            else:
                result.operations.append(OperationResult(applied, target.status, "; ".join(target.notes)))
