import unittest

from app.sqlaudit import sql_parse
from app.sqlaudit.sql_parse import (
    KIND_ADD_COLUMN,
    KIND_CREATE_TABLE,
    KIND_MODIFY_COLUMN,
    STATUS_ERROR,
    STATUS_INTERPRETED,
    STATUS_UNSUPPORTED,
    parse_text,
)

AVAILABLE = sql_parse.is_available()


def parse_one(sql):
    operations = parse_text(sql)
    assert len(operations) == 1, operations
    return operations[0]


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class AddAndModifyColumnTests(unittest.TestCase):
    def test_add_column_scenario_a(self):
        op = parse_one("ALTER TABLE CLIENTES\nADD COLUMN CLI_EMAIL VARCHAR(100);")
        self.assertEqual((op.kind, op.status, op.table), (KIND_ADD_COLUMN, STATUS_INTERPRETED, "CLIENTES"))
        self.assertEqual((op.column.name, op.column.data_type), ("CLI_EMAIL", "varchar(100)"))
        self.assertEqual((op.statement.line_start, op.statement.line_end), (1, 2))

    def test_modify_column_scenario_b(self):
        op = parse_one("ALTER TABLE `CLIENTES` MODIFY COLUMN `CLI_EMAIL` VARCHAR(200);")
        self.assertEqual((op.kind, op.status), (KIND_MODIFY_COLUMN, STATUS_INTERPRETED))
        self.assertEqual(op.column.data_type, "varchar(200)")

    def test_column_attributes(self):
        op = parse_one(
            "ALTER TABLE t ADD COLUMN a INT UNSIGNED NOT NULL DEFAULT 0 AUTO_INCREMENT COMMENT 'x' AFTER b"
        )
        column = op.column
        self.assertEqual(column.data_type, "int unsigned")
        self.assertIs(column.nullable, False)
        self.assertTrue(column.has_default)
        self.assertEqual(column.default, "0")
        self.assertTrue(column.auto_increment)
        self.assertEqual(column.comment, "x")
        self.assertEqual(column.position, "after:b")

    def test_nullable_is_unknown_when_not_declared(self):
        self.assertIsNone(parse_one("ALTER TABLE t ADD a INT").column.nullable)
        self.assertIs(parse_one("ALTER TABLE t ADD a INT NULL").column.nullable, True)

    def test_default_null_is_a_declared_default(self):
        column = parse_one("ALTER TABLE t ADD a INT DEFAULT NULL").column
        self.assertTrue(column.has_default)
        self.assertEqual(column.default, "NULL")

    def test_charset_collation_and_on_update(self):
        column = parse_one("ALTER TABLE t ADD a VARCHAR(10) CHARSET utf8 COLLATE utf8_bin").column
        self.assertEqual((column.charset, column.collation), ("utf8", "utf8_bin"))
        column = parse_one(
            "ALTER TABLE t MODIFY a TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
        ).column
        self.assertIn("CURRENT_TIMESTAMP", column.on_update)

    def test_schema_qualified_table_and_case_insensitive_keywords(self):
        op = parse_one("alter table `db`.`Clientes` add column x int")
        self.assertEqual((op.schema, op.table), ("db", "Clientes"))

    def test_multiple_actions_become_separate_operations(self):
        operations = parse_text("ALTER TABLE t ADD COLUMN a INT, MODIFY COLUMN b TEXT")
        self.assertEqual([op.kind for op in operations], [KIND_ADD_COLUMN, KIND_MODIFY_COLUMN])
        self.assertTrue(all(op.interpreted for op in operations))

    def test_unsupported_action_does_not_hide_the_supported_ones(self):
        operations = parse_text("ALTER TABLE t ADD COLUMN a INT, DROP COLUMN b")
        self.assertEqual([op.status for op in operations], [STATUS_INTERPRETED, STATUS_UNSUPPORTED])
        self.assertIn("DROP COLUMN b", operations[1].reason)

    def test_change_column_is_not_supported_yet(self):
        op = parse_one("ALTER TABLE t CHANGE COLUMN a b BIGINT")
        self.assertEqual(op.status, STATUS_UNSUPPORTED)
        self.assertIn("CHANGE COLUMN", op.reason)

    def test_generated_and_invisible_columns_are_not_supported(self):
        for attribute in ("GENERATED ALWAYS AS (b + 1) VIRTUAL", "INVISIBLE", "CHECK (a > 0)"):
            op = parse_one(f"ALTER TABLE t ADD a INT {attribute}")
            self.assertEqual(op.status, STATUS_UNSUPPORTED, attribute)

    def test_actions_that_do_not_touch_columns_are_marked(self):
        harmless = ("ADD INDEX ix (a)", "DROP INDEX ix", "DROP PRIMARY KEY", "ADD CONSTRAINT fk FOREIGN KEY (a) REFERENCES o (id)")
        for action in harmless:
            self.assertFalse(parse_one(f"ALTER TABLE t {action}").affects_columns, action)
        for action in ("DROP COLUMN b", "CHANGE COLUMN a b INT", "RENAME COLUMN a TO b", "RENAME TO u"):
            self.assertTrue(parse_one(f"ALTER TABLE t {action}").affects_columns, action)

    def test_statements_that_fail_fidelity_still_name_their_table(self):
        for sql in ("ALTER TABLE t RENAME TO u", "ALTER TABLE t ALGORITHM=INPLACE, ADD a INT", "ALTER TABLE t ENGINE=InnoDB"):
            op = parse_one(sql)
            self.assertEqual((op.status, op.table), (STATUS_UNSUPPORTED, "t"), sql)

    def test_drop_table_names_each_dropped_table(self):
        operations = parse_text("DROP TABLE IF EXISTS db.a, b")
        self.assertEqual([(op.table, op.schema, op.status) for op in operations], [("a", "db", STATUS_UNSUPPORTED), ("b", "", STATUS_UNSUPPORTED)])
        self.assertEqual(parse_one("DROP VIEW v").table, "")
        self.assertEqual(parse_one("INSERT INTO t VALUES (1)").table, "")

    def test_executable_comment_is_unwrapped_with_min_version(self):
        op = parse_one("/*!50001 ALTER TABLE t ADD a INT */;")
        self.assertEqual((op.status, op.min_version), (STATUS_INTERPRETED, "50001"))

    def test_executable_comment_in_the_middle_is_not_supported(self):
        op = parse_one("ALTER TABLE t /*!50100 ADD a INT */")
        self.assertEqual(op.status, STATUS_UNSUPPORTED)


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class CreateTableTests(unittest.TestCase):
    def test_columns_and_options(self):
        operations = parse_text(
            "CREATE TABLE IF NOT EXISTS t (id INT NOT NULL AUTO_INCREMENT, n VARCHAR(50) DEFAULT NULL) "
            "ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci"
        )
        (op,) = operations
        self.assertEqual((op.kind, op.status, op.table), (KIND_CREATE_TABLE, STATUS_INTERPRETED, "t"))
        self.assertTrue(op.if_not_exists)
        self.assertEqual([c.name for c in op.columns], ["id", "n"])
        self.assertTrue(op.columns[0].auto_increment)
        self.assertEqual(dict(op.options), {"engine": "innodb", "charset": "latin1", "collation": "latin1_swedish_ci"})

    def test_keys_and_constraints_are_reported_as_not_compared(self):
        operations = parse_text(
            "CREATE TABLE t (id INT, n INT, PRIMARY KEY (id), KEY ix (n), "
            "CONSTRAINT fk FOREIGN KEY (n) REFERENCES o (id)) ENGINE=InnoDB"
        )
        self.assertEqual(operations[0].status, STATUS_INTERPRETED)
        self.assertEqual([op.status for op in operations[1:]], [STATUS_UNSUPPORTED] * 3)
        self.assertTrue(all(op.kind == KIND_CREATE_TABLE for op in operations))

    def test_table_option_not_compared_is_reported(self):
        operations = parse_text("CREATE TABLE t (id INT) ENGINE=InnoDB AUTO_INCREMENT=5 ROW_FORMAT=DYNAMIC")
        self.assertEqual(operations[0].status, STATUS_INTERPRETED)
        self.assertEqual(len([op for op in operations if op.status == STATUS_UNSUPPORTED]), 2)

    def test_like_select_and_temporary_are_not_supported(self):
        for sql in (
            "CREATE TABLE t LIKE o",
            "CREATE TABLE t AS SELECT 1",
            "CREATE TEMPORARY TABLE t (a INT)",
        ):
            self.assertEqual(parse_one(sql).status, STATUS_UNSUPPORTED, sql)

    def test_other_create_kinds_are_not_supported(self):
        for sql in ("CREATE INDEX ix ON t (a)", "CREATE VIEW v AS SELECT 1", "CREATE DATABASE d"):
            self.assertEqual(parse_one(sql).status, STATUS_UNSUPPORTED, sql)

    def test_unsupported_column_attribute_blocks_the_whole_table(self):
        op = parse_one("CREATE TABLE t (a INT, b INT GENERATED ALWAYS AS (a + 1) VIRTUAL)")
        self.assertEqual(op.status, STATUS_UNSUPPORTED)


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class NeverValidateWhatWasNotUnderstoodTests(unittest.TestCase):
    def test_out_of_scope_statements_are_unsupported(self):
        for sql in (
            "FOO BAR",
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a = 1",
            "DROP TABLE t",
            "RENAME TABLE a TO b",
            "TRUNCATE TABLE t",
            "SET @sql = 'ALTER TABLE t ADD a INT'",
            "PREPARE s FROM @sql",
            "USE banco",
        ):
            op = parse_one(sql)
            self.assertEqual(op.status, STATUS_UNSUPPORTED, sql)
            self.assertFalse(op.interpreted)

    def test_silently_dropped_parts_make_the_statement_unsupported(self):
        for sql in (
            "ALTER TABLE t ALGORITHM=INPLACE, ADD a INT",
            "ALTER TABLE t ADD a INT, LOCK=NONE",
        ):
            ops = parse_text(sql)
            self.assertEqual([op.status for op in ops], [STATUS_UNSUPPORTED], sql)

    def test_syntax_errors_and_leftovers_are_errors(self):
        for sql in (
            "ALTER TABLE t ADD COLUMN a INT,",
            "ALTER TABLE t ADD COLUMN (a INT, b INT)",
            "CREATE TABLE t (a INT",
        ):
            ops = parse_text(sql)
            self.assertTrue(all(op.status != STATUS_INTERPRETED for op in ops), sql)
        self.assertEqual(parse_one("ALTER TABLE t ADD COLUMN a INT,").status, STATUS_ERROR)

    def test_unclosed_quote_or_comment_is_an_error(self):
        self.assertEqual(parse_one("ALTER TABLE t ADD a INT DEFAULT 'abc").status, STATUS_ERROR)
        self.assertEqual(parse_one("ALTER TABLE t ADD a INT /* sem fim").status, STATUS_ERROR)

    def test_failure_in_one_statement_does_not_affect_the_others(self):
        operations = parse_text("ALTER TABLE a ADD x INT; FOO BAR; ALTER TABLE b MODIFY y TEXT;")
        self.assertEqual(
            [op.status for op in operations], [STATUS_INTERPRETED, STATUS_UNSUPPORTED, STATUS_INTERPRETED]
        )
        self.assertEqual([op.statement.index for op in operations], [1, 2, 3])

    def test_every_non_interpreted_operation_explains_why(self):
        operations = parse_text("FOO BAR; DROP TABLE t; ALTER TABLE t ADD a INT,; ALTER TABLE t DROP COLUMN b;")
        for op in operations:
            self.assertFalse(op.interpreted)
            self.assertTrue(op.reason)


if __name__ == "__main__":
    unittest.main()
