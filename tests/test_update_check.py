import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import settings
from app.update_check import (
    UpdateInfo,
    check_for_update,
    parse_version,
    read_published_version,
    start_update,
)


class ParseVersionTests(unittest.TestCase):
    def test_compares_numerically_not_alphabetically(self):
        self.assertGreater(parse_version("2.6.10"), parse_version("2.6.9"))
        self.assertGreater(parse_version("2.10.0"), parse_version("2.9.9"))
        self.assertEqual(parse_version("v2.7"), (2, 7))
        self.assertEqual(parse_version(" 2.6.3 "), (2, 6, 3))

    def test_invalid_text_has_no_version(self):
        for text in ("", "lixo", "2.x.1", None, "versao 2.6.3"):
            self.assertEqual(parse_version(text), ())


class UpdateInfoTests(unittest.TestCase):
    def test_available_only_when_published_is_newer(self):
        folder = Path("x")
        self.assertTrue(UpdateInfo("2.6.3", "2.6.4", folder).available)
        self.assertTrue(UpdateInfo("2.6.9", "2.6.10", folder).available)
        self.assertFalse(UpdateInfo("2.6.3", "2.6.3", folder).available)
        # Exe de teste mais novo que o publicado: nao avisa.
        self.assertFalse(UpdateInfo("2.7.0", "2.6.3", folder).available)


class ReadPublishedVersionTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name)

    def write(self, text, encoding="utf-8"):
        (self.folder / "versao.txt").write_text(text, encoding=encoding)

    def test_reads_first_line_ignoring_spaces_bom_and_prefix(self):
        self.write("2.6.4\r\n", encoding="utf-8-sig")
        self.assertEqual(read_published_version(self.folder), "2.6.4")
        self.write("  v2.7.0  \nresto ignorado")
        self.assertEqual(read_published_version(self.folder), "2.7.0")

    def test_missing_folder_or_file_or_garbage_returns_none(self):
        self.assertIsNone(read_published_version(self.folder / "nao_existe"))
        self.assertIsNone(read_published_version(self.folder))  # sem versao.txt
        self.write("isto nao e uma versao")
        self.assertIsNone(read_published_version(self.folder))
        self.write("")
        self.assertIsNone(read_published_version(self.folder))

    def test_check_for_update_builds_info_for_the_running_version(self):
        self.write("2.6.4")
        info = check_for_update(self.folder, current="2.6.3")

        self.assertEqual((info.current, info.published, info.directory), ("2.6.3", "2.6.4", self.folder))
        self.assertTrue(info.available)
        self.assertIsNone(check_for_update(self.folder / "outra", current="2.6.3"))


