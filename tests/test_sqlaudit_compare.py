import copy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.sqlaudit import sql_parse
from app.sqlaudit.comparator import (
    AUSENTE,
    COMPATIVEL,
    DIVERGENTE,
    ERRO,
    INDETERMINADO,
    NAO_SUPORTADO,
    SUBSTITUIDO,
    ComparisonError,
    FileSummary,
    compare,
    worst,
)
from app.sqlaudit.db_reader import DbColumn, DbForeignKey, DbIndex, DbSchema, DbTable
from app.sqlaudit.expected_model import build_expected_model
from app.sqlaudit.file_scanner import scan_sql_files
from app.sqlaudit.fork_hint import suggest_fork
from app.sqlaudit.version_tree import VersionTree, effective_scripts

AVAILABLE = sql_parse.is_available()


def col(name, column_type, null=True, default=None, extra="", charset=None, collation=None, comment=""):
    return DbColumn(name, 0, column_type, null, default, extra, charset, collation, comment)


def idx(name, *columns, unique=False, kind="BTREE", prefixes=None):
    return DbIndex(name, tuple(columns), tuple(prefixes or (None,) * len(columns)), unique, kind)


def fk(name, columns, ref_table, ref_columns, delete="RESTRICT", update="RESTRICT"):
    return DbForeignKey(name, tuple(columns), ref_table, tuple(ref_columns), delete, update)


def db_table(name, *columns, engine="InnoDB", collation="utf8mb4_general_ci", created=None, indexes=(), foreign_keys=()):
    return DbTable(
        name, engine, collation, created, {c.name.lower(): c for c in columns},
        {i.name.lower(): i for i in indexes}, {f.name.lower(): f for f in foreign_keys},
    )


def db_schema(*tables, lctn=1, database="BANCO_X"):
    return DbSchema(database, "8.0.36", lctn, "SYSTEM", False, {t.name.lower(): t for t in tables})


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class CompareTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def model(self, layout=None, target="1.1", tree=None, **single):
        layout = layout or {"1.1": single}
        scripts = {}
        for version, files in layout.items():
            folder = self.root / version / "SCRIPTS"
            folder.mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                (folder / name).write_text(content, encoding="utf-8")
            scripts[version] = scan_sql_files(folder).files
        tree = tree or VersionTree.build(list(layout))
        return build_expected_model(effective_scripts(tree, scripts, target))

    def column(self, result, table, name):
        return next(o for o in result.objects if o.kind == "coluna" and o.table.lower() == table.lower() and (o.column or "").lower() == name.lower())

    def table(self, result, name):
        return next(o for o in result.objects if o.kind == "tabela" and o.table.lower() == name.lower())

    def one(self, add_sql, db_column, **kwargs):
        """Compara um ADD COLUMN de T.A com uma coluna do banco."""
        model = self.model(**{"SCRIPT_1.SQL": f"CREATE TABLE T (ID INT NOT NULL); ALTER TABLE T ADD COLUMN A {add_sql};"})
        schema = db_schema(db_table("T", col("ID", "int", null=False), db_column))
        return self.column(compare(model, schema, **kwargs), "T", "A")


