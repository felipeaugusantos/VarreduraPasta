import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import settings
from app.script_monitor import (
    LedgerEvent,
    check_scripts,
    format_event,
    ledger_path,
    mark_scripts_done,
    parse_event,
    read_ledger,
)


class ScriptMonitorTestCase(unittest.TestCase):
    """Cada operador do TS tem seu settings.json; o controle e compartilhado."""

    def setUp(self):
        self._temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.scripts = root / "Scripts"
        self.scripts.mkdir()
        self.control = root / "_CONTROLE_VARREDURA"
        self.root = root
        self._original_settings_file = settings.SETTINGS_FILE
        self.addCleanup(setattr, settings, "SETTINGS_FILE", self._original_settings_file)
        log_patcher = patch("app.script_monitor.write_log")
        self.write_log = log_patcher.start()
        self.addCleanup(log_patcher.stop)

    @contextmanager
    def as_user(self, user):
        settings.SETTINGS_FILE = self.root / f"settings_{user}.json"
        with patch.dict("os.environ", {"USERNAME": user}):
            yield

    def add_scripts(self, *names):
        for name in names:
            path = self.scripts / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("select 1;", encoding="utf-8")

    def check(self):
        return check_scripts(directory=self.scripts, control_directory=self.control)


class FirstUseTests(ScriptMonitorTestCase):
    def test_existing_scripts_become_baseline_without_pending(self):
        self.add_scripts("2026/10/0001.sql", "2026/10/0002.sql")
        with self.as_user("ana"):
            result = self.check()

        self.assertEqual(result.pending, [])
        self.assertEqual(result.error, "")
        events = read_ledger(self.control)
        self.assertEqual(events[0].event, "inicio")
        self.assertEqual(
            sorted(event.script for event in events if event.event == "base"),
            [str(Path("2026/10/0001.sql")), str(Path("2026/10/0002.sql"))],
        )

    def test_script_created_after_empty_baseline_is_detected(self):
        with self.as_user("ana"):
            self.check()
            self.add_scripts("primeiro.sql")
            result = self.check()

        self.assertEqual(result.pending_names, ["primeiro.sql"])
        self.assertEqual(result.new_pending, ["primeiro.sql"])

    def test_previous_local_pending_scripts_stay_pending_after_migration(self):
        self.add_scripts("0001.sql", "0002.sql", "0003.sql")
        with self.as_user("ana"):
            # Estado da versao anterior: lista local de conhecidos e pendentes.
            settings.save_script_monitor_state(
                "2026-10-21", ["0001.sql", "0002.sql", "0003.sql"], ["0003.sql"]
            )
            result = self.check()

        self.assertEqual(result.pending_names, ["0003.sql"])
        # A lista grande de conhecidos sai do settings.json do operador.
        with self.as_user("ana"):
            self.assertEqual(settings.load_script_monitor_state()["known_files"], [])


class SharedControlTests(ScriptMonitorTestCase):
    def setUp(self):
        super().setUp()
        with self.as_user("ana"):
            self.check()
        self.add_scripts("0004_nova_tabela_pix.sql", "0005_atualiza_cfop.sql")

    def test_new_script_is_announced_once_per_operator(self):
        with self.as_user("ana"):
            first = self.check()
            second = self.check()
        with self.as_user("bruno"):
            other = self.check()

        self.assertEqual(first.new_pending, ["0004_nova_tabela_pix.sql", "0005_atualiza_cfop.sql"])
        self.assertEqual(second.new_pending, [])
        self.assertEqual(len(second.pending), 2)
        # Outro operador tambem recebe o aviso uma vez.
        self.assertEqual(len(other.new_pending), 2)

    def test_script_marked_by_one_operator_is_done_for_everyone(self):
        with self.as_user("ana"):
            marked, skipped = mark_scripts_done(["0004_nova_tabela_pix.sql"], control_directory=self.control)
        with self.as_user("bruno"):
            result = self.check()

        self.assertEqual((marked, skipped), (["0004_nova_tabela_pix.sql"], []))
        self.assertEqual(result.pending_names, ["0005_atualiza_cfop.sql"])
        latest = result.history[0]
        self.assertEqual((latest.user, latest.script, latest.event), ("ana", "0004_nova_tabela_pix.sql", "feito"))
        audit = self.write_log.call_args_list[-2].args[0]
        self.assertIn("marcado_feito | usuario=ana", audit)
        self.assertIn("0004_nova_tabela_pix.sql", audit)

    def test_marking_script_already_done_by_other_operator_is_skipped(self):
        with self.as_user("ana"):
            mark_scripts_done(["0004_nova_tabela_pix.sql"], control_directory=self.control)
        with self.as_user("bruno"):
            marked, skipped = mark_scripts_done(
                ["0004_nova_tabela_pix.sql", "0005_atualiza_cfop.sql"],
                control_directory=self.control,
            )

        self.assertEqual(marked, ["0005_atualiza_cfop.sql"])
        self.assertEqual(skipped, ["0004_nova_tabela_pix.sql"])
        done_events = [event for event in read_ledger(self.control) if event.event == "feito"]
        self.assertEqual(len(done_events), 2)


class ErrorTests(ScriptMonitorTestCase):
    def test_missing_scripts_folder_returns_error_without_writing_control(self):
        with self.as_user("ana"):
            result = check_scripts(directory=self.root / "nao_existe", control_directory=self.control)

        self.assertIn("Pasta de scripts nao encontrada", result.error)
        self.assertFalse(ledger_path(self.control).exists())

    def test_unreachable_control_folder_returns_error(self):
        blocked = self.root / "arquivo_no_lugar_da_pasta"
        blocked.write_text("x", encoding="utf-8")
        with self.as_user("ana"):
            result = check_scripts(directory=self.scripts, control_directory=blocked / "sub")

        self.assertIn("Erro ao verificar scripts", result.error)


class LedgerFormatTests(unittest.TestCase):
    def test_roundtrip_and_separator_is_sanitized(self):
        event = LedgerEvent("2026-10-22 09:14:03", "ana", "pasta|x/0001.sql", "feito", "linha\nquebrada")
        line = format_event(event)
        parsed = parse_event(line)

        self.assertEqual(parsed.script, "pasta/x/0001.sql")
        self.assertEqual(parsed.detail, "linha quebrada")
        self.assertIsNone(parse_event("linha invalida"))


if __name__ == "__main__":
    unittest.main()
