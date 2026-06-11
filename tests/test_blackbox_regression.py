import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from app import pattern, scanner, settings
from app.scanner import scan_projects, validate_zip_names


EXPECTED_FILE = "48.02.30.74"
EXPECTED_PRODUCT = "02.30.74.150"


def create_file(path, size=128):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as file:
        file.truncate(size)


def create_zip(path, inner_name, size=128):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        archive.writestr(inner_name, b"x" * size)


def fake_versions(path):
    name = Path(path).name.lower()
    if "wrong" in name:
        return "999.999.999.999", "999.999.999.999"
    return EXPECTED_FILE, EXPECTED_PRODUCT


class BlackBoxRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.base = self.root / "VERSOES_FECHADAS"
        self.base.mkdir()
        self.original_settings_file = settings.SETTINGS_FILE
        self.original_pattern_file = pattern.PATTERN_FILE
        settings.SETTINGS_FILE = self.root / "settings.json"
        pattern.PATTERN_FILE = self.root / "project_pattern.json"
        settings.save_ignored_project_folders({"pastaTeste"})

    def tearDown(self):
        settings.SETTINGS_FILE = self.original_settings_file
        pattern.PATTERN_FILE = self.original_pattern_file
        self.temp_directory.cleanup()

    def test_empty_and_ignored_folders_do_not_appear(self):
        (self.base / "379.48.2.30.74.150.90").mkdir()
        ignored = self.base / "pastaTeste"
        ignored.mkdir()
        create_file(ignored / "Autcom.exe")

        with patch("app.scanner.read_exe_versions", side_effect=fake_versions):
            projects = scan_projects(self.base)

        self.assertEqual(projects, [])

    def test_local_project_with_all_required_files_is_ok(self):
        project_path = self.base / "379.48.2.30.74.150.90"
        for file_name in (
            "Autcom.exe",
            "AutcomTinta.exe",
            "libAutban.dll",
            "libAuttin.dll",
        ):
            create_file(project_path / file_name, size=1024)

        with patch("app.scanner.read_exe_versions", side_effect=fake_versions):
            projects = scan_projects(self.base)

        self.assertEqual(len(projects), 1)
        project = projects[0]
        self.assertEqual(project.status, "OK")
        self.assertEqual(project.display_file_version, EXPECTED_FILE)
        self.assertEqual(project.display_product_version, EXPECTED_PRODUCT)
        self.assertTrue(project.local_copy_allowed)
        self.assertFalse(project.cloud_copy_allowed)

    def test_cloud_project_with_large_autcom_is_cloud_copy_allowed(self):
        original_cloud_min = scanner.CLOUD_MIN_AUTCOM_MB
        original_local_max = scanner.LOCAL_MAX_AUTCOM_MB
        scanner.CLOUD_MIN_AUTCOM_MB = 1
        scanner.LOCAL_MAX_AUTCOM_MB = 1
        project_path = self.base / "379.48.2.30.74.150.90_CLOUD"
        try:
            create_file(project_path / "Autcom.exe", size=2 * 1024 * 1024)
            create_file(project_path / "AutcomTinta.exe", size=1024)
            create_file(project_path / "AutBan.exe", size=1024)
            create_file(project_path / "Auttin.exe", size=1024)

            with patch("app.scanner.read_exe_versions", side_effect=fake_versions):
                projects = scan_projects(self.base)
        finally:
            scanner.CLOUD_MIN_AUTCOM_MB = original_cloud_min
            scanner.LOCAL_MAX_AUTCOM_MB = original_local_max

        self.assertEqual(len(projects), 1)
        project = projects[0]
        self.assertEqual(project.status, "OK")
        self.assertFalse(project.local_copy_allowed)
        self.assertTrue(project.cloud_copy_allowed)

    def test_wrong_version_is_reported_as_status_error(self):
        project_path = self.base / "379.48.2.30.74.150.90"
        create_file(project_path / "wrongAutcom.exe")
        create_file(project_path / "Autcom.exe")
        create_file(project_path / "AutcomTinta.exe")
        create_file(project_path / "libAutban.dll")
        create_file(project_path / "libAuttin.dll")

        def versions(path):
            if Path(path).name.lower() == "autcom.exe":
                return "999.999.999.999", "999.999.999.999"
            return fake_versions(path)

        with patch("app.scanner.read_exe_versions", side_effect=versions):
            projects = scan_projects(self.base)

        self.assertEqual(len(projects), 1)
        self.assertIn("Autcom FileVersion incorreto", projects[0].status)
        self.assertIn("Autcom ProductVersion incorreto", projects[0].status)

    def test_numbered_zip_is_blocking_even_when_inner_name_matches(self):
        project_path = self.base / "379.48.2.30.74.150.90"
        create_zip(project_path / "autcom (1).zip", "Autcom.exe")

        errors = validate_zip_names(project_path)

        self.assertEqual(len(errors), 1)
        self.assertIn("esperado Autcom.zip", errors[0])


if __name__ == "__main__":
    unittest.main()
