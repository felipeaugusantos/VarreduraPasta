"""Janela de Auditoria de Estrutura SQL com Tk de verdade e conexao falsa."""

import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import settings
from app.sqlaudit import audit_runner, db_reader, report, shared_log, sql_parse
from app.sqlaudit.audit_runner import AuditRun, VersionAudit
from tests.test_sqlaudit_reader import FakeConnection, full_responses

try:
    import tkinter
except ImportError:  # pragma: no cover
    tkinter = None

AVAILABLE = sql_parse.is_available()
PASSWORD = "SenhaSecreta#42"
ROOT_NAME = "379.48.2.30"


def fake_connect(responses_by_database):
    """Substitui connect_readonly: conexao falsa por banco, mantendo a validacao real."""
    real = db_reader.connect_readonly

    def connect(host, port, database, username, password, **kwargs):
        connection = FakeConnection(responses_by_database[database])
        return real(host, port, database, username, password, connector=lambda **_: connection, **{
            k: v for k, v in kwargs.items() if k != "connector"})

    return connect


def responses_for(database, columns):
    responses = full_responses(database)
    responses[db_reader.SQL_TABLES] = [("CLIENTES", "InnoDB", "utf8mb4_general_ci", datetime(2026, 5, 1, 10, 0))]
    responses[db_reader.SQL_COLUMNS] = [
        ("CLIENTES", name, i, column_type, "NO" if name == "CLI_ID" else "YES", None, "", None, None, "")  # CLI_ID e NOT NULL no script
        for i, (name, column_type) in enumerate(columns, 1)
    ]
    return responses


