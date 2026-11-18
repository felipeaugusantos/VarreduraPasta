"""Janela `Comparar com o banco modelo`: lista OK / Falta rodar por script. So leitura."""

import csv
import time
import unittest
from unittest.mock import patch

from app import settings
from app.sqlaudit import db_reader
from tests.test_sqlaudit_reader import FakeConnection, full_responses
from tests.test_sqlaudit_window import PASSWORD, WindowTestCase

try:
    import tkinter
except ImportError:  # pragma: no cover
    tkinter = None


@unittest.skipIf(tkinter is None, "tkinter nao instalado")
class ModelCheckWindowTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.scripts = self.tmp / "scripts_modelo"
        (self.scripts / "0_GERAL").mkdir(parents=True)
        (self.scripts / "0_GERAL" / "SCRIPT_GER_1.sql").write_bytes(b"ALTER TABLE CLIENTES ADD COLUMN CLI_EMAIL VARCHAR(200);")
        (self.scripts / "0_GERAL" / "SCRIPT_GER_2.sql").write_bytes(b"ALTER TABLE CLIENTES ADD COLUMN CLI_FALTA INT; ALTER TABLE NOVA ADD COLUMN X INT;")
        (self.scripts / "SCRIPT_DADOS.sql").write_bytes(b"INSERT INTO CLIENTES (CLI_ID) VALUES (1);")
        settings.save_model_db("10.0.0.35", 43309, "BANCO_MODELO", "")
        self.shown = []
        for name in ("showinfo", "showwarning", "showerror", "askyesno"):
            patcher = patch(f"app.model_check_window.messagebox.{name}", side_effect=lambda *a, _n=name, **k: self.shown.append((_n, a)) or True)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("app.model_check_window.write_log", side_effect=self.logged.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_window(self):
        from app.model_check_window import open_model_check_window

        window = open_model_check_window(self.app, self.app, self.scripts, "ana")
        self.addCleanup(lambda: window.window.winfo_exists() and window.window.destroy())
        return window

    def pump_window(self, window, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            self.app.update()
            if not window.busy and window.queue.empty():
                self.app.update()
                return
            time.sleep(0.01)
        self.fail(f"a janela ficou ocupada ({window.busy})")

    def fake_database(self, database="BANCO_MODELO", responses=None):
        data = responses or full_responses(database)
        return patch.object(db_reader, "_default_connector", lambda **options: FakeConnection(data))

    def compare(self, window, user="leitura", **kwargs):
        with patch("app.model_check_window.ask_credentials", return_value=(user, PASSWORD)), self.fake_database(**kwargs):
            window.compare()
            self.pump_window(window)

    def table_rows(self, window):
        return [window.table.item(i, "values") for i in window.table.get_children()]

    def test_lists_ok_and_what_is_missing_per_script_with_the_pending_ones_first(self):
        window = self.open_window()
        self.compare(window)
        rows = self.table_rows(window)
        by_name = {row[0].replace("\\", "/"): row for row in rows}
        ok = by_name["0_GERAL/SCRIPT_GER_1.sql"]
        self.assertEqual(ok[1], "OK")
        self.assertIn("Tudo ja esta no banco", ok[2])
        falta = by_name["0_GERAL/SCRIPT_GER_2.sql"]
        self.assertEqual(falta[1], "Falta rodar")
        self.assertNotIn("Falta rodar", falta[2])  # o resultado ja diz isso
        self.assertIn("campo CLIENTES.CLI_FALTA", falta[2])
        self.assertIn("tabela NOVA", falta[2])
        self.assertEqual(by_name["SCRIPT_DADOS.sql"][1], "Sem campos para conferir")
        self.assertEqual(rows[0][1], "Falta rodar")  # o que falta rodar vem primeiro
        self.assertIn("3 script(s)", window.status_var.get())
        self.assertIn("1 falta rodar", window.status_var.get())
        self.assertEqual(str(window.export_button.cget("state")), "normal")

    def test_numbers_in_names_are_sorted_as_numbers(self):
        (self.scripts / "0_GERAL" / "SCRIPT_GER_11.sql").write_bytes(b"ALTER TABLE CLIENTES ADD COLUMN F11 INT;")
        window = self.open_window()
        self.compare(window)
        names = [row[0].replace("\\", "/") for row in self.table_rows(window) if row[1] == "Falta rodar"]
        self.assertEqual(names, ["0_GERAL/SCRIPT_GER_2.sql", "0_GERAL/SCRIPT_GER_11.sql"])

    def test_filter_search_and_detail(self):
        window = self.open_window()
        self.compare(window)
        window.filter_var.set("Falta rodar")
        self.assertEqual(len(self.table_rows(window)), 1)
        window.filter_var.set("Todos")
        window.search_var.set("GER_1")
        self.assertEqual(len(self.table_rows(window)), 1)
        window.search_var.set("")
        window.filter_var.set("Falta rodar")
        window.table.selection_set(window.table.get_children())
        window.show_detail()
        text = window.detail.get("1.0", "end")
        self.assertIn("Falta rodar (2):", text)
        self.assertIn("campo CLIENTES.CLI_FALTA", text)

    def test_credentials_are_asked_each_time_never_saved_and_the_audit_line_has_no_secrets(self):
        window = self.open_window()
        self.compare(window, user="leitura")
        self.assertEqual(settings.load_model_db()["username"], "leitura")
        self.assertNotIn(PASSWORD, settings.SETTINGS_FILE.read_text(encoding="utf-8"))
        line = [item for item in self.logged if item.startswith("Banco modelo comparacao concluida")][-1]
        self.assertIn("destino=BANCO_MODELO", line)
        self.assertIn("3 script(s)", line)
        self.assertNotIn(PASSWORD, line + window.status_var.get() + repr(window.run))
        with patch("app.model_check_window.ask_credentials", return_value=(None)) as ask, self.fake_database():
            window.compare()
        ask.assert_called_once()  # pediu de novo (nada ficou guardado)
        self.assertEqual(self.shown, [])

    def test_cancelling_the_login_runs_nothing(self):
        window = self.open_window()
        with patch("app.model_check_window.ask_credentials", return_value=None):
            window.compare()
        self.assertIsNone(window.run)
        self.assertEqual(self.table_rows(window), [])

    def test_without_the_model_database_configured_it_asks_to_configure_first(self):
        settings.save_model_db("", 3306, "", "")
        window = self.open_window()
        with patch("app.model_check_window.open_db_settings") as open_settings, \
             patch("app.model_check_window.ask_credentials") as ask:
            window.compare()
        open_settings.assert_called_once()
        ask.assert_not_called()
        self.assertEqual(self.shown[0][0], "showinfo")

    def test_a_failing_database_is_a_message_without_the_password_and_the_window_is_usable_again(self):
        window = self.open_window()

        def refuse(**options):
            raise Exception(1045, f"Access denied (using password: YES) {options['password']}")

        with patch("app.model_check_window.ask_credentials", return_value=("leitura", PASSWORD)), \
             patch.object(db_reader, "_default_connector", refuse):
            window.compare()
            self.pump_window(window)
        self.assertEqual(self.shown[-1][0], "showerror")
        self.assertNotIn(PASSWORD, str(self.shown) + window.status_var.get())
        self.assertEqual(str(window.compare_button.cget("state")), "normal")
        self.assertEqual(str(window.cancel_button.cget("state")), "disabled")

    def test_export_writes_a_csv_of_all_scripts(self):
        window = self.open_window()
        self.compare(window)
        path = self.tmp / "comparacao.csv"
        with patch("app.model_check_window.filedialog.asksaveasfilename", return_value=str(path)):
            window.export()
            self.pump_window(window)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        self.assertEqual(rows[0][:4], ["script", "resultado", "faltando", "observacoes"])
        self.assertEqual(len(rows) - 1, 3)
        faltando = {row[0].replace("\\", "/"): row[2] for row in rows[1:]}
        self.assertIn("campo CLIENTES.CLI_FALTA", faltando["0_GERAL/SCRIPT_GER_2.sql"])
        self.assertNotIn(PASSWORD, path.read_text(encoding="utf-8-sig"))

    def test_opening_twice_reuses_the_same_window(self):
        first = self.open_window()
        second = self.open_window()
        self.assertIs(first, second)

    def test_the_button_exists_in_the_script_monitor_window(self):
        with patch.object(self.interface.VersionScannerApp, "_run_script_monitor_check", lambda *a, **k: None), \
             patch("app.interface.open_model_check_window") as opened:
            self.app.open_script_monitor_window()
            monitor = self.app.script_monitor_window
            self.addCleanup(lambda: monitor.winfo_exists() and monitor.destroy())
            buttons = [b for b in WindowTestCase.find(monitor, "TButton") if b.cget("text") == "Comparar com o banco"]
            self.assertEqual(len(buttons), 1)
            buttons[0].invoke()
        opened.assert_called_once()
        self.assertEqual(str(opened.call_args.args[2]), str(settings.load_script_directory()))


if __name__ == "__main__":
    unittest.main()