class ScenarioTests(CompareTestCase):
    SCRIPTS = {
        "SCRIPT_1.SQL": "ALTER TABLE CLIENTES\nADD COLUMN CLI_EMAIL VARCHAR(100);",
        "SCRIPT_2.SQL": "ALTER TABLE CLIENTES\nMODIFY COLUMN CLI_EMAIL VARCHAR(200);",
    }

    def test_a_b_scenario_with_the_final_definition_in_the_database(self):
        result = compare(self.model(**self.SCRIPTS), db_schema(db_table("CLIENTES", col("CLI_EMAIL", "varchar(200)"))))
        self.assertEqual(self.column(result, "CLIENTES", "CLI_EMAIL").status, COMPATIVEL)
        self.assertEqual([o.status for o in result.operations], [SUBSTITUIDO, COMPATIVEL])
        self.assertEqual(result.operations[0].detail, "substituido por SCRIPT_2.SQL (linha 1)")
        files = {f.script: f.status for f in result.files()}
        self.assertEqual(files, {"SCRIPT_1.SQL": SUBSTITUIDO, "SCRIPT_2.SQL": COMPATIVEL})
        self.assertIn("nao comprova", result.disclaimer)

    def test_database_still_has_the_old_definition(self):
        result = compare(self.model(**self.SCRIPTS), db_schema(db_table("CLIENTES", col("CLI_EMAIL", "varchar(100)"))))
        column = self.column(result, "CLIENTES", "CLI_EMAIL")
        self.assertEqual(column.status, DIVERGENTE)
        self.assertEqual([(d.attribute, d.expected, d.found) for d in column.differences], [("tipo", "varchar(200)", "varchar(100)")])
        self.assertEqual([o.status for o in result.operations], [SUBSTITUIDO, DIVERGENTE])

    def test_missing_column_and_missing_table(self):
        model = self.model(**self.SCRIPTS)
        result = compare(model, db_schema(db_table("CLIENTES", col("OUTRA", "int"))))
        self.assertEqual(self.column(result, "CLIENTES", "CLI_EMAIL").status, AUSENTE)
        result = compare(model, db_schema(db_table("OUTRA_TABELA", col("X", "int"))))
        self.assertEqual(self.table(result, "CLIENTES").status, AUSENTE)
        self.assertEqual(self.column(result, "CLIENTES", "CLI_EMAIL").status, AUSENTE)

    def test_does_not_change_the_inputs(self):
        model = self.model(**self.SCRIPTS)
        schema = db_schema(db_table("CLIENTES", col("CLI_EMAIL", "varchar(100)")))
        before = (copy.deepcopy(model), copy.deepcopy(schema))
        compare(model, schema)
        self.assertEqual((model, schema), before)

    def test_database_mismatch_and_empty_schema(self):
        model = self.model(**self.SCRIPTS)
        with self.assertRaises(ComparisonError):
            compare(model, db_schema(database="OUTRO"), expected_database="BANCO_X")
        result = compare(model, db_schema())
        self.assertTrue(any("Nenhuma tabela visivel" in w for w in result.warnings))
        self.assertEqual({o.status for o in result.objects}, {ERRO})
        self.assertNotIn(AUSENTE, {o.status for o in result.operations})


