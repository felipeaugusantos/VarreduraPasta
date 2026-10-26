import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import log_cleanup, scanner, version_cache


class VersionCacheTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        patcher = patch.object(version_cache, "CACHE_FILE", Path(temp.name) / "zip_versions.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        version_cache.reset()
        self.addCleanup(version_cache.reset)

    def test_result_survives_restart_when_zip_is_unchanged(self):
        version_cache.store("C:/p/autcom.zip", "Autcom.exe", True, (10, 20), ("1.0", "2.0", 50.0, None))
        version_cache.flush()
        version_cache.reset()  # "reabriu o sistema"

        self.assertEqual(
            version_cache.lookup("C:/P/autcom.zip", "autcom.exe", True, (10, 20)),
            ("1.0", "2.0", 50.0, None),
        )

    def test_changed_zip_invalidates_entry(self):
        version_cache.store("a.zip", "Autcom.exe", True, (10, 20), ("1", "2", 1.0, None))
        self.assertIsNone(version_cache.lookup("a.zip", "Autcom.exe", True, (11, 20)))

    def test_errors_are_not_persisted(self):
        version_cache.store("a.zip", "Autcom.exe", True, (1, 1), (None, None, None, "Zip invalido"))
        self.assertIsNone(version_cache.lookup("a.zip", "Autcom.exe", True, (1, 1)))

    def test_corrupted_file_is_ignored(self):
        version_cache.CACHE_FILE.write_text("{ nao e json", encoding="utf-8")
        self.assertIsNone(version_cache.lookup("a.zip", "Autcom.exe", True, (1, 1)))

    def test_scanner_reads_zip_once_across_restarts(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            zip_path = Path(folder) / "autcom.zip"
            from zipfile import ZipFile

            with ZipFile(zip_path, "w") as archive:
                archive.writestr("Autcom.exe", b"x" * 100)
            with patch.object(scanner, "read_exe_versions", return_value=("1.0", "2.0")) as reader:
                first = scanner._read_version_from_zip(zip_path, "Autcom.exe")
                version_cache.flush()
                scanner._ZIP_VERSION_CACHE.clear()
                version_cache.reset()  # novo processo
                second = scanner._read_version_from_zip(zip_path, "Autcom.exe")
            self.assertEqual(first, second)
            self.assertEqual(reader.call_count, 1)


class LogCleanupTests(unittest.TestCase):
    def test_only_old_bat_logs_are_removed(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            logs = Path(folder)
            bat = logs / "fechamentos_bat"
            bat.mkdir()
            old, new = bat / "velho.log", bat / "novo.log"
            audit = logs / "fechamentos-2020-01.log"
            for path in (old, new, audit):
                path.write_text("x", encoding="utf-8")
            past = time.time() - 120 * 86400
            for path in (old, audit):
                os.utime(path, (past, past))

            removed = log_cleanup.remove_old_bat_logs(logs)

            self.assertEqual(removed, 1)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            self.assertTrue(audit.exists())  # Auditoria nunca e apagada

    def test_missing_folder_is_not_an_error(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            self.assertEqual(log_cleanup.remove_old_bat_logs(Path(folder)), 0)


if __name__ == "__main__":
    unittest.main()
