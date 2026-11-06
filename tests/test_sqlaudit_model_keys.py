"""Fase 6 do modelo esperado: replay de DROP/CHANGE/RENAME, indices, chaves e charset."""

import tempfile
import unittest
from pathlib import Path

from app.sqlaudit import sql_parse
from app.sqlaudit.expected_model import OUTCOME_APPLIED, OUTCOME_CONFLICT, build_expected_model
from app.sqlaudit.file_scanner import scan_sql_files
from app.sqlaudit.version_tree import VersionTree, effective_scripts

AVAILABLE = sql_parse.is_available()


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class KeyModelTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def model(self, *scripts):
        folder = self.root / "1.1" / "SCRIPTS"
        folder.mkdir(parents=True, exist_ok=True)
        for number, text in enumerate(scripts, start=1):
            (folder / f"SCRIPT_{number}.SQL").write_bytes(text.encode("utf-8"))
        found = {"1.1": scan_sql_files(folder).files}
        return build_expected_model(effective_scripts(VersionTree.build(["1.1"]), found, "1.1"))

    @staticmethod
    def entries(model, kind):
        return [a for a in model.applied if a.operation.kind == kind]


class DropAndChangeTests(KeyModelTestCase):
    def test_drop_column_removes_it_supersedes_its_definition_and_expects_it_absent(self):
        model = self.model("CREATE TABLE t (a INT, b INT);", "ALTER TABLE t DROP COLUMN b;")
        table = model.tables["t"]
        self.assertEqual(list(table.columns), ["a"])
        self.assertEqual(table.dropped_columns["b"].provenance.script, "SCRIPT_2.SQL")
        defined = next(a for a in model.applied if a.target == ("coluna", "t", "b") and a.operation.kind == "create_table")
        self.assertEqual(defined.superseded_by.script, "SCRIPT_2.SQL")
        self.assertTrue(table.fully_known)

    def test_readding_a_dropped_column_supersedes_the_drop(self):
        model = self.model("CREATE TABLE t (a INT, b INT);", "ALTER TABLE t DROP COLUMN b;", "ALTER TABLE t ADD b TEXT;")
        table = model.tables["t"]
        self.assertEqual(table.columns["b"].spec.data_type, "text")
        self.assertNotIn("b", table.dropped_columns)
        self.assertEqual(self.entries(model, "drop_column")[0].superseded_by.script, "SCRIPT_3.SQL")

    def test_drop_of_unknown_column_is_a_conflict_in_a_known_table_but_not_in_a_base_table(self):
        known = self.model("CREATE TABLE t (a INT);", "ALTER TABLE t DROP COLUMN zz;")
        self.assertEqual([f.kind for f in known.findings], ["coluna_inexistente"])
        self.assertEqual(self.entries(known, "drop_column")[0].outcome, OUTCOME_CONFLICT)
        base = self.model("ALTER TABLE antiga DROP COLUMN zz;")
        self.assertEqual(base.findings, [])
        self.assertTrue(base.tables["antiga"].dropped_columns["zz"].origin_unknown)

    def test_drop_twice_is_a_conflict(self):
        model = self.model("CREATE TABLE t (a INT, b INT);", "ALTER TABLE t DROP COLUMN b;", "ALTER TABLE t DROP COLUMN b;")
        self.assertEqual([f.kind for f in model.findings], ["coluna_inexistente"])

    def test_change_column_renames_and_redefines_and_keys_follow(self):
        model = self.model(
            "CREATE TABLE t (a INT NOT NULL, b INT, PRIMARY KEY (a), KEY ix (a, b), "
            "CONSTRAINT fk FOREIGN KEY (a) REFERENCES o (id));",
            "ALTER TABLE t CHANGE COLUMN a a2 BIGINT NOT NULL;",
        )
        table = model.tables["t"]
        self.assertEqual(list(table.columns), ["b", "a2"])
        self.assertEqual(table.columns["a2"].spec.data_type, "bigint")
        self.assertEqual(table.primary_key.spec.columns, ("a2",))
        self.assertEqual(table.indexes["ix"].spec.columns, ("a2", "b"))
        self.assertEqual(table.foreign_keys["fk"].spec.columns, ("a2",))
        self.assertIn("a", table.dropped_columns)  # o nome antigo nao pode existir
        self.assertEqual(table.implicit_not_null, {"a2"})

    def test_change_column_without_rename_is_a_plain_redefinition(self):
        model = self.model("CREATE TABLE t (a INT);", "ALTER TABLE t CHANGE COLUMN a a BIGINT;")
        table = model.tables["t"]
        self.assertEqual(table.columns["a"].spec.data_type, "bigint")
        self.assertEqual(table.dropped_columns, {})

    def test_change_to_an_existing_name_is_a_conflict_and_changes_nothing(self):
        model = self.model("CREATE TABLE t (a INT, b INT);", "ALTER TABLE t CHANGE COLUMN a b BIGINT;")
        self.assertEqual([f.kind for f in model.findings], ["coluna_repetida"])
        self.assertEqual(model.tables["t"].columns["a"].spec.data_type, "int")

    def test_rename_column_keeps_the_definition_and_retargets_its_operation(self):
        model = self.model("CREATE TABLE t (a VARCHAR(10), b INT);", "ALTER TABLE t RENAME COLUMN a TO c;")
        table = model.tables["t"]
        self.assertEqual((table.columns["c"].spec.name, table.columns["c"].spec.data_type), ("c", "varchar(10)"))
        defined = next(a for a in model.applied if a.operation.column and a.operation.column.name == "a")
        self.assertEqual(defined.target, ("coluna", "t", "c"))
        self.assertIn("a", table.dropped_columns)

    def test_drop_column_leaves_multicolumn_indexes_and_drops_empty_ones(self):
        model = self.model(
            "CREATE TABLE t (a INT, b INT, c INT, KEY ix (a, b(5), c), KEY solo (b));",
            "ALTER TABLE t DROP COLUMN b;",
        )
        table = model.tables["t"]
        self.assertEqual(table.indexes["ix"].spec.columns, ("a", "c"))
        self.assertEqual(table.indexes["ix"].spec.prefixes, (None, None))
        self.assertNotIn("solo", table.indexes)

    def test_dropping_the_pk_column_removes_the_pk_but_the_other_columns_stay_not_null(self):
        model = self.model("CREATE TABLE t (a INT, b INT, PRIMARY KEY (a, b));", "ALTER TABLE t DROP COLUMN a;")
        table = model.tables["t"]
        self.assertEqual(table.primary_key.spec.columns, ("b",))
        self.assertEqual(table.implicit_not_null, {"b"})


