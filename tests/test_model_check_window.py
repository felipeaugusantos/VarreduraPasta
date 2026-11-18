"""Janela `Comparar com o banco modelo`: lista OK / Falta rodar por script. So leitura."""

import csv
import re
import time
import unittest
from unittest.mock import patch

from app import settings
from app.sqlaudit import db_reader
from tests.test_secret_store import FakeBackend
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
        self.assertIn("Tudo ja esta no banco", ok[3])
        falta = by_name["0_GERAL/SCRIPT_GER_2.sql"]
        self.assertEqual(falta[1], "Falta rodar")
        self.assertNotIn("Falta rodar", falta[3])  # o resultado ja diz isso
        self.assertIn("campo CLIENTES.CLI_FALTA", falta[3])
        self.assertIn("tabela NOVA", falta[3])
        self.assertEqual(by_name["SCRIPT_DADOS.sql"][1], "Sem campos para conferir")
        self.assertEqual(rows[0][1], "Falta rodar")  # o que falta rodar vem primeiro
        self.assertIn("3 script(s)", window.status_var.get())
        self.assertIn("1 falta rodar", window.status_var.get())
        self.assertEqual(str(window.export_button.cget("state")), "normal")

    # ---- controle compartilhado (feito/pendente) ----
    def use_control(self):
        from app import script_monitor

        self.control_dir = self.tmp / "controle"
        settings.save_script_directory(self.scripts)
        settings.save_script_control_directory(self.control_dir)
        # o controle ja existe (senao o monitor trataria tudo como base)
        script_monitor.append_events(
            [script_monitor.LedgerEvent("2026-01-01 00:00:00", "ana", "SCRIPT_ANTIGO.sql", "feito")], self.control_dir
        )
        return script_monitor

    def test_control_column_shows_pending_and_done_from_the_shared_ledger(self):
        monitor = self.use_control()
        monitor.mark_scripts_done(["SCRIPT_DADOS.sql"], control_directory=self.control_dir, directory=self.scripts)
        window = self.open_window()
        self.compare(window)
        by_name = {row[0].replace("\\", "/"): row for row in self.table_rows(window)}
        self.assertEqual(by_name["SCRIPT_DADOS.sql"][2], "Feito")
        self.assertEqual(by_name["0_GERAL/SCRIPT_GER_1.sql"][2], "Pendente")

    def test_control_is_blank_when_the_folder_is_not_the_monitored_one(self):
        self.use_control()
        settings.save_script_directory(self.tmp / "outra")
        window = self.open_window()
        self.compare(window)
        self.assertTrue(all(row[2] == "—" for row in self.table_rows(window)))
        self.assertEqual(str(window.mark_button.cget("state")), "disabled")

    def test_mark_ok_as_done_writes_only_ok_scripts_to_the_ledger(self):
        monitor = self.use_control()
        window = self.open_window()
        self.compare(window)
        self.assertEqual(str(window.mark_button.cget("state")), "normal")
        window.mark_ok_done()
        self.pump_window(window)
        events = [e for e in monitor.read_ledger(self.control_dir) if e.event == "feito" and e.user != "ana"]
        self.assertEqual([e.script.replace("\\", "/") for e in events], ["0_GERAL/SCRIPT_GER_1.sql"])
        self.assertIn("conferido no banco modelo", events[0].detail)
        by_name = {row[0].replace("\\", "/"): row for row in self.table_rows(window)}
        self.assertEqual(by_name["0_GERAL/SCRIPT_GER_1.sql"][2], "Feito")
        self.assertEqual(by_name["0_GERAL/SCRIPT_GER_2.sql"][2], "Pendente")  # falta rodar: nao marca
        self.assertEqual(str(window.mark_button.cget("state")), "disabled")
        self.assertTrue(any("marcado_feito_pelo_banco" in line for line in self.logged))

    def test_declining_the_confirmation_writes_nothing(self):
        monitor = self.use_control()
        window = self.open_window()
        self.compare(window)
        with patch("app.model_check_window.messagebox.askyesno", return_value=False):
            window.mark_ok_done()
        self.assertEqual([e for e in monitor.read_ledger(self.control_dir) if e.script != "SCRIPT_ANTIGO.sql"], [])

    def test_done_in_the_ledger_but_missing_in_the_database_is_flagged(self):
        monitor = self.use_control()
        monitor.mark_scripts_done(["0_GERAL/SCRIPT_GER_2.sql"], control_directory=self.control_dir, directory=self.scripts)
        window = self.open_window()
        self.compare(window)
        window.table.selection_set("0_GERAL/SCRIPT_GER_2.sql")
        window.show_detail()
        text = window.detail.get("1.0", "end")
        self.assertIn("Marcado como feito no controle, mas ainda falta no banco", text)
        self.assertIn("conflict", window.table.item("0_GERAL/SCRIPT_GER_2.sql", "tags"))

    def test_summary_tiles_show_the_counts_and_clicking_one_filters_the_list(self):
        window = self.open_window()
        self.assertEqual({k: t.number.cget("text") for k, t in window.tiles.items()}["Todos"], "–")  # antes de comparar
        self.compare(window)
        counts = {key: tile.number.cget("text") for key, tile in window.tiles.items()}
        self.assertEqual(counts, {"Todos": "3", "Falta rodar": "1", "OK": "1", "Sem campos para conferir": "1", "Copia identica": "0", "Nao foi possivel ler": "0"})
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

    def add_dependency_scripts(self):
        folder = self.scripts / "2_WEBSERVICE"
        folder.mkdir(exist_ok=True)
        (folder / "SCRIPT_WS_676.sql").write_bytes(b"ALTER TABLE CLIENTES ADD COLUMN CLI_NOVA VARCHAR(50);")
        (folder / "SCRIPT_WS_677.sql").write_bytes(
            b"UPDATE CLIENTES SET CLI_NOVA = '' WHERE CLI_NOVA IS NULL; ALTER TABLE CLIENTES MODIFY CLI_NOVA VARCHAR(50) NOT NULL DEFAULT '';"
        )

    def test_detail_says_which_script_to_run_first_and_the_order_dialog_lists_them(self):
        self.add_dependency_scripts()
        window = self.open_window()
        self.compare(window)
        sep = __import__("os").sep
        name_676, name_677 = "2_WEBSERVICE" + sep + "SCRIPT_WS_676.sql", "2_WEBSERVICE" + sep + "SCRIPT_WS_677.sql"
        rows = {row[0]: row for row in self.table_rows(window)}
        self.assertIn("criado por", rows[name_677][3])
        window.table.selection_set(name_677)
        window.show_detail()
        text = window.detail.get("1.0", "end")
        self.assertIn("Rode ANTES", text)
        self.assertIn(f"→ {name_676} (também falta rodar)", text)
        self.assertEqual(str(window.order_button.cget("state")), "normal")
        window.show_order()
        dialog = window.order_dialog
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        box = [w for w in WindowTestCase.find(dialog, "Text")][0]
        lines = box.get("1.0", "end").strip().splitlines()
        positions = {name: next(i for i, line in enumerate(lines) if name in line) for name in (name_676, name_677)}
        self.assertLess(positions[name_676], positions[name_677])  # quem cria vem antes
        self.assertIn("(depois de:", lines[positions[name_677]])
        self.assertTrue(lines[0].startswith("1. "))

    def test_order_dialog_says_when_nothing_is_missing_and_csv_carries_the_dependency_columns(self):
        window = self.open_window()
        self.assertEqual(str(window.order_button.cget("state")), "disabled")  # antes de comparar
        for path in list(self.scripts.rglob("SCRIPT_GER_2.sql")):
            path.unlink()  # remove o unico que faltava
        self.compare(window)
        with patch("app.model_check_window.messagebox.showinfo") as info:
            window.show_order()
        info.assert_called_once()
        self.add_dependency_scripts()
        self.compare(window)
        path = self.tmp / "dep.csv"
        with patch("app.model_check_window.filedialog.asksaveasfilename", return_value=str(path)):
            window.export()
            self.pump_window(window)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        by_name = {row["script"].replace("\\", "/"): row for row in rows}
        ws677 = by_name["2_WEBSERVICE/SCRIPT_WS_677.sql"]
        self.assertIn("SCRIPT_WS_676.sql", ws677["rodar_antes"])
        self.assertEqual((by_name["2_WEBSERVICE/SCRIPT_WS_676.sql"]["ordem_sugerida"], ws677["ordem_sugerida"]), ("1", "2"))
        self.assertEqual(by_name["0_GERAL/SCRIPT_GER_1.sql"]["ordem_sugerida"], "")  # OK: fora da ordem

    def test_generate_the_missing_script_for_the_selected_pending_script(self):
        window = self.open_window()
        self.compare(window)
        sep = __import__("os").sep
        name = "0_GERAL" + sep + "SCRIPT_GER_2.sql"
        window.table.selection_set(name)
        path = self.tmp / "faltando.sql"
        with patch("app.model_check_window.filedialog.asksaveasfilename", return_value=str(path)) as dialog:
            window.generate_missing_script()
            self.pump_window(window)
        self.assertEqual(dialog.call_args.kwargs["initialfile"], "faltando-SCRIPT_GER_2.sql")
        text = path.read_text(encoding="utf-8")
        self.assertIn("ALTER TABLE `CLIENTES` ADD COLUMN `CLI_FALTA` int", text)
        self.assertNotIn("ALTER TABLE `CLIENTES` ADD COLUMN `CLI_EMAIL`", text)  # esse ja existe: nao entra
        self.assertIn("-- Script de origem: " + name, text)
        self.assertIn("-- USE `BANCO_MODELO`;", text)
        self.assertNotIn(PASSWORD, text)
        self.assertIn("comando(s) do que falta rodar", window.status_var.get())
        line = [item for item in self.logged if item.startswith("Banco modelo comparacao script_faltante")][-1]
        self.assertIn("SCRIPT_GER_2.sql", line)
        self.assertNotIn(PASSWORD, line)

    def test_generate_asks_for_a_pending_script_and_never_writes_for_an_ok_one(self):
        window = self.open_window()
        self.compare(window)
        sep = __import__("os").sep
        with patch("app.model_check_window.messagebox.showinfo") as info, \
             patch("app.model_check_window.filedialog.asksaveasfilename") as save:
            window.table.selection_set(())
            window.generate_missing_script()  # nada selecionado
            window.table.selection_set("0_GERAL" + sep + "SCRIPT_GER_1.sql")
            window.generate_missing_script()  # OK: nada a gerar
        self.assertEqual(info.call_count, 2)
        save.assert_not_called()

    def test_cancelling_the_save_dialog_writes_nothing(self):
        window = self.open_window()
        self.compare(window)
        sep = __import__("os").sep
        window.table.selection_set("0_GERAL" + sep + "SCRIPT_GER_2.sql")
        with patch("app.model_check_window.filedialog.asksaveasfilename", return_value=""):
            window.generate_missing_script()
        self.assertFalse(list(self.tmp.glob("*.sql")))

    def test_generate_the_whole_plan_in_one_file_in_the_suggested_order(self):
        self.add_dependency_scripts()
        window = self.open_window()
        self.compare(window)
        self.assertEqual(str(window.plan_button.cget("state")), "normal")
        path = self.tmp / "plano.sql"
        with patch("app.model_check_window.filedialog.asksaveasfilename", return_value=str(path)) as dialog:
            window.generate_plan()
            self.pump_window(window)
        self.assertTrue(dialog.call_args.kwargs["initialfile"].startswith("plano-banco-modelo-"))
        text = path.read_text(encoding="utf-8")
        sep = __import__("os").sep
        names = re.findall(r"^-- \d+/\d+  (\S+)$", text, re.MULTILINE)
        self.assertLess(names.index("2_WEBSERVICE" + sep + "SCRIPT_WS_676.sql"), names.index("2_WEBSERVICE" + sep + "SCRIPT_WS_677.sql"))
        self.assertIn("ALTER TABLE `CLIENTES` ADD COLUMN `CLI_NOVA`", text)
        self.assertNotIn(PASSWORD, text)
        self.assertIn("Plano gravado", window.status_var.get())
        line = [item for item in self.logged if item.startswith("Banco modelo comparacao plano_faltante")][-1]
        self.assertNotIn(PASSWORD, line)

    def test_plan_button_follows_the_state_and_cancelling_writes_nothing(self):
        window = self.open_window()
        self.assertEqual(str(window.plan_button.cget("state")), "disabled")  # antes de comparar
        with patch("app.model_check_window.messagebox.showinfo") as info:
            window.generate_plan()
        info.assert_called_once()
        self.compare(window)
        with patch("app.model_check_window.filedialog.asksaveasfilename", return_value=""):
            window.generate_plan()
        self.assertFalse(list(self.tmp.glob("*.sql")))

    def test_an_identical_copy_is_listed_as_a_copy_and_is_left_out_of_the_order(self):
        self.add_dependency_scripts()
        (self.scripts / "3_TAENTREGUE").mkdir()
        (self.scripts / "3_TAENTREGUE" / "SCRIPT_ENTREGA_676.sql").write_bytes(
            (self.scripts / "2_WEBSERVICE" / "SCRIPT_WS_676.sql").read_bytes()
        )
        window = self.open_window()
        self.compare(window)
        sep = __import__("os").sep
        copy = "3_TAENTREGUE" + sep + "SCRIPT_ENTREGA_676.sql"
        rows = {row[0]: row for row in self.table_rows(window)}
        self.assertEqual(rows[copy][1], "Copia identica")
        self.assertIn("Conteudo identico a", rows[copy][3])
        self.assertEqual(window.tiles["Copia identica"].number.cget("text"), "1")
        window.table.selection_set(copy)
        window.show_detail()
        self.assertIn("não entra na ordem de execução", window.detail.get("1.0", "end"))
        order = [name for name, _ in __import__("app.sqlaudit.model_check", fromlist=["run_order"]).run_order(window.run.results)]
        self.assertNotIn(copy, order)

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
        self.assertEqual(rows[0][:5], ["script", "resultado", "faltando", "rodar_antes", "ordem_sugerida"])
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
             patch.object(self.interface.VersionScannerApp, "open_model_check_view") as opened:
            self.app.open_script_monitor_window()
            monitor = self.app.script_monitor_window
            self.addCleanup(lambda: monitor.winfo_exists() and monitor.destroy())
            buttons = [b for b in WindowTestCase.find(monitor, "TButton") if b.cget("text") == "Comparar com o banco"]
            self.assertEqual(len(buttons), 1)
            buttons[0].invoke()
        opened.assert_called_once()  # abre o painel da tela inicial, sem janela empilhada

    def test_home_screen_switches_to_the_embedded_panel_and_back(self):
        self.assertEqual(self.app.current_view, "projetos")
        panel = self.app.show_view("banco")
        self.assertTrue(panel.embedded)
        self.assertEqual(self.app.current_view, "banco")
        self.assertTrue(self.app.model_view.winfo_manager())
        self.assertIs(self.app.show_view("banco"), None)
        self.assertIs(self.app.open_model_check_view(), panel)
        self.app.show_view("projetos")
        self.assertFalse(self.app.model_view.winfo_manager())
        self.assertTrue(all(w.winfo_manager() for w in self.app.project_widgets))
        panel.close()  # painel embutido nao fecha
        self.assertTrue(panel.window.winfo_exists())


