import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path

from app.sqlaudit.file_scanner import (
    SqlFileError,
    read_sql_text,
    scan_sql_files,
    script_number,
    sequence_warnings,
)


class ScannerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "SCRIPTS"
        self.root.mkdir()

    def write(self, relative, content="SELECT 1;", binary=False):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # bytes exatos: write_text converteria \n em \r\n no Windows
        path.write_bytes(content if binary else content.encode("utf-8"))
        return path


class ScriptNumberTests(unittest.TestCase):
    def test_number_ignores_case_and_folder(self):
        self.assertEqual(script_number("SCRIPT_5319.SQL"), 5319)
        self.assertEqual(script_number("sub\\script_0042.sql"), 42)
        self.assertIsNone(script_number("ajuste.sql"))
        self.assertIsNone(script_number("SCRIPT_12_v2.sql"))


class ScanTests(ScannerTestCase):
    def test_orders_by_integer_number_not_by_text(self):
        for number in (1000, 999, 5319, 5131):
            self.write(f"SCRIPT_{number}.SQL")
        result = scan_sql_files(self.root)
        self.assertEqual([f.number for f in result.files], [999, 1000, 5131, 5319])

    def test_finds_uppercase_extension_subfolders_and_ignores_other_files(self):
        self.write("SCRIPT_1.SQL")
        self.write("sub/script_2.sql")
        self.write("leia-me.txt")
        self.write("SCRIPT_3.sql.bak")
        result = scan_sql_files(self.root)
        self.assertEqual(sorted(f.relative.lower() for f in result.files), ["script_1.sql", os.path.join("sub", "script_2.sql")])

    def test_unnumbered_files_come_last_and_are_reported(self):
        self.write("ajuste.sql")
        self.write("SCRIPT_7.SQL")
        result = scan_sql_files(self.root)
        self.assertEqual([f.relative for f in result.files], ["SCRIPT_7.SQL", "ajuste.sql"])
        self.assertTrue(any("fora do padrao" in warning for warning in result.warnings))

    def test_records_sha256_and_size(self):
        self.write("SCRIPT_1.SQL", "ALTER TABLE a ADD b INT;")
        (script,) = scan_sql_files(self.root).files
        self.assertEqual(script.sha256, hashlib.sha256(b"ALTER TABLE a ADD b INT;").hexdigest())
        self.assertEqual(script.size, len(b"ALTER TABLE a ADD b INT;"))

    def test_large_file_is_skipped_with_reason(self):
        self.write("SCRIPT_1.SQL", "x" * 100)
        result = scan_sql_files(self.root, max_file_bytes=50)
        self.assertEqual(result.files, [])
        self.assertIn("maior que", result.skipped[0].reason)

    def test_file_limit_is_enforced(self):
        for number in range(1, 6):
            self.write(f"SCRIPT_{number}.SQL")
        result = scan_sql_files(self.root, max_files=3)
        self.assertEqual(len(result.files), 3)
        self.assertEqual(len(result.skipped), 2)

    def test_symlink_pointing_outside_root_is_skipped(self):
        outside = Path(self._tmp.name) / "fora.sql"
        outside.write_text("DROP TABLE x;", encoding="utf-8")
        try:
            (self.root / "SCRIPT_1.SQL").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("sem permissao para criar link simbolico")
        result = scan_sql_files(self.root)
        self.assertEqual(result.files, [])
        self.assertIn("fora da pasta", result.skipped[0].reason)

    def test_missing_folder_raises_clear_error(self):
        with self.assertRaises(SqlFileError):
            scan_sql_files(self.root / "nao-existe")

    def test_cancel_stops_the_scan(self):
        for number in range(1, 4):
            self.write(f"SCRIPT_{number}.SQL")
        cancel = threading.Event()
        cancel.set()
        result = scan_sql_files(self.root, cancel_event=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.files, [])

    def test_progress_is_reported(self):
        for number in range(1, 4):
            self.write(f"SCRIPT_{number}.SQL")
        seen = []
        scan_sql_files(self.root, progress=seen.append)
        self.assertEqual(seen, [1, 2, 3])


class SequenceWarningTests(ScannerTestCase):
    def scan(self):
        return scan_sql_files(self.root)

    def test_gaps_are_reported_as_ranges(self):
        for number in (5128, 5131, 5132, 5135):
            self.write(f"SCRIPT_{number}.SQL")
        warnings = self.scan().warnings
        self.assertIn("Numeros ausentes na sequencia: 5129-5130, 5133-5134", warnings)

    def test_contiguous_sequence_has_no_warnings(self):
        for number in (10, 11, 12):
            self.write(f"SCRIPT_{number}.SQL")
        self.assertEqual(self.scan().warnings, [])

    def test_duplicate_number_with_different_content_is_flagged(self):
        self.write("SCRIPT_7.SQL", "ALTER TABLE a ADD b INT;")
        self.write("outra/SCRIPT_7.SQL", "ALTER TABLE a ADD c INT;")
        warnings = self.scan().warnings
        self.assertTrue(any("repetido (conteudo diferente)" in warning for warning in warnings))

    def test_duplicate_number_with_same_content_is_flagged_as_such(self):
        self.write("SCRIPT_7.SQL", "SELECT 1;")
        self.write("outra/SCRIPT_7.SQL", "SELECT 1;")
        warnings = self.scan().warnings
        self.assertTrue(any("repetido (mesmo conteudo)" in warning for warning in warnings))

    def test_empty_list_has_no_warnings(self):
        self.assertEqual(sequence_warnings([]), [])


class ReadSqlTextTests(ScannerTestCase):
    def test_utf8_with_and_without_bom(self):
        path = self.write("a.sql", "-- ação\nSELECT 1;")
        self.assertEqual(read_sql_text(path), ("-- ação\nSELECT 1;", "utf-8"))
        path = self.write("b.sql", b"\xef\xbb\xbfSELECT 2;", binary=True)
        self.assertEqual(read_sql_text(path), ("SELECT 2;", "utf-8-sig"))

    def test_utf16_with_bom(self):
        path = self.write("c.sql", "SELECT 3; -- ação".encode("utf-16"), binary=True)
        text, encoding = read_sql_text(path)
        self.assertEqual((text, encoding), ("SELECT 3; -- ação", "utf-16"))

    def test_cp1252_fallback(self):
        path = self.write("d.sql", "-- ação".encode("cp1252"), binary=True)
        self.assertEqual(read_sql_text(path), ("-- ação", "cp1252"))

    def test_utf16_without_bom_is_an_error_not_garbage(self):
        path = self.write("e.sql", "SELECT 1;".encode("utf-16-le"), binary=True)
        with self.assertRaises(SqlFileError):
            read_sql_text(path)

    def test_oversized_and_missing_files_raise(self):
        path = self.write("f.sql", "x" * 100)
        with self.assertRaises(SqlFileError):
            read_sql_text(path, max_bytes=10)
        with self.assertRaises(SqlFileError):
            read_sql_text(self.root / "nao-existe.sql")


if __name__ == "__main__":
    unittest.main()