class AttributeTests(CompareTestCase):
    def test_equivalent_types_are_compatible(self):
        self.assertEqual(self.one("INTEGER", col("A", "int(11)")).status, COMPATIVEL)
        self.assertEqual(self.one("BOOLEAN", col("A", "tinyint(1)")).status, COMPATIVEL)
        self.assertEqual(self.one("DECIMAL(10, 2)", col("A", "decimal(10,2)")).status, COMPATIVEL)

    def test_type_length_and_unsigned_differences(self):
        self.assertEqual(self.one("VARCHAR(50)", col("A", "varchar(60)")).status, DIVERGENTE)
        self.assertEqual(self.one("INT UNSIGNED", col("A", "int")).status, DIVERGENTE)

    def test_unknown_type_is_inconclusive_not_divergent(self):
        result = self.one("SERIAL", col("A", "bigint unsigned"))
        self.assertEqual(result.status, INDETERMINADO)
        self.assertIn("tipo nao reconhecido", result.notes[0])

    def test_nullability(self):
        self.assertEqual(self.one("INT NOT NULL", col("A", "int", null=True)).status, DIVERGENTE)
        self.assertEqual(self.one("INT NULL", col("A", "int", null=False)).status, DIVERGENTE)
        self.assertEqual(self.one("INT NOT NULL", col("A", "int", null=False)).status, COMPATIVEL)
        self.assertEqual(self.one("INT", col("A", "int", null=True)).status, COMPATIVEL)

    def test_not_null_without_declaration_is_divergent_only_when_nothing_explains_it(self):
        self.assertEqual(self.one("INT", col("A", "int", null=False)).status, DIVERGENTE)
        pk = self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT, PRIMARY KEY (A));"})
        result = compare(pk, db_schema(db_table("T", col("A", "int", null=False), indexes=[idx("PRIMARY", "A", unique=True)])))
        self.assertEqual(self.column(result, "T", "A").status, COMPATIVEL)  # a PK explica o NOT NULL
        result = compare(pk, db_schema(db_table("T", col("A", "int", null=True), indexes=[idx("PRIMARY", "A", unique=True)])))
        self.assertEqual(self.column(result, "T", "A").differences[0].expected, "NOT NULL (chave primaria)")
        check = self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT, CONSTRAINT ck CHECK (A > 0));"})
        result = compare(check, db_schema(db_table("T", col("A", "int", null=False))))
        self.assertEqual(self.column(result, "T", "A").status, INDETERMINADO)  # restricao nao comparada pode explicar

    def test_defaults(self):
        self.assertEqual(self.one("INT DEFAULT 0", col("A", "int", default="0")).status, COMPATIVEL)
        self.assertEqual(self.one("DECIMAL(10,2) DEFAULT 0", col("A", "decimal(10,2)", default="0.00")).status, COMPATIVEL)
        self.assertEqual(self.one("CHAR(1) DEFAULT 'N'", col("A", "char(1)", default="N")).status, COMPATIVEL)
        self.assertEqual(self.one("CHAR(1) DEFAULT 'N'", col("A", "char(1)", default="S")).status, DIVERGENTE)
        self.assertEqual(self.one("INT DEFAULT NULL", col("A", "int", default=None)).status, COMPATIVEL)
        self.assertEqual(self.one("INT DEFAULT 5", col("A", "int", default=None)).status, DIVERGENTE)
        self.assertEqual(self.one("INT DEFAULT 5", col("A", "int", default="6")).status, DIVERGENTE)

    def test_default_dropped_by_modify_is_detected_but_not_for_timestamps(self):
        self.assertEqual(self.one("INT", col("A", "int", default="0")).status, DIVERGENTE)
        self.assertEqual(self.one("TIMESTAMP NULL", col("A", "timestamp", default="CURRENT_TIMESTAMP")).status, COMPATIVEL)
        now = self.one("TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP", col("A", "timestamp", null=False, default="CURRENT_TIMESTAMP(3)"))
        self.assertEqual(now.status, COMPATIVEL)

    def test_auto_increment_in_both_directions(self):
        self.assertEqual(self.one("INT AUTO_INCREMENT", col("A", "int", extra="auto_increment")).status, COMPATIVEL)
        self.assertEqual(self.one("INT AUTO_INCREMENT", col("A", "int")).status, DIVERGENTE)
        self.assertEqual(self.one("INT", col("A", "int", extra="auto_increment")).status, DIVERGENTE)

    def test_on_update_and_generated(self):
        sql = "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
        self.assertEqual(self.one(sql, col("A", "timestamp", default="CURRENT_TIMESTAMP", extra="on update CURRENT_TIMESTAMP")).status, COMPATIVEL)
        self.assertEqual(self.one(sql, col("A", "timestamp", default="CURRENT_TIMESTAMP")).status, DIVERGENTE)
        self.assertEqual(self.one("INT", col("A", "int", extra="VIRTUAL GENERATED")).status, DIVERGENTE)

    def test_charset_collation_and_comment_only_when_declared_or_present(self):
        declared = "VARCHAR(10) CHARACTER SET latin1 COLLATE latin1_bin"
        self.assertEqual(self.one(declared, col("A", "varchar(10)", charset="latin1", collation="latin1_bin")).status, COMPATIVEL)
        self.assertEqual(self.one(declared, col("A", "varchar(10)", charset="utf8mb4", collation="utf8mb4_bin")).status, DIVERGENTE)
        self.assertEqual(self.one("VARCHAR(10)", col("A", "varchar(10)", charset="utf8mb4", collation="utf8mb4_general_ci")).status, COMPATIVEL)
        self.assertEqual(self.one("INT COMMENT 'x'", col("A", "int", comment="x")).status, COMPATIVEL)
        self.assertEqual(self.one("INT COMMENT 'x'", col("A", "int", comment="y")).status, DIVERGENTE)
        self.assertEqual(self.one("INT", col("A", "int", comment="novo")).status, DIVERGENTE)

    def test_several_differences_are_all_listed(self):
        result = self.one("VARCHAR(50) NOT NULL DEFAULT 'a'", col("A", "varchar(60)", null=True, default="b"))
        self.assertEqual({d.attribute for d in result.differences}, {"tipo", "nulabilidade", "default"})


