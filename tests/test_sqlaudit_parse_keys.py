"""Fase 6 do parser: DROP/CHANGE/RENAME, indices, PK, FK, opcoes de tabela e charset."""

import unittest

from app.sqlaudit import sql_parse
from app.sqlaudit.sql_parse import STATUS_INTERPRETED, STATUS_UNSUPPORTED, parse_text

AVAILABLE = sql_parse.is_available()


def one(sql):
    operations = parse_text(sql)
    assert len(operations) == 1, operations
    return operations[0]


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class ColumnAndTableChangeTests(unittest.TestCase):
    def test_drop_column_one_operation_per_column(self):
        operations = parse_text("ALTER TABLE t DROP COLUMN a, DROP b")
        self.assertEqual([(o.kind, o.name, o.status) for o in operations], [("drop_column", "a", STATUS_INTERPRETED), ("drop_column", "b", STATUS_INTERPRETED)])

    def test_rename_column_and_rename_table(self):
        op = one("ALTER TABLE t RENAME COLUMN a TO b")
        self.assertEqual((op.kind, op.name, op.new_name), ("rename_column", "a", "b"))
        op = one("ALTER TABLE `db`.`t` RENAME TO u")
        self.assertEqual((op.kind, op.table, op.schema, op.new_name), ("rename_table", "t", "db", "u"))

    def test_rename_table_statement_with_several_pairs_and_quoting(self):
        operations = parse_text("RENAME TABLE `a` TO `b`, x.c TO x.d")
        self.assertEqual([(o.table, o.new_name, o.schema) for o in operations], [("a", "b", ""), ("c", "d", "x")])
        for bad in ("RENAME TABLE a TO other.b", "RENAME TABLE a", "RENAME TABLE a TO b,"):
            self.assertFalse(one(bad).interpreted, bad)

    def test_drop_table_and_drop_view(self):
        op = one("DROP TABLE t")
        self.assertEqual((op.kind, op.table, op.if_exists), ("drop_table", "t", False))
        self.assertFalse(one("DROP VIEW v").interpreted)

    def test_multiple_actions_in_one_alter_keep_their_order(self):
        kinds = [o.kind for o in parse_text("ALTER TABLE t DROP COLUMN a, ADD b INT, CHANGE c d BIGINT, DROP INDEX ix")]
        self.assertEqual(kinds, ["drop_column", "add_column", "change_column", "drop_index"])


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class IndexAndKeyTests(unittest.TestCase):
    def test_named_unnamed_unique_prefix_and_fulltext_indexes(self):
        cases = {
            "ALTER TABLE t ADD INDEX ix (a, b)": ("ix", ("a", "b"), False, ""),
            "ALTER TABLE t ADD KEY (a)": (None, ("a",), False, ""),
            "ALTER TABLE t ADD UNIQUE KEY uq (a)": ("uq", ("a",), True, ""),
            "ALTER TABLE t ADD UNIQUE (a, b DESC)": (None, ("a", "b"), True, ""),
            "ALTER TABLE t ADD FULLTEXT INDEX ft (a)": ("ft", ("a",), False, "fulltext"),
            "ALTER TABLE t ADD SPATIAL INDEX sp (a)": ("sp", ("a",), False, "spatial"),
            "CREATE INDEX ix ON t (a, b)": ("ix", ("a", "b"), False, ""),
            "CREATE UNIQUE INDEX ux ON t (a)": ("ux", ("a",), True, ""),
        }
        for sql, (name, columns, unique, kind) in cases.items():
            op = one(sql)
            self.assertEqual((op.kind, op.table, op.status), ("add_index", "t", STATUS_INTERPRETED), sql)
            self.assertEqual((op.index.name, op.index.columns, op.index.unique, op.index.kind), (name, columns, unique, kind), sql)

    def test_prefix_length_is_kept_per_column(self):
        self.assertEqual(one("ALTER TABLE t ADD INDEX ix (a(10), b)").index.prefixes, (10, None))

    def test_drop_index_both_forms(self):
        for sql in ("ALTER TABLE t DROP INDEX ix", "ALTER TABLE t DROP KEY ix", "DROP INDEX ix ON t"):
            op = one(sql)
            self.assertEqual((op.kind, op.table, op.name), ("drop_index", "t", "ix"), sql)

    def test_primary_key_add_and_drop(self):
        self.assertEqual(one("ALTER TABLE t ADD PRIMARY KEY (a, b)").primary_key, ("a", "b"))
        self.assertEqual(one("ALTER TABLE t ADD CONSTRAINT pk PRIMARY KEY (a)").kind, "add_primary_key")
        self.assertEqual(one("ALTER TABLE t DROP PRIMARY KEY").kind, "drop_primary_key")

    def test_foreign_key_columns_reference_and_rules(self):
        fk = one("ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (a, b) REFERENCES db.o (x, y) ON DELETE CASCADE ON UPDATE SET NULL").foreign_key
        self.assertEqual((fk.name, fk.columns, fk.ref_table, fk.ref_schema, fk.ref_columns), ("fk", ("a", "b"), "o", "db", ("x", "y")))
        self.assertEqual((fk.on_delete, fk.on_update), ("CASCADE", "SET NULL"))
        unnamed = one("ALTER TABLE t ADD FOREIGN KEY (a) REFERENCES o (id)").foreign_key
        self.assertEqual((unnamed.name, unnamed.on_delete, unnamed.on_update), (None, "", ""))
        self.assertEqual(one("ALTER TABLE t DROP FOREIGN KEY fk").name, "fk")

    def test_what_is_not_understood_stays_unsupported_and_names_the_table(self):
        for sql in (
            "ALTER TABLE t ADD CONSTRAINT ck CHECK (a > 0)",
            "ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (a) REFERENCES o (id) MATCH FULL",
            "ALTER TABLE t ADD INDEX ix ((a + 1))",
        ):
            op = one(sql)
            self.assertEqual((op.status, op.table), (STATUS_UNSUPPORTED, "t"), sql)

    def test_create_table_carries_pk_indexes_and_foreign_keys(self):
        ops = parse_text(
            "CREATE TABLE t (a INT, b INT, PRIMARY KEY (a), UNIQUE KEY uq (b), KEY ix (a, b), "
            "CONSTRAINT fk FOREIGN KEY (a) REFERENCES o (id) ON DELETE CASCADE, FOREIGN KEY (b) REFERENCES p (id), "
            "FULLTEXT KEY ft (b)) ENGINE=InnoDB"
        )
        (main,) = ops
        self.assertEqual(main.primary_key, ("a",))
        self.assertEqual([(i.name, i.unique, i.kind) for i in main.indexes], [("uq", True, ""), ("ix", False, ""), ("ft", False, "fulltext")])
        self.assertEqual([(f.name, f.ref_table, f.on_delete) for f in main.foreign_keys], [("fk", "o", "CASCADE"), (None, "p", "")])

    def test_inline_primary_key_and_unique_stay_on_the_column(self):
        (main,) = parse_text("CREATE TABLE t (a INT PRIMARY KEY, b INT UNIQUE)")
        self.assertEqual([(c.name, c.primary_key, c.unique) for c in main.columns], [("a", True, False), ("b", False, True)])


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class TableOptionTests(unittest.TestCase):
    def test_alter_engine_charset_and_collation(self):
        self.assertEqual(one("ALTER TABLE t ENGINE=MyISAM").options, (("engine", "myisam"),))
        self.assertEqual(
            one("ALTER TABLE t DEFAULT CHARSET=utf8mb4 COLLATE utf8mb4_bin").options,
            (("charset", "utf8mb4"), ("collation", "utf8mb4_bin")),
        )

    def test_convert_to_character_set_with_and_without_collation(self):
        op = one("ALTER TABLE `db`.`t` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        self.assertEqual((op.kind, op.table, op.schema, op.options), ("convert_charset", "t", "db", (("charset", "utf8mb4"), ("collation", "utf8mb4_bin"))))
        self.assertEqual(one("alter table t convert to charset latin1").options, (("charset", "latin1"),))

    def test_counter_and_execution_hints_are_not_structure(self):
        self.assertEqual(parse_text("ALTER TABLE t AUTO_INCREMENT=10"), [])
        self.assertEqual(parse_text("ALTER TABLE t ALGORITHM=INPLACE, LOCK=NONE"), [])

    def test_options_not_compared_yet(self):
        for sql in ("ALTER TABLE t ROW_FORMAT=DYNAMIC", "ALTER TABLE t COMMENT='x'", "ALTER TABLE t ORDER BY a"):
            op = one(sql)
            self.assertEqual((op.status, op.table, op.affects_columns), (STATUS_UNSUPPORTED, "t", False), sql)


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class FidelityStillProtectsTests(unittest.TestCase):
    def test_nothing_new_is_validated_without_being_understood(self):
        for sql in (
            "ALTER TABLE t ADD a INT VISIBLE",
            "ALTER TABLE t PARTITION BY HASH(a) PARTITIONS 4",
            "ALTER TABLE t DROP CHECK ck",
            "ALTER TABLE t DROP CONSTRAINT ck",
            "CREATE INDEX ix ON t ((a + 1))",
            "DROP INDEX ix",
            "TRUNCATE TABLE t",
        ):
            self.assertFalse(all(op.interpreted for op in parse_text(sql)) and parse_text(sql), sql)

    def test_a_drop_with_a_trailing_comma_is_a_syntax_error(self):
        self.assertEqual(one("ALTER TABLE t DROP COLUMN a,").status, sql_parse.STATUS_ERROR)


if __name__ == "__main__":
    unittest.main()
