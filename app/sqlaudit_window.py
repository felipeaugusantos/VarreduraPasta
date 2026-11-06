"""Janela `Monitoramento > Auditoria de Estrutura SQL` (Tkinter).

So desenha e conversa com `app/sqlaudit/`; a logica fica la, sem Tkinter. Descoberta
de pastas, teste de conexao, analise e exportacao rodam em thread com fila (nada
de rede ou disco na thread da interface). A senha so existe em memoria durante a
execucao: nao e gravada, nem logada, nem vai para o relatorio.
"""

import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app import theme
from app.logging_utils import write_log
from app.model_db_dialogs import ask_credentials
from app.settings import (
    load_sqlaudit_config,
    load_sqlaudit_server,
    save_sqlaudit_config,
    save_sqlaudit_server,
)
from app.sqlaudit import audit_runner, fix_suggestions, report, shared_log
from app.sqlaudit.audit_store import AuditStore
from app.sqlaudit.comparator import PRECEDENCE, worst
from app.sqlaudit.db_reader import TLS_ENCRYPT, TLS_VERIFY
from app.sqlaudit.version_discovery import discover_versions, version_sort_key
from app.sqlaudit.version_tree import TreeError, VersionTree

TITLE = "Auditoria de Estrutura SQL"
MAX_ROWS_SHOWN = 5000
POLL_MS = 150
ALL = "Todos"
PROBLEMS = "Com problema (erro, divergente, ausente)"
NO_BASE = "(nenhuma)"
TLS_LABELS = {TLS_ENCRYPT: "Criptografar (sem verificar certificado)", TLS_VERIFY: "Verificar certificado"}
CHECKED, UNCHECKED = "☑", "☐"
ORIGIN_LOCAL, ORIGIN_SHARED = "Meus registros", "Todos os operadores"
CHANGE_LABELS = {
    "piorou": "Piorou", "melhorou": "Melhorou", "novo": "Novo", "removido": "Removido",
    "detalhe": "Detalhe mudou", "alterado": "Script alterado", "adicionado": "Script adicionado",
}
EXPORT_TYPES = [("Relatório HTML", "*.html"), ("Planilha CSV", "*.csv"), ("Dados JSON", "*.json")]
FIX_TYPES = [("Script SQL para revisão", "*.sql")]


def open_sqlaudit_window(app):
    return SqlAuditWindow(app)


