import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.actions import (
    _current_log_file,
    _copy_project_files,
    _write_action_audit,
    _build_copy_safety_checks,
    _copy_block_reasons,
    _find_destination_folders,
    _verify_critical_file_hashes,
    fechamento_local,
)


def make_project(**overrides):
    values = {
        "folder_name": "379.48.2.30.74.150.99",
        "path": Path("C:/VERSOES_FECHADAS/379.48.2.30.74.150.99"),
        "display_file_version": "48.02.30.74",
        "display_product_version": "02.30.74.150",
        "expected_file_version": "48.02.30.74",
        "expected_product_version": "02.30.74.150",
        "display_autcom_size_mb": 52.52,
        "local_copy_allowed": True,
        "cloud_copy_allowed": False,
        "status": "OK",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class CopySafetyChecksTests(unittest.TestCase):
    def test_valid_local_copy_has_no_block_reasons(self):
        target_root = Path("C:/destino")
        project = make_project()
        destination = target_root / project.folder_name

        checks = _build_copy_safety_checks(
            project,
            destination,
            target_root,
            "Copiar Local",
        )

        self.assertEqual(_copy_block_reasons(checks), [])

    def test_cloud_project_is_blocked_on_local_copy(self):
        target_root = Path("C:/destino")
        project = make_project(
            folder_name="379.48.2.30.74.150.99_CLOUD",
            path=Path("C:/VERSOES_FECHADAS/379.48.2.30.74.150.99_CLOUD"),
            display_autcom_size_mb=204.61,
            local_copy_allowed=False,
            cloud_copy_allowed=True,
        )
        destination = target_root / project.folder_name

        checks = _build_copy_safety_checks(
            project,
            destination,
            target_root,
            "Copiar Local",
        )
        reasons = _copy_block_reasons(checks)

        self.assertTrue(any(reason.startswith("Tipo do projeto") for reason in reasons))
        self.assertTrue(any(reason.startswith("Limite Local") for reason in reasons))

    def test_wrong_version_blocks_copy(self):
        target_root = Path("C:/destino")
        project = make_project(display_file_version="999.999.999.999")
        destination = target_root / project.folder_name

        checks = _build_copy_safety_checks(
            project,
            destination,
            target_root,
            "Copiar Local",
        )

        self.assertTrue(
            any(reason.startswith("FileVersion") for reason in _copy_block_reasons(checks))
        )


class FindDestinationFoldersTests(unittest.TestCase):
    def test_finds_all_matching_destinations(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            (root / "a" / "Projeto").mkdir(parents=True)
            (root / "b" / "Projeto").mkdir(parents=True)

            destinations = _find_destination_folders(root, "Projeto")

            self.assertEqual(len(destinations), 2)


class ActionAuditTests(unittest.TestCase):
    def test_current_log_file_rotates_by_month(self):
        import app.logging_utils as logging_utils
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            original_log_file = logging_utils.LOG_FILE
            original_logs_directory = logging_utils.LOGS_DIRECTORY
            logging_utils.LOG_FILE = None
            logging_utils.LOGS_DIRECTORY = Path(temp_directory) / "logs"
            try:
                log_file = _current_log_file(datetime(2026, 6, 10, 12, 0, 0))
            finally:
                logging_utils.LOG_FILE = original_log_file
                logging_utils.LOGS_DIRECTORY = original_logs_directory

        self.assertEqual(log_file.name, "fechamentos-2026-06.log")

    def test_action_audit_uses_standard_fields(self):
        import app.logging_utils as logging_utils
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            original_log_file = logging_utils.LOG_FILE
            logging_utils.LOG_FILE = Path(temp_directory) / "fechamentos.log"
            try:
                project = make_project()
                _write_action_audit(
                    "Fechamento Local",
                    "processo_aberto",
                    project,
                    bat_path=project.path / "comandosCMD" / "_FechamentoArquivos.bat",
                    reason="pid=123",
                )
                content = logging_utils.LOG_FILE.read_text(encoding="utf-8")
            finally:
                logging_utils.LOG_FILE = original_log_file

        self.assertIn("Fechamento Local processo_aberto | usuario=", content)
        self.assertIn(" | projeto=379.48.2.30.74.150.99 | ", content)
        self.assertIn(" | tipo=Local | ", content)
        self.assertIn(" | origem=C:\\VERSOES_FECHADAS\\379.48.2.30.74.150.99 | ", content)
        self.assertIn(" | destino= | ", content)
        self.assertIn(" | bat=C:\\VERSOES_FECHADAS\\379.48.2.30.74.150.99", content)
        self.assertIn(" | fileversion=48.02.30.74 | ", content)
        self.assertIn(" | productversion=02.30.74.150 | ", content)
        self.assertIn(" | autcom_mb=52.52 | ", content)
        self.assertIn(" | motivo=pid=123", content)

    def test_wrong_local_closing_type_is_audited(self):
        import app.logging_utils as logging_utils
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            original_log_file = logging_utils.LOG_FILE
            logging_utils.LOG_FILE = Path(temp_directory) / "fechamentos.log"
            try:
                project = make_project(
                    folder_name="379.48.2.30.74.150.99_CLOUD",
                    path=Path("C:/VERSOES_FECHADAS/379.48.2.30.74.150.99_CLOUD"),
                )
                with patch("app.actions.messagebox.showerror"):
                    fechamento_local(project)
                content = logging_utils.LOG_FILE.read_text(encoding="utf-8")
            finally:
                logging_utils.LOG_FILE = original_log_file

        self.assertIn("Fechamento Local bloqueado | usuario=", content)
        self.assertIn(" | tipo=Cloud | ", content)
        self.assertIn("projeto CLOUD nao pode usar fechamento local", content)


class CopyHashVerificationTests(unittest.TestCase):
    def test_critical_file_hash_match_passes(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            source = root / "origem"
            destination = root / "destino"
            source.mkdir()
            destination.mkdir()
            (source / "Autcom.exe").write_bytes(b"abc123")
            (destination / "Autcom.exe").write_bytes(b"abc123")

            hashes, mismatches = _verify_critical_file_hashes(source, destination)

        self.assertEqual(mismatches, [])
        self.assertIn("Autcom.exe", hashes)

    def test_critical_file_same_size_different_bytes_fails(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            source = root / "origem"
            destination = root / "destino"
            source.mkdir()
            destination.mkdir()
            (source / "Autcom.exe").write_bytes(b"abc123")
            (destination / "Autcom.exe").write_bytes(b"xyz789")

            _hashes, mismatches = _verify_critical_file_hashes(source, destination)

        self.assertEqual(mismatches, ["Autcom.exe"])

    def test_copy_project_files_reports_critical_hash(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            source = root / "origem"
            destination = root / "destino"
            source.mkdir()
            destination.mkdir()
            (source / "Autcom.exe").write_bytes(b"abc123")

            copied_files, hashes = _copy_project_files(source, destination)

        self.assertEqual(copied_files, 1)
        self.assertIn("Autcom.exe", hashes)


if __name__ == "__main__":
    unittest.main()
