"""Leitura SOMENTE LEITURA da estrutura de um banco MySQL (INFORMATION_SCHEMA).

Este e o unico modulo do pacote que fala com o MySQL, e so executa as consultas
FIXAS definidas abaixo, como constantes (um teste garante isso). Ele nao importa
nada que leia SQL de arquivos: o texto dos scripts nunca chega ao banco.

Observacoes:
- A sessao e aberta `READ ONLY`. Mesmo assim, use um usuario so com SELECT.
- `information_schema` so mostra objetos sobre os quais o usuario tem algum
  privilegio. Sem SELECT nas tabelas, elas SOMEM (e pareceriam "ausentes"): por
  isso um resultado sem nenhuma tabela e tratado como erro, nunca como "tudo ausente".
- Usuario e senha so existem em memoria; nunca vao para mensagens de erro.
"""

from dataclasses import dataclass, field

CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 60
MAX_EXECUTION_MS = 60000
TLS_ENCRYPT = "encrypt"  # criptografa, NAO confere o certificado
TLS_VERIFY = "verify"  # confere o certificado (precisa do arquivo da CA)

SQL_READ_ONLY = "SET SESSION TRANSACTION READ ONLY"
SQL_MAX_EXECUTION = f"SET SESSION max_execution_time = {MAX_EXECUTION_MS}"
SQL_SERVER_INFO = "SELECT VERSION(), @@lower_case_table_names, @@time_zone, DATABASE()"
SQL_DATABASES = "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME"
SQL_TABLES = (
    "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, CREATE_TIME "
    "FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'"
)
SQL_INDEXES = (
    "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART, INDEX_TYPE "
    "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
)
SQL_FOREIGN_KEYS = (
    "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.ORDINAL_POSITION, k.REFERENCED_TABLE_NAME, "
    "k.REFERENCED_COLUMN_NAME, r.DELETE_RULE, r.UPDATE_RULE "
    "FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
    "ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME AND r.TABLE_NAME = k.TABLE_NAME "
    "WHERE k.TABLE_SCHEMA = %s AND k.REFERENCED_TABLE_NAME IS NOT NULL "
    "ORDER BY k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION"
)
SQL_COLUMNS = (
    "SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
    "EXTRA, CHARACTER_SET_NAME, COLLATION_NAME, COLUMN_COMMENT "
    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION"
)


# Bancos internos do MySQL: nao sao da aplicacao, entao nao entram na lista de escolha.
SYSTEM_DATABASES = frozenset({"information_schema", "mysql", "performance_schema", "sys"})


class AuditDbError(Exception):
    """Falha de conexao ou consulta, com mensagem pronta para o operador."""


@dataclass(frozen=True)
class DbColumn:
    name: str
    position: int
    column_type: str
    nullable: bool
    default: str | None
    extra: str
    charset: str | None
    collation: str | None
    comment: str


@dataclass(frozen=True)
class DbIndex:
    name: str
    columns: tuple
    prefixes: tuple  # SUB_PART por coluna (None se inteira)
    unique: bool
    index_type: str  # BTREE, HASH, FULLTEXT, SPATIAL


@dataclass(frozen=True)
class DbForeignKey:
    name: str
    columns: tuple
    ref_table: str
    ref_columns: tuple
    delete_rule: str
    update_rule: str


@dataclass
class DbTable:
    name: str
    engine: str | None = None
    collation: str | None = None
    created: object = None
    columns: dict = field(default_factory=dict)  # nome minusculo -> DbColumn
    indexes: dict = field(default_factory=dict)  # nome minusculo -> DbIndex (a chave primaria e "PRIMARY")
    foreign_keys: dict = field(default_factory=dict)  # nome minusculo -> DbForeignKey


@dataclass
class DbSchema:
    database: str
    server_version: str = ""
    lower_case_table_names: int = 0
    time_zone: str = ""
    tls_verified: bool = False
    tables: dict = field(default_factory=dict)  # nome minusculo -> DbTable

    def earliest_table_creation(self):
        """Menor CREATE_TIME: limite superior do momento da restauracao do dump."""
        dates = [table.created for table in self.tables.values() if table.created is not None]
        return min(dates) if dates else None


def _describe(error):
    args = getattr(error, "args", ())
    return f"({args[0]}) {args[1]}" if len(args) >= 2 else str(error)


def _default_connector(**kwargs):
    import pymysql  # importado so aqui: o modulo carrega sem o driver

    return pymysql.connect(**kwargs)


