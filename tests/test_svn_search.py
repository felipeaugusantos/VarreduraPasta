import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.svn_search import parse_svn_log, search_svn


class SvnSearchTests(unittest.TestCase):
    def test_requires_base_path_and_search_term(self):
        result = search_svn("")
        self.assertFalse(result.ok)
        self.assertIn("caminho base", result.error.lower())

        result = search_svn(r"C:\svn")
        self.assertFalse(result.ok)
        self.assertIn("revisao", result.error.lower())

    @patch("app.svn_search.subprocess.run")
    def test_search_by_revision_uses_svn_log(self, run_mock):
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "r123 | usuario | data | 1 line\nREQ-9 ajuste\n"
        run_mock.return_value.stderr = ""

        result = search_svn(r"C:\repo", revision="123")

        self.assertTrue(result.ok)
        self.assertEqual(result.source, "svn")
        self.assertIn("REQ-9 ajuste", result.lines)
        run_mock.assert_called_once()
        self.assertIn("-r", run_mock.call_args.args[0])
        self.assertIn("123", run_mock.call_args.args[0])

    @patch("app.svn_search.subprocess.run")
    def test_falls_back_to_local_file_search_when_svn_is_missing(self, run_mock):
        run_mock.side_effect = FileNotFoundError()
        with TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "script_REQ-10.sql"
            path.write_text("select 1", encoding="utf-8")

            result = search_svn(temp_directory, requirement="REQ-10")

        self.assertTrue(result.ok)
        self.assertEqual(result.source, "arquivos")
        self.assertEqual(result.lines, ["script_REQ-10.sql"])

    def test_parse_svn_log_splits_entries_for_table_view(self):
        lines = [
            "------------------------------------------------------------------------",
            "r483981 | rafael.alves | 2026-07-14 17:08:45 -0300 (ter, 14 jul 2026) | 126 lines",
            "Changed paths:",
            "   M /Padrao/versao/projeto_cloud",
            "?Req. 450831",
            "Analise de Requisito:",
            "Resumo do ajuste",
            "------------------------------------------------------------------------",
            "r483980 | maria | 2026-07-14 17:04:47 -0300 (ter, 14 jul 2026) | 8 lines",
            "Changed paths:",
            "   A /Padrao/versao/outro_projeto",
            "",
            "Req. 450832",
            "Outro resumo",
            "------------------------------------------------------------------------",
        ]

        entries = parse_svn_log(lines)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].revision, "r483981")
        self.assertEqual(entries[0].author, "rafael.alves")
        self.assertEqual(entries[0].requirement, "Req. 450831")
        self.assertEqual(entries[0].paths, ["M /Padrao/versao/projeto_cloud"])
        self.assertEqual(entries[0].display_date, "2026-07-14 17:08:45")
        self.assertEqual(entries[0].first_changed_path, "/Padrao/versao/projeto_cloud")
        self.assertEqual(entries[0].changed_version, "projeto_cloud")
        self.assertEqual(entries[0].changed_parent_path, "/Padrao/versao")
        self.assertEqual(entries[0].summary, "Resumo do ajuste")
        self.assertIn("Caminhos alterados:", entries[0].detail_text)
        self.assertEqual(entries[1].revision, "r483980")

    @patch("app.svn_search.subprocess.run")
    def test_revision_and_requirement_filter_keeps_complete_entry(self, run_mock):
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "\n".join(
            [
                "------------------------------------------------------------------------",
                "r123 | joao | 2026-07-14 10:00:00 -0300 (ter, 14 jul 2026) | 4 lines",
                "Changed paths:",
                "   M /Projeto",
                "Req. 55",
                "Descricao completa",
                "------------------------------------------------------------------------",
            ]
        )
        run_mock.return_value.stderr = ""

        result = search_svn(r"C:\repo", revision="123", requirement="55")

        self.assertTrue(result.ok)
        self.assertEqual(len(result.entries), 1)
        self.assertIn("Descricao completa", result.lines)


if __name__ == "__main__":
    unittest.main()
