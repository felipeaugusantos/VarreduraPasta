import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import model_db, settings
from app.model_db import ModelDbError, split_statements
from app.script_monitor import EVENT_EXECUTED, ScriptMonitorResult, read_ledger
from app.script_runner import execute_scripts
from tests.test_script_monitor import ScriptMonitorTestCase

SETTINGS = {"host": "srv", "port": 3306, "database": "modelo", "username": ""}


class SplitStatementsTests(unittest.TestCase):
    def test_splits_on_semicolons_and_drops_comments(self):
        text = "-- comentario\nCREATE TABLE a (id INT); # outro\n/* bloco\n; */ INSERT INTO a VALUES (1);\n"
        self.assertEqual(
            split_statements(text),
            ["CREATE TABLE a (id INT)", "INSERT INTO a VALUES (1)"],
        )

    def test_semicolon_inside_text_is_not_a_separator(self):
        text = "INSERT INTO t VALUES ('a;b', \"c;d\", `e;f`);\nINSERT INTO t VALUES ('it''s; ok', 'x\\'y;z');"
        statements = split_statements(text)
        self.assertEqual(len(statements), 2)
        self.assertIn("'a;b'", statements[0])
        self.assertIn("'it''s; ok'", statements[1])

    def test_dash_dash_without_space_is_not_a_comment(self):
        self.assertEqual(split_statements("SELECT 5--3;"), ["SELECT 5--3"])

    def test_delimiter_command_keeps_procedure_body_whole(self):
        text = (
            "DELIMITER //\n"
            "CREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\n  SELECT 2;\nEND//\n"
            "DELIMITER ;\n"
            "CALL p();\n"
        )
        statements = split_statements(text)
        self.assertEqual(len(statements), 2)
        self.assertTrue(statements[0].startswith("CREATE PROCEDURE p()"))
        self.assertIn("SELECT 2;", statements[0])
        self.assertEqual(statements[1], "CALL p()")

    def test_executable_comment_is_kept(self):
        self.assertEqual(split_statements("/*!40101 SET x=1 */;"), ["/*!40101 SET x=1 */"])

    def test_last_statement_without_semicolon_and_blank_script(self):
        self.assertEqual(split_statements("SELECT 1"), ["SELECT 1"])
        self.assertEqual(split_statements("  \n-- so comentario\n"), [])


class FakeConnection:
    """Conexao falsa: registra comandos e falha quando o texto pedir."""

    def __init__(self, fail_on=None):
        self.executed = []
        self.fail_on = fail_on
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        connection = self

        class Cursor:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def execute(self_inner, statement):
                if connection.fail_on and connection.fail_on in statement:
                    raise RuntimeError(1146, "Table 'x' doesn't exist")
                connection.executed.append(statement)

            def fetchone(self_inner):
                return ("8.0.36", "modelo")

        return Cursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class RunScriptsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def write(self, name, text, encoding="utf-8"):
        (self.dir / name).write_bytes(text.encode(encoding))

    def test_runs_in_order_and_commits_each_script(self):
        self.write("1.sql", "INSERT INTO a VALUES (1);\nINSERT INTO a VALUES (2);")
        self.write("2.sql", "UPDATE a SET x=1;")
        connection = FakeConnection()

        results = model_db.run_scripts(
            self.dir, ["1.sql", "2.sql"], "u", "p", connector=lambda *a: connection, settings=SETTINGS
        )

        self.assertEqual([(r.name, r.ok, r.statements) for r in results], [("1.sql", True, 2), ("2.sql", True, 1)])
        self.assertEqual(connection.commits, 2)
        self.assertTrue(connection.closed)

    def test_stops_at_the_first_failure_and_rolls_back(self):
        self.write("1.sql", "SELECT 1;")
        self.write("2.sql", "SELECT 2;\nINSERT INTO falta VALUES (1);\nSELECT 3;")
        self.write("3.sql", "SELECT 4;")
        connection = FakeConnection(fail_on="falta")

        results = model_db.run_scripts(
            self.dir, ["1.sql", "2.sql", "3.sql"], "u", "p", connector=lambda *a: connection, settings=SETTINGS
        )

        self.assertEqual([r.ok for r in results], [True, False])  # o 3.sql nao rodou
        self.assertIn("comando 2 de 3", results[1].message)
        self.assertIn("(1146)", results[1].message)
        self.assertIn("INSERT INTO falta", results[1].message)
        self.assertEqual(connection.rollbacks, 1)
        self.assertNotIn("SELECT 4", connection.executed)

    def test_empty_script_and_missing_file_are_reported(self):
        self.write("vazio.sql", "-- nada\n")
        results = model_db.run_scripts(
            self.dir, ["vazio.sql", "nao_existe.sql"], "u", "p", connector=lambda *a: FakeConnection(), settings=SETTINGS
        )
        self.assertFalse(results[0].ok)
        self.assertIn("nenhum comando", results[0].message)
        self.assertEqual(len(results), 1)  # parou no primeiro

        results = model_db.run_scripts(
            self.dir, ["nao_existe.sql"], "u", "p", connector=lambda *a: FakeConnection(), settings=SETTINGS
        )
        self.assertFalse(results[0].ok)

    def test_reads_latin1_scripts(self):
        self.write("acentos.sql", "INSERT INTO t VALUES ('Ação');", encoding="cp1252")
        connection = FakeConnection()
        model_db.run_scripts(
            self.dir, ["acentos.sql"], "u", "p", connector=lambda *a: connection, settings=SETTINGS
        )
        self.assertEqual(connection.executed, ["INSERT INTO t VALUES ('Ação')"])

    def test_connection_failure_is_not_swallowed(self):
        def broken(*_args):
            raise ModelDbError("Usuario ou senha do banco incorretos.")

        with self.assertRaisesRegex(ModelDbError, "incorretos"):
            model_db.run_scripts(self.dir, ["1.sql"], "u", "p", connector=broken, settings=SETTINGS)

    def test_test_connection_reports_version_and_database(self):
        ok, message = model_db.test_connection(SETTINGS, "u", "p", connector=lambda *a: FakeConnection())
        self.assertTrue(ok)
        self.assertIn("8.0.36", message)
        self.assertIn("modelo", message)

        def refused(*_args):
            raise ModelDbError("Nao foi possivel conectar")

        ok, message = model_db.test_connection(SETTINGS, "u", "p", connector=refused)
        self.assertFalse(ok)


