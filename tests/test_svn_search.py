import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from app.svn_search import (
    _SvnSearchCancelled,
    _run_svn_command,
    parse_svn_log,
    search_svn,
)


class SvnSearchTests(unittest.TestCase):
    @patch("app.svn_search.subprocess.Popen")
    def test_running_svn_process_can_be_cancelled(self, popen_mock):
        process = popen_mock.return_value
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["svn"], 0.2),
            ("", ""),
        ]
        cancellation = threading.Event()
        cancellation.set()

        with self.assertRaises(_SvnSearchCancelled):
            _run_svn_command(["svn", "log"], 60, cancellation)

        process.terminate.assert_called_once()

    @patch("app.svn_search.subprocess.run")
    def test_find_all_continues_after_first_matching_batch(self, run_mock):
        latest = SimpleNamespace(
            returncode=0,
            stdout="r10000 | usuario | data | 1 line\n",
            stderr="",
        )
        newest_match = SimpleNamespace(
            returncode=0,
            stdout=(
                "r9000 | usuario | data | 1 line\nREQUISITO 77\n"
            ),
            stderr="",
        )
        oldest_match = SimpleNamespace(
            returncode=0,
            stdout=(
                "r4000 | usuario | data | 1 line\nREQUISITO 77\n"
            ),
            stderr="",
        )
        run_mock.side_effect = [latest, newest_match, oldest_match]

        result = search_svn(
            "svn://servidor/repositorio",
            requirement="77",
            find_all=True,
        )

        self.assertEqual(
            [entry.revision for entry in result.entries],
            ["r9000", "r4000"],
        )

    @patch("app.svn_search.subprocess.run")
    def test_interrupted_svn_batch_is_retried_with_smaller_range(self, run_mock):
        latest = SimpleNamespace(
            returncode=0,
            stdout="r10000 | usuario | data | 1 line\n",
            stderr="",
        )
        interrupted = SimpleNamespace(
            returncode=3221225786,
            stdout="",
            stderr="",
        )
        matching = SimpleNamespace(
            returncode=0,
            stdout="\n".join(
                [
                    "------------------------------------------------------------------------",
                    "r9000 | usuario | data | 1 line",
                    "REQUISITO 362150",
                    "------------------------------------------------------------------------",
                ]
            ),
            stderr="",
        )
        run_mock.side_effect = [latest, interrupted, matching]
        progress = []

        result = search_svn(
            "svn://servidor/repositorio",
            requirement="362150",
            progress_callback=progress.append,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.entries[0].revision, "r9000")
        self.assertTrue(
            any("bloco de 2500 revisoes" in message for message in progress)
        )
        retried_command = run_mock.call_args.args[0]
        self.assertIn("10000:7501", retried_command)

    @patch("app.svn_search.subprocess.run")
    def test_requirement_search_moves_to_older_revision_batch(self, run_mock):
        latest = SimpleNamespace(
            returncode=0,
            stdout=(
                "------------------------------------------------------------------------\n"
                "r10000 | usuario | data | 1 line\n"
                "------------------------------------------------------------------------\n"
            ),
            stderr="",
        )
        empty_batch = SimpleNamespace(
            returncode=0,
            stdout="------------------------------------------------------------------------\n",
            stderr="",
        )
        matching_batch = SimpleNamespace(
            returncode=0,
            stdout="\n".join(
                [
                    "------------------------------------------------------------------------",
                    "r4550 | usuario | data | 2 lines",
                    "REQUISITO 362150",
                    "ajuste",
                    "------------------------------------------------------------------------",
                ]
            ),
            stderr="",
        )
        run_mock.side_effect = [latest, empty_batch, matching_batch]
        progress = []

        result = search_svn(
            "svn://servidor/repositorio",
            requirement="362150",
            progress_callback=progress.append,
        )

        self.assertEqual(result.entries[0].revision, "r4550")
        self.assertIn("10000 a 5001", progress[0])
        self.assertIn("5000 a 1", progress[1])
        self.assertEqual(run_mock.call_count, 3)

    @patch("app.svn_search.subprocess.run")
    def test_requirement_search_does_not_limit_revisions_examined(self, run_mock):
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "\n".join(
            [
                "------------------------------------------------------------------------",
                "r455383 | banzato | 2026-01-30 10:00:00 -0300 | 5 lines",
                "Changed paths:",
                "   M /Padrao/379.48.2.30.74.150.45/padra_atualizado/fonte.pas",
                "",
                "REQUISITO 362150",
                "aplicar na 30.74.150.45",
                "------------------------------------------------------------------------",
            ]
        )
        run_mock.return_value.stderr = ""

        result = search_svn(
            "svn://10.0.0.205/Padrao/027.18/27.18.379",
            requirement="362150",
        )

        command = run_mock.call_args.args[0]
        self.assertIn("--search", command)
        self.assertNotIn("-l", command)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].revision, "r455383")
        self.assertEqual(result.entries[0].requirement, "REQUISITO 362150")

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

    def test_changed_version_uses_project_version_folder_not_changed_file(self):
        lines = [
            "------------------------------------------------------------------------",
            "r483733 | thobias | 2026-07-13 15:05:30 -0300 (seg, 13 jul 2026) | 10 lines",
            "Changed paths:",
            "   M /Padrao/027.18/27.18.379/379.48.02.REV.FECHADAs/379.48.02.30.FECHADAS/379.48.2.30.74.150.45.59/padra_atualizado/fontes/movimentacaoitens/untTransfArmazens.pas",
            "Req. 446120",
            "Analise de Requisito:",
            "------------------------------------------------------------------------",
        ]

        entry = parse_svn_log(lines)[0]

        self.assertEqual(entry.changed_version, "379.48.2.30.74.150.45.59")
        self.assertEqual(
            entry.changed_parent_path,
            "/Padrao/027.18/27.18.379/379.48.02.REV.FECHADAs/379.48.02.30.FECHADAS",
        )

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