class TableTests(KeyModelTestCase):
    def test_drop_table_supersedes_everything_and_expects_absence(self):
        model = self.model("CREATE TABLE t (a INT);", "ALTER TABLE t ADD b INT;", "DROP TABLE t;")
        self.assertEqual(model.tables, {})
        self.assertIn("t", model.absent_tables)
        live = [a for a in model.applied if a.target and a.target[1] == "t" and a.superseded_by is None]
        self.assertEqual([a.operation.kind for a in live], ["drop_table"])

    def test_recreating_a_dropped_table_supersedes_the_drop(self):
        model = self.model("CREATE TABLE t (a INT);", "DROP TABLE t;", "CREATE TABLE t (a BIGINT);")
        self.assertIn("t", model.tables)
        self.assertNotIn("t", model.absent_tables)
        self.assertEqual(self.entries(model, "drop_table")[0].superseded_by.script, "SCRIPT_3.SQL")
        self.assertEqual(model.tables["t"].columns["a"].spec.data_type, "bigint")

    def test_drop_unknown_table_is_expected_absent_and_if_exists_twice_is_fine(self):
        model = self.model("DROP TABLE IF EXISTS fantasma;", "DROP TABLE IF EXISTS fantasma;")
        self.assertEqual(model.findings, [])
        self.assertTrue(model.absent_tables["fantasma"].origin_unknown)
        twice = self.model("CREATE TABLE t (a INT);", "DROP TABLE t;", "DROP TABLE t;")
        self.assertEqual([f.kind for f in twice.findings], ["tabela_inexistente"])

    def test_rename_table_moves_the_state_retargets_operations_and_expects_the_old_name_absent(self):
        model = self.model("CREATE TABLE t (a INT, KEY ix (a));", "ALTER TABLE t RENAME TO u;", "RENAME TABLE u TO v;")
        self.assertEqual(list(model.tables), ["v"])
        self.assertEqual(model.tables["v"].name, "v")
        self.assertEqual(set(model.absent_tables), {"t", "u"})
        live = {a.target for a in model.applied if a.target and a.superseded_by is None}
        self.assertTrue(all(target[1] == "v" for target in live))
        self.assertIn(("coluna", "v", "a"), live)
        self.assertIn(("indice", "v", "ix"), live)

    def test_changing_a_table_that_was_dropped_or_renamed_away_is_a_conflict(self):
        for first in ("DROP TABLE t;", "RENAME TABLE t TO u;"):
            model = self.model(f"CREATE TABLE t (a INT); {first}", "ALTER TABLE t ADD b INT;")
            self.assertEqual([f.kind for f in model.findings], ["tabela_inexistente"], first)
            self.assertNotIn("t", model.tables)  # nao inventa uma tabela do banco-base

    def test_renaming_into_a_dropped_name_recreates_it(self):
        model = self.model("CREATE TABLE t (a INT); CREATE TABLE u (a INT);", "DROP TABLE u;", "RENAME TABLE t TO u;")
        self.assertIn("u", model.tables)
        self.assertNotIn("u", model.absent_tables)
        self.assertEqual(self.entries(model, "drop_table")[0].superseded_by.script, "SCRIPT_3.SQL")

    def test_rename_to_an_existing_table_is_a_conflict(self):
        model = self.model("CREATE TABLE t (a INT); CREATE TABLE u (a INT);", "ALTER TABLE t RENAME TO u;")
        self.assertEqual([f.kind for f in model.findings], ["tabela_repetida"])
        self.assertEqual(set(model.tables), {"t", "u"})

    def test_renaming_a_base_table_makes_the_new_one_incomplete(self):
        model = self.model("ALTER TABLE antiga RENAME TO nova;")
        table = model.tables["nova"]
        self.assertFalse(table.fully_known)  # as colunas dela nao sao conhecidas
        self.assertIn("antiga", model.absent_tables)

    def test_alter_options_update_engine_charset_and_collation(self):
        model = self.model(
            "CREATE TABLE t (a INT) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_bin;",
            "ALTER TABLE t ENGINE=MyISAM;",
            "ALTER TABLE t DEFAULT CHARSET=utf8mb4 COLLATE utf8mb4_bin;",
        )
        self.assertEqual(model.tables["t"].options, {"engine": "myisam", "charset": "utf8mb4", "collation": "utf8mb4_bin"})

    def test_convert_charset_changes_only_text_columns_and_resets_the_collation(self):
        model = self.model(
            "CREATE TABLE t (n VARCHAR(10) CHARACTER SET latin1 COLLATE latin1_bin, i INT, e ENUM('a'), x TEXT) DEFAULT CHARSET=latin1 COLLATE=latin1_bin;",
            "ALTER TABLE t CONVERT TO CHARACTER SET utf8mb4;",
        )
        table = model.tables["t"]
        self.assertEqual(table.options, {"charset": "utf8mb4"})
        self.assertEqual((table.columns["n"].spec.charset, table.columns["n"].spec.collation), ("utf8mb4", None))
        self.assertEqual(table.columns["x"].spec.charset, "utf8mb4")
        self.assertEqual(table.columns["e"].spec.charset, "utf8mb4")
        self.assertIsNone(table.columns["i"].spec.charset)

    def test_convert_charset_on_a_base_table_makes_it_incomplete(self):
        model = self.model("ALTER TABLE antiga CONVERT TO CHARACTER SET utf8mb4;")
        self.assertFalse(model.tables["antiga"].fully_known)