class ConnectTests(unittest.TestCase):
    def test_requires_server_and_database(self):
        with self.assertRaisesRegex(ModelDbError, "Configure"):
            model_db.connect({"host": "", "port": 3306, "database": ""}, "u", "p")

    def test_wrong_password_and_unknown_database_have_clear_messages(self):
        import pymysql

        for code, expected in ((1045, "incorretos"), (1049, "nao existe")):
            with patch("pymysql.connect", side_effect=pymysql.err.OperationalError(code, "x")):
                with self.assertRaisesRegex(ModelDbError, expected):
                    model_db.connect(SETTINGS, "u", "p")

    def test_password_is_passed_to_the_driver_but_never_to_the_message(self):
        import pymysql

        with patch("pymysql.connect", side_effect=pymysql.err.OperationalError(2003, "recusou")) as connect:
            with self.assertRaises(ModelDbError) as caught:
                model_db.connect(SETTINGS, "operador", "SegredoXYZ")

        self.assertEqual(connect.call_args.kwargs["password"], "SegredoXYZ")
        self.assertNotIn("SegredoXYZ", str(caught.exception))
        self.assertEqual(connect.call_args.kwargs["autocommit"], False)


class ModelDbSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        original = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = Path(temp.name) / "settings.json"
        self.addCleanup(setattr, settings, "SETTINGS_FILE", original)

    def test_defaults_and_roundtrip_without_any_password(self):
        self.assertEqual(
            settings.load_model_db(), {"host": "", "port": 3306, "database": "", "username": ""}
        )
        settings.save_model_db(" srv ", "3307", "modelo", "ana")

        self.assertEqual(
            settings.load_model_db(), {"host": "srv", "port": 3307, "database": "modelo", "username": "ana"}
        )
        self.assertNotIn("senha", settings.SETTINGS_FILE.read_text(encoding="utf-8").lower())

    def test_invalid_port_falls_back_to_default(self):
        settings.save_model_db("srv", "abc", "modelo")
        self.assertEqual(settings.load_model_db()["port"], 3306)


