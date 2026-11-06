import threading
import unittest
from datetime import datetime

from app.sqlaudit import db_reader
from app.sqlaudit.db_reader import AuditDbError, connect_readonly
from app.sqlaudit.type_normalizer import normalize_type

ALL_CONSTANTS = {value for name, value in vars(db_reader).items() if name.startswith("SQL_")}


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, statement, params=None):
        self.connection.executed.append((statement, params))
        failure = self.connection.fail_on.get(statement)
        if failure:
            raise failure
        self._rows = self.connection.responses.get(statement, [])
        self.description = [("x",)] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, responses=None, fail_on=None):
        self.executed = []
        self.responses = responses or {}
        self.fail_on = fail_on or {}
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def server_rows(database="BANCO_X", version="8.0.36", lctn=1):
    return {db_reader.SQL_SERVER_INFO: [(version, lctn, "SYSTEM", database)]}


def full_responses(database="BANCO_X"):
    responses = server_rows(database)
    responses[db_reader.SQL_TABLES] = [
        ("CLIENTES", "InnoDB", "utf8mb4_general_ci", datetime(2026, 5, 1, 10, 0)),
        ("PEDIDOS", "InnoDB", "utf8mb4_general_ci", datetime(2026, 5, 1, 10, 5)),
    ]
    responses[db_reader.SQL_COLUMNS] = [
        ("CLIENTES", "CLI_ID", 1, "int(11)", "NO", None, "auto_increment", None, None, ""),
        ("CLIENTES", "CLI_EMAIL", 2, "varchar(200)", "YES", None, "", "utf8mb4", "utf8mb4_general_ci", "e-mail"),
        ("PEDIDOS", "PED_ID", 1, "bigint", "NO", "0", "", None, None, ""),
        ("VISAO_FANTASMA", "X", 1, "int", "YES", None, "", None, None, ""),  # tabela que nao veio em TABLES
    ]
    return responses


class ReaderTestCase(unittest.TestCase):
    def connect(self, connection, **kwargs):
        captured = {}

        def connector(**options):
            captured.update(options)
            return connection

        audit = connect_readonly("10.0.0.35", 43309, "BANCO_X", "leitura", "segredo", connector=connector, **kwargs)
        return audit, captured


class ConnectionTests(ReaderTestCase):
    def test_session_is_read_only_and_uses_the_given_options(self):
        connection = FakeConnection()
        _audit, options = self.connect(connection)
        self.assertEqual(connection.executed[0][0], db_reader.SQL_READ_ONLY)
        self.assertEqual(
            (options["host"], options["port"], options["database"], options["user"], options["autocommit"]),
            ("10.0.0.35", 43309, "BANCO_X", "leitura", True),
        )
        self.assertEqual(options["ssl"], {"verify_mode": "none"})

    def test_verify_mode_passes_the_ca_and_requires_it(self):
        _audit, options = self.connect(FakeConnection(), tls="verify", ca_file="C:\\ca.pem")
        self.assertEqual(options["ssl"]["ca"], "C:\\ca.pem")
        with self.assertRaises(AuditDbError):
            self.connect(FakeConnection(), tls="verify")

    def test_server_without_max_execution_time_is_still_usable(self):
        connection = FakeConnection(fail_on={db_reader.SQL_MAX_EXECUTION: Exception(1193, "Unknown system variable")})
        audit, _ = self.connect(connection)
        self.assertIsNotNone(audit)

    def test_host_and_database_are_required(self):
        with self.assertRaises(AuditDbError):
            connect_readonly("", 3306, "BANCO_X", "u", "p", connector=lambda **_: None)
        with self.assertRaises(AuditDbError):
            connect_readonly("srv", 3306, "", "u", "p", connector=lambda **_: None)

    def test_driver_errors_become_clear_messages_without_the_password(self):
        cases = {
            1045: "Usuario ou senha",
            1049: "nao existe no servidor",
            1044: "permissao de leitura",
            1142: "permissao de leitura",
        }
        for code, expected in cases.items():
            def connector(**_options):
                raise Exception(code, "Access denied for user 'leitura'@'x' (using password: YES) segredo")

            with self.assertRaises(AuditDbError) as raised:
                connect_readonly("srv", 3306, "BANCO_X", "leitura", "segredo", connector=connector)
            self.assertIn(expected, str(raised.exception))
            self.assertNotIn("segredo", str(raised.exception))

    def test_network_failure_and_missing_driver(self):
        def refused(**_options):
            raise OSError("timed out")

        with self.assertRaises(AuditDbError) as raised:
            connect_readonly("10.0.0.35", 43309, "BANCO_X", "u", "senha-secreta", connector=refused)
        self.assertIn("10.0.0.35:43309", str(raised.exception))
        self.assertNotIn("senha-secreta", str(raised.exception))

        def no_driver(**_options):
            raise ImportError("No module named 'pymysql'")

        with self.assertRaises(AuditDbError):
            connect_readonly("srv", 3306, "BANCO_X", "u", "p", connector=no_driver)


