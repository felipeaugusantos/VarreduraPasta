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
    def test_missing_required_versions_are_reported(self):
        checks = [make_check(file_version=None, product_version=None)]
        errors = _version_errors(checks, "48.02.30.74", "02.30.74.150")
        self.assertEqual(
            errors,
            [
                "Autcom FileVersion ausente",
                "Autcom ProductVersion ausente",
            ],
        )

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
    def test_corrupted_zip_is_reported(self):
        with TemporaryDirectory() as temp_directory:
            folder = Path(temp_directory)
            (folder / "autcom.zip").write_bytes(b"nao e um zip")

            errors = validate_zip_names(folder)

        self.assertEqual(len(errors), 1)
        self.assertIn("ZIP invalido ou corrompido", errors[0])

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


class SpecificCopyTests(unittest.TestCase):
    """Copias especificas (5 partes, ex.: 379.48.2.40.5) tem arquivos proprios."""

    FOLDER = "379.48.2.40.5"
    VERSIONS = ("379.48.02.40", "48.02.40.05")

    def setUp(self):
        from unittest.mock import patch
        from app import pattern

        self._temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.base = root / "VERSOES_FECHADAS"
        self.project_path = self.base / self.FOLDER
        self.project_path.mkdir(parents=True)
        # Padrao geral "de copia completa" exige muito mais que a copia especifica tem.
        pattern_file = root / "project_pattern.json"
        pattern_file.write_text(
            '{"required_groups": ['
            '{"name": "Autcom", "accepted_files": ["Autcom.exe"], "validate_version": true},'
            '{"name": "libTintaSuvinil", "accepted_files": ["libTintaSuvinil.dll"], "validate_version": false},'
            '{"name": "libCartoesCielo", "accepted_files": ["libCartoesCielo.dll"], "validate_version": false}'
            ']}',
            encoding="utf-8",
        )
        for target, value in (
            ((pattern, "PATTERN_FILE"), pattern_file),
            (("app.scanner.read_exe_versions",), lambda _path: self.VERSIONS),
        ):
            patcher = patch.object(*target, value) if len(target) == 2 else patch(target[0], value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_files(self, *names):
        for name in names:
            (self.project_path / name).write_bytes(b"MZ")

    def scan(self):
        from app.scanner import scan_projects

        return {project.folder_name: project for project in scan_projects(self.base)}[self.FOLDER]

    def test_expected_versions_for_specific_copy(self):
        self.assertEqual(expected_versions_from_folder("379.48.2.40.5"), self.VERSIONS)
        self.assertEqual(expected_versions_from_folder("379.48.2.40.5_CLOUD"), self.VERSIONS)

    def test_without_own_pattern_only_autcom_is_required(self):
        self.write_files("Autcom.exe", "libNfe.dll")

        project = self.scan()

        # Antes: exigia o padrao geral e AutcomTinta/Autban/Auttin -> "Arquivo ausente".
        self.assertEqual(project.status, "OK")
        self.assertEqual([check.group_name for check in project.file_checks], ["Autcom"])

    def test_learns_own_files_on_closing_and_flags_when_one_disappears(self):
        from app.pattern import save_project_pattern

        self.write_files("Autcom.exe", "libNfe.dll", "AutBan.exe")
        saved, key = save_project_pattern(self.scan())
        self.assertTrue(saved)
        self.assertEqual(key, "379.48.2.40.5|local")

        # Depois do BAT, tudo vira ZIP: continua OK.
        for name in ("Autcom.exe", "libNfe.dll", "AutBan.exe"):
            with ZipFile(self.project_path / f"{Path(name).stem}.zip", "w") as archive:
                archive.writestr(name, b"MZ")
            (self.project_path / name).unlink()
        self.assertEqual(self.scan().status, "OK")

        # Se a DLL da propria copia sumir, ai sim aparece como ausente.
        (self.project_path / "libNfe.zip").unlink()
        status = self.scan().status
        self.assertIn("Ausente: libNfe.dll", status)
        self.assertNotIn("Suvinil", status)
        self.assertNotIn("AutcomTinta", status)

    def test_standard_copy_keeps_using_general_pattern(self):
        standard = self.base / "379.48.2.30.74.74.92"
        standard.mkdir()
        (standard / "Autcom.exe").write_bytes(b"MZ")
        from app.scanner import scan_projects

        result = {p.folder_name: p for p in scan_projects(self.base)}["379.48.2.30.74.74.92"]
        self.assertIn("libTintaSuvinil.dll", result.status)


class UnreadableZipTests(unittest.TestCase):
    """Um ZIP que o Python nao abre nao pode esvaziar a tabela inteira."""

    def setUp(self):
        from unittest.mock import patch
        from app import pattern

        self._temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name) / "VERSOES_FECHADAS"
        for name in ("379.48.2.30.74.150.100.7", "379.48.2.30.74.150.100.8"):
            (self.base / name).mkdir(parents=True)
            with ZipFile(self.base / name / "Autcom.zip", "w") as archive:
                archive.writestr("Autcom.exe", b"MZ")
        patchers = [
            patch.object(pattern, "PATTERN_FILE", Path(self._temp.name) / "pattern.json"),
            patch("app.scanner.read_exe_versions", lambda _path: ("48.02.30.74", "02.30.74.150")),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def scan(self):
        from app.scanner import scan_projects

        return {project.folder_name: project for project in scan_projects(self.base)}

    def scan_with_member_error(self, error):
        from unittest.mock import patch
        import zipfile

        original_open = zipfile.ZipFile.open

        def open_member(archive, name, *args, **kwargs):
            if "100.8" in str(archive.filename):
                raise error
            return original_open(archive, name, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "open", open_member):
            return self.scan()

    def test_unsupported_compression_affects_only_that_project(self):
        results = self.scan_with_member_error(
            NotImplementedError("That compression method is not supported")
        )

        self.assertEqual(len(results), 2)
        self.assertNotIn("ilegivel", results["379.48.2.30.74.150.100.7"].status)
        self.assertIn(
            "Autcom: ZIP ilegivel (formato nao suportado ou protegido por senha)",
            results["379.48.2.30.74.150.100.8"].status,
        )

    def test_zip_still_being_written_is_reported_as_incomplete(self):
        import zlib

        results = self.scan_with_member_error(zlib.error("Error -3 while decompressing data"))

        self.assertIn("ZIP incompleto ou corrompido", results["379.48.2.30.74.150.100.8"].status)

    def test_unexpected_error_in_one_project_keeps_the_others(self):
        from unittest.mock import patch
        from app import scanner

        original = scanner._analyze_project

        def analyze(project_path):
            if project_path.name.endswith("100.8"):
                raise KeyError("campo inesperado")
            return original(project_path)

        with patch.object(scanner, "_analyze_project", analyze):
            results = self.scan()

        self.assertEqual(len(results), 2)
        failed = results["379.48.2.30.74.150.100.8"]
        self.assertIn("Erro ao analisar o projeto: KeyError", failed.status)
        self.assertFalse(failed.local_copy_allowed)
