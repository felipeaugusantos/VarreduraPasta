import tempfile
import threading
import unittest
from pathlib import Path

from app.sqlaudit import sql_parse
from app.sqlaudit.expected_model import (
    OUTCOME_APPLIED,
    OUTCOME_CONFLICT,
    OUTCOME_ERROR,
    OUTCOME_IGNORED,
    OUTCOME_UNSUPPORTED,
    ScriptOperations,
    build_expected_model,
    load_script_operations,
)
from app.sqlaudit.file_scanner import scan_sql_files
from app.sqlaudit.version_tree import VersionTree, effective_scripts

AVAILABLE = sql_parse.is_available()
BASE = "48.2.30.74.150"
CHILD = "48.2.30.74.150.45"


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class ModelTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def model_for(self, layout, target, tree=None, **kwargs):
        """layout: {versao: {nome: sql}}. Retorna (modelo, efetivos)."""
        scripts = {}
        for version, files in layout.items():
            folder = self.root / version / "SCRIPTS"
            folder.mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                (folder / name).write_text(content, encoding="utf-8")
            scripts[version] = scan_sql_files(folder).files
        tree = tree or VersionTree.build(list(layout))
        effective = effective_scripts(tree, scripts, target)
        return build_expected_model(effective, **kwargs), effective

    def single(self, **scripts):
        return self.model_for({"1.1": scripts}, "1.1")[0]


class ReplayTests(ModelTestCase):
    def test_modify_after_add_supersedes_the_earlier_definition(self):
        model = self.single(
            **{
                "SCRIPT_1.SQL": "ALTER TABLE CLIENTES\nADD COLUMN CLI_EMAIL VARCHAR(100);",
                "SCRIPT_2.SQL": "ALTER TABLE CLIENTES\nMODIFY COLUMN CLI_EMAIL VARCHAR(200);",
            }
        )
        column = model.tables["clientes"].columns["cli_email"]
        self.assertEqual(column.spec.data_type, "varchar(200)")
        self.assertEqual((column.provenance.script, column.provenance.number, column.provenance.line), ("SCRIPT_2.SQL", 2, 1))
        first, second = model.applied
        self.assertEqual(first.provenance.script, "SCRIPT_1.SQL")
        self.assertEqual(first.superseded_by.script, "SCRIPT_2.SQL")
        self.assertIsNone(second.superseded_by)
        self.assertFalse(column.origin_unknown)

    def test_replay_follows_numeric_order_not_text_order(self):
        model = self.single(
            **{
                "SCRIPT_999.SQL": "ALTER TABLE t ADD a VARCHAR(10);",
                "SCRIPT_1000.SQL": "ALTER TABLE t MODIFY a VARCHAR(20);",
            }
        )
        self.assertEqual(model.tables["t"].columns["a"].spec.data_type, "varchar(20)")

    def test_create_table_then_alter(self):
        model = self.single(
            **{
                "SCRIPT_1.SQL": "CREATE TABLE t (id INT NOT NULL, n VARCHAR(10)) ENGINE=InnoDB;",
                "SCRIPT_2.SQL": "ALTER TABLE t ADD COLUMN extra TEXT, MODIFY n VARCHAR(50);",
            }
        )
        table = model.tables["t"]
        self.assertEqual(list(table.columns), ["id", "n", "extra"])
        self.assertEqual(table.columns["n"].spec.data_type, "varchar(50)")
        self.assertEqual(table.options, {"engine": "innodb"})
        self.assertTrue(table.fully_known)
        self.assertFalse(table.origin_unknown)

    def test_table_and_column_names_are_case_insensitive_in_the_model(self):
        model = self.single(
            **{"SCRIPT_1.SQL": "ALTER TABLE Clientes ADD Cli_Email INT;", "SCRIPT_2.SQL": "ALTER TABLE CLIENTES MODIFY CLI_EMAIL BIGINT;"}
        )
        self.assertEqual(model.tables["clientes"].columns["cli_email"].spec.data_type, "bigint")

    def test_table_never_created_by_scripts_comes_from_the_base_database(self):
        model = self.single(**{"SCRIPT_1.SQL": "ALTER TABLE antiga ADD a INT; ALTER TABLE antiga MODIFY b TEXT;"})
        table = model.tables["antiga"]
        self.assertTrue(table.origin_unknown)
        self.assertFalse(table.fully_known)
        self.assertTrue(table.columns["b"].origin_unknown)
        self.assertEqual(model.findings, [])

    def test_create_table_if_not_exists_on_existing_table_is_ignored(self):
        model = self.single(
            **{"SCRIPT_1.SQL": "CREATE TABLE t (a INT);", "SCRIPT_2.SQL": "CREATE TABLE IF NOT EXISTS t (a BIGINT, b INT);"}
        )
        self.assertEqual(list(model.tables["t"].columns), ["a"])
        self.assertEqual(model.tables["t"].columns["a"].spec.data_type, "int")
        self.assertIn(OUTCOME_IGNORED, model.counts())
        self.assertEqual(model.findings, [])

    def test_repeated_create_and_repeated_add_are_conflicts_that_keep_the_state(self):
        model = self.single(
            **{
                "SCRIPT_1.SQL": "CREATE TABLE t (a INT);",
                "SCRIPT_2.SQL": "CREATE TABLE t (a BIGINT);",
                "SCRIPT_3.SQL": "ALTER TABLE t ADD a TEXT;",
            }
        )
        self.assertEqual(model.tables["t"].columns["a"].spec.data_type, "int")
        self.assertEqual([f.kind for f in model.findings], ["tabela_repetida", "coluna_repetida"])
        self.assertEqual(model.counts()[OUTCOME_CONFLICT], 2)

    def test_modify_of_unknown_column_in_a_fully_known_table_is_a_conflict(self):
        model = self.single(**{"SCRIPT_1.SQL": "CREATE TABLE t (a INT);", "SCRIPT_2.SQL": "ALTER TABLE t MODIFY zz INT;"})
        self.assertNotIn("zz", model.tables["t"].columns)
        self.assertEqual([f.kind for f in model.findings], ["coluna_inexistente"])