class ReadSchemaTests(ReaderTestCase):
    def test_reads_server_tables_and_columns(self):
        audit, _ = self.connect(FakeConnection(full_responses()))
        schema = audit.read_schema("BANCO_X")
        self.assertEqual((schema.server_version, schema.lower_case_table_names, schema.database), ("8.0.36", 1, "BANCO_X"))
        self.assertEqual(sorted(schema.tables), ["clientes", "pedidos"])
        email = schema.tables["clientes"].columns["cli_email"]
        self.assertEqual((email.column_type, email.nullable, email.charset, email.comment), ("varchar(200)", True, "utf8mb4", "e-mail"))
        self.assertFalse(schema.tables["clientes"].columns["cli_id"].nullable)
        self.assertEqual(schema.tables["pedidos"].columns["ped_id"].default, "0")
        self.assertNotIn("visao_fantasma", schema.tables)

    def test_earliest_table_creation_is_the_restore_upper_bound(self):
        audit, _ = self.connect(FakeConnection(full_responses()))
        self.assertEqual(audit.read_schema("BANCO_X").earliest_table_creation(), datetime(2026, 5, 1, 10, 0))

    def test_database_name_is_checked_case_insensitively_and_mismatch_is_an_error(self):
        audit, _ = self.connect(FakeConnection(full_responses("banco_x")))
        self.assertEqual(audit.read_schema("BANCO_X").database, "banco_x")
        audit, _ = self.connect(FakeConnection(full_responses("OUTRO_BANCO")))
        with self.assertRaises(AuditDbError):
            audit.read_schema("BANCO_X")

    def test_schema_name_is_a_parameter_never_part_of_the_sql(self):
        connection = FakeConnection(full_responses())
        audit, _ = self.connect(connection)
        audit.read_schema("BANCO_X")
        queries = [(statement, params) for statement, params in connection.executed if params is not None]
        self.assertEqual({params for _statement, params in queries}, {("BANCO_X",)})
        for statement, _params in queries:
            self.assertNotIn("BANCO_X", statement)

    def test_only_fixed_queries_are_ever_executed(self):
        connection = FakeConnection(full_responses())
        audit, _ = self.connect(connection)
        audit.read_schema("BANCO_X")
        self.assertTrue({statement for statement, _ in connection.executed} <= ALL_CONSTANTS)

    def test_empty_database_is_returned_empty_for_the_comparator_to_flag(self):
        audit, _ = self.connect(FakeConnection(server_rows()))
        self.assertEqual(audit.read_schema("BANCO_X").tables, {})

    def test_query_failure_is_a_clear_error(self):
        connection = FakeConnection(full_responses(), fail_on={db_reader.SQL_COLUMNS: Exception(1142, "SELECT command denied")})
        audit, _ = self.connect(connection)
        with self.assertRaises(AuditDbError) as raised:
            audit.read_schema("BANCO_X")
        self.assertIn("SELECT command denied", str(raised.exception))

    def test_cancel_skips_the_column_query_and_close_is_safe(self):
        connection = FakeConnection(full_responses())
        audit, _ = self.connect(connection)
        cancel = threading.Event()
        cancel.set()
        schema = audit.read_schema("BANCO_X", cancel_event=cancel)
        self.assertEqual(schema.tables["clientes"].columns, {})
        audit.close()
        audit.close()
        self.assertTrue(connection.closed)

    def test_context_manager_closes(self):
        connection = FakeConnection(full_responses())
        audit, _ = self.connect(connection)
        with audit:
            pass
        self.assertTrue(connection.closed)


class TypeNormalizerTests(unittest.TestCase):
    def same(self, left, right):
        a, b = normalize_type(left), normalize_type(right)
        self.assertTrue(a.recognized and b.recognized, (left, right))
        self.assertEqual(a.text, b.text, (left, right))

    def different(self, left, right):
        a, b = normalize_type(left), normalize_type(right)
        self.assertTrue(a.recognized and b.recognized, (left, right))
        self.assertNotEqual(a.text, b.text, (left, right))

    def test_equivalent_forms(self):
        for left, right in (
            ("INTEGER", "int(11)"),
            ("int unsigned", "int(10) unsigned"),
            ("BOOLEAN", "tinyint(1)"),
            ("BOOL", "tinyint(1)"),
            ("tinyint", "tinyint(4)"),
            ("bigint(20)", "BIGINT"),
            ("decimal", "decimal(10,0)"),
            ("DECIMAL(10)", "decimal(10,0)"),
            ("DECIMAL(10, 2)", "decimal(10,2)"),
            ("NUMERIC(8,3)", "decimal(8,3)"),
            ("VARCHAR(100)", "varchar(100)"),
            ("datetime(0)", "datetime"),
            ("char", "char(1)"),
            ("year(4)", "year"),
            ("double precision", "double"),
            ("REAL", "double"),
            ("TEXT", "text"),
            ("enum('a', 'b')", "enum('a','b')"),
            ("int  unsigned  zerofill", "int(10) unsigned zerofill"),
            ("INT SIGNED", "int"),
        ):
            self.same(left, right)

    def test_real_differences_are_kept(self):
        for left, right in (
            ("varchar(100)", "varchar(200)"),
            ("int", "bigint"),
            ("int unsigned", "int"),
            ("tinyint(1)", "tinyint"),
            ("decimal(10,2)", "decimal(10,3)"),
            ("datetime(3)", "datetime"),
            ("text", "longtext"),
            ("char(1)", "varchar(1)"),
            ("enum('A')", "enum('a')"),
            ("timestamp", "datetime"),
        ):
            self.different(left, right)

    def test_unknown_types_are_flagged_not_forced(self):
        for text in ("serial", "foo(1,2,3)", "varchar", "int zzz", "", "decimal(a)", "text(10)"):
            self.assertFalse(normalize_type(text).recognized, text)


if __name__ == "__main__":
    unittest.main()
