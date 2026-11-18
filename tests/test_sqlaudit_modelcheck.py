"""Conferencia script a script contra o banco modelo: OK ou "Falta rodar ...". So leitura."""

import tempfile
import threading
import unittest
from pathlib import Path

from app.sqlaudit import db_reader, sql_parse
from app.sqlaudit.model_check import (
    natural_key,
    run_order,
    STATUS_ERROR,
    STATUS_MISSING,
    STATUS_NO_STRUCTURE,
    STATUS_OK,
    ModelCheckError,
    ScriptCheck,
    check_operations,
    run_model_check,
    sql_script_names,
)
from app import settings
from app.sqlaudit.model_check import list_model_databases
from tests.test_settings import SettingsTestCase
from tests.test_sqlaudit_compare import col, db_schema, db_table, fk, idx
from tests.test_sqlaudit_reader import FakeConnection, full_responses

AVAILABLE = sql_parse.is_available()
PASSWORD = "SenhaSecreta#42"


def parsed(**scripts):
    return [(name, sql_parse.parse_text(text)) for name, text in scripts.items()]


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class CheckOperationsTests(unittest.TestCase):
    def check(self, schema, **scripts):
        return {item.script: item for item in check_operations(parsed(**scripts), schema, "BANCO_X")}

    def test_everything_present_is_ok(self):
        schema = db_schema(db_table("T", col("A", "int"), col("B", "varchar(10)")))
        result = self.check(schema, **{"s1.sql": "CREATE TABLE T (A INT, B VARCHAR(10)); ALTER TABLE T ADD COLUMN B VARCHAR(10);"})["s1.sql"]
        self.assertEqual((result.status, result.summary, result.missing), (STATUS_OK, "OK", []))
        self.assertGreaterEqual(result.checked, 3)

    def test_missing_column_is_reported_by_name(self):
        schema = db_schema(db_table("T", col("A", "int")))
        result = self.check(schema, **{"s1.sql": "ALTER TABLE T ADD COLUMN NOVO INT;"})["s1.sql"]
        self.assertEqual(result.status, STATUS_MISSING)
        self.assertEqual(result.summary, "Falta rodar: campo T.NOVO")
        self.assertEqual([(m.kind, m.table, m.name) for m in result.missing], [("campo", "T", "NOVO")])

    def test_a_missing_table_is_one_finding_not_one_per_column(self):
        schema = db_schema(db_table("OUTRA", col("A", "int")))
        result = self.check(schema, **{"s1.sql": "CREATE TABLE T (A INT, B INT, C INT); ALTER TABLE T ADD COLUMN D INT;"})["s1.sql"]
        self.assertEqual([m.text for m in result.missing], ["tabela T"])
        self.assertEqual(result.summary, "Falta rodar: tabela T")

    def test_existing_table_with_missing_parts(self):
        schema = db_schema(db_table("T", col("A", "int")))
        result = self.check(
            schema,
            **{"s1.sql": "CREATE TABLE T (A INT, B INT, KEY IX (A), CONSTRAINT F FOREIGN KEY (A) REFERENCES O (ID), PRIMARY KEY (A));"},
        )["s1.sql"]
        self.assertEqual(
            sorted(m.text for m in result.missing),
            ["campo T.B", "chave estrangeira T.F", "chave primaria T", "indice T.IX"],
        )

    def test_present_index_foreign_key_and_primary_key_count_as_ok(self):
        schema = db_schema(
            db_table("T", col("A", "int"), indexes=[idx("IX", "A"), idx("PRIMARY", "A", unique=True)],
                     foreign_keys=[fk("F", ["A"], "O", ["ID"])])
        )
        result = self.check(
            schema,
            **{"s1.sql": "CREATE TABLE T (A INT, KEY IX (A), CONSTRAINT F FOREIGN KEY (A) REFERENCES O (ID), PRIMARY KEY (A));"},
        )["s1.sql"]
        self.assertEqual(result.status, STATUS_OK)

    def test_unnamed_index_and_foreign_key_are_found_by_their_columns(self):
        schema = db_schema(db_table("T", col("A", "int"), indexes=[idx("T_ibfk_1", "A")], foreign_keys=[fk("T_ibfk_1", ["A"], "O", ["ID"])]))
        ok = self.check(schema, **{"s.sql": "ALTER TABLE T ADD INDEX (A); ALTER TABLE T ADD FOREIGN KEY (A) REFERENCES O (ID);"})["s.sql"]
        self.assertEqual(ok.status, STATUS_OK)
        missing = self.check(schema, **{"s.sql": "ALTER TABLE T ADD INDEX (B);"})["s.sql"]
        self.assertEqual(missing.status, STATUS_MISSING)

    def test_what_another_script_removes_is_not_charged_to_the_script_that_added_it(self):
        schema = db_schema(db_table("T", col("A", "int")))
        results = self.check(
            schema,
            **{"s1.sql": "ALTER TABLE T ADD COLUMN X INT; ALTER TABLE T ADD INDEX IX (A);",
               "s2.sql": "ALTER TABLE T DROP COLUMN X; ALTER TABLE T DROP INDEX IX;"},
        )
        self.assertEqual(results["s1.sql"].status, STATUS_OK, results["s1.sql"].summary)
        self.assertEqual(results["s2.sql"].status, STATUS_OK)  # o que ele remove ja nao existe

    def test_removal_still_pending_is_reported_unless_a_script_recreates_the_object(self):
        schema = db_schema(db_table("T", col("A", "int"), col("X", "int")))
        pending = self.check(schema, **{"s.sql": "ALTER TABLE T DROP COLUMN X;"})["s.sql"]
        self.assertEqual(pending.summary, "Falta rodar: remover campo T.X")
        recreated = self.check(
            schema, **{"s1.sql": "ALTER TABLE T DROP COLUMN X;", "s2.sql": "ALTER TABLE T ADD COLUMN X INT;"}
        )
        self.assertEqual(recreated["s1.sql"].status, STATUS_OK)
        gone = self.check(schema, **{"s.sql": "DROP TABLE V;"})["s.sql"]
        self.assertEqual(gone.status, STATUS_OK)
        still = self.check(db_schema(db_table("V", col("A", "int"))), **{"s.sql": "DROP TABLE V;"})["s.sql"]
        self.assertEqual(still.summary, "Falta rodar: remover tabela V")

    def test_type_difference_is_only_a_note_and_vanishes_when_another_script_changes_the_column(self):
        schema = db_schema(db_table("T", col("A", "varchar(30)")))
        alone = self.check(schema, **{"s.sql": "ALTER TABLE T ADD COLUMN A VARCHAR(20);"})["s.sql"]
        self.assertEqual(alone.status, STATUS_OK)
        self.assertEqual(alone.notes, ["T.A: tipo no script varchar(20), no banco varchar(30)"])
        both = self.check(
            schema, **{"s1.sql": "ALTER TABLE T ADD COLUMN A VARCHAR(20);", "s2.sql": "ALTER TABLE T MODIFY COLUMN A VARCHAR(30);"}
        )
        self.assertEqual(both["s1.sql"].notes, [])
        self.assertEqual(both["s2.sql"].notes, [])

    def test_equivalent_types_do_not_generate_notes(self):
        schema = db_schema(db_table("T", col("A", "int(11)"), col("B", "varchar(05)")))
        result = self.check(schema, **{"s.sql": "ALTER TABLE T ADD COLUMN A INTEGER; ALTER TABLE T ADD COLUMN B VARCHAR(5);"})["s.sql"]
        self.assertEqual(result.notes, [])

    def test_dml_only_and_unsupported_scripts_have_nothing_to_check(self):
        schema = db_schema(db_table("T", col("A", "int")))
        results = self.check(
            schema,
            **{"dados.sql": "INSERT INTO T (A) VALUES (1); UPDATE T SET A = 2;",
               "proc.sql": "CALL ALGO;"},
        )
        self.assertEqual(results["dados.sql"].status, STATUS_NO_STRUCTURE)
        self.assertEqual(results["dados.sql"].summary, "Sem campos para conferir")
        self.assertGreaterEqual(results["dados.sql"].unverifiable, 1)
        self.assertEqual(results["proc.sql"].status, STATUS_NO_STRUCTURE)

    def test_mixed_script_checks_the_structure_and_counts_the_rest_as_unverifiable(self):
        schema = db_schema(db_table("T", col("A", "int")))
        result = self.check(schema, **{"s.sql": "ALTER TABLE T ADD COLUMN B INT; INSERT INTO T (A) VALUES (1);"})["s.sql"]
        self.assertEqual(result.status, STATUS_MISSING)
        self.assertGreaterEqual(result.unverifiable, 1)

    def test_other_database_references_are_not_checked_against_this_one(self):
        schema = db_schema(db_table("T", col("A", "int")))
        result = self.check(schema, **{"s.sql": "ALTER TABLE OUTRO_BANCO.T ADD COLUMN B INT;"})["s.sql"]
        self.assertEqual(result.status, STATUS_NO_STRUCTURE)
        self.assertEqual(result.unverifiable, 1)

    def test_summary_is_shortened_but_keeps_every_item_in_the_list(self):
        schema = db_schema(db_table("T", col("A", "int")))
        columns = ", ".join(f"ADD COLUMN C{n} INT" for n in range(9))
        result = self.check(schema, **{"s.sql": f"ALTER TABLE T {columns};"})["s.sql"]
        self.assertEqual(len(result.missing), 9)
        self.assertTrue(result.summary.endswith("(+4)"), result.summary)
        self.assertEqual(result.summary.count("campo T.C"), 5)

    def test_names_are_compared_ignoring_case(self):
        schema = db_schema(db_table("Cliente", col("Cli_Id", "int")))
        result = self.check(schema, **{"s.sql": "ALTER TABLE CLIENTE ADD COLUMN CLI_ID INT;"})["s.sql"]
        self.assertEqual(result.status, STATUS_OK)

    def test_rename_column_checks_the_new_name(self):
        schema = db_schema(db_table("T", col("NOVO", "int")))
        ok = self.check(schema, **{"s.sql": "ALTER TABLE T RENAME COLUMN VELHO TO NOVO;"})["s.sql"]
        self.assertEqual(ok.status, STATUS_OK)
        missing = self.check(db_schema(db_table("T", col("VELHO", "int"))), **{"s.sql": "ALTER TABLE T RENAME COLUMN VELHO TO NOVO;"})["s.sql"]
        self.assertEqual(missing.summary, "Falta rodar: campo T.NOVO")


