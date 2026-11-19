"""Interpreta comandos SQL do MySQL para a auditoria de estrutura.

Escopo: `CREATE TABLE` (colunas, chaves, indices, FK, opcoes), `ALTER TABLE` com
`ADD/MODIFY/CHANGE/DROP/RENAME COLUMN`, `RENAME TO`, `ADD/DROP INDEX|UNIQUE|PRIMARY
KEY|FOREIGN KEY`, opcoes de tabela e `CONVERT TO CHARACTER SET`; `CREATE/DROP INDEX`
e `DROP TABLE`. Tudo o mais vira `nao_suportado` (ou `erro` se nao for SQL valido)
com o motivo: a auditoria nunca trata como validado o que nao foi interpretado com
seguranca.

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
KIND_CHANGE_COLUMN = "change_column"  # name = nome antigo, column = nova definicao
KIND_DROP_COLUMN = "drop_column"
KIND_RENAME_COLUMN = "rename_column"  # name -> new_name
KIND_DROP_TABLE = "drop_table"
KIND_RENAME_TABLE = "rename_table"  # table -> new_name
KIND_ADD_INDEX = "add_index"
KIND_DROP_INDEX = "drop_index"
KIND_ADD_PRIMARY_KEY = "add_primary_key"
KIND_DROP_PRIMARY_KEY = "drop_primary_key"
KIND_ADD_FOREIGN_KEY = "add_foreign_key"
KIND_DROP_FOREIGN_KEY = "drop_foreign_key"
KIND_ALTER_OPTIONS = "alter_options"
KIND_CONVERT_CHARSET = "convert_charset"
KIND_OTHER = "outro"

PREVIEW_CHARS = 120
_EXECUTABLE_COMMENT = re.compile(r"^/\*!(\d{5,6})?\s*(.*?)\s*\*/$", re.DOTALL)
# Tokens sem significado estrutural (pontuacao) e palavras opcionais.
_IGNORED_TOKEN_TYPES = {"L_PAREN", "R_PAREN", "COMMA", "EQ", "SEMICOLON", "DOT"}
_OPTIONAL_WORDS = {"COLUMN", "KEY", "INDEX", "TO"}
_CONVERT_CHARSET = re.compile(
    r"^ALTER\s+TABLE\s+(?:`?(?P<db>[\w$]+)`?\.)?`?(?P<table>[\w$]+)`?\s+CONVERT\s+TO\s+"
    r"(?:CHARACTER\s+SET|CHARSET)\s+`?(?P<charset>\w+)`?(?:\s+COLLATE\s+`?(?P<collation>\w+)`?)?\s*$",
    re.IGNORECASE,
)
_NAME = r"(?:`?(?P<{db}>[\w$]+)`?\.)?`?(?P<{table}>[\w$]+)`?"
_RENAME_PAIR = re.compile(
    r"^" + _NAME.format(db="db", table="old") + r"\s+TO\s+" + _NAME.format(db="db2", table="new") + r"$", re.IGNORECASE
)
_FK_RULE = re.compile(r"^ON\s+(DELETE|UPDATE)\s+(RESTRICT|CASCADE|SET\s+NULL|NO\s+ACTION|SET\s+DEFAULT)$", re.IGNORECASE)
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
class IndexSpec:
    name: str | None  # None: o MySQL escolhe o nome (primeira coluna, com sufixo _2, _3...)
    columns: tuple  # nomes, na ordem
    prefixes: tuple = ()  # comprimento de prefixo por coluna (None se inteira)
    unique: bool = False
    kind: str = ""  # "", "fulltext" ou "spatial"


@dataclass(frozen=True)
class ForeignKeySpec:
    name: str | None
    columns: tuple
    ref_table: str
    ref_columns: tuple
    ref_schema: str = ""
    on_delete: str = ""  # "" = nao declarado (equivale a RESTRICT/NO ACTION)
    on_update: str = ""


@dataclass(frozen=True)
class Operation:
    kind: str
    status: str
    table: str = ""
    schema: str = ""
    column: ColumnSpec | None = None
    columns: tuple = ()
    options: tuple = ()  # (nome, valor) de opcoes de tabela interpretadas
    name: str = ""  # coluna/indice/chave afetado (nome antigo em CHANGE/RENAME)
    new_name: str = ""
    index: IndexSpec | None = None
    foreign_key: ForeignKeySpec | None = None
    primary_key: tuple = ()  # colunas da chave primaria (ADD PRIMARY KEY ou do CREATE TABLE)
    indexes: tuple = ()  # IndexSpec do CREATE TABLE
    foreign_keys: tuple = ()  # ForeignKeySpec do CREATE TABLE
    if_exists: bool = False  # DROP ... IF EXISTS
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

    if isinstance(expression, exp.Command):
        return [_with_version(op, min_version) if min_version else op for op in _command(statement, sql)]
    if isinstance(expression, exp.Create):
        if str(expression.args.get("kind") or "").upper() == "INDEX":
            operations = _create_index(statement, expression, exp)
        else:
            operations = _create_table(statement, expression, exp)
    elif isinstance(expression, exp.Alter):
        operations = _alter_table(statement, expression, exp)
    elif isinstance(expression, exp.Drop):
        operations = _drop(statement, expression, exp)
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


def _key_names(items, exp):
    """(nomes, prefixos) das colunas de uma chave/indice, ou None se algum item nao for entendido."""
    names, prefixes = [], []
    for item in items or []:
        prefix = None
        if isinstance(item, exp.Ordered):  # ASC/DESC nao muda quais colunas formam o indice
            item = item.this
        if isinstance(item, exp.ColumnPrefix):
            literal = item.args.get("expression")
            if not isinstance(literal, exp.Literal) or not str(literal.this).isdigit():
                return None
            prefix = int(literal.this)
            item = item.this
        if not isinstance(item, (exp.Column, exp.Identifier)) or not item.name:
            return None
        names.append(item.name)
        prefixes.append(prefix)
    return (tuple(names), tuple(prefixes)) if names else None


def _index_spec(item, exp, name=None):
    """IndexSpec de IndexColumnConstraint/UniqueColumnConstraint, ou None."""
    if isinstance(item, exp.UniqueColumnConstraint):
        schema = item.this
        if not isinstance(schema, exp.Schema):
            return None
        found = _key_names(schema.expressions, exp)
        label = name or (schema.this.name if schema.this is not None else None)
        return IndexSpec(label or None, found[0], found[1], unique=True) if found else None
    kind = str(item.args.get("kind") or "").lower()
    if kind not in ("", "fulltext", "spatial"):
        return None
    found = _key_names(item.expressions, exp)
    label = name or (item.this.name if item.this is not None else None)
    return IndexSpec(label or None, found[0], found[1], kind=kind) if found else None


def _foreign_key_spec(item, exp, name=None):
    reference = item.args.get("reference")
    columns = _key_names(item.expressions, exp)
    if reference is None or columns is None or not isinstance(reference.this, exp.Schema):
        return None
    target = reference.this.this
    referenced = _key_names(reference.this.expressions, exp)
    if not isinstance(target, exp.Table) or referenced is None:
        return None
    rules = {"DELETE": "", "UPDATE": ""}
    for option in reference.args.get("options") or []:
        text = option if isinstance(option, str) else option.sql(dialect="mysql")
        match = _FK_RULE.match(" ".join(text.split()))
        if match is None:
            return None  # MATCH FULL etc.: nao entendido
        rules[match.group(1).upper()] = " ".join(match.group(2).upper().split())
    return ForeignKeySpec(
        name or None, columns[0], target.name, referenced[0], target.db or "", rules["DELETE"], rules["UPDATE"]
    )


def _constraint_item(item, exp, name=None):
    """('pk', colunas) | ('index', IndexSpec) | ('fk', ForeignKeySpec) | None se nao for entendido."""
    if isinstance(item, exp.Constraint):
        inner = item.expressions
        if len(inner) != 1:
            return None
        label = item.this.name if item.this is not None else None
        return _constraint_item(inner[0], exp, name=label)
    if isinstance(item, exp.PrimaryKey):
        found = _key_names(item.expressions, exp)
        return ("pk", found[0]) if found else None
    if isinstance(item, (exp.IndexColumnConstraint, exp.UniqueColumnConstraint)):
        spec = _index_spec(item, exp, name)
        return ("index", spec) if spec else None
    if isinstance(item, exp.ForeignKey):
        spec = _foreign_key_spec(item, exp, name)
        return ("fk", spec) if spec else None
    return None


def _table_options(properties, exp):
    """(opcoes interpretadas, opcoes nao comparadas) de CREATE/ALTER TABLE."""
    options, others = [], []
    for item in properties:
        if isinstance(item, exp.EngineProperty):
            options.append(("engine", item.this.name.lower()))
        elif isinstance(item, exp.CharacterSetProperty):
            options.append(("charset", item.this.name.lower()))
        elif isinstance(item, exp.CollateProperty):
            options.append(("collation", item.this.name.lower()))
        elif isinstance(item, (exp.AlgorithmProperty, exp.LockProperty, exp.AutoIncrementProperty)):
            continue  # dica de execucao / contador: sem efeito na estrutura comparada
        else:
            others.append(item)
    return options, others


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

    columns, extras, indexes, foreign_keys = [], [], [], []
    primary_key = ()
    for element in schema.expressions:
        if isinstance(element, exp.ColumnDef):
            spec, problem = _column_spec(element, exp, dialect)
            if problem:
                return [_unsupported(statement, f"Coluna {element.name}: {problem}.")]
            columns.append(spec)
            continue
        found = _constraint_item(element, exp)
        if found is None:
            extras.append(f"Elemento de CREATE TABLE ainda nao comparado: {element.sql(dialect='mysql')[:60]}")
        elif found[0] == "pk":
            primary_key = found[1]
        elif found[0] == "index":
            indexes.append(found[1])
        else:
            foreign_keys.append(found[1])
    options, others = _table_options(property_list, exp)
    extras.extend(f"Opcao de tabela ainda nao comparada: {item.sql(dialect='mysql')[:60]}" for item in others)
    if not columns:
        return [_unsupported(statement, "CREATE TABLE sem colunas.", status=STATUS_ERROR)]
    main = Operation(
        kind=KIND_CREATE_TABLE,
        status=STATUS_INTERPRETED,
        table=table,
        schema=database,
        statement=statement,
        columns=tuple(columns),
        options=tuple(options),
        primary_key=primary_key,
        indexes=tuple(indexes),
        foreign_keys=tuple(foreign_keys),
        if_not_exists=bool(create.args.get("exists")),
    )
    unchecked = [
        _unsupported(statement, reason, kind=KIND_CREATE_TABLE, table=table, schema=database, affects_columns=False)
        for reason in extras
    ]
    return [main, *unchecked]


def _create_index(statement, create, exp):
    index = create.this
    if not isinstance(index, exp.Index) or not isinstance(index.args.get("table"), exp.Table):
        return [_unsupported(statement, "CREATE INDEX sem tabela reconhecida.")]
    table, database = _table_ref(index.args["table"])
    params = index.args.get("params")
    found = _key_names(params.args.get("columns") if params is not None else None, exp)
    if found is None:
        return [_unsupported(statement, "CREATE INDEX com colunas nao entendidas.", table=table, schema=database, affects_columns=False)]
    spec = IndexSpec(index.this.name if index.this is not None else None, found[0], found[1], unique=bool(create.args.get("unique")))
    return [Operation(kind=KIND_ADD_INDEX, status=STATUS_INTERPRETED, table=table, schema=database, index=spec, statement=statement)]


def _drop(statement, drop, exp):
    kind = str(drop.args.get("kind") or "").upper()
    exists = bool(drop.args.get("exists"))
    items = [item for item in drop.args.get("tables") or [] if isinstance(item, exp.Table)]
    if kind == "TABLE" and items:
        return [
            Operation(
                kind=KIND_DROP_TABLE, status=STATUS_INTERPRETED, table=item.name, schema=item.db or "",
                if_exists=exists, statement=statement,
            )
            for item in items
        ]
    if kind == "INDEX" and len(items) == 1 and isinstance(drop.args.get("cluster"), exp.OnProperty):
        target = drop.args["cluster"].this
        if isinstance(target, exp.Table):
            return [
                Operation(
                    kind=KIND_DROP_INDEX, status=STATUS_INTERPRETED, table=target.name, schema=target.db or "",
                    name=items[0].name, statement=statement,
                )
            ]
    return [_unsupported(statement, f"DROP {kind or '?'} fora do escopo desta fase: {_preview(drop.sql(dialect='mysql'))}")]


def _rename_tables(statement, sql):
    """`RENAME TABLE a TO b[, c TO d]` -> uma operacao por par; None se nao for esse comando."""
    match = re.match(r"^RENAME\s+TABLE\s+(?P<pairs>.+)$", sql, re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    operations = []
    for pair in match.group("pairs").split(","):
        found = _RENAME_PAIR.match(pair.strip())
        if found is None or (found.group("db") or "").lower() != (found.group("db2") or "").lower():
            return [_unsupported(statement, f"RENAME TABLE nao entendido (ou entre bancos): {_preview(sql)}")]
        operations.append(
            Operation(
                kind=KIND_RENAME_TABLE, status=STATUS_INTERPRETED, table=found.group("old"),
                schema=found.group("db") or "", new_name=found.group("new"), statement=statement,
            )
        )
    return operations


def _command(statement, sql):
    """exp.Command do sqlglot: so `ALTER TABLE ... CONVERT TO CHARACTER SET` e entendido."""
    match = _CONVERT_CHARSET.match(sql)
    if match:
        options = [("charset", match.group("charset").lower())]
        if match.group("collation"):
            options.append(("collation", match.group("collation").lower()))
        return [
            Operation(
                kind=KIND_CONVERT_CHARSET, status=STATUS_INTERPRETED, table=match.group("table"),
                schema=match.group("db") or "", options=tuple(options), statement=statement,
            )
        ]
    renamed = _rename_tables(statement, sql)
    if renamed is not None:
        return renamed
    alter = re.match(r"^ALTER\s+TABLE\s+(?:`?(?P<db>[\w$]+)`?\.)?`?(?P<table>[\w$]+)`?\s", sql, re.IGNORECASE)
    table, database = (alter.group("table"), alter.group("db") or "") if alter else ("", "")
    return [
        _unsupported(
            statement,
            f"Tipo de comando fora do escopo desta fase ({_command_name(sql)}): {_preview(sql)}",
            table=table,
            schema=database,
        )
    ]


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
    base = dict(table=table, schema=database)
    extra_args = [key for key, value in alter.args.items() if value and key not in ("this", "kind", "actions", "options")]
    if extra_args:
        return [_unsupported(statement, "Opcao de ALTER TABLE nao suportada: " + ", ".join(sorted(extra_args)), **base)]

    def unsupported(reason, affects=True):
        return _unsupported(statement, reason, affects_columns=affects, **base)

    def done(kind, **fields):
        return Operation(kind=kind, status=STATUS_INTERPRETED, statement=statement, **base, **fields)

    operations = []
    options, others = _table_options(alter.args.get("options") or [], exp)
    if options:
        operations.append(done(KIND_ALTER_OPTIONS, options=tuple(options)))
    operations.extend(unsupported(f"Opcao de tabela ainda nao comparada: {item.sql(dialect='mysql')[:60]}", False) for item in others)

    for action in alter.args.get("actions") or []:
        if isinstance(action, exp.ColumnDef) or isinstance(action, exp.ModifyColumn):
            definition = action if isinstance(action, exp.ColumnDef) else action.this
            if isinstance(action, exp.ColumnDef) and action.args.get("exists"):
                operations.append(_unsupported(statement, "ADD COLUMN IF NOT EXISTS nao existe no MySQL.", status=STATUS_ERROR))
                continue
            spec, problem = _column_spec(definition, exp, dialect)
            renamed = None if isinstance(action, exp.ColumnDef) else action.args.get("rename_from")
            kind = KIND_ADD_COLUMN if isinstance(action, exp.ColumnDef) else (KIND_CHANGE_COLUMN if renamed else KIND_MODIFY_COLUMN)
            if problem:
                operations.append(_unsupported(statement, f"{definition.name}: {problem}.", kind=kind, **base))
            else:
                operations.append(done(kind, column=spec, name=renamed.name if renamed else ""))
        elif isinstance(action, exp.Drop):
            kind = str(action.args.get("kind") or "").upper()
            names = [item.name for item in action.args.get("tables") or [] if getattr(item, "name", "")]
            if kind == "COLUMN" and names:
                operations.extend(done(KIND_DROP_COLUMN, name=name) for name in names)
            elif kind == "INDEX" and names:
                operations.extend(done(KIND_DROP_INDEX, name=name) for name in names)
            elif kind == "FOREIGN KEY" and names:
                operations.extend(done(KIND_DROP_FOREIGN_KEY, name=name) for name in names)
            else:
                operations.append(unsupported(f"Acao de ALTER TABLE ainda nao suportada (DROP {kind}): {action.sql(dialect='mysql')[:60]}", _affects_columns(action, exp)))
        elif isinstance(action, exp.DropPrimaryKey):
            operations.append(done(KIND_DROP_PRIMARY_KEY))
        elif isinstance(action, exp.RenameColumn) and isinstance(action.args.get("to"), exp.Column):
            operations.append(done(KIND_RENAME_COLUMN, name=action.this.name, new_name=action.args["to"].name))
        elif isinstance(action, exp.AlterRename) and isinstance(action.this, exp.Table):
            operations.append(done(KIND_RENAME_TABLE, new_name=action.this.name))
        elif isinstance(action, exp.AddConstraint):
            for item in action.expressions:
                found = _constraint_item(item, exp)
                if found is None:
                    operations.append(unsupported(f"Restricao ainda nao comparada: {item.sql(dialect='mysql')[:60]}", False))
                elif found[0] == "pk":
                    operations.append(done(KIND_ADD_PRIMARY_KEY, primary_key=found[1]))
                elif found[0] == "index":
                    operations.append(done(KIND_ADD_INDEX, index=found[1]))
                else:
                    operations.append(done(KIND_ADD_FOREIGN_KEY, foreign_key=found[1]))
        else:
            operations.append(
                unsupported(
                    f"Acao de ALTER TABLE ainda nao suportada ({type(action).__name__}): {action.sql(dialect='mysql')[:60]}",
                    _affects_columns(action, exp),
                )
            )
    if not operations and not alter.args.get("actions") and not alter.args.get("options"):
        return [unsupported("ALTER TABLE sem acoes reconhecidas.")]
    return operations