class WhatTheModelCannotFollowTests(ModelTestCase):
    def test_unsupported_action_marks_the_table_incomplete_but_keeps_the_rest(self):
        model = self.single(
            **{"SCRIPT_1.SQL": "CREATE TABLE t (a INT, b INT);", "SCRIPT_2.SQL": "ALTER TABLE t ADD c INT, DROP COLUMN b;"}
        )
        table = model.tables["t"]
        self.assertIn("c", table.columns)
        self.assertIn("b", table.columns)  # o modelo nao sabe que saiu: por isso a tabela fica incompleta
        self.assertFalse(table.fully_known)
        self.assertEqual(len(table.incomplete), 1)
        self.assertEqual(table.incomplete[0].outcome, OUTCOME_UNSUPPORTED)

    def test_index_and_key_actions_do_not_make_the_table_incomplete(self):
        model = self.single(
            **{
                "SCRIPT_1.SQL": "CREATE TABLE t (a INT, b INT);",
                "SCRIPT_2.SQL": "ALTER TABLE t ADD INDEX ix (a); ALTER TABLE t DROP INDEX ix; ALTER TABLE t ADD PRIMARY KEY (a);",
            }
        )
        table = model.tables["t"]
        self.assertTrue(table.fully_known)
        self.assertEqual(len(table.unchecked_elements), 3)

    def test_rename_and_column_actions_make_the_table_incomplete(self):
        for action in ("DROP COLUMN b", "CHANGE COLUMN b c INT", "RENAME COLUMN b TO c", "RENAME TO u", "ALTER COLUMN b SET DEFAULT 1"):
            model = self.single(**{"SCRIPT_1.SQL": f"CREATE TABLE t (a INT, b INT); ALTER TABLE t {action};"})
            self.assertFalse(model.tables["t"].fully_known, action)

    def test_keys_and_options_of_create_table_are_kept_as_unchecked_elements(self):
        model = self.single(**{"SCRIPT_1.SQL": "CREATE TABLE t (id INT, PRIMARY KEY (id)) ENGINE=InnoDB AUTO_INCREMENT=5;"})
        table = model.tables["t"]
        self.assertEqual(len(table.unchecked_elements), 2)
        self.assertTrue(table.fully_known)

    def test_dropped_table_is_marked_instead_of_trusted(self):
        model = self.single(**{"SCRIPT_1.SQL": "CREATE TABLE t (a INT); DROP TABLE t;"})
        self.assertFalse(model.tables["t"].fully_known)
        self.assertEqual(model.unresolved, [])

    def test_statement_level_unsupported_goes_to_unresolved(self):
        model = self.single(**{"SCRIPT_1.SQL": "INSERT INTO t VALUES (1); FOO BAR; DROP VIEW v;"})
        self.assertEqual(len(model.unresolved), 3)
        self.assertEqual(model.tables, {})
        self.assertTrue(all(item.outcome in (OUTCOME_UNSUPPORTED, OUTCOME_ERROR) for item in model.unresolved))

    def test_unreadable_script_is_recorded_without_stopping_the_replay(self):
        folder = self.root / "1.1" / "SCRIPTS"
        folder.mkdir(parents=True)
        (folder / "SCRIPT_1.SQL").write_bytes("SELECT 1;".encode("utf-16-le"))
        (folder / "SCRIPT_2.SQL").write_text("ALTER TABLE t ADD a INT;", encoding="utf-8")
        scripts = {"1.1": scan_sql_files(folder).files}
        effective = effective_scripts(VersionTree.build(["1.1"]), scripts, "1.1")
        model = build_expected_model(effective)
        self.assertIn("a", model.tables["t"].columns)
        self.assertEqual(model.unresolved[0].outcome, OUTCOME_ERROR)
        self.assertIn("bytes nulos", model.unresolved[0].note)