class TableLevelTests(CompareTestCase):
    def test_engine_and_collation_are_compared_only_when_declared(self):
        model = self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT) ENGINE=InnoDB;"})
        ok = compare(model, db_schema(db_table("T", col("A", "int"), engine="InnoDB")))
        self.assertEqual(self.table(ok, "T").status, COMPATIVEL)
        bad = compare(model, db_schema(db_table("T", col("A", "int"), engine="MyISAM")))
        self.assertEqual(self.table(bad, "T").status, DIVERGENTE)
        silent = self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT);"})
        self.assertEqual(self.table(compare(silent, db_schema(db_table("T", col("A", "int"), engine="MyISAM"))), "T").status, COMPATIVEL)

    def test_case_difference_is_noted_only_when_the_server_is_case_sensitive(self):
        model = self.model(**{"SCRIPT_1.SQL": "CREATE TABLE Clientes (A INT);"})
        sensitive = compare(model, db_schema(db_table("CLIENTES", col("A", "int")), lctn=0))
        self.assertTrue(any("maiusculas" in n for n in self.table(sensitive, "clientes").notes))
        insensitive = compare(model, db_schema(db_table("CLIENTES", col("A", "int")), lctn=1))
        self.assertEqual(self.table(insensitive, "clientes").notes[:1], ())
        self.assertEqual(self.table(insensitive, "clientes").status, COMPATIVEL)

    def test_database_only_column_in_a_fully_known_table_is_indeterminate(self):
        model = self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT);"})
        result = compare(model, db_schema(db_table("T", col("A", "int"), col("SOBRA", "int"))))
        self.assertEqual(self.column(result, "T", "SOBRA").status, INDETERMINADO)
        old = self.model(**{"SCRIPT_1.SQL": "ALTER TABLE T ADD A INT;"})  # tabela do banco-base: nao e conhecida por inteiro
        result = compare(old, db_schema(db_table("T", col("A", "int"), col("SOBRA", "int"))))
        self.assertEqual([o.column for o in result.objects if o.kind == "coluna"], ["A"])


class ReliabilityTests(CompareTestCase):
    def layout(self):
        return {
            "1.1": {"SCRIPT_100.SQL": "CREATE TABLE T (A INT, B INT);"},
            "1.2": {"SCRIPT_200.SQL": "ALTER TABLE T ADD C INT;"},
        }

    def test_unknown_fork_makes_absence_and_difference_indeterminate_but_match_stays_compatible(self):
        tree = VersionTree.build(["1.1", "1.2"])
        tree.set_base("1.2", "1.1")
        model = self.model(self.layout(), "1.2", tree)
        schema = db_schema(db_table("T", col("A", "bigint"), col("C", "int")))  # A diverge, B falta, C confere
        result = compare(model, schema)
        self.assertEqual(self.column(result, "T", "A").status, INDETERMINADO)
        self.assertEqual(self.column(result, "T", "A").differences[0].attribute, "tipo")
        self.assertEqual(self.column(result, "T", "B").status, INDETERMINADO)
        self.assertEqual(self.column(result, "T", "C").status, COMPATIVEL)
        self.assertTrue(any("origem incerta" in n for n in self.column(result, "T", "B").notes))

    def test_declared_fork_makes_the_same_findings_firm(self):
        tree = VersionTree.build(["1.1", "1.2"])
        tree.set_base("1.2", "1.1", fork=100)
        result = compare(self.model(self.layout(), "1.2", tree), db_schema(db_table("T", col("A", "bigint"), col("C", "int"))))
        self.assertEqual(self.column(result, "T", "A").status, DIVERGENTE)
        self.assertEqual(self.column(result, "T", "B").status, AUSENTE)

    def test_only_an_action_the_model_cannot_follow_makes_the_table_inconclusive(self):
        unfollowed = self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT); ALTER TABLE T ALTER COLUMN B SET DEFAULT 1;"})
        result = compare(unfollowed, db_schema(db_table("T", col("A", "int"))))
        self.assertEqual(self.column(result, "T", "B").status, INDETERMINADO)
        followed = self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT); ALTER TABLE T DROP COLUMN A; ALTER TABLE T ADD INDEX ix (B);"})
        result = compare(followed, db_schema(db_table("T", col("B", "int"), indexes=[idx("ix", "B")])))
        self.assertEqual({o.status for o in result.objects}, {COMPATIVEL})

    def test_dropped_table_is_compatible_when_absent_and_divergent_when_it_still_exists(self):
        model = self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT); DROP TABLE T;"})
        absent = compare(model, db_schema(db_table("OUTRA", col("X", "int"))))
        dropped = self.table(absent, "T")
        self.assertEqual((dropped.status, dropped.expected_absent), (COMPATIVEL, True))
        self.assertIn("esperado ausente", dropped.notes[0])
        present = compare(model, db_schema(db_table("T", col("A", "int"))))
        self.assertEqual(self.table(present, "T").status, DIVERGENTE)
        self.assertEqual(self.table(present, "T").differences[0].found, "sim")

    def test_unsupported_error_and_conflict_operations_keep_their_own_state(self):
        model = self.model(
            **{
                "SCRIPT_1.SQL": "CREATE TABLE T (A INT); FOO BAR; ALTER TABLE T ADD A INT; ALTER TABLE T ADD B INT,;",
            }
        )
        result = compare(model, db_schema(db_table("T", col("A", "int"))))
        statuses = [o.status for o in result.operations]
        self.assertIn(NAO_SUPORTADO, statuses)
        self.assertIn(ERRO, statuses)
        conflict = next(o for o in result.operations if o.applied.outcome == "conflito")
        self.assertEqual(conflict.status, INDETERMINADO)
        self.assertTrue(any("nao foram interpretados" in w for w in result.warnings))