class ExecuteScriptsTests(ScriptMonitorTestCase):
    """Execucao + controle compartilhado: o que rodou vira 'executado' para todos."""

    def run_new_scripts(self, scripts, connection, password="p"):
        """Primeiro uso (base vazia), cria `scripts` {nome: texto} e executa todos."""
        with self.as_user("ana"):
            self.check()
            for name, text in scripts.items():
                (self.scripts / name).write_text(text, encoding="utf-8")
            return execute_scripts(
                list(scripts), "operador", password,
                directory=self.scripts, control_directory=self.control,
                connector=lambda *a: connection, settings=SETTINGS,
            )

    def test_successful_scripts_are_recorded_as_executed_for_everyone(self):
        results = self.run_new_scripts({"4.sql": "SELECT 1;", "5.sql": "SELECT 2;"}, FakeConnection())

        self.assertTrue(all(r.ok for r in results))
        executed = [e.script for e in read_ledger(self.control) if e.event == EVENT_EXECUTED]
        self.assertEqual(executed, ["4.sql", "5.sql"])
        with self.as_user("joao"):
            self.assertEqual(self.check().pending, [])

    def test_failure_leaves_the_script_pending_and_keeps_the_ones_before(self):
        results = self.run_new_scripts(
            {"a.sql": "SELECT 1;", "b.sql": "INSERT INTO falta VALUES (1);", "c.sql": "SELECT 3;"},
            FakeConnection(fail_on="falta"),
        )
        with self.as_user("ana"):
            pending = self.check().pending_names

        self.assertEqual([r.ok for r in results], [True, False])
        self.assertEqual(pending, ["b.sql", "c.sql"])

    def test_audit_never_contains_the_password(self):
        self.run_new_scripts({"a.sql": "SELECT 1;"}, FakeConnection(), password="SegredoXYZ")

        logged = " ".join(str(call) for call in self.write_log.call_args_list)
        self.assertIn("executado", logged)
        self.assertNotIn("SegredoXYZ", logged)


class DialogTests(unittest.TestCase):
    """Com Tk de verdade: login e configuracao do banco."""

    def setUp(self):
        patcher = patch.dict("os.environ", {"VARREDURA_SISTEMA_DATA_DIR": tempfile.mkdtemp()})
        patcher.start()
        self.addCleanup(patcher.stop)
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        original = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = Path(temp.name) / "settings.json"
        self.addCleanup(setattr, settings, "SETTINGS_FILE", original)
        from app import interface

        self.interface = interface
        with patch.object(interface.VersionScannerApp, "refresh", lambda self: None), \
             patch.object(interface.VersionScannerApp, "check_scripts_periodically", lambda self: None), \
             patch.object(interface.VersionScannerApp, "check_for_update_periodically", lambda self: None), \
             patch.object(interface.VersionScannerApp, "check_jenkins_periodically", lambda self: None):
            self.app = interface.VersionScannerApp()
        self.app.withdraw()
        self.addCleanup(self.app.destroy)

    @staticmethod
    def find(widget, cls):
        found = []
        for child in widget.winfo_children():
            if child.winfo_class() == cls:
                found.append(child)
            found.extend(DialogTests.find(child, cls))
        return found

    def test_login_dialog_returns_typed_credentials(self):
        from app import model_db_dialogs

        def fake_wait(window):
            entries = self.find(window, "TEntry")
            self.assertEqual(str(entries[1].cget("show")), "*")  # senha mascarada
            entries[0].delete(0, "end")
            entries[0].insert(0, "operador")
            entries[1].insert(0, "Senha1")
            [b for b in self.find(window, "TButton") if b.cget("text") == "Continuar"][0].invoke()

        self.app.wait_window = fake_wait
        self.assertEqual(model_db_dialogs.ask_credentials(self.app, "x"), ("operador", "Senha1"))

    def test_login_dialog_cancel_returns_none(self):
        from app import model_db_dialogs

        self.app.wait_window = lambda window: window.destroy()
        self.assertIsNone(model_db_dialogs.ask_credentials(self.app))

    def test_settings_dialog_saves_server_and_database(self):
        from app import model_db_dialogs

        window = model_db_dialogs.open_db_settings(self.app)
        self.app.update()
        fields = self.find(window, "TEntry")
        fields[0].insert(0, "srv-banco")
        fields[1].delete(0, "end")
        fields[1].insert(0, "3307")
        fields[2].insert(0, "modelo")
        save_button = [b for b in self.find(window, "TButton") if b.cget("text") == "Salvar"][0]
        save_button.invoke()

        self.assertEqual(
            settings.load_model_db(),
            {"host": "srv-banco", "port": 3307, "database": "modelo", "username": ""},
        )

    def test_script_window_has_the_database_buttons(self):
        with patch.object(self.interface, "check_scripts") as check:
            check.return_value = ScriptMonitorResult(Path("x"), Path("y"))
            self.app.open_script_monitor_window()
            self.app.update()

        texts = [b.cget("text") for b in self.find(self.app.script_monitor_window, "TButton")]
        self.assertIn("Executar no banco", texts)
        self.assertIn("Banco...", texts)


if __name__ == "__main__":
    unittest.main()