@unittest.skipIf(tkinter is None, "tkinter nao instalado")
@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class WindowTestCase(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict("os.environ", {"VARREDURA_SISTEMA_DATA_DIR": tempfile.mkdtemp()})
        patcher.start()
        self.addCleanup(patcher.stop)
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        original = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = self.tmp / "settings.json"
        self.addCleanup(setattr, settings, "SETTINGS_FILE", original)
        from app import interface

        self.interface = interface
        try:
            with patch.object(interface.VersionScannerApp, "refresh", lambda self: None), \
                 patch.object(interface.VersionScannerApp, "check_scripts_periodically", lambda self: None), \
                 patch.object(interface.VersionScannerApp, "check_for_update_periodically", lambda self: None), \
                 patch.object(interface.VersionScannerApp, "check_jenkins_periodically", lambda self: None):
                self.app = interface.VersionScannerApp()
        except tkinter.TclError as error:
            self.skipTest(f"sem display: {error}")
        self.app.withdraw()
        self.addCleanup(self.app.destroy)

        self.root = self.tmp / ROOT_NAME
        for version, files in {
            "48.2.30.72": {"SCRIPT_1.SQL": "CREATE TABLE CLIENTES (CLI_ID INT NOT NULL);",
                           "SCRIPT_2.SQL": "ALTER TABLE CLIENTES ADD COLUMN CLI_EMAIL VARCHAR(100);",
                           "SCRIPT_3.SQL": "ALTER TABLE CLIENTES MODIFY COLUMN CLI_EMAIL VARCHAR(200);"},
            "Versões Descontinuada/48.2.30.74.150": {"SCRIPT_1.SQL": "CREATE TABLE CLIENTES (CLI_ID INT);"},
        }.items():
            folder = self.root / version / "SCRIPTS"
            folder.mkdir(parents=True)
            for name, text in files.items():
                (folder / name).write_text(text, encoding="utf-8")
        self.databases = {
            "48.2.30.72": "BANCO_AUTCOM_379_48_02_30_72",
            "48.2.30.74.150": "BANCO_AUTCOM_379_48_02_30_74_150",
        }
        self.logged = []
        patcher = patch("app.sqlaudit_window.write_log", side_effect=self.logged.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        settings.save_sqlaudit_server("10.0.0.35", 43309, "", "encrypt", "")
        self.control = self.tmp / "controle"  # nunca a pasta de rede de verdade
        settings.save_script_control_directory(str(self.control))
        # Dialogo modal inesperado travaria o teste: registra e falha no fim.
        self.unexpected = []
        for name, answer in (("showinfo", "ok"), ("showwarning", "ok"), ("showerror", "ok"), ("askyesno", False)):
            patcher = patch(
                f"app.sqlaudit_window.messagebox.{name}",
                side_effect=lambda *args, _name=name, _answer=answer, **kwargs: self.unexpected.append((_name, args)) or _answer,
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: self.assertEqual(self.unexpected, [], "dialogo inesperado"))

    def open(self, root=True):
        if root:
            settings.save_sqlaudit_config(str(self.root), {}, {})
        with patch.object(self.interface.VersionScannerApp, "update", wraps=self.app.update):
            self.app.open_sqlaudit_window()
        self.sa = self.app.sqlaudit_window
        self.addCleanup(lambda: self.sa.window.winfo_exists() and self.sa.window.destroy())
        return self.sa

    def pump(self, timeout=8.0, min_wait=0.0):
        start = time.time()
        end = start + timeout
        while time.time() < end:
            self.app.update()
            if time.time() - start >= min_wait and not self.sa.busy and self.sa.queue.empty():
                self.app.update()
                return
            time.sleep(0.01)
        self.fail(f"a janela ficou ocupada ({self.sa.busy})")

    def discover(self):
        self.sa.root_var.set(str(self.root))
        self.sa.discover()
        self.pump()

    def login(self, user="leitura"):
        return patch("app.sqlaudit_window.ask_credentials", return_value=(user, PASSWORD))

    def connection(self, **by_database):
        return patch("app.sqlaudit.audit_runner.connect_readonly", fake_connect(by_database or {
            "BANCO_AUTCOM_379_48_02_30_72": responses_for("BANCO_AUTCOM_379_48_02_30_72", [("CLI_ID", "int(11)"), ("CLI_EMAIL", "varchar(200)")]),
            "BANCO_AUTCOM_379_48_02_30_74_150": responses_for("BANCO_AUTCOM_379_48_02_30_74_150", [("CLI_ID", "int(11)")]),
        }))

    @staticmethod
    def rows(table):
        return [table.item(iid, "values") for iid in table.get_children()]

    def edit(self, version, database=None, base=None, fork=None, register=False):
        def fake_wait(dialog):
            entries = self.find(dialog, "TEntry")
            boxes = self.find(dialog, "TCombobox")
            if database is not None:
                boxes[0].set(database)  # o banco agora e uma lista (editavel)
            if base is not None:
                boxes[1].set(base)
            if fork is not None:
                entries[0].delete(0, "end")
                entries[0].insert(0, fork)
            wanted = "Salvar e registrar para todos" if register else "Salvar"
            [b for b in self.find(dialog, "TButton") if b.cget("text") == wanted][0].invoke()

        self.sa.window.wait_window = fake_wait
        self.sa._edit_version(version)

    @classmethod
    def find(cls, widget, kind):
        found = []
        for child in widget.winfo_children():
            if child.winfo_class() == kind:
                found.append(child)
            found.extend(cls.find(child, kind))
        return found


class DiscoveryTests(WindowTestCase):
    def test_menu_opens_one_window_and_discovers_versions_from_the_saved_root(self):
        self.open()
        self.pump(min_wait=0.3)  # a descoberta automatica dispara 50 ms depois de abrir
        self.assertEqual([r[1] for r in self.rows(self.sa.versions_table)], ["48.2.30.72", "48.2.30.74.150"])
        self.app.open_sqlaudit_window()
        self.assertIs(self.app.sqlaudit_window, self.sa)

    def test_suggested_databases_and_default_selection(self):
        self.open(root=False)
        self.discover()
        rows = {r[1]: r for r in self.rows(self.sa.versions_table)}
        self.assertEqual(rows["48.2.30.72"][4], self.databases["48.2.30.72"])
        self.assertEqual(rows["48.2.30.74.150"][4], self.databases["48.2.30.74.150"])
        self.assertEqual(rows["48.2.30.72"][0], "☑")
        self.assertEqual((rows["48.2.30.74.150"][0], rows["48.2.30.74.150"][5]), ("☐", "Descontinuada"))  # descontinuada nao vem marcada

    def test_missing_root_asks_for_it_and_empty_root_reports_nothing_found(self):
        self.open(root=False)
        with patch("app.sqlaudit_window.messagebox.showinfo") as info:
            self.sa.discover()
        info.assert_called_once()
        empty = self.tmp / "vazia"
        empty.mkdir()
        self.sa.root_var.set(str(empty))
        self.sa.discover()
        self.pump()
        self.assertIn("Nenhuma pasta", self.sa.status_var.get())

    def test_click_toggles_selection_only_in_the_first_column(self):
        self.open(root=False)
        self.discover()
        table = self.sa.versions_table
        event = type("E", (), {"x": 5, "y": 5})()
        with patch.object(table, "identify_region", return_value="cell"), \
             patch.object(table, "identify_column", return_value="#1"), \
             patch.object(table, "identify_row", return_value="48.2.30.72"):
            self.assertEqual(self.sa._on_versions_click(event), "break")
        self.assertNotIn("48.2.30.72", self.sa.checked)
        with patch.object(table, "identify_region", return_value="cell"), \
             patch.object(table, "identify_column", return_value="#2"):
            self.assertIsNone(self.sa._on_versions_click(event))
        self.assertNotIn("48.2.30.72", self.sa.checked)
        self.sa._check_all(True)
        self.assertEqual(len(self.sa.checked), 2)


class AnalysisTests(WindowTestCase):
    def analyze(self):
        self.open(root=False)
        self.discover()
        with self.login(), self.connection():
            self.sa.analyze()
            self.pump()

    def test_full_analysis_shows_results_filters_and_details(self):
        self.analyze()
        sa = self.sa
        self.assertIsNotNone(sa.run)
        self.assertEqual([a.version for a in sa.run.versions], ["48.2.30.72"])  # a descontinuada nao estava marcada
        rows = self.rows(sa.results_table)
        self.assertTrue(all(r[1] == "48.2.30.72" for r in rows))
        states = {r[3]: r[0] for r in rows}
        self.assertEqual(states["CLIENTES.CLI_EMAIL"], "Compatível")
        self.assertIn("Compatível", sa.status_var.get())
        self.assertIn("não comprova", sa.status_var.get())
        # arquivo SCRIPT_2 foi substituido pelo SCRIPT_3
        files = {r[1]: r[2] for r in self.rows(sa.files_table)}
        self.assertEqual(files["SCRIPT_2.SQL"], "Substituído")
        self.assertEqual(self.rows(sa.versions_table)[0][5], "Compatível")
        self.assertEqual(str(sa.export_button.cget("state")), "normal")

    def test_divergence_is_listed_with_expected_and_found(self):
        self.open(root=False)
        self.discover()
        divergent = {"BANCO_AUTCOM_379_48_02_30_72": responses_for("BANCO_AUTCOM_379_48_02_30_72", [("CLI_ID", "int"), ("CLI_EMAIL", "varchar(100)")])}
        with self.login(), self.connection(**divergent):
            self.sa.analyze()
            self.pump()
        self.sa.state_filter.set("Com problema (erro, divergente, ausente)")
        rows = self.rows(self.sa.results_table)
        self.assertEqual([(r[0], r[3]) for r in rows], [("Divergente", "CLIENTES.CLI_EMAIL")])
        self.sa.results_table.selection_set(self.sa.results_table.get_children()[0])
        self.sa.show_detail()
        detail = self.sa.detail.get("1.0", "end")
        self.assertIn("esperado varchar(200), encontrado varchar(100)", detail)
        self.assertIn("SCRIPT_3.SQL, linha 1", detail)
        self.sa.search_var.set("nao-existe")
        self.assertEqual(self.rows(self.sa.results_table), [])
        self.sa.search_var.set("")
        self.sa.state_filter.set("Todos")
        self.sa.version_filter.set("outra")
        self.assertEqual(self.rows(self.sa.results_table), [])

    def test_password_never_reaches_status_logs_widgets_settings_or_report(self):
        self.analyze()
        path = self.tmp / "relatorio.html"
        with patch("app.sqlaudit_window.filedialog.asksaveasfilename", return_value=str(path)):
            self.sa.export()
            self.pump()
        self.assertIn("gravado", self.sa.status_var.get())
        haystacks = [
            self.sa.status_var.get(),
            " ".join(self.logged),
            settings.SETTINGS_FILE.read_text(encoding="utf-8"),
            path.read_text(encoding="utf-8"),
            str(self.rows(self.sa.results_table)),
            str(self.rows(self.sa.warnings_table)),
        ]
        for text in haystacks:
            self.assertNotIn(PASSWORD, text)
        self.assertIn("leitura", settings.SETTINGS_FILE.read_text(encoding="utf-8"))  # usuario sim, senha nunca

    def test_run_is_written_to_the_audit_log_without_secrets(self):
        self.analyze()
        self.assertEqual(len(self.logged), 1)
        self.assertTrue(self.logged[0].startswith("Auditoria SQL concluida | usuario="))
        self.assertIn("destino=BANCO_AUTCOM_379_48_02_30_72", self.logged[0])

    def test_database_error_is_shown_per_version_and_other_versions_continue(self):
        self.open(root=False)
        self.discover()
        self.sa._check_all(True)
        only_one = {"BANCO_AUTCOM_379_48_02_30_72": responses_for("BANCO_AUTCOM_379_48_02_30_72", [("CLI_ID", "int"), ("CLI_EMAIL", "varchar(200)")])}
        with self.login(), self.connection(**only_one):  # o outro banco nao existe -> KeyError dentro do conector
            self.sa.analyze()
            self.pump()
        by_version = {a.version: a for a in self.sa.run.versions}
        self.assertFalse(by_version["48.2.30.72"].error)
        self.assertIn("Erro inesperado", by_version["48.2.30.74.150"].error)
        self.assertEqual(self.rows(self.sa.versions_table)[1][5], "Erro")
        self.assertTrue(any("Erro:" in r[1] for r in self.rows(self.sa.warnings_table)))

    def test_row_cap_is_shown_and_asks_to_refine(self):
        self.analyze()
        self.sa.rows = [dict(self.sa.rows[0], tabela=f"T{i}") for i in range(6000)]
        self.sa.apply_filters()
        self.assertEqual(len(self.sa.results_table.get_children()), 5000)
        self.assertTrue(self.sa.count_var.get().startswith("5000 de 6000"))
        self.assertIn("refine", self.sa.count_var.get())


class ControlTests(WindowTestCase):
    def test_validation_messages_before_any_connection(self):
        self.open(root=False)
        self.discover()
        with patch("app.sqlaudit_window.messagebox.showinfo") as info:
            self.sa._check_all(False)
            self.sa.analyze()
        info.assert_called_once()
        self.sa._check_all(True)
        with patch("app.sqlaudit_window.messagebox.showwarning") as warning:
            self.sa.databases["48.2.30.72"] = ""
            self.sa.analyze()
            self.assertIn("Informe o banco", warning.call_args.args[1])
            self.sa.databases["48.2.30.72"] = "X"
            self.sa.port_var.set("abc")
            self.sa.analyze()
            self.assertIn("porta", warning.call_args.args[1])
            self.sa.port_var.set("43309")
            self.sa.tls_var.set("Verificar certificado")
            self.sa.ca_var.set("")
            self.sa.analyze()
            self.assertIn("CA", warning.call_args.args[1])

    def test_login_cancel_starts_nothing(self):
        self.open(root=False)
        self.discover()
        with patch("app.sqlaudit_window.ask_credentials", return_value=None), \
             patch("app.sqlaudit.audit_runner.run_audit") as run:
            self.sa.analyze()
        run.assert_not_called()
        self.assertEqual(self.sa.busy, "")

    def test_cancel_stops_the_running_analysis(self):
        self.open(root=False)
        self.discover()
        started = threading.Event()

        def slow(request, progress=None, cancel_event=None, connector=None):
            progress("[1/1] 48.2.30.72: lendo scripts", 0.3)
            started.set()
            cancel_event.wait(5)
            return AuditRun(datetime.now(), "x", request.root, [VersionAudit("48.2.30.72", "B")], datetime.now(), cancelled=cancel_event.is_set())

        with self.login(), patch("app.sqlaudit.audit_runner.run_audit", slow):
            self.sa.analyze()
            self.assertEqual(self.sa.busy, "analisando")
            self.assertEqual(str(self.sa.cancel_button.cget("state")), "normal")
            self.assertEqual(str(self.sa.analyze_button.cget("state")), "disabled")
            self.assertTrue(started.wait(3))
            self.sa.cancel()
            self.pump()
        self.assertTrue(self.sa.run.cancelled)
        self.assertIn("cancelada", self.sa.status_var.get())
        self.assertTrue(self.logged[-1].startswith("Auditoria SQL cancelada"))
        self.assertEqual(str(self.sa.cancel_button.cget("state")), "disabled")

    def test_closing_while_running_asks_and_cancels(self):
        self.open(root=False)
        self.discover()
        release = threading.Event()

        def slow(request, progress=None, cancel_event=None, connector=None):
            cancel_event.wait(5)
            release.set()
            return AuditRun(datetime.now(), "x", request.root)

        with self.login(), patch("app.sqlaudit.audit_runner.run_audit", slow):
            self.sa.analyze()
            with patch("app.sqlaudit_window.messagebox.askyesno", return_value=False):
                self.sa.close()
            self.assertTrue(self.sa.window.winfo_exists())
            with patch("app.sqlaudit_window.messagebox.askyesno", return_value=True):
                self.sa.close()
            self.assertTrue(release.wait(3))
        self.assertFalse(self.sa.window.winfo_exists())

    def test_unexpected_worker_failure_is_shown_not_swallowed(self):
        self.open(root=False)
        self.discover()
        with self.login(), patch("app.sqlaudit.audit_runner.run_audit", side_effect=RuntimeError("boom")), \
             patch("app.sqlaudit_window.messagebox.showerror") as error:
            self.sa.analyze()
            self.pump()
        self.assertIn("boom", error.call_args.args[1])
        self.assertEqual(self.sa.busy, "")
        self.assertEqual(str(self.sa.analyze_button.cget("state")), "normal")

    def test_connection_test_reports_ok_and_failure(self):
        self.open(root=False)
        self.discover()
        self.sa.versions_table.selection_set("48.2.30.72")
        with self.login(), self.connection():
            self.sa.test_connection()
            self.pump()
        self.assertTrue(self.sa.status_var.get().startswith("✔"))
        self.assertIn("1 tabela(s) visivel(is)", self.sa.status_var.get())
        with self.login(), patch("app.sqlaudit.audit_runner.connect_readonly", side_effect=db_reader.AuditDbError("Usuario ou senha do banco incorretos.")):
            self.sa.test_connection()
            self.pump()
        self.assertTrue(self.sa.status_var.get().startswith("✖"))
        self.assertIn("incorretos", self.sa.status_var.get())

    def test_server_settings_are_remembered_but_never_the_password(self):
        self.open(root=False)
        self.discover()
        self.sa.host_var.set("10.0.0.35")
        self.sa.port_var.set("43309")
        with self.login("auditor"), self.connection():
            self.sa.analyze()
            self.pump()
        saved = settings.load_sqlaudit_server()
        self.assertEqual((saved["host"], saved["port"], saved["username"]), ("10.0.0.35", 43309, "auditor"))
        self.assertNotIn(PASSWORD, settings.SETTINGS_FILE.read_text(encoding="utf-8"))


class EditVersionTests(WindowTestCase):
    def test_base_fork_and_database_are_saved_and_survive_a_new_discovery(self):
        self.open(root=False)
        self.discover()
        self.edit("48.2.30.74.150", database="BANCO_CORRIGIDO", base="48.2.30.72", fork="2")
        node = self.sa.tree.nodes["48.2.30.74.150"]
        self.assertEqual((node.base, node.fork), ("48.2.30.72", 2))
        row = {r[1]: r for r in self.rows(self.sa.versions_table)}["48.2.30.74.150"]
        self.assertEqual((row[2], row[3], row[4]), ("48.2.30.72", "2", "BANCO_CORRIGIDO"))
        config = settings.load_sqlaudit_config()
        self.assertEqual(config["tree"]["48.2.30.74.150"], {"base": "48.2.30.72", "fork": 2})
        self.assertEqual(config["databases"]["48.2.30.74.150"], "BANCO_CORRIGIDO")
        self.sa._saved_tree, self.sa._saved_databases = config["tree"], config["databases"]
        self.sa.discover()
        self.pump()
        self.assertEqual(self.sa.tree.nodes["48.2.30.74.150"].fork, 2)

    def test_invalid_fork_and_cycle_are_refused_without_changing_anything(self):
        self.open(root=False)
        self.discover()
        with patch("app.sqlaudit_window.messagebox.showwarning") as warning:
            self.edit("48.2.30.72", fork="abc")
            self.assertIn("inteiro", warning.call_args.args[1])
        self.edit("48.2.30.74.150", base="48.2.30.72", fork="1")
        with patch("app.sqlaudit_window.messagebox.showwarning") as warning:
            self.edit("48.2.30.72", base="48.2.30.74.150")
            self.assertIn("Ciclo", warning.call_args.args[1])
        self.assertIsNone(self.sa.tree.nodes["48.2.30.72"].base)

    def test_edit_without_selection_asks_to_select(self):
        self.open(root=False)
        self.discover()
        with patch("app.sqlaudit_window.messagebox.showinfo") as info:
            self.sa.edit_selected()
        info.assert_called_once()


class HistoryTests(WindowTestCase):
    GOOD = {"BANCO_AUTCOM_379_48_02_30_72": None}

    def banks(self, email_type):
        database = "BANCO_AUTCOM_379_48_02_30_72"
        return {database: responses_for(database, [("CLI_ID", "int"), ("CLI_EMAIL", email_type)])}

    def run_analysis(self, email_type="varchar(200)"):
        with self.login(), self.connection(**self.banks(email_type)):
            self.sa.analyze()
            self.pump()

    def start(self):
        self.open(root=False)
        self.discover()

    def history_ids(self):
        return list(self.sa.history_table.get_children())

    def test_analysis_is_saved_in_the_local_history_and_the_shared_summary(self):
        self.start()
        self.run_analysis()
        self.assertEqual(len(self.history_ids()), 1)
        self.assertIn("Histórico #1", self.sa.status_var.get())
        row = self.sa.history_table.item(self.history_ids()[0], "values")
        self.assertEqual(row[1], audit_runner.os_user())
        self.assertIn("48.2.30.72", row[2])
        events = shared_log.read_events(self.control)
        self.assertEqual([(e.event, e.version) for e in events], [("auditoria", "48.2.30.72")])
        self.assertIn("objetos=compativel:3", events[0].detail)
        self.assertNotIn(PASSWORD, shared_log.log_path(self.control).read_text(encoding="utf-8"))
        self.assertNotIn(PASSWORD, str(self.sa.store.load_objects(1)) + str(self.sa.store.load_files(1)))

    def test_history_failures_never_lose_the_result(self):
        self.start()
        with patch.object(self.sa.store, "save_run", side_effect=RuntimeError("disco cheio")), \
             patch("app.sqlaudit.shared_log.append_events", side_effect=OSError("rede fora")):
            self.run_analysis()
        self.assertIsNotNone(self.sa.run)
        self.assertTrue(self.rows(self.sa.results_table))
        status = self.sa.status_var.get()
        self.assertIn("Histórico local não gravado: disco cheio", status)
        self.assertIn("Registro compartilhado indisponível: rede fora", status)

    def test_cancelled_run_is_kept_locally_but_not_announced_to_everyone(self):
        self.start()

        def slow(request, progress=None, cancel_event=None, connector=None):
            cancel_event.wait(5)
            return AuditRun(datetime.now(), "x", request.root, [VersionAudit("48.2.30.72", "B")], datetime.now(), cancelled=True)

        with self.login(), patch("app.sqlaudit.audit_runner.run_audit", slow):
            self.sa.analyze()
            self.sa.cancel()
            self.pump()
        self.assertEqual(len(self.history_ids()), 1)
        self.assertIn("cancelada", self.sa.history_table.item(self.history_ids()[0], "values")[3])
        self.assertEqual(shared_log.read_events(self.control), [])

    def test_view_a_past_run_in_the_objects_tab_and_block_export(self):
        self.start()
        self.run_analysis("varchar(100)")
        self.run_analysis("varchar(200)")
        older = self.history_ids()[-1]
        self.sa.history_table.selection_set(older)
        self.sa.view_selected_run()
        self.assertIsNotNone(self.sa.viewing)
        shown = {r[3]: r[0] for r in self.rows(self.sa.results_table)}
        self.assertEqual(shown["CLIENTES.CLI_EMAIL"], "Divergente")
        self.assertIn("Exibindo a auditoria #1", self.sa.status_var.get())
        with patch("app.sqlaudit_window.messagebox.showinfo") as info, \
             patch("app.sqlaudit_window.filedialog.asksaveasfilename") as save:
            self.sa.export()
        self.assertIn("histórico", info.call_args.args[1])
        save.assert_not_called()

    def test_compare_two_runs_shows_what_got_worse(self):
        self.start()
        self.run_analysis("varchar(200)")
        self.run_analysis("varchar(100)")  # piorou: agora diverge do script
        self.sa.history_table.selection_set(*self.history_ids())
        self.sa.compare_selected()
        self.pump()
        rows = self.rows(self.sa.diff_table)
        worse = [r for r in rows if r[0] == "Piorou"]
        self.assertEqual([(r[1], r[2], r[3], r[4]) for r in worse], [("48.2.30.72", "CLIENTES.CLI_EMAIL", "Compatível", "Divergente")])
        self.assertIn("esperado varchar(200), encontrado varchar(100)", worse[0][5])
        self.assertIn("1 piorou", self.sa.status_var.get())

    def test_compare_one_selected_run_with_the_previous_one(self):
        self.start()
        self.run_analysis("varchar(100)")
        self.run_analysis("varchar(200)")
        newest = self.history_ids()[0]
        self.sa.history_table.selection_set(newest)
        self.sa.compare_selected()
        self.pump()
        self.assertEqual([r[0] for r in self.rows(self.sa.diff_table)], ["Melhorou"])
        with patch("app.sqlaudit_window.messagebox.showinfo") as info:
            self.sa.history_table.selection_set(self.history_ids()[-1])
            self.sa.compare_selected()
        self.assertIn("anterior", info.call_args.args[1])

    def test_selection_rules_and_delete(self):
        self.start()
        self.run_analysis()
        with patch("app.sqlaudit_window.messagebox.showinfo") as info:
            self.sa.history_table.selection_remove(*self.history_ids())
            self.sa.view_selected_run()
            self.sa.compare_selected()
        self.assertEqual(info.call_count, 2)
        self.sa.history_table.selection_set(self.history_ids())
        with patch("app.sqlaudit_window.messagebox.askyesno", return_value=False):
            self.sa.delete_selected()
        self.assertEqual(len(self.history_ids()), 1)
        with patch("app.sqlaudit_window.messagebox.askyesno", return_value=True):
            self.sa.delete_selected()
        self.assertEqual(self.history_ids(), [])
        self.assertEqual(self.sa.store.list_runs(), [])

    def test_shared_origin_lists_every_operators_audits_and_reports_an_unavailable_share(self):
        self.start()
        shared_log.append_events([shared_log.SharedEvent("2026-11-05 09:00:00", "ana", "48.2.30.72", "auditoria", "banco=B; objetos=ausente:2")], self.control)
        shared_log.append_events([shared_log.fork_event("bia", "48.2.30.72", "x", 1)], self.control)
        self.sa.origin_var.set("Todos os operadores")
        self.sa.refresh_history()
        self.pump()
        rows = self.rows(self.sa.history_table)
        self.assertEqual([(r[1], r[2]) for r in rows], [("ana", "48.2.30.72")])  # so eventos de auditoria
        with patch("app.sqlaudit_window.messagebox.showinfo") as info:
            self.sa.view_selected_run()
        self.assertIn("Meus registros", info.call_args.args[1])
        with patch("app.sqlaudit.shared_log.read_events", side_effect=OSError("sem rede")):
            self.sa.refresh_history()
            self.pump()
        self.assertIn("indisponível", self.sa.status_var.get())


class SharedForkTests(WindowTestCase):
    def start(self):
        self.open(root=False)
        self.discover()

    def test_register_birth_for_everyone_writes_an_append_only_event(self):
        self.start()
        with patch("app.sqlaudit_window.messagebox.askyesno", return_value=True) as ask:
            self.edit("48.2.30.74.150", base="48.2.30.72", fork="2", register=True)
            self.pump()
        self.assertIn("SCRIPT_2", ask.call_args.args[1])
        self.assertIn("registrado para todos", self.sa.status_var.get())
        (event,) = shared_log.read_events(self.control)
        self.assertEqual((event.event, event.version, event.user), ("fork", "48.2.30.74.150", audit_runner.os_user()))
        fork = shared_log.latest_forks([event])["48.2.30.74.150"]
        self.assertEqual((fork.base, fork.fork, fork.database), ("48.2.30.72", 2, "BANCO_AUTCOM_379_48_02_30_74_150"))

    def test_registering_needs_base_and_fork_and_a_confirmation(self):
        self.start()
        with patch("app.sqlaudit_window.messagebox.showwarning") as warning:
            self.edit("48.2.30.74.150", base="48.2.30.72", register=True)  # sem fork
            self.assertIn("base e o fork", warning.call_args.args[1])
        self.assertIsNone(self.sa.tree.nodes["48.2.30.74.150"].base)  # nada foi salvo
        with patch("app.sqlaudit_window.messagebox.askyesno", return_value=False):
            self.edit("48.2.30.74.150", base="48.2.30.72", fork="2", register=True)
        self.assertEqual(shared_log.read_events(self.control), [])  # salvou local, mas nao registrou

    def test_unavailable_share_is_reported_when_registering(self):
        self.start()
        with patch("app.sqlaudit_window.messagebox.askyesno", return_value=True), \
             patch("app.sqlaudit.shared_log.append_events", side_effect=OSError("rede fora")):
            self.edit("48.2.30.74.150", base="48.2.30.72", fork="2", register=True)
            self.pump()
        self.assertTrue(self.sa.status_var.get().startswith("✖"))
        self.assertIn("rede fora", self.sa.status_var.get())

    def test_discovery_applies_a_fork_registered_by_another_operator(self):
        shared_log.append_events([shared_log.fork_event("bia", "48.2.30.74.150", "48.2.30.72", 2, "BANCO_X")], self.control)
        self.start()
        node = self.sa.tree.nodes["48.2.30.74.150"]
        self.assertEqual((node.base, node.fork), ("48.2.30.72", 2))
        texts = [r[1] for r in self.rows(self.sa.warnings_table)]
        self.assertTrue(any("registrado por bia" in t and "aplicado" in t for t in texts))

    def test_local_fork_wins_over_the_shared_one_with_a_notice(self):
        shared_log.append_events([shared_log.fork_event("bia", "48.2.30.74.150", "48.2.30.72", 2)], self.control)
        settings.save_sqlaudit_config("", {"48.2.30.74.150": {"base": "48.2.30.72", "fork": 7}}, {})
        self.open(root=False)
        self.sa._saved_tree = settings.load_sqlaudit_config()["tree"]
        self.discover()
        self.assertEqual(self.sa.tree.nodes["48.2.30.74.150"].fork, 7)
        self.assertTrue(any("difere do registrado por bia" in r[1] for r in self.rows(self.sa.warnings_table)))

    def test_shared_fork_with_unknown_base_is_not_applied_and_unavailable_share_is_tolerated(self):
        shared_log.append_events([shared_log.fork_event("bia", "48.2.30.74.150", "9.9.9", 2)], self.control)
        self.start()
        self.assertIsNone(self.sa.tree.nodes["48.2.30.74.150"].base)
        self.assertTrue(any("base desconhecida" in r[1] for r in self.rows(self.sa.warnings_table)))
        with patch("app.sqlaudit.shared_log.read_events", side_effect=OSError("sem rede")):
            self.sa.discover()
            self.pump()
        self.assertEqual(len(self.sa.order), 2)  # a descoberta funciona mesmo sem a pasta de controle
        self.assertTrue(any("indisponível" in r[1] for r in self.rows(self.sa.warnings_table)))


class FixScriptTests(WindowTestCase):
    def start(self):
        self.open(root=False)
        self.discover()

    def analyze(self, email_type="varchar(100)"):
        database = "BANCO_AUTCOM_379_48_02_30_72"
        banks = {database: responses_for(database, [("CLI_ID", "int"), ("CLI_EMAIL", email_type)])}
        with self.login(), self.connection(**banks):
            self.sa.analyze()
            self.pump()

    def generate(self, path):
        with patch("app.sqlaudit_window.filedialog.asksaveasfilename", return_value=str(path)):
            self.sa.generate_fix_script()
            self.pump()

    def test_button_follows_the_state_of_the_analysis(self):
        self.start()
        self.assertEqual(str(self.sa.fix_button.cget("state")), "disabled")
        with patch("app.sqlaudit_window.messagebox.showinfo") as info, \
             patch("app.sqlaudit_window.filedialog.asksaveasfilename") as save:
            self.sa.generate_fix_script()
        self.assertIn("Faça uma análise", info.call_args.args[1])
        save.assert_not_called()
        self.analyze()
        self.assertEqual(str(self.sa.fix_button.cget("state")), "normal")
        self.sa._set_busy("analisando")
        self.assertEqual(str(self.sa.fix_button.cget("state")), "disabled")
        self.sa._set_busy("")

    def test_generates_a_reviewable_script_and_logs_it_without_secrets(self):
        self.start()
        self.analyze("varchar(20)")  # o script pede varchar(200): divergencia de tipo
        path = self.tmp / "corretivo.sql"
        self.generate(path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("NAO EXECUTE SEM REVISAR", text)
        self.assertIn("-- USE `BANCO_AUTCOM_379_48_02_30_72`;", text)
        self.assertIn("-- ALTER TABLE `CLIENTES` MODIFY COLUMN `CLI_EMAIL` varchar(200);", text)  # alto risco: comentado
        self.assertFalse([l for l in text.splitlines() if l.startswith("ALTER TABLE")])
        status = self.sa.status_var.get()
        self.assertTrue(status.startswith("✔ Script corretivo gravado"))
        self.assertIn("1 comando(s), 1 comentado(s)", status)
        self.assertIn("REVISE", status)
        line = self.logged[-1]
        self.assertTrue(line.startswith("Auditoria SQL script_corretivo | usuario="))
        self.assertIn("1 comando(s) para revisao, 1 comentado(s)", line)
        for haystack in (text, status, " ".join(self.logged)):
            self.assertNotIn(PASSWORD, haystack)

    def test_low_risk_fix_is_written_as_a_live_statement(self):
        self.start()
        database = "BANCO_AUTCOM_379_48_02_30_72"
        banks = {database: responses_for(database, [("CLI_ID", "int")])}  # falta CLI_EMAIL
        with self.login(), self.connection(**banks):
            self.sa.analyze()
            self.pump()
        path = self.tmp / "c.sql"
        self.generate(path)
        live = [l for l in path.read_text(encoding="utf-8").splitlines() if l and not l.startswith("--")]
        self.assertEqual(live, ["ALTER TABLE `CLIENTES` ADD COLUMN `CLI_EMAIL` varchar(200);"])

    def test_cancelled_dialog_and_unwritable_path(self):
        self.start()
        self.analyze()
        with patch("app.sqlaudit_window.filedialog.asksaveasfilename", return_value=""):
            self.sa.generate_fix_script()
        self.assertEqual(self.sa.busy, "")
        self.generate(self.tmp / "nao-existe" / "x.sql")
        self.assertTrue(self.sa.status_var.get().startswith("✖ Não foi possível gravar o script"))
        self.generate(self.tmp / "x.txt")
        self.assertIn(".sql", self.sa.status_var.get())

    def test_not_available_while_viewing_a_past_audit(self):
        self.start()
        self.analyze()
        self.analyze("varchar(200)")
        self.sa.history_table.selection_set(self.sa.history_table.get_children()[-1])
        self.sa.view_selected_run()
        with patch("app.sqlaudit_window.messagebox.showinfo") as info, \
             patch("app.sqlaudit_window.filedialog.asksaveasfilename") as save:
            self.sa.generate_fix_script()
        self.assertIn("histórico", info.call_args.args[1])
        save.assert_not_called()

    def test_versions_without_a_result_are_left_out_and_reported(self):
        self.start()
        self.sa._check_all(True)  # a segunda versao nao tem banco na conexao falsa -> erro
        database = "BANCO_AUTCOM_379_48_02_30_72"
        banks = {database: responses_for(database, [("CLI_ID", "int"), ("CLI_EMAIL", "varchar(20)")])}
        with self.login(), self.connection(**banks):
            self.sa.analyze()
            self.pump()
        self.generate(self.tmp / "c.sql")
        self.assertIn("versão(ões) sem resultado fora do arquivo", self.sa.status_var.get())

    def test_the_model_never_reaches_history_or_reports(self):
        self.start()
        self.analyze("varchar(20)")
        self.assertIsNotNone(self.sa.run.versions[0].model)
        import json

        self.assertNotIn("model", json.dumps(report.build_json(self.sa.run), default=str).lower().replace("modelo", ""))
        self.assertEqual(len(self.sa.store.list_runs()), 1)  # o historico gravou sem o modelo


class DatabaseSelectionTests(WindowTestCase):
    DB_72 = "BANCO_AUTCOM_379_48_02_30_72"
    DB_150 = "BANCO_AUTCOM_379_48_02_30_74_150"

    def start(self):
        self.open(root=False)
        self.discover()
        self.sa.host_var.set("10.0.0.35")
        self.sa.port_var.set("43309")

    def listing(self, names, ok=True, message="Conectado: banco(s) visivel(is) para o usuario."):
        return patch("app.sqlaudit.audit_runner.list_databases", return_value=(ok, names, message))

    def dialog(self, choose=None, button="Aplicar", filter_text=None, record=None):
        """Substitui o wait_window do dialogo de bancos: preenche e aperta um botao."""
        record = {} if record is None else record

        def fake_wait(dialog):
            combos = self.find(dialog, "TCombobox")
            record["combos"] = combos
            record["values"] = [list(c.cget("values")) for c in combos]  # lido agora: depois o dialogo e destruido
            labels = [self.sa.window.getvar(str(l.cget("textvariable"))) for l in self.find(dialog, "TLabel") if str(l.cget("textvariable"))]
            record["indicators"] = labels
            if filter_text is not None:
                self.find(dialog, "TEntry")[0].insert(0, filter_text)
                dialog.update()
                record["filtered"] = [tuple(c.cget("values")) for c in combos]
            for position, name in (choose or {}).items():
                combos[position].set(name)
            [b for b in self.find(dialog, "TButton") if b.cget("text") == button][0].invoke()

        self.sa.window.wait_window = fake_wait
        return record

    def connect(self, names, **dialog_kwargs):
        self.start()
        record = self.dialog(**dialog_kwargs)
        with self.login(), self.listing(names) as listing:
            self.sa.connect_server()
            self.pump()
        return record, listing

    def test_connect_asks_for_the_login_once_lists_the_databases_and_opens_the_selection(self):
        record, listing = self.connect(["banco_autcom_379_48_02_30_72", "OUTRO", self.DB_150.lower()], choose={1: "OUTRO"})
        server = listing.call_args.args[0]
        self.assertEqual((server.host, server.port, server.username, server.password), ("10.0.0.35", 43309, "leitura", PASSWORD))
        self.assertEqual(len(record["combos"]), 2)  # uma por versao descoberta
        self.assertEqual(record["values"][0], ["banco_autcom_379_48_02_30_72", "OUTRO", self.DB_150.lower()])
        self.assertEqual(record["indicators"], ["✔ existe no servidor", "✔ existe no servidor"])  # casou ignorando maiusculas
        self.assertEqual(self.sa.databases["48.2.30.72"], "banco_autcom_379_48_02_30_72")  # nome canonico do servidor
        self.assertEqual(self.sa.databases["48.2.30.74.150"], "OUTRO")  # escolhido
        self.assertEqual(settings.load_sqlaudit_config()["databases"]["48.2.30.74.150"], "OUTRO")
        rows = {r[1]: r for r in self.rows(self.sa.versions_table)}
        self.assertEqual(rows["48.2.30.74.150"][4], "OUTRO")
        self.assertIn("Bancos aplicados: 2 versão(ões) com banco encontrado", self.sa.status_var.get())
        self.assertEqual(self.sa.busy, "")

    def test_a_suggested_database_that_does_not_exist_is_flagged_everywhere(self):
        record, _ = self.connect([self.DB_72, "OUTRO"], button="Cancelar")
        self.assertEqual(record["indicators"], ["✔ existe no servidor", "✖ não existe no servidor"])
        self.assertEqual(self.sa.server_databases, [self.DB_72, "OUTRO"])  # a lista fica carregada mesmo cancelando
        self.assertEqual(self.sa.databases["48.2.30.74.150"], self.DB_150)  # nada foi alterado
        rows = {r[1]: r for r in self.rows(self.sa.versions_table)}
        self.assertEqual(rows["48.2.30.74.150"][5], "Banco não encontrado")
        notes = [r for r in self.rows(self.sa.warnings_table) if r[0] == "48.2.30.74.150"]
        self.assertTrue(notes and "não existe no servidor" in notes[0][1])

    def test_applying_a_typed_database_that_is_not_on_the_server_is_allowed_but_stays_flagged(self):
        self.connect([self.DB_72], choose={1: "digitado_a_mao"})
        self.assertEqual(self.sa.databases["48.2.30.74.150"], "digitado_a_mao")
        self.assertIn("1 sem banco no servidor", self.sa.status_var.get())

    def test_cancelling_the_login_connects_to_nothing(self):
        self.start()
        with patch("app.sqlaudit_window.ask_credentials", return_value=None), self.listing([self.DB_72]) as listing:
            self.sa.connect_server()
        listing.assert_not_called()
        self.assertEqual(self.sa.busy, "")
        self.assertEqual(self.sa.server_databases, [])

    def test_failures_and_an_empty_list_show_the_reason_and_open_no_dialog(self):
        self.start()

        def must_not_open(_dialog):
            raise AssertionError("o dialogo nao deveria abrir")

        self.sa.window.wait_window = must_not_open
        for message in ("Usuario ou senha do banco incorretos.", "Conectado, mas nenhum banco visivel para este usuario. Confira se ele tem SELECT"):
            with self.login(), self.listing([], ok=False, message=message):
                self.sa.connect_server()
                self.pump()
            self.assertEqual(self.sa.status_var.get(), "✖ " + message)
        self.assertEqual(self.sa.server_databases, [])

    def test_validation_before_asking_for_the_login(self):
        self.start()
        self.sa.port_var.set("abc")
        with patch("app.sqlaudit_window.messagebox.showwarning") as warning, patch("app.sqlaudit_window.ask_credentials") as ask:
            self.sa.connect_server()
        self.assertIn("porta", warning.call_args.args[1])
        ask.assert_not_called()

    def test_without_discovered_versions_it_only_reports_how_many_databases_are_visible(self):
        self.open(root=False)
        self.sa.host_var.set("10.0.0.35")
        self.sa.port_var.set("43309")
        with self.login(), self.listing(["A", "B", "C"]), patch("app.sqlaudit_window.messagebox.showinfo") as info:
            self.sa.connect_server()
            self.pump()
        self.assertIn("3 banco(s) visível(is)", info.call_args.args[1])
        self.assertEqual(self.sa.server_databases, ["A", "B", "C"])
        with patch("app.sqlaudit_window.messagebox.showinfo") as info:
            self.sa.choose_databases()
        self.assertIn("Descubra as versões", info.call_args.args[1])

    def test_the_selection_screen_needs_a_loaded_list(self):
        self.start()
        with patch("app.sqlaudit_window.messagebox.showinfo") as info:
            self.sa.choose_databases()
        self.assertIn("Conecte ao servidor", info.call_args.args[1])

    def test_enter_in_server_or_port_and_the_button_start_the_connection(self):
        self.start()
        self.assertTrue(self.sa.host_entry.bind("<Return>"))
        self.assertTrue(self.sa.port_entry.bind("<Return>"))
        self.assertEqual(str(self.sa.connect_button.cget("text")), "Conectar e listar bancos...")
        record = self.dialog(button="Cancelar")
        with self.login(), self.listing([self.DB_72]):
            self.sa.connect_button.invoke()
            self.pump()
        self.assertIn("combos", record)

    def test_the_connect_button_is_disabled_while_busy(self):
        self.start()
        self.sa._set_busy("analisando")
        self.assertEqual(str(self.sa.connect_button.cget("state")), "disabled")
        with self.login() as ask:
            self.sa.connect_server()
        ask.assert_not_called()
        self.sa._set_busy("")
        self.assertEqual(str(self.sa.connect_button.cget("state")), "normal")

    def test_the_filter_narrows_every_list(self):
        record, _ = self.connect([self.DB_72, self.DB_150, "OUTRO"], button="Cancelar", filter_text="74_150")
        self.assertEqual(record["filtered"], [(self.DB_150,), (self.DB_150,)])

    def test_the_edit_dialog_offers_the_server_databases(self):
        self.connect([self.DB_72, "OUTRO"], button="Cancelar")
        seen = {}

        def fake_wait(dialog):
            seen["values"] = list(self.find(dialog, "TCombobox")[0].cget("values"))
            [b for b in self.find(dialog, "TButton") if b.cget("text") == "Cancelar"][0].invoke()

        self.sa.window.wait_window = fake_wait
        self.sa._edit_version("48.2.30.72")
        self.assertEqual(seen["values"], [self.DB_72, "OUTRO"])

    def test_analysis_warns_about_a_database_missing_from_the_loaded_list(self):
        self.connect([self.DB_72], button="Cancelar")  # a 74.150 nao existe no servidor
        self.sa._check_all(True)
        with patch("app.sqlaudit_window.messagebox.askyesno", return_value=False) as ask, self.login() as login:
            self.sa.analyze()
        self.assertIn(self.DB_150, ask.call_args.args[1])
        self.assertIn("Continuar mesmo assim", ask.call_args.args[1])
        login.assert_not_called()  # recusou: nem pediu a senha
        self.assertEqual(self.sa.busy, "")
        banks = {self.DB_72: responses_for(self.DB_72, [("CLI_ID", "int"), ("CLI_EMAIL", "varchar(200)")])}
        with patch("app.sqlaudit_window.messagebox.askyesno", return_value=True), self.login(), self.connection(**banks):
            self.sa.analyze()
            self.pump()
        self.assertEqual(len(self.sa.run.versions), 2)

    def test_no_warning_when_every_database_is_on_the_list_or_no_list_was_loaded(self):
        self.start()
        banks = {self.DB_72: responses_for(self.DB_72, [("CLI_ID", "int"), ("CLI_EMAIL", "varchar(200)")])}
        with patch("app.sqlaudit_window.messagebox.askyesno") as ask, self.login(), self.connection(**banks):
            self.sa.analyze()  # sem lista carregada: comportamento de antes
            self.pump()
        ask.assert_not_called()

    def test_the_password_never_reaches_status_settings_logs_or_the_tables(self):
        self.connect([self.DB_72, "OUTRO"], choose={1: "OUTRO"})
        haystacks = [
            self.sa.status_var.get(),
            " ".join(self.logged),
            settings.SETTINGS_FILE.read_text(encoding="utf-8"),
            str(self.rows(self.sa.versions_table)),
            str(self.rows(self.sa.warnings_table)),
        ]
        for text in haystacks:
            self.assertNotIn(PASSWORD, text)
        self.assertEqual(settings.load_sqlaudit_server()["username"], "leitura")


if __name__ == "__main__":
    unittest.main()
