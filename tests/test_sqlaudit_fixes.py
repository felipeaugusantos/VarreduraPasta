"""Fase 7: script corretivo para revisao. Nunca executa; so gera texto."""

import re
import tempfile
import unittest
from pathlib import Path

from app.sqlaudit import fix_suggestions as fix
from app.sqlaudit import sql_parse
from app.sqlaudit.comparator import compare
from app.sqlaudit.fix_suggestions import (
    BUCKET_CREATE_TABLE,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    FixError,
    build_fix_plan,
    render_script,
    write_script,
)
from app.sqlaudit.sql_parse import parse_text
from tests.test_sqlaudit_compare import CompareTestCase, col, db_schema, db_table, fk, idx

AVAILABLE = sql_parse.is_available()


def one(sql):
    operations = parse_text(sql)
    assert len(operations) == 1, operations
    return operations[0]


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class RenderingRoundTripTests(unittest.TestCase):
    """O SQL gerado tem que significar o mesmo que o modelo: releitura pelo parser do sistema."""

    COLUMNS = [
        "INT",
        "VARCHAR(100) NOT NULL DEFAULT ''",
        "DECIMAL(10,2) NULL DEFAULT 0",
        "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        "VARCHAR(10) CHARACTER SET latin1 COLLATE latin1_bin COMMENT 'it''s a \\\\ test'",
        "INT UNSIGNED NOT NULL AUTO_INCREMENT",
        "ENUM('a','b') DEFAULT 'a'",
        "TEXT NULL",
        "BIGINT NOT NULL DEFAULT -1",
        "DATETIME(3) NULL DEFAULT NULL",
        "CHAR(1) NOT NULL DEFAULT 'N' COMMENT 'sim ou nao'",
    ]

    def test_column_definition_survives_a_round_trip(self):
        for source in self.COLUMNS:
            original = one(f"ALTER TABLE t ADD COLUMN c {source}").column
            rendered = fix.column_definition(original)
            again = one(f"ALTER TABLE t ADD COLUMN {rendered}").column
            self.assertEqual(again, original, f"{source} -> {rendered}")

    def test_index_definition_survives_a_round_trip(self):
        for source in (
            "INDEX ix (a, b)", "UNIQUE INDEX ux (a)", "FULLTEXT INDEX ft (a)", "INDEX ix (a(10), b)", "INDEX (a)",
        ):
            original = one(f"ALTER TABLE t ADD {source}").index
            again = one("ALTER TABLE t ADD " + fix.index_definition(original)).index
            self.assertEqual(again, original, source)

    def test_foreign_key_definition_survives_a_round_trip(self):
        for source in (
            "CONSTRAINT fk FOREIGN KEY (a) REFERENCES o (id)",
            "CONSTRAINT fk FOREIGN KEY (a, b) REFERENCES db.o (x, y) ON DELETE CASCADE ON UPDATE SET NULL",
            "FOREIGN KEY (a) REFERENCES o (id) ON DELETE RESTRICT",
        ):
            original = one(f"ALTER TABLE t ADD {source}").foreign_key
            again = one("ALTER TABLE t ADD " + fix.foreign_key_definition(original)).foreign_key
            self.assertEqual(again, original, source)

    def test_unnamed_foreign_key_is_rendered_without_a_constraint_name(self):
        spec = one("ALTER TABLE t ADD FOREIGN KEY (a) REFERENCES o (id)").foreign_key
        self.assertNotIn("CONSTRAINT", fix.foreign_key_definition(spec, named=False))


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class CreateTableRenderingTests(CompareTestCase):
    def test_create_table_statement_is_understood_and_equivalent(self):
        model = self.model(
            **{
                "SCRIPT_1.SQL": "CREATE TABLE T (ID INT NOT NULL AUTO_INCREMENT, NOME VARCHAR(80) NOT NULL DEFAULT '', COD INT, TXT TEXT, "
                "PRIMARY KEY (ID), UNIQUE KEY UQ (COD), KEY IX (NOME(10), COD), FULLTEXT KEY FT (TXT)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;"
            }
        )
        state = model.tables["t"]
        (op,) = parse_text(fix.create_table_statement(state))
        self.assertEqual([c.name for c in op.columns], ["ID", "NOME", "COD", "TXT"])
        self.assertEqual([c.data_type for c in op.columns], [c.spec.data_type for c in state.columns.values()])
        self.assertEqual(op.primary_key, ("ID",))
        self.assertEqual([(i.name, i.columns, i.prefixes, i.unique, i.kind) for i in op.indexes], [(i.spec.name, i.spec.columns, i.spec.prefixes, i.spec.unique, i.spec.kind) for i in state.indexes.values()])
        self.assertEqual(dict(op.options), {"engine": "innodb", "charset": "utf8mb4", "collation": "utf8mb4_bin"})
        self.assertFalse(op.columns[0].nullable is True)

    def test_primary_key_columns_are_rendered_not_null(self):
        state = self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT, PRIMARY KEY (A));"}).tables["t"]
        (op,) = parse_text(fix.create_table_statement(state))
        self.assertIs(op.columns[0].nullable, False)


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class SafetyHelpersTests(unittest.TestCase):
    def test_identifiers_and_literals_are_escaped(self):
        self.assertEqual(fix.quote("we`ird"), "`we``ird`")
        self.assertEqual(fix.literal("it's \\ ok"), "'it''s \\\\ ok'")

    def test_unsafe_words_and_types_are_refused(self):
        for value in ("utf8mb4; DROP TABLE x", "a b", "", "x`y"):
            with self.assertRaises(FixError):
                fix._word(value, "charset")
        for data_type in ("int; DROP TABLE x", "int -- x", "int /* x */", "int\nint", ""):
            with self.assertRaises(FixError):
                fix._safe_type(data_type)

    def test_alter_helper_quotes_the_table(self):
        self.assertEqual(fix.alter("a`b", "DROP INDEX `x`"), "ALTER TABLE `a``b` DROP INDEX `x`;")


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class PlanTests(CompareTestCase):
    def plan(self, scripts, table, **model_kwargs):
        model = self.model(**scripts)
        return build_fix_plan(model, compare(model, db_schema(*([table] if not isinstance(table, list) else table))), "1.1", "BANCO_X")

    def one_item(self, plan, **expect):
        self.assertEqual(len(plan.items), 1, [i.statement for i in plan.items])
        item = plan.items[0]
        for key, value in expect.items():
            self.assertEqual(getattr(item, key), value, key)
        return item

    # --- colunas ---
    def test_missing_nullable_column_is_a_low_risk_add(self):
        plan = self.plan({"SCRIPT_7.SQL": "CREATE TABLE T (A INT);\nALTER TABLE T ADD COLUMN B VARCHAR(10);"}, db_table("T", col("A", "int")))
        item = self.one_item(plan, risk=RISK_LOW, commented=False, destructive=False)
        self.assertEqual(item.statement, "ALTER TABLE `T` ADD COLUMN `B` varchar(10);")
        self.assertEqual((item.source, item.reason), ("SCRIPT_7.SQL linha 2", "coluna ausente no banco"))

    def test_missing_not_null_column_without_default_is_medium_with_a_warning(self):
        plan = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A INT); ALTER TABLE T ADD B INT NOT NULL;"}, db_table("T", col("A", "int")))
        item = self.one_item(plan, risk=RISK_MEDIUM, commented=False)
        self.assertTrue(item.warnings)

    def test_position_is_kept_only_when_the_reference_column_exists(self):
        plan = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A INT); ALTER TABLE T ADD B INT AFTER A;"}, db_table("T", col("A", "int")))
        self.assertTrue(plan.items[0].statement.endswith("AFTER `A`;"))
        plan = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A INT); ALTER TABLE T ADD B INT AFTER ZZ;"}, db_table("T", col("A", "int")))
        self.assertNotIn("AFTER", plan.items[0].statement)

    def test_type_change_is_high_risk_and_commented(self):
        plan = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A VARCHAR(50));"}, db_table("T", col("A", "varchar(20)")))
        item = self.one_item(plan, risk=RISK_HIGH, commented=True)
        self.assertEqual(item.statement, "ALTER TABLE `T` MODIFY COLUMN `A` varchar(50);")
        self.assertIn("esperado varchar(50), encontrado varchar(20)", item.reason)

    def test_becoming_not_null_is_high_but_a_default_change_is_medium_and_runs(self):
        not_null = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A INT NOT NULL);"}, db_table("T", col("A", "int", null=True)))
        self.one_item(not_null, risk=RISK_HIGH, commented=True)
        default = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A INT DEFAULT 5);"}, db_table("T", col("A", "int", default="6")))
        self.one_item(default, risk=RISK_MEDIUM, commented=False)

    def test_generated_column_in_the_database_is_not_touched(self):
        plan = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A INT);"}, db_table("T", col("A", "int", extra="VIRTUAL GENERATED")))
        self.assertEqual(plan.items, [])
        self.assertIn("coluna gerada", plan.skipped[0][1])

    # --- indices e chaves ---
    def test_missing_indexes(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT, KEY IX (A), UNIQUE KEY UQ (B));"}
        plan = self.plan(scripts, db_table("T", col("A", "int"), col("B", "int")))
        by_text = {i.statement: i for i in plan.items}
        plain = by_text["ALTER TABLE `T` ADD INDEX `IX` (`A`);"]
        unique = by_text["ALTER TABLE `T` ADD UNIQUE INDEX `UQ` (`B`);"]
        self.assertEqual((plain.risk, unique.risk), (RISK_LOW, RISK_MEDIUM))
        self.assertTrue(unique.warnings)

    def test_automatic_index_is_added_without_a_name(self):
        plan = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A INT); ALTER TABLE T ADD INDEX (A);"}, db_table("T", col("A", "int")))
        self.assertEqual(plan.items[0].statement, "ALTER TABLE `T` ADD INDEX (`A`);")

    def test_divergent_named_index_is_rebuilt_in_a_single_statement_but_automatic_one_is_not(self):
        named = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT, KEY IX (A, B));"}, db_table("T", col("A", "int"), col("B", "int"), indexes=[idx("IX", "A")]))
        self.assertEqual(named.items[0].statement, "ALTER TABLE `T` DROP INDEX `IX`, ADD INDEX `IX` (`A`, `B`);")
        automatic = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A VARCHAR(100)); ALTER TABLE T ADD INDEX (A(10));"}, db_table("T", col("A", "varchar(100)"), indexes=[idx("a", "A", prefixes=[20])]))
        self.assertEqual(automatic.items, [])
        self.assertIn("nome automatico", automatic.skipped[0][1])

    def test_primary_key_add_and_change(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT, PRIMARY KEY (A, B));"}
        columns = [col("A", "int", null=False), col("B", "int", null=False)]
        missing = self.plan(scripts, db_table("T", *columns))
        self.one_item(missing, statement="ALTER TABLE `T` ADD PRIMARY KEY (`A`, `B`);", risk=RISK_MEDIUM)
        different = self.plan(scripts, db_table("T", *columns, indexes=[idx("PRIMARY", "A", unique=True)]))
        self.one_item(different, statement="ALTER TABLE `T` DROP PRIMARY KEY, ADD PRIMARY KEY (`A`, `B`);", risk=RISK_HIGH, commented=True)

    def test_foreign_key_add_and_change(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, CONSTRAINT F FOREIGN KEY (A) REFERENCES O (ID) ON DELETE CASCADE);"}
        missing = self.plan(scripts, db_table("T", col("A", "int")))
        self.assertEqual(missing.items[0].statement, "ALTER TABLE `T` ADD CONSTRAINT `F` FOREIGN KEY (`A`) REFERENCES `O` (`ID`) ON DELETE CASCADE;")
        wrong = self.plan(scripts, db_table("T", col("A", "int"), foreign_keys=[fk("F", ["A"], "O", ["ID"], delete="RESTRICT")]))
        self.assertEqual(wrong.items[0].statement.split(",")[0], "ALTER TABLE `T` DROP FOREIGN KEY `F`")

    # --- tabelas ---
    def test_missing_table_gets_one_create_table_and_its_children_are_not_repeated(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT, PRIMARY KEY (A), KEY IX (B), CONSTRAINT F FOREIGN KEY (B) REFERENCES O (ID));"}
        plan = self.plan(scripts, db_table("OUTRA", col("X", "int")))
        kinds = [(i.bucket, i.statement.split(" ")[0] + " " + i.statement.split(" ")[1]) for i in plan.items]
        self.assertEqual(kinds, [(BUCKET_CREATE_TABLE, "CREATE TABLE"), (fix.BUCKET_FOREIGN, "ALTER TABLE")])
        self.assertIn("ADD CONSTRAINT `F` FOREIGN KEY", plan.items[1].statement)

    def test_table_known_only_from_the_base_database_cannot_be_recreated(self):
        plan = self.plan({"SCRIPT_1.SQL": "ALTER TABLE ANTIGA ADD A INT;"}, db_table("OUTRA", col("X", "int")))
        self.assertEqual(plan.items, [])
        self.assertIn("nao e conhecida por inteiro", plan.skipped[0][1])

    def test_table_options_engine_is_high_risk_and_charset_is_medium(self):
        scripts = {"SCRIPT_1.SQL": "CREATE TABLE T (A INT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"}
        plan = self.plan(scripts, db_table("T", col("A", "int"), engine="MyISAM", collation="latin1_swedish_ci"))
        risks = {i.statement: i.risk for i in plan.items}
        self.assertEqual(risks["ALTER TABLE `T` ENGINE=innodb;"], RISK_HIGH)
        self.assertEqual(risks["ALTER TABLE `T` DEFAULT CHARACTER SET utf8mb4;"], RISK_MEDIUM)

    # --- removidos ---
    def test_things_that_scripts_removed_and_still_exist(self):
        scripts = {
            "SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT, PRIMARY KEY (A), KEY IX (A), CONSTRAINT F FOREIGN KEY (A) REFERENCES O (ID)); CREATE TABLE V (X INT);",
            "SCRIPT_2.SQL": "DROP INDEX IX ON T; ALTER TABLE T DROP COLUMN B; ALTER TABLE T DROP PRIMARY KEY; ALTER TABLE T DROP FOREIGN KEY F; DROP TABLE V;",
        }
        still = db_table(
            "T", col("A", "int", null=False), col("B", "int"),
            indexes=[idx("IX", "A"), idx("PRIMARY", "A", unique=True)], foreign_keys=[fk("F", ["A"], "O", ["ID"])],
        )
        plan = self.plan(scripts, [still, db_table("V", col("X", "int"))])
        by = {i.statement: i for i in plan.items}
        drop_column = by["ALTER TABLE `T` DROP COLUMN `B`;"]
        drop_table = by["DROP TABLE `V`;"]
        self.assertTrue(drop_column.destructive and drop_table.destructive)
        self.assertTrue(drop_column.commented and drop_table.commented)
        self.assertTrue(by["ALTER TABLE `T` DROP PRIMARY KEY;"].commented)
        self.assertFalse(by["ALTER TABLE `T` DROP INDEX `IX`;"].commented)
        self.assertFalse(by["ALTER TABLE `T` DROP FOREIGN KEY `F`;"].commented)

    # --- o que NAO gera comando ---
    def test_indeterminate_uncertain_and_compatible_findings_generate_nothing(self):
        from app.sqlaudit.version_tree import VersionTree

        tree = VersionTree.build(["1.1", "1.2"])
        tree.set_base("1.2", "1.1")  # fork nao informado: o herdado e incerto
        model = self.model({"1.1": {"SCRIPT_1.SQL": "CREATE TABLE T (A INT, B INT, KEY IX (A));"}, "1.2": {"SCRIPT_2.SQL": "ALTER TABLE T ADD C INT;"}}, "1.2", tree)
        plan = build_fix_plan(model, compare(model, db_schema(db_table("T", col("A", "bigint"), col("C", "int")))), "1.2", "B")
        self.assertEqual(plan.items, [])
        self.assertGreaterEqual(plan.not_handled.get("INDETERMINADO", 0), 2)

    def test_a_clean_comparison_generates_nothing(self):
        plan = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A INT);"}, db_table("T", col("A", "int")))
        self.assertEqual((plan.items, plan.skipped, plan.not_handled), ([], [], {}))

    def test_uninterpreted_commands_are_announced(self):
        plan = self.plan({"SCRIPT_1.SQL": "CREATE TABLE T (A INT); INSERT INTO T VALUES (1);"}, db_table("T", col("A", "int")))
        self.assertTrue(any("nao foram interpretados" in n for n in plan.notes))

    def test_statements_are_ordered_by_dependency(self):
        scripts = {
            "SCRIPT_1.SQL": "CREATE TABLE NOVA (A INT, CONSTRAINT F FOREIGN KEY (A) REFERENCES O (ID)); CREATE TABLE T (A INT, B INT, KEY IX (A)); CREATE TABLE V (X INT);",
            "SCRIPT_2.SQL": "ALTER TABLE T ADD C INT; ALTER TABLE T DROP COLUMN B; DROP TABLE V;",
        }
        plan = self.plan(scripts, [db_table("T", col("A", "int"), col("B", "int")), db_table("V", col("X", "int"))])
        buckets = [i.bucket for i in plan.items]
        self.assertEqual(buckets, sorted(buckets))
        self.assertEqual(plan.items[0].bucket, BUCKET_CREATE_TABLE)
        self.assertEqual(plan.items[-1].bucket, fix.BUCKET_DROP_TABLE)


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class ScriptFileTests(CompareTestCase):
    def mixed_plan(self):
        scripts = {
            "SCRIPT_1.SQL": "CREATE TABLE T (A VARCHAR(50), B INT, KEY IX (B), CONSTRAINT F FOREIGN KEY (B) REFERENCES O (ID)); CREATE TABLE V (X INT);",
            "SCRIPT_2.SQL": "ALTER TABLE T ADD C INT; ALTER TABLE T DROP COLUMN B; DROP TABLE V; CREATE TABLE NOVA (A INT, PRIMARY KEY (A));",
        }
        model = self.model(**scripts)
        schema = db_schema(
            db_table("T", col("A", "varchar(20)"), col("B", "int"), indexes=[idx("IX", "B")], foreign_keys=[fk("F", ["B"], "O", ["ID"])]),
            db_table("V", col("X", "int")),
        )
        return build_fix_plan(model, compare(model, schema), "48.2.30.72", "BANCO_AUTCOM_379_48_02_30_72")

    def test_header_use_is_commented_and_every_risky_command_is_commented(self):
        plan = self.mixed_plan()
        text = render_script([plan], user="ana")
        self.assertIn("NAO EXECUTE SEM REVISAR", text)
        self.assertIn("por ana", text)
        self.assertIn("-- USE `BANCO_AUTCOM_379_48_02_30_72`;", text)
        live = [line for line in text.splitlines() if line.strip() and not line.startswith("--")]
        for item in plan.items:
            first_line = item.statement.splitlines()[0]
            if item.commented:
                self.assertNotIn(first_line, live, item.statement)
                self.assertIn("-- " + first_line, text.splitlines())
            else:
                self.assertIn(first_line, live, item.statement)
        self.assertTrue(plan.commented_count >= 2)
        self.assertFalse([line for line in live if re.search(r"\bDROP (TABLE|COLUMN|PRIMARY)\b", line)])
        self.assertFalse([line for line in live if line.startswith("USE ")])

    def test_every_executable_statement_is_fully_understood_by_the_system_parser(self):
        plan = self.mixed_plan()
        self.assertTrue(any(not i.commented for i in plan.items))
        for item in plan.items:  # tambem os comentados: se alguem descomentar, o SQL tem que estar correto
            operations = parse_text(item.statement)
            self.assertTrue(operations and all(op.interpreted for op in operations), item.statement)

    def test_each_command_carries_reason_origin_and_warnings(self):
        text = render_script([self.mixed_plan()])
        self.assertIn("motivo: coluna ausente no banco (origem: SCRIPT_2.SQL linha 1)", text)
        self.assertIn("cuidado:", text)
        self.assertIn("COMENTADO: descomente apenas depois de revisar", text)
        self.assertIn("[ALTO | DESTRUTIVO]", text)

    def test_several_versions_and_empty_plan(self):
        empty = build_fix_plan(self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT);"}), compare(self.model(**{"SCRIPT_1.SQL": "CREATE TABLE T (A INT);"}), db_schema(db_table("T", col("A", "int")))), "1.9", "B2")
        text = render_script([self.mixed_plan(), empty])
        self.assertIn("-- Versao 48.2.30.72 | banco", text)
        self.assertIn("-- Versao 1.9 | banco B2", text)
        self.assertIn("-- Nenhum comando para esta versao.", text)

    def test_write_script_is_atomic_utf8_and_only_sql(self):
        plan = self.mixed_plan()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "corretivo.sql"
            path.write_text("antigo", encoding="utf-8")
            text = write_script([plan], path, user="ana")
            self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertNotIn("\r", path.read_bytes().decode("utf-8"))
            with self.assertRaises(ValueError):
                write_script([plan], Path(folder) / "corretivo.txt")
            with self.assertRaises(OSError):
                write_script([plan], Path(folder) / "nao-existe" / "x.sql")
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["corretivo.sql"])

    def test_the_script_never_contains_secrets_or_the_model_internals(self):
        text = render_script([self.mixed_plan()])
        for forbidden in ("password", "senha", "ExpectedModel", "sha256"):
            self.assertNotIn(forbidden, text.lower() if forbidden != "ExpectedModel" else text)


if __name__ == "__main__":
    unittest.main()