class SqlAuditWindow:
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app)
        self.window.title(TITLE)
        theme.size_window(self.window, 1180, 880)
        self.window.minsize(960, 640)
        self.window.transient(app)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.folders = {}
        self.order = []
        self.tree = VersionTree()
        self.databases = {}
        self.checked = set()
        self.discovery_warnings = []
        self.run = None
        self.rows = []
        self.shown = {}
        self.store = AuditStore()
        self.viewing = None  # RunInfo do historico exibido na aba Objetos
        self.history_runs = {}
        self.server_databases = []  # bancos que o usuario enxerga no servidor (carregados ao conectar)
        self.database_notes = []  # (versao, aviso): banco sugerido que nao existe no servidor
        self.queue = queue.Queue()
        self.cancel_event = None
        self.busy = ""  # "", "descobrindo", "testando", "analisando", "exportando"
        self._polling = False

        self.root_var = tk.StringVar()
        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.tls_var = tk.StringVar()
        self.ca_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Escolha a pasta raiz das versões e clique em Descobrir versões.")
        self.state_filter = tk.StringVar(value=ALL)
        self.version_filter = tk.StringVar(value=ALL)
        self.search_var = tk.StringVar()
        self.count_var = tk.StringVar()
        self.origin_var = tk.StringVar(value=ORIGIN_LOCAL)

        self._build()
        self._load_saved()

    # ---- construcao ----
    def _build(self):
        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

        ttk.Label(frame, text="Raiz das versões", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.root_var).grid(row=0, column=1, sticky="ew", padx=(10, 0))
        root_buttons = ttk.Frame(frame)
        root_buttons.grid(row=0, column=2, padx=(8, 0))
        self.choose_button = ttk.Button(root_buttons, text="Escolher...", command=self.choose_root)
        self.choose_button.grid(row=0, column=0, padx=(0, 6))
        self.discover_button = ttk.Button(root_buttons, text="Descobrir versões", command=self.discover)
        self.discover_button.grid(row=0, column=1)

        server = ttk.LabelFrame(frame, text="Servidor MySQL (somente leitura)", padding=8)
        server.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        server.columnconfigure(5, weight=1)
        ttk.Label(server, text="Servidor:").grid(row=0, column=0, sticky="w")
        self.host_entry = ttk.Entry(server, textvariable=self.host_var, width=18)
        self.host_entry.grid(row=0, column=1, padx=(6, 12))
        ttk.Label(server, text="Porta:").grid(row=0, column=2, sticky="w")
        self.port_entry = ttk.Entry(server, textvariable=self.port_var, width=7)
        self.port_entry.grid(row=0, column=3, padx=(6, 12))
        for entry in (self.host_entry, self.port_entry):
            entry.bind("<Return>", lambda _event: self.connect_server())
        self.connect_button = ttk.Button(server, text="Conectar e listar bancos...", command=self.connect_server)
        self.connect_button.grid(row=0, column=6, padx=(12, 0), sticky="e")
        ttk.Label(server, text="TLS:").grid(row=0, column=4, sticky="w")
        self.tls_box = ttk.Combobox(server, textvariable=self.tls_var, values=list(TLS_LABELS.values()), state="readonly", width=38)
        self.tls_box.grid(row=0, column=5, sticky="w", padx=(6, 0))
        ttk.Label(server, text="Arquivo da CA:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(server, textvariable=self.ca_var).grid(row=1, column=1, columnspan=5, sticky="ew", padx=(6, 6), pady=(6, 0))
        ttk.Button(server, text="...", width=3, command=self.choose_ca).grid(row=1, column=6, pady=(6, 0))
        ttk.Label(
            server,
            text="Informe servidor e porta e clique em Conectar (ou Enter): pede o usuário e a senha e lista os bancos. "
            "Usuário e senha não são gravados. Use um usuário só com SELECT.",
            style="Muted.TLabel",
        ).grid(row=2, column=0, columnspan=7, sticky="w", pady=(6, 0))

        ttk.Label(frame, text="Versões (clique em ☐ para marcar)", font=self._font("section")).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(12, 4)
        )
        versions_frame = ttk.Frame(frame)
        versions_frame.grid(row=3, column=0, columnspan=3, sticky="ew")
        versions_frame.columnconfigure(0, weight=1)
        columns = ("use", "version", "base", "fork", "database", "state")
        self.versions_table = ttk.Treeview(versions_frame, columns=columns, show="headings", height=4, selectmode="browse")
        headings = {"use": "Auditar", "version": "Versão", "base": "Base", "fork": "Fork", "database": "Banco", "state": "Situação"}
        widths = {"use": 60, "version": 150, "base": 150, "fork": 70, "database": 320, "state": 200}
        for column in columns:
            anchor = "w" if column in ("database", "state") else "center"
            self.versions_table.heading(column, text=headings[column], anchor=anchor)
            self.versions_table.column(column, width=widths[column], anchor=anchor, stretch=column == "database")
        scroll = ttk.Scrollbar(versions_frame, orient="vertical", command=self.versions_table.yview)
        self.versions_table.configure(yscrollcommand=scroll.set)
        self.versions_table.grid(row=0, column=0, sticky="ew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.versions_table.bind("<Button-1>", self._on_versions_click)
        self.versions_table.bind("<Double-1>", lambda _event: self.edit_selected())
        version_buttons = ttk.Frame(versions_frame)
        version_buttons.grid(row=0, column=2, padx=(8, 0), sticky="n")
        self.edit_button = ttk.Button(version_buttons, text="Editar versão...", command=self.edit_selected)
        self.edit_button.grid(row=0, column=0, pady=(0, 4), sticky="ew")
        ttk.Button(version_buttons, text="Bancos...", command=self.choose_databases).grid(row=1, column=0, pady=(0, 4), sticky="ew")
        ttk.Button(version_buttons, text="Marcar todas", command=lambda: self._check_all(True)).grid(row=2, column=0, pady=(0, 4), sticky="ew")
        ttk.Button(version_buttons, text="Desmarcar todas", command=lambda: self._check_all(False)).grid(row=3, column=0, sticky="ew")

        results = ttk.Frame(frame)
        results.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        results.columnconfigure(0, weight=1)
        results.rowconfigure(1, weight=1)
        action_bar = ttk.Frame(results)
        action_bar.grid(row=0, column=0, sticky="ew")
        action_bar.columnconfigure(5, weight=1)
        self.test_button = ttk.Button(action_bar, text="Testar conexão", command=self.test_connection)
        self.test_button.grid(row=0, column=0, padx=(0, 6))
        self.analyze_button = ttk.Button(action_bar, text="Analisar", style="Primary.TButton", command=self.analyze)
        self.analyze_button.grid(row=0, column=1, padx=(0, 6))
        self.cancel_button = ttk.Button(action_bar, text="Cancelar", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=0, column=2, padx=(0, 6))
        self.export_button = ttk.Button(action_bar, text="Exportar...", command=self.export, state="disabled")
        self.export_button.grid(row=0, column=3, padx=(0, 6))
        self.fix_button = ttk.Button(action_bar, text="Script corretivo...", command=self.generate_fix_script, state="disabled")
        self.fix_button.grid(row=0, column=4, padx=(0, 12))
        colors = theme.COLORS
        ttk.Style(self.window).configure(
            "Audit.Horizontal.TProgressbar",
            troughcolor=colors["heading_bg"],
            background=colors["primary"],
            bordercolor=colors["border"],
            lightcolor=colors["primary"],
            darkcolor=colors["primary"],
        )
        self.progress = ttk.Progressbar(action_bar, mode="determinate", maximum=100, style="Audit.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=5, sticky="ew")

        self.notebook = ttk.Notebook(results)
        self.notebook.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self._build_objects_tab()
        self._build_files_tab()
        self._build_warnings_tab()
        self._build_history_tab()

        ttk.Label(frame, textvariable=self.status_var, wraplength=1100, justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )
        footer = ttk.Frame(frame)
        footer.grid(row=6, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(footer, text="Fechar", command=self.close).grid(row=0, column=0)

    def _font(self, name):
        fonts = getattr(self.app, "fonts", None)
        return fonts[name] if fonts and name in fonts else None

    def _table(self, parent, columns, headings, widths, stretch, height=6):
        holder = ttk.Frame(parent)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        table = ttk.Treeview(holder, columns=columns, show="headings", height=height, selectmode="browse")
        for column in columns:
            table.heading(column, text=headings[column], anchor="w")
            table.column(column, width=widths[column], anchor="w", stretch=column in stretch)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        table.tag_configure("bad", background=theme.COLORS["row_error"])
        table.tag_configure("warn", background=theme.COLORS["row_warning"])
        table.tag_configure("info", background=theme.COLORS["row_info"])
        return holder, table

    @staticmethod
    def _tag(status):
        if status in report.PROBLEM_STATUSES:
            return ("bad",)
        if status in ("INDETERMINADO", "NAO_SUPORTADO"):
            return ("warn",)
        if status == "SUBSTITUIDO":
            return ("info",)
        return ()

    def _build_objects_tab(self):
        tab = ttk.Frame(self.notebook, padding=8)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=3)
        tab.rowconfigure(3, weight=3)
        self.notebook.add(tab, text="Objetos")
        filters = ttk.Frame(tab)
        filters.grid(row=0, column=0, sticky="ew")
        ttk.Label(filters, text="Estado:").grid(row=0, column=0)
        states = [ALL, PROBLEMS] + [report.label(status) for status in PRECEDENCE]
        self.state_box = ttk.Combobox(filters, textvariable=self.state_filter, values=states, state="readonly", width=30)
        self.state_box.grid(row=0, column=1, padx=(6, 12))
        ttk.Label(filters, text="Versão:").grid(row=0, column=2)
        self.version_box = ttk.Combobox(filters, textvariable=self.version_filter, values=[ALL], state="readonly", width=16)
        self.version_box.grid(row=0, column=3, padx=(6, 12))
        ttk.Label(filters, text="Buscar:").grid(row=0, column=4)
        ttk.Entry(filters, textvariable=self.search_var, width=18).grid(row=0, column=5, padx=(6, 12))
        ttk.Label(filters, textvariable=self.count_var, style="Muted.TLabel").grid(row=0, column=6, sticky="w")
        for variable in (self.state_filter, self.version_filter, self.search_var):
            variable.trace_add("write", lambda *_args: self.apply_filters())
        holder, self.results_table = self._table(
            tab,
            ("state", "version", "kind", "object", "detail", "script"),
            {"state": "Estado", "version": "Versão", "kind": "Tipo", "object": "Objeto", "detail": "Detalhe", "script": "Script:linha"},
            {"state": 110, "version": 160, "kind": 70, "object": 250, "detail": 380, "script": 150},
            {"object", "detail"},
        )
        holder.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.results_table.bind("<<TreeviewSelect>>", lambda _event: self.show_detail())
        self.results_table.bind("<Up>", lambda _event: self.window.after_idle(self._see_selection))
        self.results_table.bind("<Down>", lambda _event: self.window.after_idle(self._see_selection))
        ttk.Label(tab, text="Detalhe (esperado × encontrado)", font=self._font("section")).grid(row=2, column=0, sticky="w", pady=(8, 2))
        self.detail = tk.Text(tab, height=9, wrap="word", state="disabled", relief="solid", borderwidth=1)
        self.detail.grid(row=3, column=0, sticky="nsew")

    def _build_files_tab(self):
        tab = ttk.Frame(self.notebook, padding=8)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.notebook.add(tab, text="Scripts")
        holder, self.files_table = self._table(
            tab,
            ("version", "script", "state", "counts"),
            {"version": "Versão", "script": "Script", "state": "Estado do arquivo", "counts": "Comandos"},
            {"version": 130, "script": 280, "state": 150, "counts": 460},
            {"counts"},
        )
        holder.grid(row=0, column=0, sticky="nsew")

    def _build_warnings_tab(self):
        tab = ttk.Frame(self.notebook, padding=8)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.notebook.add(tab, text="Avisos e fork")
        holder, self.warnings_table = self._table(
            tab, ("version", "text"), {"version": "Versão", "text": "Aviso"}, {"version": 130, "text": 900}, {"text"}
        )
        holder.grid(row=0, column=0, sticky="nsew")

    # ---- configuracao salva ----
    def _load_saved(self):
        saved = load_sqlaudit_config()
        server = load_sqlaudit_server()
        self.root_var.set(saved["root"])
        self.host_var.set(server["host"])
        self.port_var.set(str(server["port"]))
        self.tls_var.set(TLS_LABELS[server["tls"]])
        self.ca_var.set(server["ca_file"])
        self._saved_tree = saved["tree"]
        self._saved_databases = saved["databases"]
        self._saved_username = server["username"]
        self.refresh_history()
        if saved["root"]:
            self.window.after(50, self.discover)

    def _tls_mode(self):
        return TLS_VERIFY if self.tls_var.get() == TLS_LABELS[TLS_VERIFY] else TLS_ENCRYPT

    def _persist(self):
        save_sqlaudit_config(self.root_var.get(), self.tree.to_saved(), self.databases)
        port = self.port_var.get().strip()
        save_sqlaudit_server(self.host_var.get(), port, self._saved_username, self._tls_mode(), self.ca_var.get())

    # ---- ocupado / fila ----
    def _set_busy(self, busy):
        self.busy = busy
        idle = not busy
        for button in (self.choose_button, self.discover_button, self.test_button, self.analyze_button, self.edit_button, self.connect_button):
            button.config(state="normal" if idle else "disabled")
        self.export_button.config(state="normal" if idle and self.run is not None else "disabled")
        self.fix_button.config(state="normal" if idle and self._fixable() else "disabled")
        self.cancel_button.config(state="normal" if busy in ("analisando", "testando") else "disabled")
        if idle:
            self.progress.config(value=0)

    def _start(self, kind, target):
        self._set_busy(kind)

        def worker():
            try:
                self.queue.put(target())
            except Exception as error:  # a interface nunca fica esperando por uma thread que morreu
                self.queue.put(("error", f"Falha inesperada: {type(error).__name__}: {error}"))

        threading.Thread(target=worker, daemon=True).start()
        if not self._polling:
            self._polling = True
            self.window.after(POLL_MS, self._poll)

    def _poll(self):
        if not self.window.winfo_exists():
            self._polling = False
            return
        while True:
            try:
                message = self.queue.get_nowait()
            except queue.Empty:
                break
            self._handle(message)
        if self.busy or not self.queue.empty():
            self.window.after(POLL_MS, self._poll)
        else:
            self._polling = False

    def _handle(self, message):
        kind = message[0]
        if kind == "progress":
            self.status_var.set(message[1])
            self.progress.config(value=round(message[2] * 100))
        elif kind == "discovered":
            self._set_busy("")
            self._apply_discovery(message[1], message[2], message[3])
        elif kind == "tested":
            self._set_busy("")
            self.status_var.set(("✔ " if message[1] else "✖ ") + message[2])
        elif kind == "done":
            self._set_busy("")
            self._finish_run(message[1], message[2])
        elif kind == "info":
            self._set_busy("")
            self.status_var.set(message[1])
        elif kind == "databases":
            self._set_busy("")
            self._on_databases(message[1], message[2], message[3])
        elif kind == "shared_history":
            self._set_busy("")
            self._fill_shared_history(message[1], message[2])
        elif kind == "diff":
            self._set_busy("")
            self._fill_diff(message[1])
        elif kind == "exported":
            self._set_busy("")
            self.status_var.set(message[1])
        elif kind == "error":
            self._set_busy("")
            self.status_var.set("✖ " + message[1])
            messagebox.showerror(TITLE, message[1], parent=self.window)

    # ---- raiz e versoes ----
    def choose_root(self):
        chosen = filedialog.askdirectory(title="Raiz das versões", initialdir=self.root_var.get() or "C:\\", parent=self.window)
        if chosen:
            self.root_var.set(chosen)

    def choose_ca(self):
        chosen = filedialog.askopenfilename(title="Arquivo da CA", parent=self.window)
        if chosen:
            self.ca_var.set(chosen)

    def discover(self):
        root = self.root_var.get().strip()
        if not root:
            messagebox.showinfo(TITLE, "Informe a pasta raiz das versões (por exemplo, ...\\379.48.2.30).", parent=self.window)
            return
        self.status_var.set("Procurando pastas SCRIPTS...")

        def work():
            result = discover_versions(Path(root))
            forks, warning = {}, ""
            try:
                forks = shared_log.latest_forks(shared_log.read_events())
            except OSError as error:
                warning = f"Registro compartilhado de forks indisponível: {error}"
            return ("discovered", result, forks, warning)

        self._start("descobrindo", work)

    def _apply_discovery(self, result, forks=None, warning=""):
        folders = {}
        for item in result.versions:
            current = folders.get(item.version)
            if current is None or (current.discontinued and not item.discontinued):
                folders[item.version] = item
        self.folders = folders
        self.order = sorted(folders, key=version_sort_key)
        self.tree = VersionTree.build(self.order, self._saved_tree)
        self.databases = {v: self._saved_databases.get(v) or folders[v].database for v in self.order}
        self.checked = {v for v in self.order if not folders[v].discontinued or len(self.order) == 1}
        self.discovery_warnings = list(result.warnings)
        if warning:
            self.discovery_warnings.append(warning)
        self.discovery_warnings.extend(self._apply_shared_forks(forks or {}))
        self._refresh_versions()
        self._refresh_warnings()
        found = len(self.order)
        self.status_var.set(
            f"{found} versão(ões) encontrada(s)." if found else "Nenhuma versão encontrada. Escolha a raiz (com <versão>\\SCRIPTS), uma pasta de versão ou a própria pasta SCRIPTS."
        )
        if found:
            self._persist()

    def _apply_shared_forks(self, forks):
        """Fork registrado por outro operador vale quando este ainda nao informou o seu."""
        notes = []
        for version, shared in sorted(forks.items()):
            if version not in self.tree.nodes:
                continue
            origin = f"registrado por {shared.user} em {shared.timestamp} (base {shared.base or '-'}, fork {shared.fork})"
            local = self._saved_tree.get(version) or {}
            if local.get("fork") is None:
                if not shared.base or shared.base not in self.tree.nodes:
                    notes.append(f"Fork de {version} {origin}: base desconhecida nesta raiz; não aplicado.")
                    continue
                try:
                    self.tree.set_base(version, shared.base, shared.fork)
                    notes.append(f"Fork de {version} {origin}: aplicado.")
                except TreeError as error:
                    notes.append(f"Fork de {version} {origin}: não aplicado ({error}).")
            elif (local.get("base") or "") != shared.base or local.get("fork") != shared.fork:
                notes.append(
                    f"Fork local de {version} (base {local.get('base') or '-'}, fork {local.get('fork')}) difere do {origin}; mantido o local."
                )
        return notes

    def _version_state(self, version):
        if self.run is not None:
            for audit in self.run.versions:
                if audit.version == version:
                    if audit.error:
                        return "Erro"
                    if audit.comparison is not None:
                        return report.label(worst(item.status for item in audit.comparison.objects))
        if self._database_missing(version):
            return "Banco não encontrado"
        return "Descontinuada" if self.folders[version].discontinued else ""

    def _refresh_versions(self):
        self.versions_table.delete(*self.versions_table.get_children())
        for version in self.order:
            node = self.tree.nodes[version]
            self.versions_table.insert(
                "",
                "end",
                iid=version,
                values=(
                    CHECKED if version in self.checked else UNCHECKED,
                    version,
                    node.base or "—",
                    "—" if node.fork is None else node.fork,
                    self.databases.get(version, ""),
                    self._version_state(version),
                ),
            )

    def _on_versions_click(self, event):
        if self.versions_table.identify_region(event.x, event.y) != "cell" or self.versions_table.identify_column(event.x) != "#1":
            return None
        version = self.versions_table.identify_row(event.y)
        if version and not self.busy:
            self.checked.symmetric_difference_update({version})
            self._refresh_versions()
        return "break"

    def _check_all(self, checked):
        self.checked = set(self.order) if checked else set()
        self._refresh_versions()

    def edit_selected(self):
        selection = self.versions_table.selection()
        if not selection or self.busy:
            if not selection:
                messagebox.showinfo(TITLE, "Selecione uma versão na lista.", parent=self.window)
            return
        self._edit_version(selection[0])

    def _edit_version(self, version):
        node = self.tree.nodes[version]
        dialog = tk.Toplevel(self.window)
        dialog.title(f"Versão {version}")
        theme.size_window(dialog, 560, 330)
        dialog.resizable(False, False)
        dialog.transient(self.window)
        box = ttk.Frame(dialog, padding=14)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        database_var = tk.StringVar(value=self.databases.get(version, ""))
        base_var = tk.StringVar(value=node.base or NO_BASE)
        fork_var = tk.StringVar(value="" if node.fork is None else str(node.fork))
        ttk.Label(box, text="Banco:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(box, textvariable=database_var, values=self.server_databases).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(box, text="Versão-base:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        others = [NO_BASE] + [v for v in self.order if v != version]
        ttk.Combobox(box, textvariable=base_var, values=others, state="readonly").grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Label(box, text="Fork:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(box, textvariable=fork_var, width=12).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Label(
            box,
            text="Fork = maior SCRIPT_<n> da base que já estava no banco quando esta versão foi criada. "
            "Vazio = não informado: o que vem da base fica como incerto.",
            style="Muted.TLabel",
            wraplength=520,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        hint_text = self._hint_text(version)
        if hint_text:
            ttk.Label(box, text=hint_text, wraplength=520, justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def confirm(register=False):
            fork_text = fork_var.get().strip()
            if fork_text and not fork_text.isdigit():
                messagebox.showwarning(TITLE, "O fork deve ser um número inteiro (ou ficar vazio).", parent=dialog)
                return
            base = None if base_var.get() == NO_BASE else base_var.get()
            if register and (base is None or not fork_text):
                messagebox.showwarning(TITLE, "Para registrar para todos, informe a versão-base e o fork.", parent=dialog)
                return
            try:
                self.tree.set_base(version, base, int(fork_text) if fork_text else None)
            except TreeError as error:
                messagebox.showwarning(TITLE, str(error), parent=dialog)
                return
            self.databases[version] = database_var.get().strip()
            self._persist()
            self._refresh_versions()
            dialog.destroy()
            if register:
                self._register_fork(version)

        buttons = ttk.Frame(box)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Salvar", style="Primary.TButton", command=confirm).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Salvar e registrar para todos", command=lambda: confirm(register=True)).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).grid(row=0, column=2)
        dialog.grab_set()
        self.window.wait_window(dialog)

    def _register_fork(self, version):
        node = self.tree.nodes[version]
        if not messagebox.askyesno(
            TITLE,
            f"Registrar para todos os operadores que a versão {version} nasceu de {node.base} "
            f"até SCRIPT_{node.fork}?\n\nO registro fica na pasta de controle e não é apagado.",
            parent=self.window,
        ):
            return
        event = shared_log.fork_event(audit_runner.os_user(), version, node.base, node.fork, self.databases.get(version, ""))

        def work():
            try:
                shared_log.append_events([event])
            except OSError as error:
                return ("info", f"✖ Não foi possível registrar na pasta de controle: {error}")
            return ("info", f"✔ Fork de {version} registrado para todos os operadores.")

        self.status_var.set("Registrando...")
        self._start("registrando", work)

    def _hint_text(self, version):
        if self.run is None:
            return ""
        for audit in self.run.versions:
            hint = audit.fork_hint
            if audit.version == version and hint is not None:
                return "Sugestão da última análise: " + self._hint_summary(hint)
        return ""

    @staticmethod
    def _hint_summary(hint):
        if hint.candidate is None and hint.first_missing is None:
            return " ".join(hint.notes) or "sem evidência suficiente."
        parts = []
        if hint.candidate is not None:
            parts.append(f"fork provável {hint.candidate}")
        if hint.upper is not None and hint.upper != hint.candidate:
            parts.append(f"até {hint.upper}")
        if hint.first_missing is not None:
            parts.append(f"primeiro script ausente no banco: {hint.first_missing}")
        return ", ".join(parts) + ". Confirme antes de usar."

    # ---- bancos do servidor ----
    def connect_server(self):
        """Pede usuario e senha, conecta so ao servidor e lista os bancos que o usuario enxerga."""
        if self.busy:
            return
        server = self._server_or_none()
        if server is None:
            return
        self._persist()
        self.status_var.set(f"Conectando em {server.host}:{server.port}...")
        self._start("conectando", lambda: ("databases", *audit_runner.list_databases(server)))

    def _on_databases(self, ok, names, text):
        if not ok:
            self.status_var.set("✖ " + text)
            return
        self.server_databases = names
        self.status_var.set("✔ " + text)
        self._match_databases()
        self._refresh_versions()
        self._refresh_warnings()
        if self.order:
            self.choose_databases()
        else:
            messagebox.showinfo(
                TITLE,
                f"{len(names)} banco(s) visível(is) para este usuário. Descubra as versões para escolher o banco de cada uma.",
                parent=self.window,
            )

    def _database_missing(self, version):
        wanted = (self.databases.get(version) or "").strip().lower()
        return bool(self.server_databases) and wanted not in {name.lower() for name in self.server_databases}

    def _match_databases(self):
        """Casa o banco de cada versao com a lista do servidor (ignora maiusculas) e anota o que nao existe."""
        by_lower = {name.lower(): name for name in self.server_databases}
        self.database_notes = []
        for version in self.order:
            wanted = (self.databases.get(version) or "").strip()
            found = by_lower.get(wanted.lower())
            if found:
                self.databases[version] = found
            else:
                self.database_notes.append(
                    (version, f"Banco '{wanted or '(vazio)'}' não existe no servidor: escolha em Bancos...")
                )
        if self.order:
            self._persist()

    def choose_databases(self):
        if not self.server_databases:
            messagebox.showinfo(
                TITLE, "Conecte ao servidor primeiro: informe servidor e porta e clique em Conectar e listar bancos.", parent=self.window
            )
            return
        if not self.order:
            messagebox.showinfo(TITLE, "Descubra as versões antes de escolher o banco de cada uma.", parent=self.window)
            return
        names = list(self.server_databases)
        dialog = tk.Toplevel(self.window)
        dialog.title("Bancos do servidor")
        theme.size_window(dialog, 800, min(150 + 38 * len(self.order), 560))
        dialog.transient(self.window)
        box = ttk.Frame(dialog, padding=14)
        box.pack(fill="both", expand=True)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(2, weight=1)
        ttk.Label(
            box,
            text=f"{len(names)} banco(s) visível(is) para este usuário em {self.host_var.get().strip()}:{self.port_var.get().strip()}. "
            "Escolha o banco de cada versão (dá para digitar para procurar).",
            wraplength=760,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        filter_var = tk.StringVar()
        bar = ttk.Frame(box)
        bar.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        ttk.Label(bar, text="Filtrar a lista:").grid(row=0, column=0)
        ttk.Entry(bar, textvariable=filter_var, width=30).grid(row=0, column=1, padx=(6, 0))

        canvas = tk.Canvas(box, highlightthickness=0, background=theme.COLORS["bg"])
        scroll = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=2, column=0, sticky="nsew")
        scroll.grid(row=2, column=1, sticky="ns")
        rows = ttk.Frame(canvas)
        rows_window = canvas.create_window((0, 0), window=rows, anchor="nw")
        rows.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(rows_window, width=event.width))  # a linha acompanha a largura
        rows.columnconfigure(1, weight=1)

        by_lower = {name.lower(): name for name in names}
        variables, boxes = {}, []
        for position, version in enumerate(self.order):
            current = (self.databases.get(version) or "").strip()
            variable = tk.StringVar(value=by_lower.get(current.lower(), current))
            indicator = tk.StringVar()

            def refresh(*_args, _variable=variable, _indicator=indicator):
                found = _variable.get().strip().lower() in by_lower
                _indicator.set("✔ existe no servidor" if found else "✖ não existe no servidor")

            variable.trace_add("write", refresh)
            refresh()
            ttk.Label(rows, text=version).grid(row=position, column=0, sticky="w", pady=3, padx=(0, 10))
            combo = ttk.Combobox(rows, textvariable=variable, values=names, width=40)
            combo.grid(row=position, column=1, sticky="ew", pady=3)
            ttk.Label(rows, textvariable=indicator, style="Muted.TLabel").grid(row=position, column=2, sticky="w", padx=(10, 0))
            variables[version] = variable
            boxes.append(combo)

        def narrow(*_args):
            needle = filter_var.get().strip().lower()
            shown = [name for name in names if needle in name.lower()]
            for combo in boxes:
                combo.configure(values=shown)

        filter_var.trace_add("write", narrow)

        def apply():
            for version, variable in variables.items():
                chosen = variable.get().strip()
                if chosen:
                    self.databases[version] = chosen
            self._match_databases()
            self._persist()
            self._refresh_versions()
            self._refresh_warnings()
            missing = len(self.database_notes)
            self.status_var.set(
                f"Bancos aplicados: {len(self.order) - missing} versão(ões) com banco encontrado"
                + (f", {missing} sem banco no servidor." if missing else ".")
            )
            dialog.destroy()

        buttons = ttk.Frame(box)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Aplicar", style="Primary.TButton", command=apply).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).grid(row=0, column=1)
        dialog.grab_set()
        self.window.wait_window(dialog)

    # ---- conexao e analise ----
    def _server_or_none(self):
        host = self.host_var.get().strip()
        port = self.port_var.get().strip()
        if not host or not port.isdigit() or not (0 < int(port) < 65536):
            messagebox.showwarning(TITLE, "Informe o servidor e uma porta válida.", parent=self.window)
            return None
        if self._tls_mode() == TLS_VERIFY and not self.ca_var.get().strip():
            messagebox.showwarning(TITLE, "Para verificar o certificado, informe o arquivo da CA.", parent=self.window)
            return None
        credentials = ask_credentials(
            self.window,
            self._saved_username,
            "Usuário do MySQL para a auditoria (somente leitura).",
            title="Auditoria SQL - login",
        )
        if credentials is None:
            return None
        self._saved_username = credentials[0]
        return audit_runner.ServerConnection(host, int(port), credentials[0], credentials[1], self._tls_mode(), self.ca_var.get().strip())

    def _selected_versions(self):
        return [v for v in self.order if v in self.checked]

    def test_connection(self):
        selection = self.versions_table.selection()
        version = selection[0] if selection else (self._selected_versions() or [None])[0]
        database = self.databases.get(version, "") if version else ""
        if not database:
            messagebox.showinfo(TITLE, "Selecione uma versão com o banco informado para testar.", parent=self.window)
            return
        server = self._server_or_none()
        if server is None:
            return
        self._persist()
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self.status_var.set(f"Testando conexão com {database}...")
        self._start(
            "testando",
            lambda: ("tested", *audit_runner.test_connection(server, database, cancel_event=cancel)),
        )

    def analyze(self):
        selected = self._selected_versions()
        if not selected:
            messagebox.showinfo(TITLE, "Marque ao menos uma versão para auditar.", parent=self.window)
            return
        missing = [v for v in selected if not self.databases.get(v)]
        if missing:
            messagebox.showwarning(TITLE, "Informe o banco de: " + ", ".join(missing), parent=self.window)
            return
        unknown = [v for v in selected if self._database_missing(v)]
        if unknown and not messagebox.askyesno(
            TITLE,
            "Estes bancos não estão na lista carregada do servidor:\n\n"
            + "\n".join(f"  {v}: {self.databases[v]}" for v in unknown)
            + "\n\nSe o banco realmente não existir, a análise dessas versões falha. Continuar mesmo assim?",
            parent=self.window,
        ):
            return
        server = self._server_or_none()
        if server is None:
            return
        self._persist()
        request = audit_runner.AuditRequest(
            root=Path(self.root_var.get().strip()),
            versions=[audit_runner.VersionRequest(v, self.databases[v]) for v in selected],
            folders=dict(self.folders),
            tree=self.tree,
            server=server,
        )
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self.status_var.set("Iniciando a análise...")
        self.progress.config(value=0)
        self._start(
            "analisando",
            lambda: self._run_and_record(request, cancel),
        )

    def _run_and_record(self, request, cancel):
        """Na thread: analisa, grava o historico local e o resumo compartilhado (falhas nao perdem o resultado)."""
        run = audit_runner.run_audit(
            request,
            progress=lambda text, fraction: self.queue.put(("progress", text, fraction)),
            cancel_event=cancel,
        )
        info = {"run_id": None, "warnings": []}
        try:
            info["run_id"] = self.store.save_run(run)
        except Exception as error:  # historico e acessorio: nunca derruba o resultado
            info["warnings"].append(f"Histórico local não gravado: {error}")
        if not run.cancelled:
            try:
                shared_log.append_events([shared_log.audit_event(run.user, audit) for audit in run.versions])
            except OSError as error:
                info["warnings"].append(f"Registro compartilhado indisponível: {error}")
        return ("done", run, info)

    def cancel(self):
        if self.cancel_event is not None and self.busy in ("analisando", "testando"):
            self.cancel_event.set()
            self.cancel_button.config(state="disabled")
            self.status_var.set("Cancelando... aguardando o fim da etapa atual.")

    def _finish_run(self, run, info=None):
        info = info or {}
        self.viewing = None
        self.run = run
        self.rows = report.object_rows(run)
        write_log(report.audit_line(run, "cancelada" if run.cancelled else "concluida"))
        self.version_box.config(values=[ALL] + [audit.version for audit in run.versions])
        self.state_filter.set(ALL)
        self.version_filter.set(ALL)
        self.search_var.set("")
        self.apply_filters()
        self._refresh_files()
        self._refresh_warnings()
        self._refresh_versions()
        self._set_busy("")
        extra = f" Histórico #{info['run_id']}." if info.get("run_id") else ""
        extra += "".join(" ⚠ " + warning for warning in info.get("warnings", []))
        self.status_var.set(report.summary_text(run) + ". Compatível não comprova que o script foi executado." + extra)
        self.refresh_history()

    # ---- resultados ----
    def apply_filters(self):
        table = self.results_table
        table.delete(*table.get_children())
        self.shown = {}
        state = self.state_filter.get()
        version = self.version_filter.get()
        needle = self.search_var.get().strip().lower()
        by_label = {report.label(status): status for status in PRECEDENCE}
        matching = []
        for index, row in enumerate(self.rows):
            if state == PROBLEMS and row["estado"] not in report.PROBLEM_STATUSES:
                continue
            if state in by_label and row["estado"] != by_label[state]:
                continue
            if version != ALL and row["versao"] != version:
                continue
            if needle and needle not in " ".join(str(row[k]) for k in ("tabela", "coluna", "script", "diferencas", "notas")).lower():
                continue
            matching.append(index)
        for index in matching[:MAX_ROWS_SHOWN]:
            row = self.rows[index]
            obj = row["tabela"] + (f".{row['coluna']}" if row["coluna"] else "")
            origin = f"{row['script']}:{row['linha']}" if row["script"] else ""
            table.insert(
                "",
                "end",
                iid=str(index),
                values=(report.label(row["estado"]), row["versao"], row["tipo"], obj, row["diferencas"] or row["notas"], origin),
                tags=self._tag(row["estado"]),
            )
            self.shown[str(index)] = row
        total = len(self.rows)
        self.count_var.set(
            f"{min(len(matching), MAX_ROWS_SHOWN)} de {len(matching)}"
            + (f" (total {total})" if len(matching) != total else "")
            + ("; refine os filtros ou exporte" if len(matching) > MAX_ROWS_SHOWN else "")
        )
        self._set_detail("")

    def _see_selection(self):
        selection = self.results_table.selection()
        if selection:
            self.results_table.see(selection[0])

    def show_detail(self):
        selection = self.results_table.selection()
        row = self.shown.get(selection[0]) if selection else None
        if row is None:
            self._set_detail("")
            return
        lines = [
            f"Estado: {report.label(row['estado'])}",
            f"Objeto: {row['tipo']} {row['tabela']}" + (f".{row['coluna']}" if row["coluna"] else ""),
            f"Versão / banco: {row['versao']} / {row['banco']}",
        ]
        if row["script"]:
            certain = "origem certa" if row["origem_certa"] == "sim" else "origem INCERTA (fork não informado ou sem ordem)"
            lines.append(f"Definido em: {row['script']}, linha {row['linha']} ({certain})")
        if row["diferencas"]:
            lines.append("Diferenças (esperado × encontrado):")
            lines.extend("  • " + part for part in row["diferencas"].split("; "))
        if row["notas"]:
            lines.append("Observações:")
            lines.extend("  • " + part for part in row["notas"].split("; "))
        if row["estado"] == "COMPATIVEL":
            lines.append("Compatível com os scripts; isso não comprova que algum script foi executado.")
        self._set_detail("\n".join(lines))

    def _set_detail(self, text):
        self.detail.config(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.config(state="disabled")

    def _refresh_files(self):
        table = self.files_table
        table.delete(*table.get_children())
        for audit in (self.run.versions if self.run else []):
            if audit.comparison is None:
                continue
            for item in audit.comparison.files():
                counts = ", ".join(f"{count} {report.label(status).lower()}" for status, count in sorted(item.counts.items()))
                state = report.label(item.status) + (" (parcial)" if item.partial else "")
                table.insert("", "end", values=(audit.version, item.script, state, counts), tags=self._tag(item.status))

    def _refresh_warnings(self):
        table = self.warnings_table
        table.delete(*table.get_children())
        for text in self.discovery_warnings:
            table.insert("", "end", values=("—", text), tags=("warn",))
        for version, text in self.database_notes:
            table.insert("", "end", values=(version, text), tags=("warn",))
        for audit in (self.run.versions if self.run else []):
            if audit.error:
                table.insert("", "end", values=(audit.version, "Erro: " + audit.error), tags=("bad",))
            for text in audit.warnings + (audit.comparison.warnings if audit.comparison else []):
                table.insert("", "end", values=(audit.version, text), tags=("warn",))
            if audit.excluded:
                table.insert(
                    "", "end",
                    values=(audit.version, f"{len(audit.excluded)} script(s) da base além do fork não foram aplicados ao modelo."),
                    tags=("info",),
                )
            if audit.fork_hint is not None:
                table.insert("", "end", values=(audit.version, "Fork: " + self._hint_summary(audit.fork_hint)), tags=("info",))
                for note in audit.fork_hint.notes:
                    table.insert("", "end", values=(audit.version, note), tags=("info",))

    # ---- historico ----
    def _build_history_tab(self):
        tab = ttk.Frame(self.notebook, padding=8)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=2)
        tab.rowconfigure(4, weight=3)
        self.notebook.add(tab, text="Histórico")
        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(bar, text="Origem:").grid(row=0, column=0)
        origin_box = ttk.Combobox(
            bar, textvariable=self.origin_var, values=[ORIGIN_LOCAL, ORIGIN_SHARED], state="readonly", width=22
        )
        origin_box.grid(row=0, column=1, padx=(6, 12))
        origin_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_history())
        ttk.Button(bar, text="Atualizar", command=self.refresh_history).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(bar, text="Ver resultados", command=self.view_selected_run).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(bar, text="Comparar selecionadas", command=self.compare_selected).grid(row=0, column=4, padx=(0, 6))
        ttk.Button(bar, text="Excluir", command=self.delete_selected).grid(row=0, column=5)
        holder, self.history_table = self._table(
            tab,
            ("date", "user", "versions", "summary"),
            {"date": "Data", "user": "Operador", "versions": "Versões", "summary": "Resumo"},
            {"date": 130, "user": 110, "versions": 300, "summary": 560},
            {"summary"},
            height=5,
        )
        self.history_table.configure(selectmode="extended")
        holder.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        ttk.Label(
            tab,
            text="Selecione duas auditorias para compará-las (ou uma, para comparar com a anterior).",
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(tab, text="Diferenças entre as auditorias", font=self._font("section")).grid(row=3, column=0, sticky="w", pady=(6, 2))
        holder, self.diff_table = self._table(
            tab,
            ("change", "version", "object", "before", "after", "detail"),
            {"change": "Mudança", "version": "Versão", "object": "Objeto / script", "before": "Antes", "after": "Depois", "detail": "Detalhe"},
            {"change": 130, "version": 120, "object": 270, "before": 110, "after": 110, "detail": 360},
            {"object", "detail"},
            height=5,
        )
        holder.grid(row=4, column=0, sticky="nsew")

    @staticmethod
    def _date_text(text):
        try:
            return datetime.fromisoformat(text).strftime("%d/%m/%Y %H:%M")
        except (TypeError, ValueError):
            return str(text)

    def refresh_history(self):
        table = self.history_table
        if self.origin_var.get() == ORIGIN_SHARED:
            if self.busy:
                return

            def work():
                try:
                    events = [e for e in shared_log.read_events() if e.event == shared_log.EVENT_AUDIT]
                except OSError as error:
                    return ("info", f"✖ Registro compartilhado indisponível: {error}")
                return ("shared_history", list(reversed(events))[:300], "")

            self.status_var.set("Lendo o registro compartilhado...")
            self._start("historico", work)
            return
        table.delete(*table.get_children())
        try:
            runs = self.store.list_runs()
        except Exception as error:
            self.history_runs = {}
            self.status_var.set(f"✖ Histórico local indisponível: {error}")
            return
        self.history_runs = {str(run.id): run for run in runs}
        for run in runs:
            table.insert("", "end", iid=str(run.id), values=(self._date_text(run.started), run.user, ", ".join(run.versions), run.summary))

    def _fill_shared_history(self, events, _unused=""):
        table = self.history_table
        table.delete(*table.get_children())
        self.history_runs = {}
        for index, event in enumerate(events):
            table.insert("", "end", iid=f"s{index}", values=(event.timestamp, event.user, event.version, event.detail))
        self.status_var.set(f"{len(events)} registro(s) compartilhado(s) de auditoria (os mais recentes primeiro).")

    def _selected_runs(self):
        if self.origin_var.get() != ORIGIN_LOCAL:
            messagebox.showinfo(TITLE, "Esta ação vale só para o histórico de Meus registros.", parent=self.window)
            return None
        selected = [self.history_runs[iid] for iid in self.history_table.selection() if iid in self.history_runs]
        if not selected:
            messagebox.showinfo(TITLE, "Selecione uma auditoria na lista.", parent=self.window)
            return None
        return selected

    def view_selected_run(self):
        selected = self._selected_runs()
        if selected is None:
            return
        run = selected[0]
        try:
            rows = self.store.load_objects(run.id)
        except Exception as error:
            self.status_var.set(f"✖ Não foi possível ler o histórico: {error}")
            return
        self.viewing = run
        self.rows = rows
        self.version_box.config(values=[ALL] + list(run.versions))
        self.state_filter.set(ALL)
        self.version_filter.set(ALL)
        self.search_var.set("")
        self.apply_filters()
        self.notebook.select(0)
        self.status_var.set(
            f"Exibindo a auditoria #{run.id} de {self._date_text(run.started)} (histórico): {run.summary}. "
            "As abas Scripts e Avisos continuam mostrando a última análise."
        )

    def compare_selected(self):
        if self.busy:
            return
        selected = self._selected_runs()
        if selected is None:
            return
        ids = sorted(run.id for run in selected)
        if len(ids) == 2:
            old_id, new_id = ids
        elif len(ids) == 1:
            earlier = [run.id for run in self.history_runs.values() if run.id < ids[0]]
            if not earlier:
                messagebox.showinfo(TITLE, "Não há auditoria anterior a esta para comparar.", parent=self.window)
                return
            old_id, new_id = max(earlier), ids[0]
        else:
            messagebox.showinfo(TITLE, "Selecione no máximo duas auditorias.", parent=self.window)
            return
        self.status_var.set("Comparando auditorias...")
        self._start("comparando", lambda: ("diff", self.store.compare(old_id, new_id)))

    def _fill_diff(self, diff):
        table = self.diff_table
        table.delete(*table.get_children())
        short = lambda sha: sha[:8] if sha else "—"
        for change in diff.changes:
            tag = ("bad",) if change.kind == "piorou" or (change.kind == "novo" and change.new_status in report.PROBLEM_STATUSES) else (
                ("info",) if change.kind == "melhorou" else ("warn",)
            )
            table.insert(
                "", "end",
                values=(
                    CHANGE_LABELS[change.kind], change.version, change.label,
                    report.label(change.old_status) if change.old_status else "—",
                    report.label(change.new_status) if change.new_status else "—",
                    change.new_detail or change.old_detail,
                ),
                tags=tag,
            )
        for change in diff.script_changes:
            table.insert(
                "", "end",
                values=(CHANGE_LABELS[change.kind], change.version, change.script, short(change.old_sha256), short(change.new_sha256), "SHA-256 (início)"),
                tags=("warn",) if change.kind == "alterado" else (),
            )
        for note in diff.notes:
            table.insert("", "end", values=("Observação", "—", note, "", "", ""), tags=("warn",))
        counts = diff.counts()
        summary = ", ".join(f"{count} {CHANGE_LABELS[kind].lower()}" for kind, count in sorted(counts.items())) or "nenhuma diferença de objetos"
        scripts = f"; {len(diff.script_changes)} script(s) com mudança" if diff.script_changes else ""
        self.status_var.set(
            f"#{diff.old.id} ({self._date_text(diff.old.started)}) → #{diff.new.id} ({self._date_text(diff.new.started)}): {summary}{scripts}."
        )

    def delete_selected(self):
        selected = self._selected_runs()
        if selected is None:
            return
        if not messagebox.askyesno(TITLE, f"Excluir {len(selected)} auditoria(s) do seu histórico local?", parent=self.window):
            return
        try:
            for run in selected:
                self.store.delete_run(run.id)
        except Exception as error:
            self.status_var.set(f"✖ Não foi possível excluir: {error}")
            return
        self.refresh_history()
        self.status_var.set(f"{len(selected)} auditoria(s) excluída(s) do histórico local.")

    # ---- script corretivo (so para revisao: nada e executado) ----
    def _fixable(self):
        return self.run is not None and any(a.model is not None and a.comparison is not None for a in self.run.versions)

    def generate_fix_script(self):
        if not self._fixable():
            messagebox.showinfo(TITLE, "Faça uma análise antes de gerar o script corretivo.", parent=self.window)
            return
        if self.viewing is not None:
            messagebox.showinfo(
                TITLE, "A aba Objetos mostra uma auditoria do histórico. Faça uma nova análise para gerar o script.", parent=self.window
            )
            return
        name = f"corretivo-sql-{datetime.now():%Y%m%d-%H%M}.sql"
        path = filedialog.asksaveasfilename(
            title="Salvar script corretivo (somente para revisão)", defaultextension=".sql", initialfile=name,
            filetypes=FIX_TYPES, parent=self.window,
        )
        if not path:
            return
        run = self.run

        def work():
            audited = [a for a in run.versions if a.model is not None and a.comparison is not None]
            plans = [fix_suggestions.build_fix_plan(a.model, a.comparison, a.version, a.database) for a in audited]
            try:
                fix_suggestions.write_script(plans, path, user=run.user)
            except (OSError, ValueError) as error:
                return ("info", f"✖ Não foi possível gravar o script: {error}")
            commands = sum(len(plan.items) for plan in plans)
            commented = sum(plan.commented_count for plan in plans)
            untouched = sum(sum(plan.not_handled.values()) + len(plan.skipped) for plan in plans)
            write_log(report.audit_line(run, "script_corretivo", f"{commands} comando(s) para revisao, {commented} comentado(s)"))
            skipped_versions = len(run.versions) - len(audited)
            return (
                "info",
                f"✔ Script corretivo gravado em {path}: {commands} comando(s), {commented} comentado(s) para revisão, "
                f"{untouched} achado(s) sem correção automática"
                + (f", {skipped_versions} versão(ões) sem resultado fora do arquivo" if skipped_versions else "")
                + ". REVISE e faça backup antes de usar; o sistema não executa nada.",
            )

        self.status_var.set("Gerando o script corretivo...")
        self._start("gerando", work)

    # ---- exportar / fechar ----
    def export(self):
        if self.run is None:
            messagebox.showinfo(TITLE, "Faça uma análise antes de exportar.", parent=self.window)
            return
        if self.viewing is not None:
            messagebox.showinfo(TITLE, "A aba Objetos mostra uma auditoria do histórico. Faça uma nova análise para exportar.", parent=self.window)
            return
        name = f"auditoria-sql-{datetime.now():%Y%m%d-%H%M}.html"
        path = filedialog.asksaveasfilename(
            title="Exportar relatório", defaultextension=".html", initialfile=name, filetypes=EXPORT_TYPES, parent=self.window
        )
        if not path:
            return
        run = self.run

        def write():
            try:
                kind = report.write_report(run, path)
            except (OSError, ValueError) as error:
                return ("exported", f"✖ Não foi possível exportar: {error}")
            return ("exported", f"✔ Relatório {kind.upper()} gravado em {path}")

        self.status_var.set("Exportando...")
        self._start("exportando", write)

    def close(self):
        if self.busy == "analisando":
            if not messagebox.askyesno(TITLE, "A análise está em andamento. Cancelar e fechar?", parent=self.window):
                return
        if self.cancel_event is not None:
            self.cancel_event.set()
        self.window.destroy()