@unittest.skipIf(tkinter is None, "tkinter nao instalado")
class ModelCheckSavedPasswordTests(ModelCheckWindowTests):
    """Senha lembrada na janela de comparacao (armazenamento falso, como a DPAPI)."""

    def setUp(self):
        super().setUp()
        patcher = patch("app.secret_store.make_backend", lambda: FakeBackend())
        patcher.start()
        self.addCleanup(patcher.stop)

    def store(self):
        from app import secret_store

        return secret_store.default_store()

    def connect(self, window, answer, responses=None):
        names = {db_reader.SQL_DATABASES: [("BANCO_MODELO",)]}
        with patch("app.model_check_window.ask_credentials", return_value=answer) as ask, \
             patch.object(db_reader, "_default_connector", lambda **options: FakeConnection(responses or names)):
            window.connect_server()
            self.pump_window(window)
        return ask

    def test_remember_saves_encrypted_and_the_next_connection_and_comparison_do_not_ask(self):
        window = self.open_window()
        ask = self.connect(window, ("leitura", PASSWORD, True))
        self.assertIn("lembrada neste Windows", window.status_var.get())
        self.assertEqual(ask.call_args.kwargs["can_remember"], True)
        self.assertEqual(self.store().load("10.0.0.35", 43309, "leitura"), PASSWORD)
        self.assertEqual(str(window.forget_button.cget("state")), "normal")
        # nunca em texto, nunca no settings.json
        from app.runtime import data_directory

        self.assertNotIn(PASSWORD.encode(), (data_directory() / "credenciais.dat").read_bytes())
        self.assertNotIn(PASSWORD, settings.SETTINGS_FILE.read_text(encoding="utf-8"))
        # a proxima conexao e a comparacao usam a senha lembrada sem perguntar
        with patch("app.model_check_window.ask_credentials") as again, self.fake_database():
            window.compare()
            self.pump_window(window)
        again.assert_not_called()
        self.assertIsNotNone(window.run)
        self.assertEqual(window.run.database, "BANCO_MODELO")
        self.assertNotIn(PASSWORD, window.status_var.get() + repr(window.run) + " ".join(self.logged))

    def test_without_ticking_remember_nothing_is_saved_and_it_asks_again(self):
        window = self.open_window()
        self.connect(window, ("leitura", PASSWORD, False))
        self.assertIsNone(self.store().load("10.0.0.35", 43309, "leitura"))
        self.assertEqual(str(window.forget_button.cget("state")), "disabled")
        ask = self.connect(window, ("leitura", PASSWORD, False))
        ask.assert_called_once()

    def test_forget_button_erases_the_saved_password_and_says_when_there_is_none(self):
        window = self.open_window()
        self.connect(window, ("leitura", PASSWORD, True))
        window.forget_button.invoke()
        self.assertIsNone(self.store().load("10.0.0.35", 43309, "leitura"))
        self.assertIn("Senha salva apagada", window.status_var.get())
        self.assertEqual(str(window.forget_button.cget("state")), "disabled")
        window.forget_saved()
        self.assertEqual(self.shown[-1][0], "showinfo")  # nada mais a apagar
        ask = self.connect(window, ("leitura", PASSWORD, False))
        ask.assert_called_once()  # voltou a perguntar

    def test_a_saved_password_the_database_refuses_is_erased_and_the_operator_is_told(self):
        window = self.open_window()
        self.connect(window, ("leitura", PASSWORD, True))

        def refuse(**options):
            raise Exception(1045, f"Access denied (using password: YES) {options['password']}")

        with patch("app.model_check_window.ask_credentials") as ask, patch.object(db_reader, "_default_connector", refuse):
            window.connect_server()
            self.pump_window(window)
        ask.assert_not_called()  # usou a salva
        self.assertIn("senha salva não funcionou e foi apagada", window.status_var.get())
        self.assertNotIn(PASSWORD, window.status_var.get())
        self.assertIsNone(self.store().load("10.0.0.35", 43309, "leitura"))

    def test_a_wrong_password_that_was_typed_is_not_treated_as_saved(self):
        window = self.open_window()

        def refuse(**options):
            raise Exception(1045, "Access denied")

        with patch("app.model_check_window.ask_credentials", return_value=("leitura", PASSWORD, False)), \
             patch.object(db_reader, "_default_connector", refuse):
            window.connect_server()
            self.pump_window(window)
        self.assertNotIn("foi apagada", window.status_var.get())

    def test_the_saved_password_belongs_to_the_server_and_port(self):
        window = self.open_window()
        self.connect(window, ("leitura", PASSWORD, True))
        window.host_var.set("10.0.0.99")  # outro servidor: a senha salva nao vale
        self.assertEqual(str(window.forget_button.cget("state")), "disabled")
        ask = self.connect(window, ("leitura", "outra", False))
        ask.assert_called_once()

    def test_without_secure_storage_the_option_and_the_button_do_not_exist(self):
        from app import secret_store

        with patch("app.secret_store.make_backend", lambda: secret_store.UnavailableBackend()):
            window = self.open_window()
            self.assertEqual(window.forget_button.winfo_manager(), "")
            ask = self.connect(window, ("leitura", PASSWORD, True))  # mesmo "marcando", nao guarda
        self.assertEqual(ask.call_args.kwargs["can_remember"], False)
        self.assertFalse((__import__("app.runtime", fromlist=["data_directory"]).data_directory() / "credenciais.dat").exists())


if __name__ == "__main__":
    unittest.main()
