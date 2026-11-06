"""Fase 6 do comparador e do leitor: indices, PK, FK, objetos removidos e o que so existe no banco."""

import unittest

from app.sqlaudit import db_reader
from app.sqlaudit.comparator import (
    AUSENTE,
    COMPATIVEL,
    DIVERGENTE,
    INDETERMINADO,
    SUBSTITUIDO,
    compare,
    compare_foreign_key,
    compare_index,
)
from app.sqlaudit.fork_hint import suggest_fork
from app.sqlaudit.sql_parse import ForeignKeySpec, IndexSpec
from tests.test_sqlaudit_compare import CompareTestCase, col, db_schema, db_table, fk, idx
from tests.test_sqlaudit_reader import FakeConnection, ReaderTestCase, full_responses


def result_for(testcase, scripts, table):
    return compare(testcase.model(**scripts), db_schema(table))


class IndexComparisonTests(CompareTestCase):
    SCRIPT = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT, KEY IX (A, B), UNIQUE KEY UQ (B));"}

    def table(self, indexes, **kwargs):
        return db_table("T", col("A", "int"), col("B", "int"), indexes=indexes, **kwargs)

    def index(self, result, name):
        return next(o for o in result.objects if o.kind == "indice" and o.column.lower() == name.lower())

    def test_matching_indexes_are_compatible(self):
        result = result_for(self, self.SCRIPT, self.table([idx("IX", "A", "B"), idx("UQ", "B", unique=True)]))
        self.assertEqual([self.index(result, n).status for n in ("IX", "UQ")], [COMPATIVEL, COMPATIVEL])

    def test_missing_index(self):
        result = result_for(self, self.SCRIPT, self.table([idx("UQ", "B", unique=True)]))
        self.assertEqual(self.index(result, "IX").status, AUSENTE)

    def test_columns_order_uniqueness_and_kind_differences(self):
        result = result_for(self, self.SCRIPT, self.table([idx("IX", "B", "A"), idx("UQ", "B", unique=False)]))
        self.assertEqual({d.attribute for d in self.index(result, "IX").differences}, {"colunas"})
        self.assertEqual({d.attribute for d in self.index(result, "UQ").differences}, {"unico"})
        fulltext = result_for(self, {"SCRIPT_1.SQL": "CREATE TABLE T (A TEXT, FULLTEXT KEY FT (A));"}, db_table("T", col("A", "text"), indexes=[idx("FT", "A")]))
        self.assertEqual(self.index(fulltext, "FT").differences[0].attribute, "tipo_indice")

    def test_case_insensitive_names_and_columns(self):
        result = result_for(self, self.SCRIPT, self.table([idx("ix", "a", "b"), idx("uq", "b", unique=True)]))
        self.assertEqual([self.index(result, n).status for n in ("IX", "UQ")], [COMPATIVEL, COMPATIVEL])

    def test_prefix_length_is_compared_when_the_columns_match(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A VARCHAR(100), KEY IX (A(10)));"}
        ok = result_for(self, scripts, db_table("T", col("A", "varchar(100)"), indexes=[idx("IX", "A", prefixes=[10])]))
        self.assertEqual(self.index(ok, "IX").status, COMPATIVEL)
        bad = result_for(self, scripts, db_table("T", col("A", "varchar(100)"), indexes=[idx("IX", "A", prefixes=[20])]))
        self.assertEqual(self.index(bad, "IX").differences[0].attribute, "prefixo de A")

    def test_automatic_name_is_matched_by_columns_not_by_name(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT); ALTER TABLE T ADD INDEX (A, B);"}
        found = db_table("T", col("A", "int"), col("B", "int"), indexes=[idx("nome_que_o_mysql_escolheu", "A", "B")])
        result = result_for(self, scripts, found)
        self.assertEqual(self.index(result, "A").status, COMPATIVEL)
        self.assertIn("nome automatico", self.index(result, "A").notes[0])
        reversed_columns = result_for(self, scripts, db_table("T", col("A", "int"), col("B", "int"), indexes=[idx("x", "B", "A")]))
        self.assertEqual(self.index(reversed_columns, "A").status, AUSENTE)  # colunas em outra ordem nao sao o mesmo indice

    def test_origin_uncertain_index_findings_are_not_firm(self):
        from app.sqlaudit.version_tree import VersionTree

        tree = VersionTree.build(["1.1", "1.2"])
        tree.set_base("1.2", "1.1")
        model = self.model({"1.1": {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, KEY IX (A));"}, "1.2": {"SCRIPT_2.SQL": "ALTER TABLE T ADD B INT;"}}, "1.2", tree)
        result = compare(model, db_schema(db_table("T", col("A", "int"), col("B", "int"))))
        self.assertEqual(self.index(result, "IX").status, INDETERMINADO)

    def test_index_present_only_in_the_database_is_reported_in_a_fully_known_table(self):
        result = result_for(self, {"SCRIPT_1.SQL": "CREATE TABLE T (A INT);"}, db_table("T", col("A", "int"), indexes=[idx("SOBRA", "A")]))
        extra = self.index(result, "SOBRA")
        self.assertEqual((extra.status, extra.provenance), (INDETERMINADO, None))
        base_table = result_for(self, {"SCRIPT_1.SQL": "ALTER TABLE T ADD A INT;"}, db_table("T", col("A", "int"), indexes=[idx("SOBRA", "A")]))
        self.assertFalse([o for o in base_table.objects if o.kind == "indice"])  # tabela do banco-base: nao e conhecida por inteiro

    def test_index_for_a_foreign_key_is_not_reported_as_extra(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, CONSTRAINT FK1 FOREIGN KEY (A) REFERENCES O (ID));"}
        found = db_table("T", col("A", "int"), indexes=[idx("FK1", "A")], foreign_keys=[fk("FK1", ["A"], "O", ["ID"])])
        result = result_for(self, scripts, found)
        self.assertFalse([o for o in result.objects if o.kind == "indice"])


class PrimaryAndForeignKeyTests(CompareTestCase):
    def of(self, result, kind, name=None):
        return next(o for o in result.objects if o.kind == kind and (name is None or o.column.lower() == name.lower()))

    def test_primary_key_compatible_missing_and_different(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT, PRIMARY KEY (A, B));"}
        columns = [col("A", "int", null=False), col("B", "int", null=False)]
        ok = result_for(self, scripts, db_table("T", *columns, indexes=[idx("PRIMARY", "A", "B", unique=True)]))
        self.assertEqual(self.of(ok, "chave_primaria").status, COMPATIVEL)
        missing = result_for(self, scripts, db_table("T", *columns))
        self.assertEqual(self.of(missing, "chave_primaria").status, AUSENTE)
        different = result_for(self, scripts, db_table("T", *columns, indexes=[idx("PRIMARY", "A", unique=True)]))
        self.assertEqual(self.of(different, "chave_primaria").differences[0].attribute, "colunas")

    def test_foreign_key_checks_columns_reference_and_rules(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, CONSTRAINT FK1 FOREIGN KEY (A) REFERENCES O (ID) ON DELETE CASCADE ON UPDATE SET NULL);"}
        table = lambda f: db_table("T", col("A", "int"), foreign_keys=[f])
        ok = result_for(self, scripts, table(fk("FK1", ["A"], "o", ["id"], delete="CASCADE", update="SET NULL")))
        self.assertEqual(self.of(ok, "chave_estrangeira").status, COMPATIVEL)
        bad = result_for(self, scripts, table(fk("FK1", ["A"], "P", ["ID"], delete="RESTRICT", update="SET NULL")))
        self.assertEqual({d.attribute for d in self.of(bad, "chave_estrangeira").differences}, {"referencia", "on_delete"})
        self.assertEqual(self.of(result_for(self, scripts, db_table("T", col("A", "int"))), "chave_estrangeira").status, AUSENTE)

    def test_default_rules_are_equivalent_in_innodb(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, CONSTRAINT FK1 FOREIGN KEY (A) REFERENCES O (ID));"}
        for rule in ("RESTRICT", "NO ACTION", ""):
            found = db_table("T", col("A", "int"), foreign_keys=[fk("FK1", ["A"], "O", ["ID"], delete=rule, update=rule)])
            self.assertEqual(self.of(result_for(self, scripts, found), "chave_estrangeira").status, COMPATIVEL, rule)

    def test_unnamed_foreign_key_is_matched_by_columns_and_reference(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, FOREIGN KEY (A) REFERENCES O (ID));"}
        found = db_table("T", col("A", "int"), foreign_keys=[fk("T_ibfk_9", ["A"], "O", ["ID"])])
        self.assertEqual(self.of(result_for(self, scripts, found), "chave_estrangeira").status, COMPATIVEL)

    def test_foreign_key_only_in_the_database(self):
        found = db_table("T", col("A", "int"), foreign_keys=[fk("SOBRA", ["A"], "O", ["ID"])])
        result = result_for(self, {"SCRIPT_1.SQL": "CREATE TABLE T (A INT);"}, found)
        self.assertEqual(self.of(result, "chave_estrangeira", "SOBRA").status, INDETERMINADO)

    def test_pure_functions(self):
        spec = IndexSpec("ix", ("a", "b"), (None, None))
        self.assertEqual(compare_index(spec, idx("IX", "A", "B")), [])
        self.assertEqual(compare_foreign_key(ForeignKeySpec("f", ("a",), "o", ("id",), on_delete="CASCADE"), fk("F", ["a"], "o", ["id"], delete="CASCADE")), [])


class DroppedObjectTests(CompareTestCase):
    def of(self, result, kind, name):
        return next(o for o in result.objects if o.kind == kind and (o.column or "").lower() == name.lower())

    def test_dropped_column_is_compatible_when_absent_and_divergent_when_it_still_exists(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT);", "SCRIPT_2.SQL": "ALTER TABLE T DROP COLUMN B;"}
        absent = result_for(self, scripts, db_table("T", col("A", "int")))
        removed = self.of(absent, "coluna", "B")
        self.assertEqual((removed.status, removed.expected_absent), (COMPATIVEL, True))
        self.assertIn("removido por SCRIPT_2.SQL, linha 1", removed.notes[0])
        present = result_for(self, scripts, db_table("T", col("A", "int"), col("B", "int")))
        self.assertEqual(self.of(present, "coluna", "B").status, DIVERGENTE)
        self.assertEqual(self.of(present, "coluna", "B").differences[0].attribute, "existe")

    def test_rename_column_expects_the_new_name_present_and_the_old_one_absent(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT);", "SCRIPT_2.SQL": "ALTER TABLE T RENAME COLUMN A TO C;"}
        done = result_for(self, scripts, db_table("T", col("C", "int")))
        self.assertEqual({(o.column, o.status) for o in done.objects if o.kind == "coluna"}, {("C", COMPATIVEL), ("A", COMPATIVEL)})
        undone = result_for(self, scripts, db_table("T", col("A", "int")))
        self.assertEqual(self.of(undone, "coluna", "C").status, AUSENTE)
        self.assertEqual(self.of(undone, "coluna", "A").status, DIVERGENTE)

    def test_rename_table_and_dropped_index_pk_and_fk(self):
        scripts = {
            "SCRIPT_1.SQL": "CREATE TABLE T (A INT, PRIMARY KEY (A), KEY IX (A), CONSTRAINT F FOREIGN KEY (A) REFERENCES O (ID));",
            "SCRIPT_2.SQL": "DROP INDEX IX ON T; ALTER TABLE T DROP PRIMARY KEY; ALTER TABLE T DROP FOREIGN KEY F; RENAME TABLE T TO U;",
        }
        clean = db_table("U", col("A", "int", null=False))
        result = result_for(self, scripts, clean)
        self.assertEqual({o.status for o in result.objects}, {COMPATIVEL})
        self.assertTrue(any(o.kind == "tabela" and o.table == "T" and o.expected_absent for o in result.objects))
        leftover = db_table(
            "U", col("A", "int", null=False), indexes=[idx("IX", "A"), idx("PRIMARY", "A", unique=True)], foreign_keys=[fk("F", ["A"], "O", ["ID"])]
        )
        result = result_for(self, scripts, leftover)
        self.assertEqual(
            {(o.kind, o.status) for o in result.objects if o.expected_absent and o.table == "U"},
            {("indice", DIVERGENTE), ("chave_primaria", DIVERGENTE), ("chave_estrangeira", DIVERGENTE)},
        )
        still_old = result_for(self, scripts, db_table("T", col("A", "int")))  # o nome antigo ainda existe
        old_table = next(o for o in still_old.objects if o.kind == "tabela" and o.table == "T")
        self.assertEqual(old_table.status, DIVERGENTE)

    def test_operation_results_follow_the_object_that_verifies_them(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT);", "SCRIPT_2.SQL": "ALTER TABLE T DROP COLUMN B;"}
        result = result_for(self, scripts, db_table("T", col("A", "int"), col("B", "int")))
        by_kind = {(o.applied.operation.kind, o.applied.provenance.script): o for o in result.operations}
        drop = by_kind[("drop_column", "SCRIPT_2.SQL")]
        self.assertEqual((drop.status, drop.effect_missing), (DIVERGENTE, True))
        self.assertIn("esperado ausente", drop.detail)
        files = {f.script: f.status for f in result.files()}
        self.assertEqual(files["SCRIPT_2.SQL"], DIVERGENTE)
        superseded = [o for o in result.operations if o.status == SUBSTITUIDO]
        self.assertTrue(superseded and all(o.applied.target == ("coluna", "t", "b") for o in superseded))

    def test_dropped_objects_in_an_uncertain_origin_are_not_firm(self):
        from app.sqlaudit.version_tree import VersionTree

        tree = VersionTree.build(["1.1", "1.2"])
        tree.set_base("1.2", "1.1")
        model = self.model({"1.1": {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT); ALTER TABLE T DROP COLUMN B;"}, "1.2": {"SCRIPT_2.SQL": "ALTER TABLE T ADD C INT;"}}, "1.2", tree)
        result = compare(model, db_schema(db_table("T", col("A", "int"), col("B", "int"), col("C", "int"))))
        removed = self.of(result, "coluna", "B")
        self.assertEqual(removed.status, INDETERMINADO)
        self.assertEqual(removed.differences[0].attribute, "existe")

    def test_missing_visible_tables_make_dropped_objects_errors_not_compatible(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT); DROP TABLE T; CREATE TABLE U (A INT); ALTER TABLE U DROP COLUMN A;"}
        result = compare(self.model(**scripts), db_schema())
        self.assertEqual({o.status for o in result.objects}, {"ERRO"})


class ForkHintWithDropsTests(CompareTestCase):
    def test_a_drop_whose_effect_is_missing_marks_the_script_as_not_inherited(self):
        model = self.model(
            **{
                "SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT);",
                "SCRIPT_2.SQL": "ALTER TABLE T ADD C INT;",
                "SCRIPT_3.SQL": "ALTER TABLE T DROP COLUMN B;",
            }
        )
        child = db_schema(db_table("T", col("A", "int"), col("B", "int"), col("C", "int")))  # o DROP do script 3 nao chegou
        hint = suggest_fork(compare(model, child))
        self.assertEqual((hint.candidate, hint.first_missing), (2, 3))


class ReaderKeyTests(ReaderTestCase):
    def responses(self):
        responses = full_responses()
        responses[db_reader.SQL_INDEXES] = [
            ("CLIENTES", "PRIMARY", 0, 1, "CLI_ID", None, "BTREE"),
            ("CLIENTES", "ix_nome", 1, 1, "CLI_NOME", 10, "BTREE"),
            ("CLIENTES", "ix_nome", 1, 2, "CLI_ID", None, "BTREE"),
            ("CLIENTES", "ft_nome", 1, 1, "CLI_NOME", None, "FULLTEXT"),
            ("PEDIDOS", "PRIMARY", 0, 1, "PED_ID", None, "BTREE"),
            ("FANTASMA", "x", 1, 1, "A", None, "BTREE"),
        ]
        responses[db_reader.SQL_FOREIGN_KEYS] = [
            ("PEDIDOS", "fk_cli", "PED_CLI", 1, "CLIENTES", "CLI_ID", "CASCADE", "NO ACTION"),
            ("PEDIDOS", "fk_dupla", "A", 1, "X", "XA", "restrict", "restrict"),
            ("PEDIDOS", "fk_dupla", "B", 2, "X", "XB", "restrict", "restrict"),
            ("FANTASMA", "f", "A", 1, "X", "XA", "RESTRICT", "RESTRICT"),
        ]
        return responses

    def test_indexes_are_grouped_by_name_in_column_order_with_prefix_and_kind(self):
        audit, _ = self.connect(FakeConnection(self.responses()))
        tables = audit.read_schema("BANCO_X").tables
        clientes = tables["clientes"].indexes
        self.assertEqual(set(clientes), {"primary", "ix_nome", "ft_nome"})
        self.assertEqual((clientes["ix_nome"].columns, clientes["ix_nome"].prefixes, clientes["ix_nome"].unique), (("CLI_NOME", "CLI_ID"), (10, None), False))
        self.assertTrue(clientes["primary"].unique)
        self.assertEqual(clientes["ft_nome"].index_type, "FULLTEXT")
        self.assertNotIn("fantasma", tables)

    def test_foreign_keys_group_columns_and_normalize_rules(self):
        audit, _ = self.connect(FakeConnection(self.responses()))
        keys = audit.read_schema("BANCO_X").tables["pedidos"].foreign_keys
        self.assertEqual((keys["fk_cli"].ref_table, keys["fk_cli"].delete_rule, keys["fk_cli"].update_rule), ("CLIENTES", "CASCADE", "NO ACTION"))
        self.assertEqual((keys["fk_dupla"].columns, keys["fk_dupla"].ref_columns, keys["fk_dupla"].delete_rule), (("A", "B"), ("XA", "XB"), "RESTRICT"))

    def test_schema_without_indexes_or_keys_still_reads(self):
        audit, _ = self.connect(FakeConnection(full_responses()))
        tables = audit.read_schema("BANCO_X").tables
        self.assertEqual((tables["clientes"].indexes, tables["clientes"].foreign_keys), ({}, {}))

    def test_cancel_skips_the_index_and_key_queries(self):
        import threading

        connection = FakeConnection(self.responses())
        audit, _ = self.connect(connection)
        cancel = threading.Event()
        cancel.set()
        audit.read_schema("BANCO_X", cancel_event=cancel)
        executed = {statement for statement, _ in connection.executed}
        self.assertNotIn(db_reader.SQL_INDEXES, executed)
        self.assertNotIn(db_reader.SQL_FOREIGN_KEYS, executed)

    def test_the_new_queries_are_parameterized_fixed_selects(self):
        connection = FakeConnection(self.responses())
        audit, _ = self.connect(connection)
        audit.read_schema("BANCO_X")
        for statement, params in connection.executed:
            if statement in (db_reader.SQL_INDEXES, db_reader.SQL_FOREIGN_KEYS):
                self.assertEqual(params, ("BANCO_X",))
                self.assertNotIn("BANCO_X", statement)
                self.assertTrue(statement.lstrip().startswith("SELECT"))


if __name__ == "__main__":
    unittest.main()
