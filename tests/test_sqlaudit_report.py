import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.sqlaudit import db_reader, report, sql_parse
from app.sqlaudit.audit_runner import AuditRequest, ServerConnection, VersionRequest, run_audit
from app.sqlaudit.version_discovery import discover_versions
from app.sqlaudit.version_tree import VersionTree
from tests.test_sqlaudit_reader import FakeConnection, full_responses

AVAILABLE = sql_parse.is_available()
VERSION = "48.2.30.72"
DATABASE = "BANCO_AUTCOM_379_48_02_30_72"


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def run_with(self, script, columns, table="T"):
        root = self.tmp / "379.48.2.30"
        folder = root / VERSION / "SCRIPTS"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SCRIPT_1.SQL").write_text(script, encoding="utf-8")
        responses = full_responses(DATABASE)
        responses[db_reader.SQL_TABLES] = [(table, "InnoDB", "utf8mb4_general_ci", None)]
        responses[db_reader.SQL_COLUMNS] = [(table, n, i, t, "YES", None, "", None, None, "") for i, (n, t) in enumerate(columns, 1)]
        folders = {f.version: f for f in discover_versions(root).versions}
        request = AuditRequest(root, [VersionRequest(VERSION, DATABASE)], folders, VersionTree.build(list(folders)),
                               ServerConnection("h", 3306, "u", "Segredo123"))
        return run_audit(request, connector=lambda **_: FakeConnection(responses))

    def sample(self):
        return self.run_with("CREATE TABLE T (A INT, B VARCHAR(10)); DROP VIEW v;", [("A", "bigint")])


class SummaryTests(ReportTestCase):
    def test_summary_counts_and_audit_line(self):
        run = self.sample()
        self.assertIn("1 versao(oes)", report.summary_text(run))
        self.assertIn("divergente", report.summary_text(run))
        line = report.audit_line(run, "concluida")
        self.assertTrue(line.startswith("Auditoria SQL concluida | usuario="))
        self.assertIn(f"destino={DATABASE}", line)
        self.assertNotIn("Segredo123", line)
        self.assertEqual(line.count(" | "), 10)  # mesmo numero de campos das demais linhas de Auditoria

    def test_audit_line_never_breaks_the_field_separator(self):
        run = self.sample()
        run.root = Path("C:/a|b/c\nd")
        line = report.audit_line(run, "concluida")
        self.assertEqual(line.count(" | "), 10)
        self.assertNotIn("\n", line)

    def test_cancelled_and_failed_versions_are_in_the_summary(self):
        run = self.sample()
        run.cancelled = True
        run.versions[0].error = "falhou"
        text = report.summary_text(run)
        self.assertIn("1 com erro", text)
        self.assertIn("cancelada", text)


class ExportTests(ReportTestCase):
    def test_csv_has_bom_semicolon_header_and_one_row_per_object(self):
        run = self.sample()
        path = self.tmp / "r.csv"
        self.assertEqual(report.write_report(run, path), "csv")
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(raw.decode("utf-8-sig").splitlines(), delimiter=";"))
        self.assertEqual(tuple(rows[0]), report.CSV_COLUMNS)
        self.assertEqual(len(rows) - 1, len(report.object_rows(run)))
        by_column = {r[4]: r for r in rows[1:] if r[2] == "coluna"}
        self.assertEqual(by_column["B"][5], "AUSENTE")
        self.assertIn("esperado int, encontrado bigint", by_column["A"][6])

    def test_csv_neutralizes_spreadsheet_formulas(self):
        run = self.run_with("CREATE TABLE `=HYPERLINK(1)` (A INT);", [("A", "int")], table="=HYPERLINK(1)")
        path = self.tmp / "f.csv"
        report.write_report(run, path)
        text = path.read_text(encoding="utf-8-sig")
        self.assertIn("'=HYPERLINK(1)", text)
        self.assertNotIn(";=HYPERLINK", text)

    def test_html_escapes_object_names_and_has_no_script_injection(self):
        run = self.run_with("CREATE TABLE `<b>x</b>` (A INT);", [("A", "int")], table="<b>x</b>")
        path = self.tmp / "r.html"
        self.assertEqual(report.write_report(run, path), "html")
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("<b>x</b>", text)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", text)
        self.assertIn("não comprova", text)
        self.assertIn("Auditoria de Estrutura SQL", text)
        self.assertNotIn("Segredo123", text)

    def test_json_is_valid_complete_and_without_secrets(self):
        run = self.sample()
        path = self.tmp / "r.json"
        self.assertEqual(report.write_report(run, path), "json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["versoes"][0]["banco"], DATABASE)
        self.assertEqual(data["versoes"][0]["contagem_objetos"]["AUSENTE"], 1)
        self.assertTrue(data["versoes"][0]["comandos"])
        self.assertIn("não comprova", data["aviso"])
        self.assertNotIn("Segredo123", path.read_text(encoding="utf-8"))

    def test_unknown_extension_and_unwritable_folder_are_clean_errors_and_leave_no_temp_file(self):
        run = self.sample()
        with self.assertRaises(ValueError):
            report.write_report(run, self.tmp / "r.xlsx")
        with self.assertRaises(OSError):
            report.write_report(run, self.tmp / "nao-existe" / "r.csv")
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(".")], [])

    def test_export_overwrites_atomically(self):
        run = self.sample()
        path = self.tmp / "r.json"
        path.write_text("antigo", encoding="utf-8")
        report.write_report(run, path)
        self.assertTrue(path.read_text(encoding="utf-8").startswith("{"))

    def test_labels_are_in_portuguese_for_every_status(self):
        self.assertEqual(set(report.STATUS_LABELS), set(report.PRECEDENCE))
        self.assertEqual(report.label("NAO_SUPORTADO"), "Não suportado")


if __name__ == "__main__":
    unittest.main()
