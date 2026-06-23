import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.actions import (
    CleanupError,
    _current_log_file,
    _cleanup_source_after_copy,
    _copy_project_files,
    _finish_copy,
    _finish_closing_start,
    _remove_source_item_with_retry,
    _write_action_audit,
    _build_copy_safety_checks,
    _copy_block_reasons,
    _find_destination_folders,
    _finish_manual_cleanup,
    _verify_critical_file_hashes,
    fechamento_local,
    limpar_pasta,
    validar_pos_fechamento,
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

    def test_valid_local_copy_accepts_local_suffix_destination(self):
        target_root = Path("C:/destino")
        project = make_project()
        destination = target_root / f"{project.folder_name}_LOCAL"

        checks = _build_copy_safety_checks(
            project,
            destination,
            target_root,
            "Copiar Local",
        )

        self.assertEqual(_copy_block_reasons(checks), [])

    def test_valid_copy_accepts_destination_inside_any_configured_root(self):
        target_roots = [Path("C:/destino1"), Path("C:/destino2")]
        project = make_project()
        destination = target_roots[1] / project.folder_name

        checks = _build_copy_safety_checks(
            project,
            destination,
            target_roots,
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

    def test_finds_destinations_across_multiple_roots(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            first = root / "primeira"
            second = root / "segunda"
            (first / "Projeto").mkdir(parents=True)
            (second / "sub" / "Projeto").mkdir(parents=True)

            destinations = _find_destination_folders([first, second], "Projeto")

            self.assertEqual(
                destinations,
                [first / "Projeto", second / "sub" / "Projeto"],
            )

    def test_local_copy_finds_destination_with_local_suffix(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            (root / "rede" / "379.48.2.30.74.150.99_LOCAL").mkdir(parents=True)

            destinations = _find_destination_folders(
                root,
                "379.48.2.30.74.150.99",
                title="Copiar Local",
            )

            self.assertEqual(
                destinations,
                [root / "rede" / "379.48.2.30.74.150.99_LOCAL"],
            )

    def test_cloud_copy_does_not_accept_local_suffix_destination(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            (root / "rede" / "379.48.2.30.74.150.99_LOCAL").mkdir(parents=True)

            destinations = _find_destination_folders(
                root,
                "379.48.2.30.74.150.99",
                title="Copiar Cloud",
            )

            self.assertEqual(destinations, [])


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

    def test_closing_start_logs_pre_closing_version_context(self):
        import app.logging_utils as logging_utils
        from tempfile import TemporaryDirectory

        class FakeProgress:
            def winfo_exists(self):
                return False

        with TemporaryDirectory() as temp_directory:
            original_log_file = logging_utils.LOG_FILE
            logging_utils.LOG_FILE = Path(temp_directory) / "fechamentos.log"
            try:
                project = make_project(
                    display_file_version="999.999.999.999",
                    display_product_version="999.999.999.999",
                )
                process = SimpleNamespace(pid=23088)
                with patch("app.actions.messagebox.showinfo") as showinfo:
                    _finish_closing_start(
                        FakeProgress(),
                        project,
                        "Fechamento Cloud",
                        process,
                        None,
                        None,
                        project.path / "comandosCMD" / "_FechamentoArquivos.bat",
                    )
                content = logging_utils.LOG_FILE.read_text(encoding="utf-8")
            finally:
                logging_utils.LOG_FILE = original_log_file

        self.assertIn("motivo=versao antes do fechamento; pid=23088", content)
        showinfo.assert_called_once()
        self.assertIn("clique em Atualizar", showinfo.call_args.args[1])

    def test_post_closing_validation_is_audited(self):
        import app.logging_utils as logging_utils
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            original_log_file = logging_utils.LOG_FILE
            logging_utils.LOG_FILE = Path(temp_directory) / "fechamentos.log"
            try:
                project = make_project()
                with patch("app.actions.messagebox.showinfo") as showinfo:
                    validar_pos_fechamento(project)
                content = logging_utils.LOG_FILE.read_text(encoding="utf-8")
            finally:
                logging_utils.LOG_FILE = original_log_file

        self.assertIn("Validar Fechamento validado_ok | usuario=", content)
        self.assertIn("status_atual=OK", content)
        showinfo.assert_called_once()
        self.assertIn("Validacao registrada", showinfo.call_args.args[1])


class CopyHashVerificationTests(unittest.TestCase):
    def test_finish_copy_copies_destination_to_clipboard(self):
        class FakeProgress:
            def __init__(self):
                self.clipboard = ""
                self.updated = False

            def clipboard_clear(self):
                self.clipboard = ""

            def clipboard_append(self, text):
                self.clipboard = text

            def update(self):
                self.updated = True

            def winfo_exists(self):
                return False

        progress = FakeProgress()
        destination = Path(r"\\servidor\destino\379.48.2.30.74.150.99_LOCAL")

        with patch("app.actions.messagebox.showinfo") as showinfo:
            _finish_copy(
                progress,
                make_project(),
                destination,
                "Copiar Local",
                None,
                1,
                {},
                1,
                None,
            )

        showinfo.assert_called_once()
        self.assertEqual(progress.clipboard, str(destination))
        self.assertTrue(progress.updated)
        self.assertIn(str(destination), showinfo.call_args.args[1])
        self.assertIn("Caminho copiado", showinfo.call_args.args[1])

    def test_critical_file_hash_match_passes(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            source = root / "origem"
            destination = root / "destino"
            source.mkdir()
            destination.mkdir()
            (source / "autcom.zip").write_bytes(b"abc123")
            (destination / "autcom.zip").write_bytes(b"abc123")

            hashes, mismatches = _verify_critical_file_hashes(source, destination)

        self.assertEqual(mismatches, [])
        self.assertIn("autcom.zip", hashes)

    def test_critical_file_same_size_different_bytes_fails(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            source = root / "origem"
            destination = root / "destino"
            source.mkdir()
            destination.mkdir()
            (source / "autcom.zip").write_bytes(b"abc123")
            (destination / "autcom.zip").write_bytes(b"xyz789")

            _hashes, mismatches = _verify_critical_file_hashes(source, destination)

        self.assertEqual(mismatches, ["autcom.zip"])

    def test_copy_project_files_copies_only_zip_and_reports_critical_hash(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            source = root / "origem"
            destination = root / "destino"
            source.mkdir()
            destination.mkdir()
            (source / "Autcom.exe").write_bytes(b"abc123")
            (source / "autcom.zip").write_bytes(b"zip123")
            (source / "comandosCMD").mkdir()
            (source / "comandosCMD" / "_FechamentoArquivos.bat").write_text(
                "echo ok",
                encoding="utf-8",
            )

            copied_files, hashes, cleaned_items, cleanup_error = _copy_project_files(
                source,
                destination,
            )
            destination_files = sorted(item.name for item in destination.iterdir())
            remaining_source = sorted(item.name for item in source.iterdir())

        self.assertEqual(copied_files, 1)
        self.assertIn("autcom.zip", hashes)
        self.assertEqual(destination_files, ["autcom.zip"])
        self.assertEqual(remaining_source, ["comandosCMD"])
        self.assertEqual(cleaned_items, 2)
        self.assertIsNone(cleanup_error)

    def test_copy_project_files_fails_without_zip_and_keeps_source(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            source = root / "origem"
            destination = root / "destino"
            source.mkdir()
            destination.mkdir()
            (source / "Autcom.exe").write_bytes(b"abc123")
            (source / "comandosCMD").mkdir()

            with self.assertRaisesRegex(OSError, "Nenhum arquivo .zip"):
                _copy_project_files(source, destination)
            remaining_source = sorted(item.name for item in source.iterdir())

        self.assertEqual(remaining_source, ["Autcom.exe", "comandosCMD"])

    def test_copy_project_files_reports_cleanup_error_separately(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            source = root / "origem"
            destination = root / "destino"
            source.mkdir()
            destination.mkdir()
            (source / "autcom.zip").write_bytes(b"zip123")
            (source / "comandosCMD").mkdir()

            with patch(
                "app.actions._cleanup_source_after_copy",
                side_effect=OSError("arquivo travado"),
            ):
                copied_files, hashes, cleaned_items, cleanup_error = _copy_project_files(
                    source,
                    destination,
                )
            destination_files = sorted(item.name for item in destination.iterdir())
            remaining_source = sorted(item.name for item in source.iterdir())

        self.assertEqual(copied_files, 1)
        self.assertIn("autcom.zip", hashes)
        self.assertEqual(cleaned_items, 0)
        self.assertIsNotNone(cleanup_error)
        self.assertIn("arquivo travado", str(cleanup_error))
        self.assertEqual(destination_files, ["autcom.zip"])
        self.assertEqual(remaining_source, ["autcom.zip", "comandosCMD"])

    def test_finish_copy_audits_cleanup_error_separately(self):
        import app.logging_utils as logging_utils
        from tempfile import TemporaryDirectory

        class FakeProgress:
            def winfo_exists(self):
                return False

        with TemporaryDirectory() as temp_directory:
            original_log_file = logging_utils.LOG_FILE
            logging_utils.LOG_FILE = Path(temp_directory) / "fechamentos.log"
            try:
                project = make_project()
                with patch("app.actions.messagebox.showwarning") as showwarning:
                    _finish_copy(
                        FakeProgress(),
                        project,
                        Path("C:/destino/projeto"),
                        "Copiar Local",
                        None,
                        1,
                        {"autcom.zip": "abc123"},
                        0,
                        CleanupError("arquivo travado", 0),
                    )
                content = logging_utils.LOG_FILE.read_text(encoding="utf-8")
            finally:
                logging_utils.LOG_FILE = original_log_file

        self.assertIn("Copiar Local erro_limpeza | usuario=", content)
        self.assertIn("1 arquivo(s) copiado(s) e verificado(s)", content)
        self.assertIn("limpeza_falhou=arquivo travado", content)
        showwarning.assert_called_once()
        self.assertIn("Copia concluida e verificada", showwarning.call_args.args[1])

    def test_cleanup_retry_handles_temporary_lock(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            file_path = Path(temp_directory) / "autcom.zip"
            file_path.write_bytes(b"zip")
            calls = {"count": 0}
            original_unlink = Path.unlink

            def flaky_unlink(path):
                if path == file_path and calls["count"] == 0:
                    calls["count"] += 1
                    raise OSError("arquivo travado")
                return original_unlink(path)

            with patch("pathlib.Path.unlink", flaky_unlink), patch("app.actions.time.sleep"):
                _remove_source_item_with_retry(file_path)

            exists = file_path.exists()

        self.assertFalse(exists)
        self.assertEqual(calls["count"], 1)

    def test_cleanup_source_keeps_only_comandoscmd(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            source = Path(temp_directory)
            commands = source / "comandosCMD"
            nested = source / "pasta"
            commands.mkdir()
            nested.mkdir()
            (commands / "_FechamentoArquivos.bat").write_text("echo ok", encoding="utf-8")
            (nested / "arquivo.txt").write_text("remover", encoding="utf-8")
            (source / "Autcom.exe").write_bytes(b"abc123")
            (source / "autcom.zip").write_bytes(b"zip")

            cleaned_items = _cleanup_source_after_copy(source)
            remaining = sorted(item.name for item in source.iterdir())

        self.assertEqual(cleaned_items, 3)
        self.assertEqual(remaining, ["comandosCMD"])

    def test_manual_clean_folder_starts_background_cleanup(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            source = Path(temp_directory)
            commands = source / "comandosCMD"
            commands.mkdir()
            (commands / "_FechamentoArquivos.bat").write_text("echo ok", encoding="utf-8")
            (source / "Autcom.exe").write_bytes(b"abc123")
            (source / "autcom.zip").write_bytes(b"zip")
            project = make_project(path=source)

            with (
                patch("app.actions.messagebox.askyesno", return_value=True),
                patch("app.actions._start_manual_cleanup") as start_cleanup,
            ):
                limpar_pasta(project)

            remaining = sorted(item.name for item in source.iterdir())

        self.assertEqual(remaining, ["Autcom.exe", "autcom.zip", "comandosCMD"])
        start_cleanup.assert_called_once_with(project)

    def test_finish_manual_clean_folder_audits_success(self):
        class FakeProgress:
            def winfo_exists(self):
                return False

        project = make_project()

        with (
            patch("app.actions.messagebox.showinfo") as showinfo,
            patch("app.actions._write_action_audit") as write_audit,
        ):
            _finish_manual_cleanup(FakeProgress(), project, 2, None)

        showinfo.assert_called_once()
        write_audit.assert_called_with(
            "Limpar Pasta",
            "concluido",
            project,
            reason="2 item(ns) removido(s); preservado=comandosCMD",
        )

    def test_manual_clean_folder_cancel_does_not_remove_files(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_directory:
            source = Path(temp_directory)
            (source / "comandosCMD").mkdir()
            (source / "Autcom.exe").write_bytes(b"abc123")
            project = make_project(path=source)

            with (
                patch("app.actions.messagebox.askyesno", return_value=False),
                patch("app.actions._write_action_audit") as write_audit,
            ):
                limpar_pasta(project)

            remaining = sorted(item.name for item in source.iterdir())

        self.assertEqual(remaining, ["Autcom.exe", "comandosCMD"])
        write_audit.assert_called_with(
            "Limpar Pasta",
            "cancelado",
            project,
            reason="usuario cancelou",
        )


if __name__ == "__main__":
    unittest.main()