class AggregationTests(unittest.TestCase):
    def test_worst_follows_the_precedence(self):
        self.assertEqual(worst([COMPATIVEL, SUBSTITUIDO]), SUBSTITUIDO)
        self.assertEqual(worst([COMPATIVEL, INDETERMINADO, SUBSTITUIDO]), INDETERMINADO)
        self.assertEqual(worst([NAO_SUPORTADO, AUSENTE]), AUSENTE)
        self.assertEqual(worst([AUSENTE, DIVERGENTE]), DIVERGENTE)
        self.assertEqual(worst([DIVERGENTE, ERRO]), ERRO)
        self.assertEqual(worst([]), COMPATIVEL)

    def test_partial_file(self):
        self.assertTrue(FileSummary("v", "s", 1, AUSENTE, {COMPATIVEL: 2, AUSENTE: 1}).partial)
        self.assertFalse(FileSummary("v", "s", 1, COMPATIVEL, {COMPATIVEL: 2}).partial)
        self.assertFalse(FileSummary("v", "s", 1, AUSENTE, {AUSENTE: 2}).partial)


class ForkHintTests(CompareTestCase):
    BASE_SCRIPTS = {
        "SCRIPT_1.SQL": "CREATE TABLE T (ID INT);",
        "SCRIPT_2.SQL": "ALTER TABLE T ADD A INT;",
        "SCRIPT_3.SQL": "ALTER TABLE T ADD B INT;",
        "SCRIPT_4.SQL": "ALTER TABLE T ADD C INT;",
        "SCRIPT_5.SQL": "ALTER TABLE T ADD D INT;",
    }

    def hint(self, columns, **kwargs):
        model = self.model(**self.BASE_SCRIPTS)
        schema = db_schema(db_table("T", *[col(name, "int") for name in columns]))
        return suggest_fork(compare(model, schema), **kwargs)

    def test_first_missing_script_bounds_the_fork(self):
        hint = self.hint(["ID", "A", "B"])
        self.assertEqual((hint.candidate, hint.first_missing, hint.upper), (3, 4, 3))
        self.assertEqual(hint.inconsistent, [])

    def test_everything_present_suggests_the_last_script(self):
        hint = self.hint(["ID", "A", "B", "C", "D"])
        self.assertEqual((hint.candidate, hint.first_missing), (5, None))
        self.assertTrue(any("confirme" in n for n in hint.notes))

    def test_later_script_present_after_a_missing_one_is_flagged(self):
        hint = self.hint(["ID", "A", "C"])  # falta B (3) mas C (4) existe
        self.assertEqual((hint.candidate, hint.first_missing), (2, 3))
        self.assertEqual(hint.inconsistent, [4])
        self.assertTrue(any("ordem" in n for n in hint.notes))

    def test_nothing_verifiable_asks_for_manual_input(self):
        model = self.model(**{"SCRIPT_1.SQL": "INSERT INTO T VALUES (1);"})
        hint = suggest_fork(compare(model, db_schema(db_table("T", col("ID", "int")))))
        self.assertIsNone(hint.candidate)
        self.assertTrue(any("manualmente" in n for n in hint.notes))

    def test_superseded_scripts_are_neutral_and_restore_date_is_passed_through(self):
        model = self.model(**{"SCRIPT_1.SQL": "ALTER TABLE T ADD A INT;", "SCRIPT_2.SQL": "ALTER TABLE T MODIFY A BIGINT;"})
        when = datetime(2026, 5, 1)
        hint = suggest_fork(compare(model, db_schema(db_table("T", col("A", "bigint")))), earliest_restore=when)
        self.assertEqual(hint.candidate, 2)
        self.assertEqual(hint.earliest_restore, when)


if __name__ == "__main__":
    unittest.main()
