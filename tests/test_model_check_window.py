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

    def test_summary_tiles_show_the_counts_and_clicking_one_filters_the_list(self):
        window = self.open_window()
        self.assertEqual({k: t.number.cget("text") for k, t in window.tiles.items()}["Todos"], "–")  # antes de comparar
        self.compare(window)
        counts = {key: tile.number.cget("text") for key, tile in window.tiles.items()}
        self.assertEqual(counts, {"Todos": "3", "Falta rodar": "1", "OK": "1", "Sem campos para conferir": "1", "Nao foi possivel ler": "0"})
        for tile in window.tiles.values():  # o cartao inteiro (caixa, numero e rotulo) responde ao clique
            for widget in [tile.box, tile.number, *tile.box.winfo_children()]:
                self.assertTrue(widget.bind("<Button-1>"), widget)
        window.filter_by("Falta rodar")
        self.assertEqual(window.filter_var.get(), "Falta rodar")
        self.assertEqual(len(self.table_rows(window)), 1)
        self.assertEqual(str(window.tiles["Falta rodar"].box.cget("highlightthickness")), "2")  # o cartao ativo se destaca
        window.filter_by("Todos")
        self.assertEqual(len(self.table_rows(window)), 3)

    def test_setup_collapses_after_a_comparison_and_can_be_reopened(self):
        window = self.open_window()
        self.assertTrue(window.setup.winfo_manager())
        self.compare(window)
        self.assertFalse(window.setup.winfo_manager())
        self.assertIn("10.0.0.35:43309", window.setup_summary.cget("text"))
        self.assertIn("BANCO_MODELO", window.setup_summary.cget("text"))
        self.assertIn("scripts_modelo", window.setup_summary.cget("text"))
        self.assertTrue(window.setup_button.cget("text").startswith("▸"))
        window.setup_button.invoke()
        self.assertTrue(window.setup.winfo_manager())
        self.assertEqual(window.setup_summary.cget("text"), "")

    def test_a_failed_comparison_keeps_the_setup_open_to_fix_the_problem(self):
        window = self.open_window()

        def refuse(**options):
            raise Exception(1045, "Access denied")

        with patch("app.model_check_window.ask_credentials", return_value=("leitura", PASSWORD)), \
             patch.object(db_reader, "_default_connector", refuse):
            window.compare()
            self.pump_window(window)
        self.assertTrue(window.setup.winfo_manager())

    def test_the_status_names_each_long_phase_instead_of_freezing(self):
        window = self.open_window()
        seen = []
        original = window._handle

        def spy(message):
            if message[0] == "progress":
                seen.append((message[1], message[2]))
            original(message)

        window._handle = spy
        self.compare(window)
        texts = [text for _value, text in seen]
        self.assertTrue(any(t.startswith("Interpretando scripts (") for t in texts))
        self.assertIn("Lendo a estrutura do banco modelo...", texts)
        self.assertIn("Conferindo cada script com o banco...", texts)
        values = [value for value, _text in seen]
        self.assertEqual(values, sorted(values))  # a barra nunca anda para tras
        self.assertLess(values[-1], 100)

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

    def test_missing_pieces_are_asked_for_before_any_login(self):
        window = self.open_window()
        with patch("app.model_check_window.ask_credentials") as ask:
            window.host_var.set("")
            window.compare()
            window.host_var.set("10.0.0.35")
            window.port_var.set("abc")
            window.compare()
            window.port_var.set("43309")
            window.db_var.set("")
            window.compare()
            window.db_var.set("BANCO_MODELO")
            window.dir_var.set(str(self.tmp / "nao_existe"))
            window.compare()
            window.dir_var.set("")
            window.compare()
        ask.assert_not_called()
        self.assertEqual([name for name, _ in self.shown], ["showinfo"] * 5)
        self.assertIsNone(window.run)

    def test_choose_the_scripts_folder_and_it_is_remembered_for_the_next_time(self):
        other = self.tmp / "outra_pasta"
        other.mkdir()
        (other / "SCRIPT_X.sql").write_bytes(b"ALTER TABLE CLIENTES ADD COLUMN CLI_EMAIL VARCHAR(200);")
        window = self.open_window()
        with patch("app.model_check_window.filedialog.askdirectory", return_value=str(other)):
            window.choose_directory()
        self.assertEqual(window.dir_var.get(), str(other))
        self.compare(window)
        self.assertEqual([row[0] for row in self.table_rows(window)], ["SCRIPT_X.sql"])  # so a pasta escolhida
        self.assertEqual(str(settings.load_model_check_directory()), str(other))
        window.window.destroy()
        from app.model_check_window import open_model_check_window

        again = open_model_check_window(self.app, self.app, None, "ana")
        self.addCleanup(lambda: again.window.winfo_exists() and again.window.destroy())
        self.assertEqual(again.dir_var.get(), str(other))

    def test_cancelling_the_folder_dialog_keeps_the_current_folder(self):
        window = self.open_window()
        before = window.dir_var.get()
        with patch("app.model_check_window.filedialog.askdirectory", return_value=""):
            window.choose_directory()
        self.assertEqual(window.dir_var.get(), before)

    def list_databases(self, window, names=("information_schema", "BANCO_MODELO", "BANCO_OUTRO", "mysql"), user="leitura"):
        responses = {db_reader.SQL_DATABASES: [(name,) for name in names]}
        with patch("app.model_check_window.ask_credentials", return_value=(user, PASSWORD)) as ask, \
             patch.object(db_reader, "_default_connector", lambda **options: FakeConnection(responses)) as _:
            window.connect_server()
            self.pump_window(window)
        return ask

    def test_connect_lists_the_visible_databases_and_selects_the_saved_one(self):
        window = self.open_window()
        ask = self.list_databases(window)
        ask.assert_called_once()
        self.assertEqual(list(window.db_box.cget("values")), ["BANCO_MODELO", "BANCO_OUTRO"])  # sem os bancos internos
        self.assertEqual(window.db_var.get(), "BANCO_MODELO")
        self.assertIn("2 banco(s)", window.status_var.get())
        self.assertIn("encontrado", window.status_var.get())
        self.assertNotIn(PASSWORD, window.status_var.get())

    def test_connect_without_the_saved_database_asks_to_choose_and_the_choice_is_used(self):
        settings.save_model_db("10.0.0.35", 43309, "", "")
        window = self.open_window()
        self.list_databases(window)
        self.assertEqual(window.db_var.get(), "")
        self.assertIn("Escolha o banco modelo", window.status_var.get())
        window.db_var.set("BANCO_OUTRO")
        data = full_responses("BANCO_OUTRO")
        with patch("app.model_check_window.ask_credentials", return_value=("leitura", PASSWORD)), self.fake_database(responses=data):
            window.compare()
            self.pump_window(window)
        self.assertEqual(window.run.database, "BANCO_OUTRO")
        self.assertEqual(settings.load_model_db()["database"], "BANCO_OUTRO")  # o mesmo banco modelo do Executar no banco

    def test_connect_matches_the_database_ignoring_case(self):
        settings.save_model_db("10.0.0.35", 43309, "banco_modelo", "")
        window = self.open_window()
        self.list_databases(window)
        self.assertEqual(window.db_var.get(), "BANCO_MODELO")

    def test_a_database_outside_the_loaded_list_needs_confirmation(self):
        window = self.open_window()
        self.list_databases(window)
        window.db_var.set("BANCO_FANTASMA")
        with patch("app.model_check_window.messagebox.askyesno", return_value=False) as confirm, \
             patch("app.model_check_window.ask_credentials") as ask:
            window.compare()
        confirm.assert_called_once()
        ask.assert_not_called()
        self.assertIsNone(window.run)

    def test_connect_failures_are_messages_without_the_password(self):
        window = self.open_window()

        def refuse(**options):
            raise Exception(1045, f"Access denied (using password: YES) {options['password']}")

        with patch("app.model_check_window.ask_credentials", return_value=("leitura", PASSWORD)), \
             patch.object(db_reader, "_default_connector", refuse):
            window.connect_server()
            self.pump_window(window)
        self.assertIn("✖", window.status_var.get())
        self.assertNotIn(PASSWORD, window.status_var.get())
        self.assertEqual(str(window.connect_button.cget("state")), "normal")

    def test_connect_with_no_visible_database_explains_the_permission(self):
        window = self.open_window()
        self.list_databases(window, names=("information_schema", "mysql"))
        self.assertIn("nenhum banco visivel", window.status_var.get().lower())

    def test_connect_asks_for_server_and_port_first_and_cancelled_login_does_nothing(self):
        window = self.open_window()
        window.host_var.set("")
        with patch("app.model_check_window.ask_credentials") as ask:
            window.connect_server()
        ask.assert_not_called()
        window.host_var.set("10.0.0.35")
        with patch("app.model_check_window.ask_credentials", return_value=None), \
             patch.object(db_reader, "_default_connector") as connector:
            window.connect_server()
        connector.assert_not_called()

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
        self.assertIsNone(opened.call_args.args[2])  # a janela usa a ultima pasta escolhida (ou a do monitor)


if __name__ == "__main__":
    unittest.main()
