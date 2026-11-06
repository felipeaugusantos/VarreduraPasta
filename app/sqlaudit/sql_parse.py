"""Interpreta comandos SQL do MySQL para a auditoria de estrutura (Fase 1).

Escopo: `CREATE TABLE`, `ALTER TABLE ... ADD COLUMN` e `... MODIFY COLUMN`.
Tudo o mais vira `nao_suportado` (ou `erro` se nao for SQL valido) com o motivo:
a auditoria nunca trata como validado o que nao foi interpretado com seguranca.

O `sqlglot` aceita sintaxe invalida e descarta partes do comando sem avisar
(ex.: `FOO BAR` vira um alias; `ALGORITHM=INPLACE` some). Por isso so entram
construcoes de uma lista fechada e cada comando passa por uma checagem de
fidelidade: todo token do original precisa reaparecer no que o `sqlglot`
entendeu; se algum sumiu, o comando inteiro vira `nao_suportado`.

Nada aqui executa SQL.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass

from app.sqlaudit.sql_split import split_with_positions

STATUS_INTERPRETED = "interpretado"
STATUS_UNSUPPORTED = "nao_suportado"
STATUS_ERROR = "erro"

KIND_CREATE_TABLE = "create_table"
KIND_ADD_COLUMN = "add_column"
KIND_MODIFY_COLUMN = "modify_column"
KIND_OTHER = "outro"

PREVIEW_CHARS = 120
_EXECUTABLE_COMMENT = re.compile(r"^/\*!(\d{5,6})?\s*(.*?)\s*\*/$", re.DOTALL)
# Tokens sem significado estrutural (pontuacao) e palavras opcionais.
_IGNORED_TOKEN_TYPES = {"L_PAREN", "R_PAREN", "COMMA", "EQ", "SEMICOLON", "DOT"}
_OPTIONAL_WORDS = {"COLUMN", "KEY", "INDEX"}
_SYNONYMS = {
    "INTEGER": ("INT",),
    "BOOL": ("TINYINT",),
    "BOOLEAN": ("TINYINT",),
    "DEC": ("DECIMAL",),
    "NUMERIC": ("DECIMAL",),
    "FIXED": ("DECIMAL",),
    "CHARSET": ("CHARACTER", "SET"),
}


class SqlParserUnavailable(Exception):
    """O pacote `sqlglot` nao esta instalado."""


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    data_type: str
    nullable: bool | None = None  # None: nao declarado no script
    has_default: bool = False
    default: str | None = None
    auto_increment: bool = False
    charset: str | None = None
    collation: str | None = None
    on_update: str | None = None
    comment: str | None = None
    primary_key: bool = False
    unique: bool = False
    position: str | None = None  # "first" ou "after:<coluna>"


@dataclass(frozen=True)
class Operation:
    kind: str
    status: str
    table: str = ""
    schema: str = ""
    column: ColumnSpec | None = None
    columns: tuple = ()
    options: tuple = ()  # (nome, valor) de opcoes de tabela interpretadas
    if_not_exists: bool = False
    reason: str = ""
    statement: object = None  # SqlStatement de origem
    min_version: str = ""  # de /*!NNNNN ... */
    # False so para acoes que sabidamente nao mudam a definicao de colunas
    # (indices, chaves, FK). Na duvida e True: o modelo desconfia da tabela.
    affects_columns: bool = True

    @property
    def interpreted(self):
        return self.status == STATUS_INTERPRETED


def _load_sqlglot():
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.dialects.dialect import Dialect
    except ImportError as error:
        raise SqlParserUnavailable(f"Biblioteca sqlglot nao encontrada: {error}") from error
    logging.getLogger("sqlglot").setLevel(logging.ERROR)  # avisos de "Command" vao para o resultado
    return sqlglot, exp, Dialect.get_or_raise("mysql")


def is_available():
    try:
        _load_sqlglot()
    except SqlParserUnavailable:
        return False
    return True


def _preview(text):
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= PREVIEW_CHARS else one_line[:PREVIEW_CHARS] + "..."


def _canonical_tokens(dialect, sql):
    tokens = Counter()
    for token in dialect.tokenize(sql):
        if token.token_type.name in _IGNORED_TOKEN_TYPES:
            continue
        word = str(token.text).upper()
        if word in _OPTIONAL_WORDS:
            continue
        for part in _SYNONYMS.get(word, (word,)):
            tokens[part] += 1
    return tokens


def _lost_tokens(dialect, original, regenerated):
    """Tokens do comando original que nao aparecem no que o sqlglot entendeu."""
    missing = _canonical_tokens(dialect, original) - _canonical_tokens(dialect, regenerated)
    return sorted(missing.elements())


def _unsupported(statement, reason, kind=KIND_OTHER, status=STATUS_UNSUPPORTED, **extra):
    return Operation(kind=kind, status=status, reason=reason, statement=statement, **extra)


def parse_text(text):
    """Operacoes de todos os comandos do script (cada uma com seu `statement`)."""
    operations = []
    for statement in split_with_positions(text):
        operations.extend(parse_statement(statement))
    return operations


def parse_statement(statement):
    sqlglot, exp, dialect = _load_sqlglot()
    if statement.problem:
        return [_unsupported(statement, f"Comando incompleto: {statement.problem}.", status=STATUS_ERROR)]

    sql, min_version = statement.text, ""
    match = _EXECUTABLE_COMMENT.match(sql)
    if match:
        sql, min_version = match.group(2), match.group(1) or ""
    if "/*!" in sql:
        return [_unsupported(statement, "Comentario executavel /*! */ no meio do comando.")]
    if sql.rstrip().endswith(","):
        return [_unsupported(statement, "Virgula sobrando no fim do comando.", status=STATUS_ERROR)]

    try:
        parsed = [item for item in sqlglot.parse(sql, read="mysql") if item is not None]
    except sqlglot.errors.SqlglotError as error:
        first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
        return [_unsupported(statement, f"Nao foi possivel interpretar: {first_line}", status=STATUS_ERROR)]
    if len(parsed) != 1:
        return [_unsupported(statement, "Mais de um comando onde era esperado um so.", status=STATUS_ERROR)]
    expression = parsed[0]

    if isinstance(expression, exp.Create):
        operations = _create_table(statement, expression, exp)
    elif isinstance(expression, exp.Alter):
        operations = _alter_table(statement, expression, exp)
    elif isinstance(expression, exp.Drop) and str(expression.args.get("kind") or "").upper() == "TABLE":
        tables = [item for item in expression.args.get("tables") or [] if isinstance(item, exp.Table)]
        if tables:  # a tabela pode ter deixado de existir: o modelo precisa saber de quais
            return [
                _unsupported(
                    statement,
                    f"DROP TABLE nao e acompanhado pelo modelo: {item.name} pode ter deixado de existir.",
                    table=item.name,
                    schema=item.db or "",
                )
                for item in tables
            ]
        return [_unsupported(statement, f"DROP TABLE sem tabela reconhecida: {_preview(sql)}")]
    else:
        return [
            _unsupported(
                statement,
                f"Tipo de comando fora do escopo desta fase ({_command_name(sql)}): {_preview(sql)}",
            )
        ]

    # Checagem de fidelidade: nada do original pode ter sido descartado.
    lost = _lost_tokens(dialect, sql, expression.sql(dialect="mysql"))
    if lost:
        table, database = _alter_target(expression, exp)
        return [
            _unsupported(
                statement,
                "Partes do comando nao foram entendidas com seguranca: " + ", ".join(lost[:8]),
                table=table,
                schema=database,
            )
        ]
    if min_version:
        operations = [_with_version(operation, min_version) for operation in operations]
    return operations


def _with_version(operation, min_version):
    from dataclasses import replace

    return replace(operation, min_version=min_version)


def _command_name(sql):
    words = sql.split(None, 2)
    return " ".join(word.upper() for word in words[:2]) if words else "?"


def _table_ref(table):
    return table.name, table.db or ""


def _column_spec(column_def, exp, dialect):
    """(ColumnSpec, motivo_de_nao_suporte ou None)."""
    kind = column_def.args.get("kind")
    if kind is None:
        return None, "coluna sem tipo de dados"
    nullable = None
    has_default, default = False, None
    auto_increment = False
    charset = collation = on_update = comment = None
    primary_key = unique = False
    for constraint in column_def.args.get("constraints") or []:
        item = constraint.args.get("kind")
        if isinstance(item, exp.NotNullColumnConstraint):
            nullable = bool(item.args.get("allow_null"))
        elif isinstance(item, exp.DefaultColumnConstraint):
            has_default, default = True, item.this.sql(dialect="mysql")
        elif isinstance(item, exp.AutoIncrementColumnConstraint):
            auto_increment = True
        elif isinstance(item, exp.CharacterSetColumnConstraint):
            charset = item.this.name.lower()
        elif isinstance(item, exp.CollateColumnConstraint):
            collation = item.this.name.lower()
        elif isinstance(item, exp.OnUpdateColumnConstraint):
            on_update = item.this.sql(dialect="mysql")
        elif isinstance(item, exp.CommentColumnConstraint):
            comment = item.this.name
        elif isinstance(item, exp.PrimaryKeyColumnConstraint):
            primary_key = True
        elif isinstance(item, exp.UniqueColumnConstraint):
            unique = True
        else:
            return None, f"atributo de coluna nao suportado: {item.sql(dialect='mysql')[:40]}"
    position = None
    placement = column_def.args.get("position")
    if placement is not None:
        if placement.args.get("position") == "FIRST":
            position = "first"
        else:
            position = f"after:{placement.this.name}"
    spec = ColumnSpec(
        name=column_def.name,
        data_type=kind.sql(dialect="mysql").lower(),
        nullable=nullable,
        has_default=has_default,
        default=default,
        auto_increment=auto_increment,
        charset=charset,
        collation=collation,
        on_update=on_update,
        comment=comment,
        primary_key=primary_key,
        unique=unique,
        position=position,
    )
    return spec, None


def _create_table(statement, create, exp):
    _sqlglot, _exp, dialect = _load_sqlglot()
    if str(create.args.get("kind") or "").upper() != "TABLE":
        return [_unsupported(statement, f"CREATE {create.args.get('kind')} fora do escopo desta fase.")]
    schema = create.this
    if not isinstance(schema, exp.Schema):
        return [_unsupported(statement, "CREATE TABLE sem lista de colunas (LIKE ou AS SELECT).")]
    table, database = _table_ref(schema.this)
    properties = create.args.get("properties")
    property_list = list(properties.expressions) if properties else []
    if any(isinstance(item, exp.TemporaryProperty) for item in property_list):
        return [_unsupported(statement, "CREATE TEMPORARY TABLE nao e estrutura permanente.")]

    base = dict(table=table, schema=database, statement=statement)
    columns, extras = [], []
    for element in schema.expressions:
        if isinstance(element, exp.ColumnDef):
            spec, problem = _column_spec(element, exp, dialect)
            if problem:
                return [_unsupported(statement, f"Coluna {element.name}: {problem}.")]
            columns.append(spec)
        else:
            extras.append(
                _unsupported(
                    statement,
                    f"Elemento de CREATE TABLE ainda nao comparado: {element.sql(dialect='mysql')[:60]}",
                    kind=KIND_CREATE_TABLE,
                    table=table,
                    schema=database,
                )
            )
    options = []
    for item in property_list:
        if isinstance(item, exp.EngineProperty):
            options.append(("engine", item.this.name.lower()))
        elif isinstance(item, exp.CharacterSetProperty):
            options.append(("charset", item.this.name.lower()))
        elif isinstance(item, exp.CollateProperty):
            options.append(("collation", item.this.name.lower()))
        else:
            extras.append(
                _unsupported(
                    statement,
                    f"Opcao de tabela ainda nao comparada: {item.sql(dialect='mysql')[:60]}",
                    kind=KIND_CREATE_TABLE,
                    table=table,
                    schema=database,
                )
            )
    if not columns:
        return [_unsupported(statement, "CREATE TABLE sem colunas.", status=STATUS_ERROR)]
    main = Operation(
        kind=KIND_CREATE_TABLE,
        status=STATUS_INTERPRETED,
        columns=tuple(columns),
        options=tuple(options),
        if_not_exists=bool(create.args.get("exists")),
        **base,
    )
    return [main, *[_with_statement(extra, statement) for extra in extras]]


def _with_statement(operation, statement):
    from dataclasses import replace

    return replace(operation, statement=statement)


def _alter_target(expression, exp):
    """(tabela, schema) de um ALTER TABLE, ou ('', '') se nao for um.

    Mesmo quando o comando nao e entendido, a tabela afetada precisa ser conhecida:
    o modelo a marca como incompleta em vez de deixar so um aviso geral.
    """
    if isinstance(expression, exp.Alter) and isinstance(expression.this, exp.Table):
        return _table_ref(expression.this)
    return "", ""


def _affects_columns(action, exp):
    if isinstance(action, (exp.AddConstraint, exp.DropPrimaryKey)):
        return False
    if isinstance(action, exp.Drop) and str(action.args.get("kind") or "").upper() in ("INDEX", "FOREIGN KEY", "CHECK", "CONSTRAINT"):
        return False
    return True


def _alter_table(statement, alter, exp):
    _sqlglot, _exp, dialect = _load_sqlglot()
    if str(alter.args.get("kind") or "").upper() != "TABLE":
        return [_unsupported(statement, f"ALTER {alter.args.get('kind')} fora do escopo desta fase.")]
    table, database = _table_ref(alter.this)
    extra_args = [key for key, value in alter.args.items() if value and key not in ("this", "kind", "actions")]
    if extra_args:
        return [
            _unsupported(
                statement,
                "Opcao de ALTER TABLE nao suportada (ALGORITHM, LOCK ou similar): " + ", ".join(sorted(extra_args)),
                table=table,
                schema=database,
            )
        ]
    operations = []
    for action in alter.args.get("actions") or []:
        base = dict(table=table, schema=database)
        if isinstance(action, exp.ColumnDef):
            if action.args.get("exists"):
                operations.append(_unsupported(statement, "ADD COLUMN IF NOT EXISTS nao existe no MySQL.", status=STATUS_ERROR))
                continue
            spec, problem = _column_spec(action, exp, dialect)
            kind = KIND_ADD_COLUMN
        elif isinstance(action, exp.ModifyColumn) and not action.args.get("rename_from"):
            spec, problem = _column_spec(action.this, exp, dialect)
            kind = KIND_MODIFY_COLUMN
        else:
            label = "CHANGE COLUMN" if isinstance(action, exp.ModifyColumn) else type(action).__name__
            operations.append(
                _unsupported(
                    statement,
                    f"Acao de ALTER TABLE ainda nao suportada ({label}): {action.sql(dialect='mysql')[:60]}",
                    affects_columns=_affects_columns(action, exp),
                    **base,
                )
            )
            continue
        if problem:
            operations.append(_unsupported(statement, f"{action.name}: {problem}.", kind=kind, **base))
        else:
            operations.append(Operation(kind=kind, status=STATUS_INTERPRETED, column=spec, statement=statement, **base))
    if not operations:
        return [_unsupported(statement, "ALTER TABLE sem acoes reconhecidas.", table=table, schema=database)]
    return operations
