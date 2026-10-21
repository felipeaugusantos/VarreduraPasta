import unittest
import threading
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from app import settings
from app.config import (
    BASE_DIRECTORY,
    COPY_TARGET_DIRECTORIES,
    COPY_TARGET_DIRECTORY,
    IGNORED_PROJECT_FOLDERS,
)


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        # No Windows, antivirus/indexador pode segurar por instantes um arquivo
        # recem-gravado; isso nao deve derrubar o teste (nem o build).
        self._temp_directory = TemporaryDirectory(ignore_cleanup_errors=True)
        self._original_settings_file = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = Path(self._temp_directory.name) / "settings.json"

    def tearDown(self):
        settings.SETTINGS_FILE = self._original_settings_file
        self._temp_directory.cleanup()


class BaseDirectoryTests(SettingsTestCase):
    def test_default_when_settings_missing(self):
        self.assertEqual(settings.load_base_directory(), BASE_DIRECTORY)

    def test_roundtrip(self):
        settings.save_base_directory(r"C:\OutraPasta")
        self.assertEqual(settings.load_base_directory(), Path(r"C:\OutraPasta"))

    def test_corrupted_settings_falls_back_to_default(self):
        settings.SETTINGS_FILE.write_text("{json invalido", encoding="utf-8")
        self.assertEqual(settings.load_base_directory(), BASE_DIRECTORY)
        backup = settings.SETTINGS_FILE.with_name(
            f"{settings.SETTINGS_FILE.name}.corrupt.bak"
        )
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), "{json invalido")


class CopyTargetDirectoryTests(SettingsTestCase):
    def test_default_when_settings_missing(self):
        self.assertEqual(
            settings.load_copy_target_directory(),
            COPY_TARGET_DIRECTORY,
        )
        self.assertEqual(
            settings.load_copy_target_directories(),
            list(COPY_TARGET_DIRECTORIES),
        )

    def test_roundtrip(self):
        settings.save_copy_target_directory(r"\\servidor\destino")
        self.assertEqual(
            settings.load_copy_target_directory(),
            Path(r"\\servidor\destino"),
        )
        self.assertEqual(
            settings.load_copy_target_directories(),
            [*COPY_TARGET_DIRECTORIES, Path(r"\\servidor\destino")],
        )

    def test_empty_value_falls_back_to_default(self):
        settings.save_copy_target_directory("   ")
        self.assertEqual(
            settings.load_copy_target_directory(),
            COPY_TARGET_DIRECTORY,
        )
        self.assertEqual(
            settings.load_copy_target_directories(),
            list(COPY_TARGET_DIRECTORIES),
        )

    def test_multiple_additional_targets_are_saved_without_duplicates(self):
        settings.save_copy_target_directories(
            [
                r"\\servidor\destino1",
                r"\\servidor\destino2",
                r"\\servidor\destino1",
            ]
        )

        self.assertEqual(
            settings.load_additional_copy_target_directories(),
            [Path(r"\\servidor\destino1"), Path(r"\\servidor\destino2")],
        )
        self.assertEqual(
            settings.load_copy_target_directories(),
            [
                *COPY_TARGET_DIRECTORIES,
                Path(r"\\servidor\destino1"),
                Path(r"\\servidor\destino2"),
            ],
        )


class JenkinsConfigTests(SettingsTestCase):
    def setUp(self):
        super().setUp()
        self.password_loader = patch(
            "app.settings.load_jenkins_password", return_value=""
        )
        self.password_saver = patch(
            "app.settings.save_jenkins_password"
        )
        self.password_loader.start()
        self.password_saver_mock = self.password_saver.start()

    def tearDown(self):
        self.password_saver.stop()
        self.password_loader.stop()
        super().tearDown()

    def test_default_jenkins_config(self):
        self.assertEqual(
            settings.load_jenkins_config(),
            {
                "url": "http://localhost:8080",
                "username": "admin",
                "password": "",
            },
        )

    def test_legacy_plaintext_password_is_migrated(self):
        settings.SETTINGS_FILE.write_text(
            '{"jenkins":{"url":"http://localhost:8080","username":"admin","password":"antiga"}}',
            encoding="utf-8",
        )

        config = settings.load_jenkins_config()

        self.assertEqual(config["password"], "antiga")
        self.password_saver_mock.assert_called_once_with("admin", "antiga")
        self.assertNotIn("password", settings._load_settings()["jenkins"])

    def test_jenkins_config_roundtrip_preserves_other_settings(self):
        self.password_loader.stop()
        self.password_loader = patch(
            "app.settings.load_jenkins_password", return_value="fechadas"
        )
        self.password_loader.start()
        settings.save_base_directory(r"C:\OutraPasta")
        settings.save_jenkins_config("localhost:8080", "admin", "fechadas")

        self.assertEqual(
            settings.load_jenkins_config(),
            {
                "url": "localhost:8080",
                "username": "admin",
                "password": "fechadas",
            },
        )
        self.assertEqual(settings.load_base_directory(), Path(r"C:\OutraPasta"))
        self.password_saver_mock.assert_called_once_with("admin", "fechadas")
        self.assertNotIn(
            "password", settings._load_settings().get("jenkins", {})
        )


class SvnBasePathTests(SettingsTestCase):
    def test_default_svn_base_path(self):
        self.assertEqual(settings.load_svn_base_path(), "")

    def test_svn_base_path_roundtrip_preserves_other_settings(self):
        settings.save_base_directory(r"C:\OutraPasta")
        settings.save_svn_base_path(r"\\servidor\svn")

        self.assertEqual(settings.load_svn_base_path(), r"\\servidor\svn")
        self.assertEqual(settings.load_base_directory(), Path(r"C:\OutraPasta"))

    def test_concurrent_saves_preserve_both_values(self):
        barrier = threading.Barrier(3)

        def save_base():
            barrier.wait()
            settings.save_base_directory(r"C:\Concorrente")

        def save_svn():
            barrier.wait()
            settings.save_svn_base_path(r"\\servidor\svn")

        threads = [threading.Thread(target=save_base), threading.Thread(target=save_svn)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(settings.load_base_directory(), Path(r"C:\Concorrente"))
        self.assertEqual(settings.load_svn_base_path(), r"\\servidor\svn")


class IgnoredProjectFoldersTests(SettingsTestCase):
    def test_default_when_settings_missing(self):
        self.assertEqual(
            settings.load_ignored_project_folders(),
            set(IGNORED_PROJECT_FOLDERS),
        )

    def test_roundtrip_normalizes_to_lowercase(self):
        settings.save_ignored_project_folders({"PastaTeste", "  outra  "})
        self.assertEqual(
            settings.load_ignored_project_folders(),
            {"pastateste", "outra"},
        )

    def test_empty_list_is_respected(self):
        settings.save_ignored_project_folders(set())
        self.assertEqual(settings.load_ignored_project_folders(), set())

    def test_saving_does_not_discard_other_settings(self):
        settings.save_base_directory(r"C:\OutraPasta")
        settings.save_ignored_project_folders({"pasta"})
        self.assertEqual(settings.load_base_directory(), Path(r"C:\OutraPasta"))


if __name__ == "__main__":
    unittest.main()