class InheritanceTests(ModelTestCase):
    LAYOUT = {
        BASE: {
            "SCRIPT_100.SQL": "CREATE TABLE t (a INT);",
            "SCRIPT_101.SQL": "ALTER TABLE t ADD b INT;",
            "SCRIPT_102.SQL": "ALTER TABLE t ADD c INT;",
        },
        CHILD: {"SCRIPT_103.SQL": "ALTER TABLE t MODIFY b BIGINT;"},
    }

    def tree(self, fork):
        tree = VersionTree.build([BASE, CHILD])
        tree.set_base(CHILD, BASE, fork)
        return tree

    def test_child_inherits_the_base_up_to_the_fork(self):
        model, _ = self.model_for(self.LAYOUT, CHILD, self.tree(101))
        columns = model.tables["t"].columns
        self.assertEqual(list(columns), ["a", "b"])
        self.assertEqual(columns["b"].spec.data_type, "bigint")
        self.assertTrue(all(state.provenance.certain for state in columns.values()))
        self.assertEqual(columns["a"].provenance.version, BASE)

    def test_without_fork_inherited_definitions_are_uncertain(self):
        model, _ = self.model_for(self.LAYOUT, CHILD, self.tree(None))
        columns = model.tables["t"].columns
        self.assertEqual(list(columns), ["a", "b", "c"])
        self.assertFalse(columns["a"].provenance.certain)
        self.assertFalse(columns["c"].provenance.certain)
        self.assertTrue(columns["b"].provenance.certain)  # redefinida por script da propria versao

    def test_base_model_is_not_affected_by_the_child(self):
        model, _ = self.model_for(self.LAYOUT, BASE, self.tree(101))
        self.assertEqual(list(model.tables["t"].columns), ["a", "b", "c"])
        self.assertEqual(model.tables["t"].columns["b"].spec.data_type, "int")


class RunControlTests(ModelTestCase):
    def test_same_content_is_interpreted_once_across_versions(self):
        calls = []

        def loader(script):
            calls.append(script.relative)
            return load_script_operations(script)

        layout = {BASE: {"SCRIPT_1.SQL": "ALTER TABLE t ADD a INT;"}, CHILD: {"SCRIPT_1.SQL": "ALTER TABLE t ADD a INT;"}}
        tree = VersionTree.build([BASE, CHILD])
        model, effective = self.model_for(layout, CHILD, tree, loader=loader)
        self.assertEqual(len(effective.scripts), 1)  # a copia identica nem entra duas vezes
        self.assertEqual(len(calls), 1)

        calls.clear()
        cache = {}
        layout = {BASE: {"SCRIPT_1.SQL": "ALTER TABLE t ADD a INT;"}, CHILD: {"SCRIPT_2.SQL": "ALTER TABLE t ADD a INT;"}}
        self.model_for(layout, CHILD, tree, loader=loader, cache=cache)
        self.assertEqual(len(calls), 1)  # SHA-256 igual com numero diferente: um parse so
        self.assertEqual(len(cache), 1)

    def test_cancel_stops_before_the_next_script_and_progress_is_reported(self):
        cancel = threading.Event()
        progress = []

        def on_progress(done, total):
            progress.append((done, total))
            cancel.set()

        model, _ = self.model_for(
            {"1.1": {"SCRIPT_1.SQL": "ALTER TABLE t ADD a INT;", "SCRIPT_2.SQL": "ALTER TABLE t ADD b INT;"}},
            "1.1",
            cancel_event=cancel,
            progress=on_progress,
        )
        self.assertEqual(progress, [(1, 2)])
        self.assertEqual(list(model.tables["t"].columns), ["a"])

    def test_loader_errors_never_raise(self):
        model, _ = self.model_for(
            {"1.1": {"SCRIPT_1.SQL": "x"}}, "1.1", loader=lambda script: ScriptOperations(error="falhou")
        )
        self.assertEqual(model.unresolved[0].outcome, OUTCOME_ERROR)

    def test_applied_log_covers_every_operation(self):
        model = self.single(**{"SCRIPT_1.SQL": "CREATE TABLE t (a INT); ALTER TABLE t ADD b INT; DROP TABLE x;"})
        self.assertEqual(model.counts()[OUTCOME_APPLIED], 3)  # CREATE + coluna do CREATE + ADD
        self.assertEqual(model.counts()[OUTCOME_UNSUPPORTED], 1)


if __name__ == "__main__":
    unittest.main()
