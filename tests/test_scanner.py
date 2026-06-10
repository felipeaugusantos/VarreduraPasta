import unittest

from app.scanner import (
    FileCheck,
    _build_status,
    _version_errors,
    expected_versions_from_folder,
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


if __name__ == "__main__":
    unittest.main()