class IndexAndKeyReplayTests(KeyModelTestCase):
    def test_automatic_index_names_follow_the_mysql_rule(self):
        model = self.model("CREATE TABLE t (a INT, b INT);", "ALTER TABLE t ADD INDEX (a); ALTER TABLE t ADD INDEX (a, b); ALTER TABLE t ADD UNIQUE (b);")
        table = model.tables["t"]
        self.assertEqual(list(table.indexes), ["a", "a_2", "b"])
        self.assertTrue(all(table.indexes[name].auto_named for name in table.indexes))
        self.assertTrue(table.indexes["b"].spec.unique)

    def test_inline_primary_key_and_unique_become_keys_with_their_own_operations(self):
        model = self.model("CREATE TABLE t (id INT PRIMARY KEY, code INT UNIQUE);")
        table = model.tables["t"]
        self.assertEqual(table.primary_key.spec.columns, ("id",))
        self.assertEqual(list(table.indexes), ["code"])
        targets = {a.target for a in model.applied}
        self.assertIn(("chave_primaria", "t", "primary"), targets)
        self.assertIn(("indice", "t", "code"), targets)
        self.assertEqual(table.implicit_not_null, {"id"})

    def test_add_column_with_inline_unique(self):
        model = self.model("CREATE TABLE t (a INT);", "ALTER TABLE t ADD COLUMN b INT UNIQUE;")
        self.assertEqual(list(model.tables["t"].indexes), ["b"])

    def test_duplicate_index_pk_and_fk_names_are_conflicts_that_keep_the_state(self):
        model = self.model(
            "CREATE TABLE t (a INT, b INT, PRIMARY KEY (a), KEY ix (a), CONSTRAINT fk FOREIGN KEY (a) REFERENCES o (id));",
            "ALTER TABLE t ADD INDEX ix (b); ALTER TABLE t ADD PRIMARY KEY (b); "
            "ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (b) REFERENCES p (id);",
        )
        table = model.tables["t"]
        self.assertEqual(table.indexes["ix"].spec.columns, ("a",))
        self.assertEqual(table.primary_key.spec.columns, ("a",))
        self.assertEqual(table.foreign_keys["fk"].spec.ref_table, "o")
        self.assertEqual([f.kind for f in model.findings], ["indice_repetido", "chave_primaria_repetida", "chave_estrangeira_repetida"])

    def test_drop_index_pk_and_fk_expect_absence_and_supersede_the_creation(self):
        model = self.model(
            "CREATE TABLE t (a INT, PRIMARY KEY (a), KEY ix (a), CONSTRAINT fk FOREIGN KEY (a) REFERENCES o (id));",
            "ALTER TABLE t DROP INDEX ix; ALTER TABLE t DROP PRIMARY KEY; ALTER TABLE t DROP FOREIGN KEY fk;",
        )
        table = model.tables["t"]
        self.assertEqual((table.indexes, table.primary_key, table.foreign_keys), ({}, None, {}))
        self.assertIn("ix", table.dropped_indexes)
        self.assertIsNotNone(table.dropped_primary_key)
        self.assertIn("fk", table.dropped_foreign_keys)
        for target in (("indice", "t", "ix"), ("chave_primaria", "t", "primary"), ("chave_estrangeira", "t", "fk")):
            created = [a for a in model.applied if a.target == target and a.operation.kind == "create_table"]
            self.assertTrue(created and all(a.superseded_by for a in created), target)

    def test_drop_index_named_primary_drops_the_primary_key(self):
        model = self.model("CREATE TABLE t (a INT, PRIMARY KEY (a));", "DROP INDEX `PRIMARY` ON t;")
        self.assertIsNone(model.tables["t"].primary_key)

    def test_dropping_what_is_unknown_conflicts_only_in_a_fully_known_table(self):
        known = self.model("CREATE TABLE t (a INT);", "DROP INDEX ix ON t; ALTER TABLE t DROP PRIMARY KEY; ALTER TABLE t DROP FOREIGN KEY fk;")
        self.assertEqual(
            [f.kind for f in known.findings], ["indice_inexistente", "chave_primaria_inexistente", "chave_estrangeira_inexistente"]
        )
        base = self.model("DROP INDEX ix ON antiga; ALTER TABLE antiga DROP PRIMARY KEY;")
        self.assertEqual(base.findings, [])

    def test_recreating_a_dropped_index_supersedes_the_drop(self):
        model = self.model("CREATE TABLE t (a INT, KEY ix (a));", "DROP INDEX ix ON t;", "CREATE INDEX ix ON t (a);")
        self.assertIn("ix", model.tables["t"].indexes)
        self.assertNotIn("ix", model.tables["t"].dropped_indexes)
        self.assertEqual(self.entries(model, "drop_index")[0].superseded_by.script, "SCRIPT_3.SQL")

    def test_primary_key_makes_columns_not_null_even_after_it_is_dropped_but_modify_resets_them(self):
        model = self.model("CREATE TABLE t (a INT, b INT, PRIMARY KEY (a));", "ALTER TABLE t DROP PRIMARY KEY;")
        self.assertEqual(model.tables["t"].implicit_not_null, {"a"})
        reset = self.model("CREATE TABLE t (a INT, b INT, PRIMARY KEY (a));", "ALTER TABLE t DROP PRIMARY KEY;", "ALTER TABLE t MODIFY a INT;")
        self.assertEqual(reset.tables["t"].implicit_not_null, set())
        kept = self.model("CREATE TABLE t (a INT, b INT, PRIMARY KEY (a));", "ALTER TABLE t MODIFY a INT;")
        self.assertEqual(kept.tables["t"].implicit_not_null, {"a"})  # coluna da PK continua NOT NULL

    def test_automatic_foreign_key_names(self):
        model = self.model("CREATE TABLE t (a INT, b INT, FOREIGN KEY (a) REFERENCES o (id));", "ALTER TABLE t ADD FOREIGN KEY (b) REFERENCES p (id);")
        self.assertEqual(list(model.tables["t"].foreign_keys), ["t_ibfk_1", "t_ibfk_2"])
        self.assertTrue(model.tables["t"].foreign_keys["t_ibfk_1"].auto_named)

    def test_every_new_operation_records_the_object_that_verifies_it(self):
        model = self.model(
            "CREATE TABLE t (a INT, b INT, KEY ix (a));",
            "ALTER TABLE t DROP COLUMN b; ALTER TABLE t ADD INDEX ix2 (a); ALTER TABLE t DROP INDEX ix; ALTER TABLE t ENGINE=MyISAM;",
        )
        got = {(a.operation.kind, a.target) for a in model.applied if a.provenance.script == "SCRIPT_2.SQL"}
        self.assertEqual(
            got,
            {
                ("drop_column", ("coluna", "t", "b")),
                ("add_index", ("indice", "t", "ix2")),
                ("drop_index", ("indice", "t", "ix")),
                ("alter_options", ("tabela", "t", "")),
            },
        )
        self.assertTrue(all(a.outcome == OUTCOME_APPLIED for a in model.applied))


if __name__ == "__main__":
    unittest.main()
