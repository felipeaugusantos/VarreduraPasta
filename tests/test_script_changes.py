import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import settings
from app.script_monitor import (
    DONE_EVENTS,
    LedgerEvent,
    append_events,
    check_scripts,
    mark_scripts_done,
    read_ledger,
    recorded_mod,
)
from tests.test_script_monitor import ScriptMonitorTestCase


def touch(path, seconds_from_now):
    """Muda a data de modificacao do arquivo (positivo = futuro)."""
    moment = path.stat().st_mtime + seconds_from_now
    os.utime(path, (moment, moment))


class ChangedScriptTests(ScriptMonitorTestCase):
    def mark(self, *names):
        return mark_scripts_done(list(names), control_directory=self.control, directory=self.scripts)

    def test_marked_script_is_not_pending_while_unchanged(self):
        self.add_scripts("a.sql")
        with self.as_user("ana"):
            self.check()
            self.add_scripts("b.sql")
            self.check()
            self.mark("b.sql")
            result = self.check()

        self.assertEqual(result.pending, [])
        self.assertEqual(result.changed, [])

    def test_script_edited_after_being_marked_becomes_pending_again(self):
        with self.as_user("ana"):
            self.check()
            self.add_scripts("b.sql")
            self.check()
            self.mark("b.sql")
            self.check()  # a tela verifica de novo depois de marcar
            touch(self.scripts / "b.sql", 3600)
            result = self.check()

        self.assertEqual(result.pending_names, ["b.sql"])
        self.assertEqual(result.changed, ["b.sql"])
        self.assertTrue(result.pending[0].changed)
        self.assertEqual(result.new_pending, ["b.sql"])
        self.assertTrue(any("script_alterado" in str(call) for call in self.write_log.call_args_list))

    def test_marking_again_clears_the_change_for_every_operator(self):
        with self.as_user("ana"):
            self.check()
            self.add_scripts("b.sql")
            self.check()
            self.mark("b.sql")
            touch(self.scripts / "b.sql", 3600)
            self.check()
        with self.as_user("joao"):
            marked, skipped = self.mark("b.sql")
            result = self.check()

        self.assertEqual((marked, skipped), (["b.sql"], []))
        self.assertEqual(result.pending, [])

    def test_changed_script_is_announced_once_per_operator(self):
        with self.as_user("ana"):
            self.check()
            self.add_scripts("b.sql")
            self.check()
            self.mark("b.sql")
            self.check()
            touch(self.scripts / "b.sql", 3600)
            first = self.check()
            second = self.check()

        self.assertEqual(first.new_pending, ["b.sql"])
        self.assertEqual(second.new_pending, [])
        self.assertEqual(second.changed, ["b.sql"])  # continua pendente

    def test_baseline_script_edited_later_is_detected(self):
        self.add_scripts("antigo.sql")
        with self.as_user("ana"):
            self.check()
            touch(self.scripts / "antigo.sql", 3600)
            result = self.check()

        self.assertEqual(result.changed, ["antigo.sql"])

    def test_small_clock_difference_is_not_a_change(self):
        with self.as_user("ana"):
            self.check()
            self.add_scripts("b.sql")
            self.check()
            self.mark("b.sql")
            touch(self.scripts / "b.sql", 1)
            result = self.check()

        self.assertEqual(result.pending, [])

    def test_old_ledger_lines_without_date_never_count_as_changed(self):
        self.add_scripts("legado.sql")
        append_events(
            [
                LedgerEvent("2026-10-01 10:00:00", "ana", "-", "inicio", ""),
                LedgerEvent("2026-10-01 10:00:00", "ana", "legado.sql", "feito", "marcado manualmente"),
            ],
            self.control,
        )
        touch(self.scripts / "legado.sql", 3600)
        with self.as_user("ana"):
            result = self.check()

        self.assertEqual(result.pending, [])

    def test_mark_records_the_file_date_in_the_ledger(self):
        self.add_scripts("b.sql")
        with self.as_user("ana"):
            self.check()
            self.mark("b.sql")

        event = [e for e in read_ledger(self.control) if e.event in DONE_EVENTS and e.script == "b.sql"][-1]
        self.assertEqual(recorded_mod(event), int((self.scripts / "b.sql").stat().st_mtime))


class ScriptDirectorySettingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        original = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = Path(temp.name) / "settings.json"
        self.addCleanup(setattr, settings, "SETTINGS_FILE", original)

    def test_default_when_nothing_is_configured(self):
        from app.config import SCRIPT_MONITOR_DIRECTORY

        self.assertEqual(settings.load_script_directory(), SCRIPT_MONITOR_DIRECTORY)

    def test_saved_value_wins_and_empty_restores_the_default(self):
        from app.config import SCRIPT_MONITOR_DIRECTORY

        settings.save_script_directory(r"D:\Scripts")
        self.assertEqual(settings.load_script_directory(), Path(r"D:\Scripts"))

        settings.save_script_directory("  ")
        self.assertEqual(settings.load_script_directory(), SCRIPT_MONITOR_DIRECTORY)

    def test_check_scripts_uses_the_configured_folder_by_default(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            scripts = Path(temp) / "Meus"
            scripts.mkdir()
            (scripts / "x.sql").write_text("select 1;", encoding="utf-8")
            settings.save_script_directory(str(scripts))
            with patch("app.script_monitor.write_log"):
                result = check_scripts(control_directory=Path(temp) / "ctl")

        self.assertEqual(result.directory, scripts)
        self.assertEqual(result.total_files, 1)


class SettingsWindowScriptFolderTests(ScriptDirectorySettingTests):
    """Com Tk de verdade: a pasta de scripts aparece e e salva em Configuracoes."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict("os.environ", {"VARREDURA_SISTEMA_DATA_DIR": tempfile.mkdtemp()})
        patcher.start()
        self.addCleanup(patcher.stop)
        from app import interface

        self.interface = interface
        with patch.object(interface.VersionScannerApp, "refresh", lambda self: None),              patch.object(interface.VersionScannerApp, "check_scripts_periodically", lambda self: None),              patch.object(interface.VersionScannerApp, "check_for_update_periodically", lambda self: None),              patch.object(interface.VersionScannerApp, "check_jenkins_periodically", lambda self: None):
            self.app = interface.VersionScannerApp()
        self.app.withdraw()
        self.addCleanup(self.app.destroy)

    def test_save_settings_stores_the_script_folder(self):
        with patch.object(self.interface, "messagebox"), patch.object(self.app, "refresh"):
            self.app.save_settings(r"D:\Outra", [], "", "", script_directory=r"E:\Scripts")

        self.assertEqual(settings.load_script_directory(), Path(r"E:\Scripts"))

    def test_save_settings_without_the_argument_keeps_the_current_folder(self):
        settings.save_script_directory(r"E:\Scripts")
        with patch.object(self.interface, "messagebox"), patch.object(self.app, "refresh"):
            self.app.save_settings(r"D:\Outra", [], "", "")

        self.assertEqual(settings.load_script_directory(), Path(r"E:\Scripts"))

    def test_settings_window_shows_the_configured_folder(self):
        settings.save_script_directory(r"E:\Scripts")
        self.app.open_settings_window()
        window = self.app.config_window
        self.app.update()

        def entries(widget):
            found = []
            for child in widget.winfo_children():
                if child.winfo_class() == "TEntry":
                    found.append(child.get())
                found.extend(entries(child))
            return found

        self.assertIn(r"E:\Scripts", entries(window))
        window.destroy()


if __name__ == "__main__":
    unittest.main()
