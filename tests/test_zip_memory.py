import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import zip_memory


class ZipMemoryTestCase(unittest.TestCase):
    def setUp(self):
        self._temp_directory = TemporaryDirectory(ignore_cleanup_errors=True)
        patcher = patch.object(
            zip_memory,
            "MEMORY_FILE",
            Path(self._temp_directory.name) / "zip_memory.json",
        )
        patcher.start()
        zip_memory.reset_cache()
        self.addCleanup(self._temp_directory.cleanup)
        self.addCleanup(zip_memory.reset_cache)
        self.addCleanup(patcher.stop)


class RecordClosingTests(ZipMemoryTestCase):
    def test_first_closing_is_reference_without_alert(self):
        record = zip_memory.record_closing("379.48.2.30.74.150.100.7", 150)

        self.assertEqual((record.last_zips, record.max_zips), (150, 150))
        self.assertFalse(record.below_max)

    def test_fewer_zips_than_max_alerts_and_keeps_max(self):
        folder = "379.48.2.30.74.150.100.7"
        zip_memory.record_closing(folder, 150)

        record = zip_memory.record_closing(folder, 149)
        self.assertEqual((record.last_zips, record.max_zips), (149, 150))
        self.assertTrue(record.below_max)

        # O maior continua 150; outro fechamento com 149 segue em alerta.
        self.assertTrue(zip_memory.record_closing(folder, 149).below_max)

    def test_more_zips_raises_the_max(self):
        folder = "379.48.2.30.74.150.100.7"
        zip_memory.record_closing(folder, 150)

        record = zip_memory.record_closing(folder, 152)
        self.assertEqual((record.last_zips, record.max_zips), (152, 152))
        self.assertFalse(record.below_max)
        self.assertTrue(zip_memory.record_closing(folder, 150).below_max)

    def test_memory_is_per_exact_folder(self):
        zip_memory.record_closing("379.48.2.30.74.150.100.7", 150)
        zip_memory.record_closing("379.48.2.30.74.150.100.7_CLOUD", 90)

        self.assertEqual(zip_memory.get_record("379.48.2.30.74.150.100.7").max_zips, 150)
        self.assertEqual(
            zip_memory.get_record("379.48.2.30.74.150.100.7_CLOUD").max_zips, 90
        )
        self.assertIsNone(zip_memory.get_record("379.48.2.31.74.150.100.7"))
        # Nome da pasta nao diferencia maiusculas.
        self.assertEqual(
            zip_memory.get_record("379.48.2.30.74.150.100.7_cloud").max_zips, 90
        )

    def test_memory_survives_restart(self):
        zip_memory.record_closing("p1", 150)
        zip_memory.record_closing("p1", 149)
        zip_memory.reset_cache()

        record = zip_memory.get_record("p1")
        self.assertEqual((record.last_zips, record.max_zips), (149, 150))
        self.assertTrue(record.below_max)

    def test_corrupted_file_starts_empty_and_keeps_backup(self):
        zip_memory.MEMORY_FILE.write_text("{json invalido", encoding="utf-8")

        self.assertIsNone(zip_memory.get_record("p1"))
        self.assertTrue(
            zip_memory.MEMORY_FILE.with_name("zip_memory.json.corrupt.bak").exists()
        )
        self.assertEqual(zip_memory.record_closing("p1", 3).max_zips, 3)


class CountZipFilesTests(unittest.TestCase):
    def test_counts_only_zip_files_in_root(self):
        with TemporaryDirectory() as temp_directory:
            folder = Path(temp_directory)
            for name in ("autcom.zip", "libA.ZIP", "libB.zip"):
                (folder / name).write_bytes(b"zip")
            (folder / "Autcom.exe").write_bytes(b"exe")
            (folder / "comandosCMD").mkdir()
            (folder / "comandosCMD" / "interno.zip").write_bytes(b"zip")

            self.assertEqual(zip_memory.count_zip_files(folder), 3)


if __name__ == "__main__":
    unittest.main()