WS_677 = (
    'ALTER TABLE CONECTA_MAP_PRODUTOS DROP INDEX UK_CONECTA_MAP_PRODUTOS_04;\n'
    'UPDATE CONECTA_MAP_PRODUTOS SET MAP_PAIPLA = "" WHERE MAP_PAIPLA IS NULL;\n'
    'ALTER TABLE CONECTA_MAP_PRODUTOS MODIFY MAP_PAIPLA VARCHAR(50) NOT NULL DEFAULT "";\n'
    'ALTER TABLE CONECTA_MAP_PRODUTOS ADD UNIQUE KEY UK_CONECTA_MAP_PRODUTOS_04 (MAP_CODAUT, MAP_CODPLA, MAP_PAIPLA);\n'
)
WS_676 = "ALTER TABLE CONECTA_MAP_PRODUTOS ADD COLUMN MAP_PAIPLA VARCHAR(50) DEFAULT NULL;"


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class DependencyTests(unittest.TestCase):
    TABLE = "CONECTA_MAP_PRODUTOS"

    def schema(self, with_column=False, index_columns=None):
        columns = [col("MAP_CODAUT", "int"), col("MAP_CODPLA", "int")]
        if with_column:
            columns.append(col("MAP_PAIPLA", "varchar(50)"))
        indexes = [idx("UK_CONECTA_MAP_PRODUTOS_04", *index_columns, unique=True)] if index_columns else []
        return db_schema(db_table(self.TABLE, *columns, indexes=indexes))

    def check(self, schema, **scripts):
        return {item.script: item for item in check_operations(parsed(**scripts), schema, "BANCO_X")}

    def test_the_missing_column_names_the_script_that_creates_it_and_the_order_follows(self):
        results = self.check(self.schema(), **{"WS_676.sql": WS_676, "WS_677.sql": WS_677})
        ws677 = results["WS_677.sql"]
        self.assertEqual(ws677.status, STATUS_MISSING)
        self.assertEqual(
            [m.full_text for m in ws677.missing],
            [f"campo {self.TABLE}.MAP_PAIPLA [criado por WS_676.sql]",
             f"indice {self.TABLE}.UK_CONECTA_MAP_PRODUTOS_04"],
        )
        self.assertEqual(ws677.depends_on, ["WS_676.sql"])
        self.assertIn("criado por WS_676.sql", ws677.detail)
        self.assertEqual(run_order(list(results.values())), [("WS_676.sql", []), ("WS_677.sql", ["WS_676.sql"])])

    def test_the_script_that_drops_and_recreates_an_index_must_leave_it_in_the_database(self):
        # o proprio 677 remove e recria o indice: o estado final exige o indice, nao e "removido por outro script"
        results = self.check(self.schema(with_column=True), **{"WS_677.sql": WS_677})
        self.assertEqual([m.text for m in results["WS_677.sql"].missing], [f"indice {self.TABLE}.UK_CONECTA_MAP_PRODUTOS_04"])
        self.assertFalse([n for n in results["WS_677.sql"].notes if "removido por outro script" in n])

    def test_everything_in_place_is_ok(self):
        schema = self.schema(with_column=True, index_columns=("MAP_CODAUT", "MAP_CODPLA", "MAP_PAIPLA"))
        results = self.check(schema, **{"WS_676.sql": WS_676, "WS_677.sql": WS_677})
        self.assertEqual({k: v.status for k, v in results.items()}, {"WS_676.sql": STATUS_OK, "WS_677.sql": STATUS_OK})
        self.assertEqual(run_order(list(results.values())), [])

    def test_an_index_with_the_same_name_but_other_columns_is_pending_when_only_one_script_defines_it(self):
        old = self.schema(with_column=True, index_columns=("MAP_CODAUT", "MAP_CODPLA"))
        only = self.check(old, **{"WS_677.sql": WS_677})["WS_677.sql"]
        self.assertEqual(only.status, STATUS_MISSING)
        (missing,) = only.missing
        self.assertIn("no banco: MAP_CODAUT, MAP_CODPLA; no script: MAP_CODAUT, MAP_CODPLA, MAP_PAIPLA", missing.text)
        # se outro script tambem define o indice com outras colunas, a diferenca e historia: nao e cobrada
        older = "ALTER TABLE CONECTA_MAP_PRODUTOS ADD UNIQUE KEY UK_CONECTA_MAP_PRODUTOS_04 (MAP_CODAUT, MAP_CODPLA);"
        both = self.check(old, **{"WS_600.sql": older, "WS_677.sql": WS_677})
        self.assertEqual(both["WS_600.sql"].status, STATUS_OK)

    def test_modify_does_not_count_as_the_creator_of_a_column(self):
        schema = self.schema()
        results = self.check(
            schema, **{"a.sql": "ALTER TABLE CONECTA_MAP_PRODUTOS MODIFY MAP_PAIPLA VARCHAR(10);",
                       "b.sql": "ALTER TABLE CONECTA_MAP_PRODUTOS MODIFY MAP_PAIPLA VARCHAR(20);"},
        )
        self.assertEqual(results["a.sql"].depends_on, [])

    def test_order_puts_providers_first_keeps_numeric_name_order_and_survives_cycles(self):
        schema = db_schema(db_table("T", col("A", "int")))
        results = self.check(
            schema,
            **{"s_11.sql": "ALTER TABLE T ADD COLUMN X INT;",
               "s_2.sql": "ALTER TABLE T ADD COLUMN Y INT;",
               "s_3.sql": "ALTER TABLE T ADD COLUMN Z INT; ALTER TABLE T MODIFY X INT NOT NULL;"},
        )
        order = [name for name, _ in run_order(list(results.values()))]
        self.assertEqual(order, ["s_2.sql", "s_11.sql", "s_3.sql"])  # s_3 precisa do X criado em s_11
        a = ScriptCheck("a.sql", STATUS_MISSING, missing=[__import__("app.sqlaudit.model_check", fromlist=["Missing"]).Missing("campo", "T", "X", providers=("b.sql",))])
        b = ScriptCheck("b.sql", STATUS_MISSING, missing=[__import__("app.sqlaudit.model_check", fromlist=["Missing"]).Missing("campo", "T", "Y", providers=("a.sql",))])
        cyclic = [name for name, _ in run_order([a, b])]
        self.assertEqual(sorted(cyclic), ["a.sql", "b.sql"])  # nao trava

    def test_natural_key(self):
        self.assertEqual(sorted(["GER_11", "GER_2", "ger_1"], key=natural_key), ["ger_1", "GER_2", "GER_11"])


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class RunModelCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)

    def write(self, relative, text, binary=False):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if binary else text.encode("utf-8"))

    def responses(self, database="BANCO_MODELO"):
        result = full_responses(database)
        return result

    def run_check(self, names=None, responses=None, database="BANCO_MODELO", **kwargs):
        seen = []

        def connector(**options):
            seen.append(options)
            return FakeConnection(responses or self.responses(database))

        run = run_model_check(
            self.folder, names, "10.0.0.35", 43309, database, "leitura", PASSWORD, connector=connector, **kwargs
        )
        return run, seen

    def test_scans_subfolders_checks_each_script_and_reads_the_model_schema_read_only(self):
        self.write("0_GERAL/SCRIPT_GER_1.sql", "ALTER TABLE CLIENTES ADD COLUMN CLI_EMAIL VARCHAR(200);")
        self.write("2_WEB/SCRIPT_WS_2.sql", "ALTER TABLE CLIENTES ADD COLUMN CLI_FALTANDO INT;")
        self.write("leia-me.txt", "nao e script")
        run, seen = self.run_check()
        by_name = {item.script: item for item in run.results}
        self.assertEqual(sorted(by_name), ["0_GERAL\\SCRIPT_GER_1.sql".replace("\\", __import__("os").sep), "2_WEB" + __import__("os").sep + "SCRIPT_WS_2.sql"])
        statuses = {name.split(__import__("os").sep)[-1]: item.status for name, item in by_name.items()}
        self.assertEqual(statuses, {"SCRIPT_GER_1.sql": STATUS_OK, "SCRIPT_WS_2.sql": STATUS_MISSING})
        self.assertEqual(run.database, "BANCO_MODELO")
        self.assertGreaterEqual(run.tables, 2)
        self.assertEqual(run.counts(), {STATUS_OK: 1, STATUS_MISSING: 1})
        self.assertEqual(seen[0]["database"], "BANCO_MODELO")
        executed = [text for text, _params in FakeConnection().executed]
        self.assertEqual(executed, [])

    def test_names_filter_and_order_are_kept(self):
        self.write("b.sql", "ALTER TABLE CLIENTES ADD COLUMN X INT;")
        self.write("a.sql", "ALTER TABLE CLIENTES ADD COLUMN CLI_EMAIL VARCHAR(200);")
        run, _ = self.run_check(names=["b.sql", "a.sql"])
        self.assertEqual([item.script for item in run.results], ["b.sql", "a.sql"])
        only, _ = self.run_check(names=["a.sql"])
        self.assertEqual([item.script for item in only.results], ["a.sql"])
        self.assertEqual(sql_script_names(self.folder), ["a.sql", "b.sql"])

    def test_unreadable_script_is_an_error_row_not_a_failure_of_the_whole_run(self):
        self.write("ok.sql", "ALTER TABLE CLIENTES ADD COLUMN CLI_EMAIL VARCHAR(200);")
        self.write("binario.sql", b"\x00\x01\x02\x00\x03", binary=True)
        run, _ = self.run_check()
        by_name = {item.script: item for item in run.results}
        self.assertEqual(by_name["binario.sql"].status, STATUS_ERROR)
        self.assertIn("binario.sql", by_name["binario.sql"].summary)
        self.assertEqual(by_name["ok.sql"].status, STATUS_OK)

    def test_errors_are_messages_without_the_password(self):
        with self.assertRaises(ModelCheckError):
            run_model_check(self.folder / "nao-existe", None, "h", 1, "B", "u", PASSWORD)
        with self.assertRaises(ModelCheckError) as empty:
            run_model_check(self.folder, None, "h", 1, "B", "u", PASSWORD)
        self.assertIn("nenhum script", str(empty.exception).lower())
        self.write("a.sql", "ALTER TABLE CLIENTES ADD COLUMN X INT;")

        def refuse(**options):
            raise Exception(1045, f"Access denied for user 'u' (using password: YES) {options['password']}")

        with self.assertRaises(ModelCheckError) as refused:
            run_model_check(self.folder, None, "h", 1, "B", "u", PASSWORD, connector=refuse)
        self.assertNotIn(PASSWORD, str(refused.exception))

    def test_empty_visible_schema_and_wrong_database_are_explained(self):
        self.write("a.sql", "ALTER TABLE CLIENTES ADD COLUMN X INT;")
        empty = self.responses()
        empty[db_reader.SQL_TABLES] = []
        empty[db_reader.SQL_COLUMNS] = []
        with self.assertRaises(ModelCheckError) as error:
            self.run_check(responses=empty)
        self.assertIn("Nenhuma tabela visivel", str(error.exception))
        with self.assertRaises(ModelCheckError):
            self.run_check(responses=self.responses("OUTRO_BANCO"))

    def test_cancel_stops_before_touching_the_database(self):
        self.write("a.sql", "ALTER TABLE CLIENTES ADD COLUMN X INT;")
        cancel = threading.Event()
        cancel.set()
        calls = []
        run = run_model_check(
            self.folder, None, "h", 1, "B", "u", PASSWORD, connector=lambda **o: calls.append(o), cancel_event=cancel
        )
        self.assertTrue(run.cancelled)
        self.assertEqual(calls, [])

    def test_password_is_not_in_the_results(self):
        self.write("a.sql", "ALTER TABLE CLIENTES ADD COLUMN X INT;")
        run, _ = self.run_check()
        self.assertNotIn(PASSWORD, repr(run))

    def test_phases_are_announced_in_order_before_the_long_steps(self):
        self.write("a.sql", "SELECT 1;")
        phases = []
        self.run_check(on_phase=phases.append)
        self.assertEqual(phases, ["reading_database", "comparing"])

    def test_progress_is_reported_per_script(self):
        self.write("a.sql", "SELECT 1;")
        self.write("b.sql", "SELECT 2;")
        seen = []
        self.run_check(progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(1, 2), (2, 2)])


