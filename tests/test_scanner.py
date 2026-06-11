import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from zipfile import ZipFile

from app.scanner import (
    FileCheck,
    _build_status,
    _merge_required_core_groups,
    _version_errors,
    expected_versions_from_folder,
    validate_zip_names,
)


def make_check(**overrides):
    values = {
        "group_name": "Autcom",
        "accepted_files": ("Autcom.exe",),
        "validate_version": True,
        "found_name": "Autcom.exe",
        "source": "Pasta",
        "zip_path": None,
        "file_version": "48.02.30.74",
        "product_version": "02.30.74.150",
        "size_mb": 50.0,
        "status": "OK",
    }
    values.update(overrides)
    return FileCheck(**values)


class ExpectedVersionsFromFolderTests(unittest.TestCase):
    def test_standard_folder_name(self):
        file_version, product_version = expected_versions_from_folder(
            "379.48.2.30.74.150.90"
        )
        self.assertEqual(file_version, "48.02.30.74")
        self.assertEqual(product_version, "02.30.74.150")

    def test_cloud_suffix_is_ignored(self):
        file_version, product_version = expected_versions_from_folder(
            "379.48.2.30.74.150.90_CLOUD"
        )
        self.assertEqual(file_version, "48.02.30.74")
        self.assertEqual(product_version, "02.30.74.150")

    def test_single_digit_parts_are_zero_padded(self):
        file_version, product_version = expected_versions_from_folder(
            "1.2.3.4.5.6.7"
        )
        self.assertEqual(file_version, "02.03.04.05")
        self.assertEqual(product_version, "03.04.05.06")

    def test_folder_with_few_parts_returns_none(self):
        self.assertEqual(
            expected_versions_from_folder("379.48.2"),
            (None, None),
        )

    def test_non_numeric_folder_returns_none(self):
        self.assertEqual(
            expected_versions_from_folder("pasta_qualquer"),
            (None, None),
        )


class VersionErrorsTests(unittest.TestCase):
    def test_matching_versions_produce_no_errors(self):
        checks = [make_check()]
        self.assertEqual(
            _version_errors(checks, "48.02.30.74", "02.30.74.150"),
            [],
        )

    def test_wrong_file_version_is_reported(self):
        checks = [make_check(file_version="99.99.99.99")]
        errors = _version_errors(checks, "48.02.30.74", "02.30.74.150")
        self.assertEqual(errors, ["Autcom FileVersion incorreto"])

    def test_wrong_product_version_is_reported(self):
        checks = [make_check(product_version="99.99.99.99")]
        errors = _version_errors(checks, "48.02.30.74", "02.30.74.150")
        self.assertEqual(errors, ["Autcom ProductVersion incorreto"])

    def test_checks_without_version_validation_are_skipped(self):
        checks = [make_check(validate_version=False, file_version="99.99.99.99")]
        self.assertEqual(
            _version_errors(checks, "48.02.30.74", "02.30.74.150"),
            [],
        )

    def test_non_ok_checks_are_skipped(self):
        checks = [make_check(status="Ausente", file_version="99.99.99.99")]
        self.assertEqual(
            _version_errors(checks, "48.02.30.74", "02.30.74.150"),
            [],
        )


class RequiredCoreGroupsTests(unittest.TestCase):
    def test_autcom_is_kept_when_pattern_does_not_include_it(self):
        groups = _merge_required_core_groups(
            (
                {
                    "name": "ConsultaPagamentosPixMonitor",
                    "accepted_files": ("ConsultaPagamentosPixMonitor.exe",),
                    "validate_version": False,
                },
            )
        )
        group_names = {group["name"] for group in groups}
        self.assertIn("Autcom", group_names)
        self.assertIn("AutcomTinta", group_names)
        self.assertIn("Autban", group_names)
        self.assertIn("Auttin", group_names)


class BuildStatusTests(unittest.TestCase):
    def test_all_ok(self):
        status = _build_status("48.02.30.74", "02.30.74.150", [make_check()])
        self.assertEqual(status, "OK")

    def test_folder_out_of_pattern(self):
        status = _build_status(None, None, [make_check()])
        self.assertIn("Nome da pasta fora do padrao", status)

    def test_missing_file_is_reported(self):
        missing = make_check(
            found_name=None,
            source="Ausente",
            status="Ausente",
            file_version=None,
            product_version=None,
            size_mb=None,
        )
        status = _build_status("48.02.30.74", "02.30.74.150", [missing])
        self.assertIn("Ausente: Autcom.exe", status)

    def test_zip_error_is_reported(self):
        zip_error = make_check(
            source="Zip",
            status="Zip invalido",
            file_version=None,
            product_version=None,
        )
        status = _build_status("48.02.30.74", "02.30.74.150", [zip_error])
        self.assertIn("Autcom: Zip invalido", status)

    def test_version_mismatch_is_reported(self):
        wrong = make_check(file_version="99.99.99.99")
        status = _build_status("48.02.30.74", "02.30.74.150", [wrong])
        self.assertIn("Autcom FileVersion incorreto", status)

    def test_zip_name_errors_are_reported(self):
        status = _build_status(
            "48.02.30.74",
            "02.30.74.150",
            [make_check()],
            ["autcom (1).zip contem AutBan.exe; esperado AutBan.zip"],
        )
        self.assertIn("Nome de ZIP incorreto", status)


class ValidateZipNamesTests(unittest.TestCase):
    def test_matching_zip_name_is_ok(self):
        with TemporaryDirectory() as temp_directory:
            folder = Path(temp_directory)
            zip_path = folder / "AutBan.zip"
            with ZipFile(zip_path, "w") as archive:
                archive.writestr("AutBan.exe", b"conteudo")

            self.assertEqual(validate_zip_names(folder), [])

    def test_autcom_numbered_zip_with_other_file_is_reported(self):
        with TemporaryDirectory() as temp_directory:
            folder = Path(temp_directory)
            zip_path = folder / "autcom (1).zip"
            with ZipFile(zip_path, "w") as archive:
                archive.writestr("AutBan.exe", b"conteudo")

            errors = validate_zip_names(folder)
            self.assertEqual(len(errors), 1)
            self.assertIn("autcom (1).zip contem AutBan.exe", errors[0])
            self.assertIn("esperado AutBan.zip", errors[0])

    def test_numbered_zip_with_same_inner_file_is_reported(self):
        with TemporaryDirectory() as temp_directory:
            folder = Path(temp_directory)
            zip_path = folder / "autcom (1).zip"
            with ZipFile(zip_path, "w") as archive:
                archive.writestr("autcom.exe", b"conteudo")

            errors = validate_zip_names(folder)
            self.assertEqual(len(errors), 1)
            self.assertIn("autcom (1).zip contem autcom.exe", errors[0])
            self.assertIn("esperado autcom.zip", errors[0])


if __name__ == "__main__":
    unittest.main()