class UpdateDirectorySettingTests(unittest.TestCase):
    def test_default_and_override(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            original = settings.SETTINGS_FILE
            settings.SETTINGS_FILE = Path(temp) / "settings.json"
            self.addCleanup(setattr, settings, "SETTINGS_FILE", original)

            self.assertEqual(
                settings.load_update_directory(),
                Path(r"\\citel-fileserve\Modem\SISTEMA_COPIA"),
            )
            settings.SETTINGS_FILE.write_text('{"update_directory": "D:\\\\Oficial"}', encoding="utf-8")
            self.assertEqual(settings.load_update_directory(), Path("D:\\Oficial"))


class HeaderNoticeTests(unittest.TestCase):
    """Com Tk de verdade: o aviso aparece e some conforme a versao publicada."""

    def setUp(self):
        import tempfile

        self._data = tempfile.mkdtemp()
        patcher = patch.dict("os.environ", {"VARREDURA_SISTEMA_DATA_DIR": self._data})
        patcher.start()
        self.addCleanup(patcher.stop)
        from app import interface

        with patch.object(interface.VersionScannerApp, "refresh", lambda self: None), \
             patch.object(interface.VersionScannerApp, "check_scripts_periodically", lambda self: None), \
             patch.object(interface.VersionScannerApp, "check_for_update_periodically", lambda self: None):
            self.app = interface.VersionScannerApp()
        self.app.withdraw()
        self.addCleanup(self.app.destroy)

    def test_label_shows_only_when_newer_version_is_published(self):
        folder = Path("x")
        self.app.after = lambda *args, **kwargs: None  # sem a pergunta de atualizar
        self.app._apply_update_info(UpdateInfo("2.6.3", "2.6.4", folder))
        self.assertTrue(self.app.update_label.winfo_manager())
        self.assertIn("2.6.4", self.app.update_label.cget("text"))

        self.app._apply_update_info(UpdateInfo("2.6.3", "2.6.3", folder))
        self.assertFalse(self.app.update_label.winfo_manager())

        self.app._apply_update_info(UpdateInfo("2.6.3", "2.6.4", folder))
        self.app._apply_update_info(None)  # sem acesso a pasta: some o aviso
        self.assertFalse(self.app.update_label.winfo_manager())
        self.assertIsNone(self.app.update_info)


class StartUpdateTests(unittest.TestCase):
    def test_runs_the_launcher_from_the_official_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            launcher = Path(folder) / "Iniciar_VarreduraSistema.bat"
            launcher.write_text("@echo off", encoding="ascii")
            with patch("app.update_check.subprocess.Popen") as popen:
                start_update(folder)

        self.assertEqual(popen.call_args.args[0], ["cmd.exe", "/c", str(launcher)])

    def test_missing_launcher_is_reported(self):
        with tempfile.TemporaryDirectory() as folder, patch("app.update_check.subprocess.Popen") as popen:
            with self.assertRaises(FileNotFoundError):
                start_update(folder)
        popen.assert_not_called()


class UpdatePromptTests(unittest.TestCase):
    """Com Tk de verdade: pergunta se fecha o sistema para abrir a versao nova."""

    def setUp(self):
        self._data = tempfile.mkdtemp()
        patcher = patch.dict("os.environ", {"VARREDURA_SISTEMA_DATA_DIR": self._data})
        patcher.start()
        self.addCleanup(patcher.stop)
        from app import interface

        self.interface = interface
        with patch.object(interface.VersionScannerApp, "refresh", lambda self: None),              patch.object(interface.VersionScannerApp, "check_scripts_periodically", lambda self: None),              patch.object(interface.VersionScannerApp, "check_for_update_periodically", lambda self: None):
            self.app = interface.VersionScannerApp()
        self.app.withdraw()
        self.addCleanup(self._destroy)
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "Iniciar_VarreduraSistema.bat").write_text("@echo off", encoding="ascii")
        self.info = UpdateInfo("2.6.5", "2.6.6", self.folder)
        self.app.update_info = self.info

    def _destroy(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def test_yes_starts_the_launcher_and_closes_the_system(self):
        with patch.object(self.interface, "messagebox") as box,              patch.object(self.interface, "start_update") as start,              patch.object(self.app, "destroy") as destroy:
            box.askyesno.return_value = True
            self.app._show_update_details()

        self.assertIn("2.6.6", box.askyesno.call_args.args[1])
        start.assert_called_once_with(self.folder)
        destroy.assert_called_once()

    def test_no_keeps_the_system_open(self):
        with patch.object(self.interface, "messagebox") as box,              patch.object(self.interface, "start_update") as start,              patch.object(self.app, "destroy") as destroy:
            box.askyesno.return_value = False
            self.app._show_update_details()

        start.assert_not_called()
        destroy.assert_not_called()

    def test_closing_in_progress_blocks_the_update(self):
        with patch.object(self.interface, "messagebox") as box,              patch.object(self.interface.actions.CLOSING_MANAGER, "active_count", return_value=2),              patch.object(self.interface, "start_update") as start:
            self.app._show_update_details()

        box.askyesno.assert_not_called()
        self.assertIn("2 fechamento", box.showinfo.call_args.args[1])
        start.assert_not_called()

    def test_failure_to_start_keeps_the_system_open_and_explains(self):
        with patch.object(self.interface, "messagebox") as box,              patch.object(self.interface, "start_update", side_effect=OSError("sem acesso")),              patch.object(self.app, "destroy") as destroy:
            box.askyesno.return_value = True
            self.app._show_update_details()

        destroy.assert_not_called()
        self.assertIn("sem acesso", box.showerror.call_args.args[1])

    def test_folder_without_launcher_only_explains_how_to_update(self):
        self.app.update_info = UpdateInfo("2.6.5", "2.6.6", Path(tempfile.mkdtemp()))
        with patch.object(self.interface, "messagebox") as box:
            self.app._show_update_details()

        box.askyesno.assert_not_called()
        box.showinfo.assert_called_once()

    def test_prompt_is_scheduled_once_per_published_version(self):
        with patch.object(self.app, "after") as after:
            self.app._apply_update_info(self.info)
            self.app._apply_update_info(self.info)  # varredura de 1 h depois: nao pergunta de novo
            self.assertEqual(after.call_count, 1)
            self.app._apply_update_info(UpdateInfo("2.6.5", "2.6.7", self.folder))
            self.assertEqual(after.call_count, 2)


if __name__ == "__main__":
    unittest.main()
