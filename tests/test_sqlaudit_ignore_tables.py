import unittest

from app import settings
from app.sqlaudit.comparator import (
    AUSENTE,
    DIVERGENTE,
    compare,
    ignore_tables,
    normalize_prefixes,
    table_summary,
)
from tests.test_sqlaudit_compare import AVAILABLE, CompareTestCase, col, db_schema, db_table
from tests.test_settings import SettingsTestCase

SCRIPTS = {
    "SCRIPT_1.SQL": (
        "CREATE TABLE CONECTA_SISEXT (ID BIGINT NOT NULL, NOME VARCHAR(255));"
        "CREATE TABLE CLIENTES (ID INT NOT NULL, EMAIL VARCHAR(100));"
        "CREATE TABLE CLIENTES_X (ID INT NOT NULL);"
    )
}


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class IgnoreTablesTests(CompareTestCase):
    def compared(self):
        schema = db_schema(
            db_table("CONECTA_SISEXT", col("ID", "varchar(7)", null=False), col("NOME", "varchar(50)")),
            db_table("CLIENTES", col("ID", "int", null=False), col("EMAIL", "varchar(50)")),
            db_table("CLIENTES_X", col("ID", "int", null=False)),
        )
        return compare(self.model(**SCRIPTS), schema)

    def test_prefix_removes_only_matching_tables_and_counts_them(self):
        result = ignore_tables(self.compared(), ["conecta_"])
        self.assertFalse([o for o in result.objects if o.table.upper().startswith("CONECTA_")])
        self.assertTrue([o for o in result.objects if o.table == "CLIENTES"])
        self.assertTrue([o for o in result.objects if o.table == "CLIENTES_X"])
        self.assertEqual(list(result.ignored), ["CONECTA_SISEXT"])
        self.assertGreater(result.ignored["CONECTA_SISEXT"], 1)
        self.assertTrue([w for w in result.warnings if "ignorados pelo prefixo" in w and "conecta_" in w])

    def test_operations_of_ignored_tables_leave_the_script_summary(self):
        before = self.compared()
        self.assertTrue([o for o in before.operations if o.applied.operation.table.upper() == "CONECTA_SISEXT"])
        result = ignore_tables(before, ["CONECTA_"])
        self.assertFalse([o for o in result.operations if o.applied.operation.table.upper() == "CONECTA_SISEXT"])
        self.assertTrue(result.operations)

    def test_no_prefix_changes_nothing(self):
        result = self.compared()
        count = len(result.objects)
        ignore_tables(result, [])
        ignore_tables(result, ["", "  "])
        self.assertEqual(len(result.objects), count)
        self.assertEqual(result.ignored, {})
        self.assertFalse([w for w in result.warnings if "ignorados pelo prefixo" in w])

    def test_prefix_is_a_prefix_not_a_substring(self):
        result = ignore_tables(self.compared(), ["LIENTES"])
        self.assertEqual(result.ignored, {})

    def test_table_summary_ranks_problem_tables_first(self):
        rows = table_summary(self.compared())
        self.assertEqual([name for name, _ in rows][:2], ["CONECTA_SISEXT", "CLIENTES"])
        counts = dict(rows)["CONECTA_SISEXT"]
        self.assertGreaterEqual(counts.get(DIVERGENTE, 0) + counts.get(AUSENTE, 0), 2)
        self.assertEqual(sum(sum(c.values()) for _t, c in rows), len(self.compared().objects))


class PrefixHelperTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_prefixes([" Conecta_ ", "CONECTA_", "", "X"]), ("conecta_", "x"))
        self.assertEqual(normalize_prefixes(None), ())


class IgnoredPrefixSettingsTests(SettingsTestCase):
    def test_roundtrip_dedupes_and_keeps_other_sqlaudit_data(self):
        settings.save_sqlaudit_config("C:/raiz", {}, {})
        settings.save_sqlaudit_ignored_prefixes(["CONECTA_", "conecta_", " ", "TMP_"])
        self.assertEqual(settings.load_sqlaudit_ignored_prefixes(), ["CONECTA_", "TMP_"])
        self.assertEqual(settings.load_sqlaudit_config()["root"], "C:/raiz")
        settings.save_sqlaudit_config("C:/outra", {}, {})
        self.assertEqual(settings.load_sqlaudit_ignored_prefixes(), ["CONECTA_", "TMP_"])

    def test_defaults_to_empty(self):
        self.assertEqual(settings.load_sqlaudit_ignored_prefixes(), [])


if __name__ == "__main__":
    unittest.main()