def connect_readonly(
    host, port, database, username, password, tls=TLS_ENCRYPT, ca_file="", connector=None, require_database=True
):
    """Abre a conexao de leitura. `connector` existe para testes.

    `require_database=False` conecta so ao servidor (para listar os bancos disponiveis).
    """
    if not host or (require_database and not database):
        raise AuditDbError("Informe o servidor e o nome do banco antes de auditar.")
    if tls == TLS_VERIFY:
        if not ca_file:
            raise AuditDbError("Para verificar o certificado informe o arquivo da CA.")
        ssl_options = {"ca": str(ca_file), "check_hostname": False}
    else:
        ssl_options = {"verify_mode": "none"}
    try:
        connection = (connector or _default_connector)(
            host=host,
            port=int(port),
            user=username,
            password=password,
            **({"database": database} if database else {}),
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            ssl=ssl_options,
        )
    except ImportError as error:
        raise AuditDbError(f"Biblioteca PyMySQL nao encontrada no sistema: {error}") from error
    except OSError as error:
        raise AuditDbError(f"Nao foi possivel conectar em {host}:{port} ({error}).") from error
    except Exception as error:  # erros do driver (OperationalError etc.)
        code = error.args[0] if getattr(error, "args", None) else 0
        if code == 1045:
            raise AuditDbError("Usuario ou senha do banco incorretos.") from error
        if code == 1049:
            raise AuditDbError(f"Banco '{database}' nao existe no servidor.") from error
        if code in (1044, 1142):
            raise AuditDbError(f"O usuario nao tem permissao de leitura no banco '{database}'.") from error
        raise AuditDbError(f"Nao foi possivel conectar em {host}:{port} {_describe(error)}.") from error
    return AuditConnection(connection, tls_verified=tls == TLS_VERIFY)


class AuditConnection:
    def __init__(self, connection, tls_verified=False):
        self._connection = connection
        self.tls_verified = tls_verified
        self._query(SQL_READ_ONLY)
        try:
            self._query(SQL_MAX_EXECUTION)  # nem todo servidor tem esta variavel
        except AuditDbError:
            pass

    def _query(self, statement, params=None):
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(statement, params)
                return cursor.fetchall() if cursor.description else []
        except Exception as error:
            raise AuditDbError(f"Falha na consulta ao banco {_describe(error)}.") from error

    def close(self):
        try:
            self._connection.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def list_databases(self, include_system=False):
        """Bancos que ESTE usuario enxerga (o information_schema esconde os demais), em ordem."""
        names = [str(row[0]) for row in self._query(SQL_DATABASES)]
        if include_system:
            return names
        return [name for name in names if name.lower() not in SYSTEM_DATABASES]

    def read_schema(self, database, cancel_event=None):
        """Tabelas e colunas de `database`. Confere que a conexao esta mesmo nesse banco."""
        rows = self._query(SQL_SERVER_INFO)
        if not rows:
            raise AuditDbError("O servidor nao respondeu a consulta de versao.")
        version, lower_case, time_zone, current = rows[0]
        if str(current or "").lower() != database.lower():
            raise AuditDbError(f"Conectado ao banco '{current}', mas era esperado '{database}'.")
        schema = DbSchema(
            database=str(current),
            server_version=str(version),
            lower_case_table_names=int(lower_case or 0),
            time_zone=str(time_zone or ""),
            tls_verified=self.tls_verified,
        )
        for name, engine, collation, created in self._query(SQL_TABLES, (database,)):
            schema.tables[str(name).lower()] = DbTable(str(name), engine, collation, created)
        if cancel_event is not None and cancel_event.is_set():
            return schema
        for row in self._query(SQL_COLUMNS, (database,)):
            table_name, name, position, column_type, nullable, default, extra, charset, collation, comment = row
            table = schema.tables.get(str(table_name).lower())
            if table is None:
                continue
            table.columns[str(name).lower()] = DbColumn(
                name=str(name),
                position=int(position or 0),
                column_type=str(column_type or ""),
                nullable=str(nullable).upper() == "YES",
                default=None if default is None else str(default),
                extra=str(extra or ""),
                charset=charset,
                collation=collation,
                comment=str(comment or ""),
            )
        if cancel_event is not None and cancel_event.is_set():
            return schema
        self._read_indexes(schema, database)
        if cancel_event is not None and cancel_event.is_set():
            return schema
        self._read_foreign_keys(schema, database)
        return schema

    def _read_indexes(self, schema, database):
        grouped = {}
        for table_name, index_name, non_unique, _seq, column, sub_part, index_type in self._query(SQL_INDEXES, (database,)):
            table = schema.tables.get(str(table_name).lower())
            if table is None:
                continue
            entry = grouped.setdefault(
                (table.name.lower(), str(index_name)), [table, int(non_unique or 0) == 0, str(index_type or ""), [], []]
            )
            entry[3].append(str(column))
            entry[4].append(None if sub_part in (None, "") else int(sub_part))
        for (_table_key, name), (table, unique, index_type, columns, prefixes) in grouped.items():
            table.indexes[name.lower()] = DbIndex(name, tuple(columns), tuple(prefixes), unique, index_type.upper())

    def _read_foreign_keys(self, schema, database):
        grouped = {}
        for table_name, name, column, _pos, ref_table, ref_column, delete_rule, update_rule in self._query(SQL_FOREIGN_KEYS, (database,)):
            table = schema.tables.get(str(table_name).lower())
            if table is None:
                continue
            entry = grouped.setdefault(
                (table.name.lower(), str(name)), [table, str(ref_table), str(delete_rule or ""), str(update_rule or ""), [], []]
            )
            entry[4].append(str(column))
            entry[5].append(str(ref_column))
        for (_table_key, name), (table, ref_table, delete_rule, update_rule, columns, ref_columns) in grouped.items():
            table.foreign_keys[name.lower()] = DbForeignKey(
                name, tuple(columns), ref_table, tuple(ref_columns), delete_rule.upper(), update_rule.upper()
            )