class ListModelDatabasesTests(unittest.TestCase):
    def connector(self, names):
        responses = {db_reader.SQL_DATABASES: [(name,) for name in names]}
        return lambda **options: FakeConnection(responses)

    def test_lists_what_the_user_sees_without_the_internal_databases(self):
        ok, names, text = list_model_databases(
            "h", 3306, "u", PASSWORD, connector=self.connector(["information_schema", "BANCO_MODELO", "mysql", "sys"])
        )
        self.assertEqual((ok, names), (True, ["BANCO_MODELO"]))
        self.assertIn("1 banco(s)", text)

    def test_connects_to_the_server_only_never_to_a_database(self):
        seen = []

        def connector(**options):
            seen.append(options)
            return FakeConnection({db_reader.SQL_DATABASES: [("BANCO_MODELO",)]})

        list_model_databases("h", 3306, "u", PASSWORD, connector=connector)
        self.assertNotIn("database", seen[0])

    def test_no_visible_database_and_failures_explain_without_the_password(self):
        ok, names, text = list_model_databases("h", 3306, "u", PASSWORD, connector=self.connector(["information_schema"]))
        self.assertEqual((ok, names), (False, []))
        self.assertIn("nenhum banco visivel", text.lower())

        def refuse(**options):
            raise Exception(1045, f"Access denied (using password: YES) {options['password']}")

        ok, names, text = list_model_databases("h", 3306, "u", PASSWORD, connector=refuse)
        self.assertFalse(ok)
        self.assertNotIn(PASSWORD, text)


class ModelCheckDirectorySettingsTests(SettingsTestCase):
    def test_defaults_to_the_script_monitor_folder_and_remembers_the_choice(self):
        self.assertEqual(settings.load_model_check_directory(), settings.load_script_directory())
        settings.save_model_check_directory("C:/outra/pasta")
        self.assertEqual(str(settings.load_model_check_directory()), str(Path("C:/outra/pasta")))
        self.assertNotEqual(settings.load_script_directory(), settings.load_model_check_directory())  # nao mexe na pasta do monitor
        settings.save_model_check_directory("")
        self.assertEqual(settings.load_model_check_directory(), settings.load_script_directory())


if __name__ == "__main__":
    unittest.main()
