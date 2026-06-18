import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import settings
from app.script_monitor import (
    check_scripts,
    load_pending_scripts,
    mark_pending_scripts_done,
    should_check_today,
)


class ScriptMonitorTests(unittest.TestCase):
    def setUp(self):
        self._temp_directory = TemporaryDirectory()
        self._original_settings_file = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = Path(self._temp_directory.name) / "settings.json"

    def tearDown(self):
        settings.SETTINGS_FILE = self._original_settings_file
        self._temp_directory.cleanup()

    def test_first_check_creates_baseline_without_new_files(self):
        directory = Path(self._temp_directory.name) / "scripts"
        directory.mkdir()
        (directory / "script_001.sql").write_text("select 1", encoding="utf-8")

        with patch("app.script_monitor.write_log"):
            result = check_scripts(
                force=True,
                today=date(2026, 6, 17),
                directory=directory,
            )

        self.assertTrue(result.checked)
        self.assertTrue(result.first_run)
        self.assertEqual(result.total_files, 1)
        self.assertEqual(result.new_files, [])
        self.assertFalse(should_check_today(date(2026, 6, 17)))

    def test_next_check_detects_new_script(self):
        directory = Path(self._temp_directory.name) / "scripts"
        directory.mkdir()
        (directory / "script_001.sql").write_text("select 1", encoding="utf-8")

        with patch("app.script_monitor.write_log"):
            check_scripts(force=True, today=date(2026, 6, 17), directory=directory)
            (directory / "script_002.sql").write_text("select 2", encoding="utf-8")
            result = check_scripts(
                force=True,
                today=date(2026, 6, 18),
                directory=directory,
            )

        self.assertFalse(result.first_run)
        self.assertEqual(result.total_files, 2)
        self.assertEqual(result.new_files, ["script_002.sql"])
        self.assertEqual(result.pending_files, ["script_002.sql"])
        self.assertEqual(load_pending_scripts(), ["script_002.sql"])

    def test_daily_check_is_skipped_when_already_checked_today(self):
        directory = Path(self._temp_directory.name) / "scripts"
        directory.mkdir()
        (directory / "script_001.sql").write_text("select 1", encoding="utf-8")

        with patch("app.script_monitor.write_log"):
            check_scripts(force=True, today=date(2026, 6, 17), directory=directory)
            result = check_scripts(
                force=False,
                today=date(2026, 6, 17),
                directory=directory,
            )

        self.assertFalse(result.checked)
        self.assertEqual(result.new_files, [])

    def test_mark_pending_scripts_done_clears_pending_list(self):
        directory = Path(self._temp_directory.name) / "scripts"
        directory.mkdir()
        (directory / "script_001.sql").write_text("select 1", encoding="utf-8")

        with patch("app.script_monitor.write_log"):
            check_scripts(force=True, today=date(2026, 6, 17), directory=directory)
            (directory / "script_002.sql").write_text("select 2", encoding="utf-8")
            check_scripts(force=True, today=date(2026, 6, 18), directory=directory)
            mark_pending_scripts_done()

        self.assertEqual(load_pending_scripts(), [])


if __name__ == "__main__":
    unittest.main()
