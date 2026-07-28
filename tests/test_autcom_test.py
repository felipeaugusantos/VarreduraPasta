import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from app.autcom_test import (
    AutcomTestResult,
    resolve_test_layout,
    run_autcom_test,
    synchronize_zip_files,
    test_folder_name,
    validate_process_startup,
)


class AutcomTestMappingTests(unittest.TestCase):
    def test_version_selects_branch_and_sub_branch(self):
        self.assertEqual(
            test_folder_name("379.48.2.30.74.111.181"),
            "PastaTesteCopia_74_111",
        )
        self.assertEqual(
            test_folder_name("379.48.2.30.74.150.90_CLOUD"),
            "PastaTesteCopia_74_150",
        )

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(ValueError):
            test_folder_name("versao-invalida")

    def test_layout_uses_version_base_directory(self):
        layout = resolve_test_layout(
            Path(r"C:\VERSOES_FECHADAS") / "379.48.2.30.74.111.181"
        )
        self.assertEqual(
            layout.root.name.lower(),
            "pastatestecopia_74_111",
        )
        self.assertEqual(layout.exe_directory.name, "exe")
        self.assertEqual(layout.bat_path.name, "CopiaArquivos.bat")
        self.assertEqual(layout.autcom_path.name, "Autcom.exe")


class ZipSynchronizationTests(unittest.TestCase):
    def test_destination_receives_exact_source_zip_set(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "versao"
            destination = root / "teste" / "exe"
            source.mkdir()
            destination.mkdir(parents=True)
            (source / "Autcom.zip").write_bytes(b"novo-autcom")
            (source / "AutBan.zip").write_bytes(b"novo-autban")
            (destination / "antigo.zip").write_bytes(b"antigo")

            copied = synchronize_zip_files(source, destination)

            self.assertEqual(
                sorted(item.name for item in copied),
                ["AutBan.zip", "Autcom.zip"],
            )
            self.assertEqual(
                sorted(item.name for item in destination.glob("*.zip")),
                ["AutBan.zip", "Autcom.zip"],
            )
            self.assertEqual(
                (destination / "Autcom.zip").read_bytes(),
                b"novo-autcom",
            )

    def test_source_without_zip_is_rejected(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "versao"
            destination = root / "teste" / "exe"
            source.mkdir()
            destination.mkdir(parents=True)

            with self.assertRaisesRegex(OSError, "Nenhum arquivo ZIP"):
                synchronize_zip_files(source, destination)


class ProcessStartupTests(unittest.TestCase):
    @patch("app.autcom_test._visible_windows_for_process")
    def test_visible_responsive_window_is_success(self, windows_mock):
        windows_mock.return_value = [("AUTCOM", False)]
        process = SimpleNamespace(pid=123, poll=lambda: None)

        result = validate_process_startup(
            process,
            timeout=1,
            stability_seconds=0,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.window_title, "AUTCOM")

    def test_early_process_exit_is_failure(self):
        process = SimpleNamespace(pid=123, poll=lambda: 1)

        result = validate_process_startup(process, timeout=1)

        self.assertEqual(result.status, "FALHA")
        self.assertIn("codigo 1", result.message)

    def test_alive_process_without_window_is_inconclusive(self):
        process = SimpleNamespace(pid=123, poll=lambda: None)

        result = validate_process_startup(process, timeout=-1)

        self.assertEqual(result.status, "INCONCLUSIVO")


class CompleteAutcomTestTests(unittest.TestCase):
    @patch("app.autcom_test.validate_process_startup")
    @patch("app.autcom_test.subprocess.Popen")
    @patch("app.autcom_test.subprocess.run")
    def test_bat_runs_before_autcom_and_success_is_returned(
        self,
        run_mock,
        popen_mock,
        startup_mock,
    ):
        with TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            project = base / "379.48.2.30.74.111.181"
            test_root = base / "PastaTesteCopia_74_111"
            exe_directory = test_root / "exe"
            project.mkdir()
            exe_directory.mkdir(parents=True)
            (project / "Autcom.zip").write_bytes(b"zip")
            (exe_directory / "CopiaArquivos.bat").write_text(
                "@echo off",
                encoding="utf-8",
            )
            (test_root / "Autcom.exe").write_bytes(b"exe")
            run_mock.return_value = SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="",
            )
            popen_mock.return_value = SimpleNamespace(pid=456)
            startup_mock.return_value = AutcomTestResult(
                status="OK",
                message="abriu",
                process_id=456,
                window_title="AUTCOM",
            )

            result = run_autcom_test(project)

        self.assertTrue(result.ok)
        self.assertEqual(result.process_id, 456)
        self.assertEqual(run_mock.call_args.kwargs["cwd"], exe_directory)
        self.assertEqual(popen_mock.call_args.kwargs["cwd"], test_root)
