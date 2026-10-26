import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import settings
from app.update_check import UpdateInfo, check_for_update, parse_version, read_published_version


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
        self.app._apply_update_info(UpdateInfo("2.6.3", "2.6.4", folder))
        self.assertTrue(self.app.update_label.winfo_manager())
        self.assertIn("2.6.4", self.app.update_label.cget("text"))

        self.app._apply_update_info(UpdateInfo("2.6.3", "2.6.3", folder))
        self.assertFalse(self.app.update_label.winfo_manager())

        self.app._apply_update_info(UpdateInfo("2.6.3", "2.6.4", folder))
        self.app._apply_update_info(None)  # sem acesso a pasta: some o aviso
        self.assertFalse(self.app.update_label.winfo_manager())
        self.assertIsNone(self.app.update_info)


if __name__ == "__main__":
    unittest.main()
