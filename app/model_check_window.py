"""Janela `Scripts Banco Modelo > Comparar com o banco`: so desenho; a logica fica em `sqlaudit/model_check`.

Passos: 1) escolher a pasta de scripts; 2) informar servidor e porta, conectar e escolher o
banco modelo na lista; 3) comparar. Mostra, para cada script, `OK` (tudo o que ele cria ja
esta no banco) ou `Falta rodar: campo T.C...`. Somente leitura. Usuario e senha sao pedidos a
cada conexao/comparacao e nunca gravados. Rede e arquivos rodam em thread + fila; o Tkinter
so e tocado pela thread principal.
"""

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace

from app import theme
from app.logging_utils import write_log
from app import saved_login, secret_store
from app.model_db_dialogs import ask_credentials
from app.settings import load_model_check_directory, load_model_db, save_model_check_directory, save_model_db
from app.sqlaudit import model_check, model_fix
from app.sqlaudit.model_check import (
    STATUS_DUPLICATE,
    STATUS_ERROR,
    STATUS_LABELS,
    STATUS_MISSING,
    STATUS_NO_STRUCTURE,
    STATUS_OK,
    ModelCheckError,
)

TITLE = "Comparar com o banco modelo"
ALL = "Todos"
MAX_ROWS = 5000
LOGIN_MESSAGE = "Somente leitura: o sistema só consulta a estrutura do banco. Usuário e senha não são gravados."


_natural = model_check.natural_key


def open_model_check_window(app, parent, directory=None, user=""):
    existing = getattr(app, "model_check_window", None)
    if existing is not None and existing.window.winfo_exists():
        existing.window.focus()
        existing.window.lift()
        return existing
    window = ModelCheckWindow(app, parent, directory, user)
    app.model_check_window = window
    return window


class ModelCheckWindow:
    def __init__(self, app, parent, directory=None, user=""):
        self.app = app
        self.user = user
        self.window = tk.Toplevel(parent)
        self.window.title(TITLE)
        theme.size_window(self.window, 1100, 760)
        self.window.minsize(860, 560)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.queue = queue.Queue()
        self.cancel_event = None
        self.busy = ""
        self.run = None
        self.shown = {}
        self.server_databases = []  # bancos que o usuario enxerga (so em memoria)
        self._login = None  # ultimo login usado (so em memoria)

        saved = load_model_db()
        self.dir_var = tk.StringVar(value=str(directory or load_model_check_directory()))
        self.host_var = tk.StringVar(value=saved["host"])
        self.port_var = tk.StringVar(value=str(saved["port"]))
        self.db_var = tk.StringVar(value=saved["database"])
        self.status_var = tk.StringVar(
            value="Escolha a pasta de scripts, conecte ao servidor, escolha o banco modelo e clique em Comparar."
        )
        self.filter_var = tk.StringVar(value=ALL)
        self.search_var = tk.StringVar()
        self.count_var = tk.StringVar()
        self._build()
        self.window.after(120, self._poll)

    @property
    def directory(self):
        return Path(self.dir_var.get().strip() or ".")

    # ---- construcao ----
    def _build(self):
        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(5, weight=4)
        frame.rowconfigure(7, weight=1)
        section = getattr(self.app, "fonts", {}).get("section")
        colors = theme.COLORS

        # cabecalho: a configuracao recolhe depois da comparacao
        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        self.setup_button = ttk.Button(header, text="▾ Configuração", command=self.toggle_setup)
        self.setup_button.grid(row=0, column=0)
        self.setup_summary = ttk.Label(header, text="", style="Muted.TLabel")
        self.setup_summary.grid(row=0, column=1, sticky="w", padx=(10, 0))

        # configuracao: pasta, servidor e banco modelo, alinhados em uma grade so
        self.setup = ttk.Frame(frame)
        self.setup.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.setup.columnconfigure(1, weight=1)
        ttk.Label(self.setup, text="Pasta dos scripts", font=section).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.dir_entry = ttk.Entry(self.setup, textvariable=self.dir_var)
        self.dir_entry.grid(row=0, column=1, columnspan=4, sticky="ew", padx=(12, 8), pady=(0, 6))
        self.choose_button = ttk.Button(self.setup, text="Escolher...", command=self.choose_directory)
        self.choose_button.grid(row=0, column=5, pady=(0, 6))

        ttk.Label(self.setup, text="Servidor MySQL", font=section).grid(row=1, column=0, sticky="w", pady=(0, 6))
        server = ttk.Frame(self.setup)
        server.grid(row=1, column=1, columnspan=5, sticky="w", padx=(12, 0), pady=(0, 6))
        self.host_entry = ttk.Entry(server, textvariable=self.host_var, width=20)
        self.host_entry.grid(row=0, column=0)
        ttk.Label(server, text=":").grid(row=0, column=1, padx=4)
        self.port_entry = ttk.Entry(server, textvariable=self.port_var, width=7)
        self.port_entry.grid(row=0, column=2)
        for entry in (self.host_entry, self.port_entry):
            entry.bind("<Return>", lambda _event: self.connect_server())
        self.connect_button = ttk.Button(server, text="Conectar e listar bancos...", command=self.connect_server)
        self.connect_button.grid(row=0, column=3, padx=(12, 0))
        self.forget_button = ttk.Button(server, text="Esquecer senha salva", command=self.forget_saved, state="disabled")
        self.forget_button.grid(row=0, column=4, padx=(8, 0))
        if not secret_store.default_store().available:
            self.forget_button.grid_remove()  # sem armazenamento seguro neste computador: nada para esquecer

        ttk.Label(self.setup, text="Banco modelo", font=section).grid(row=2, column=0, sticky="w")
        self.db_box = ttk.Combobox(self.setup, textvariable=self.db_var, values=[], width=52)
        self.db_box.grid(row=2, column=1, columnspan=3, sticky="w", padx=(12, 0))
        ttk.Label(
            self.setup, text="Conectar lista os bancos que o usuário enxerga; também dá para digitar o nome.", style="Muted.TLabel"
        ).grid(row=3, column=1, columnspan=5, sticky="w", padx=(12, 0), pady=(3, 0))

        # executar: botao, progresso e status juntos
        run = ttk.Frame(frame)
        run.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        run.columnconfigure(2, weight=1)
        self.compare_button = ttk.Button(run, text="Comparar", style="Primary.TButton", command=self.compare)
        self.compare_button.grid(row=0, column=0, padx=(0, 6))
        self.cancel_button = ttk.Button(run, text="Cancelar", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=0, column=1, padx=(0, 12))
        ttk.Style(self.window).configure(
            "Audit.Horizontal.TProgressbar",
            troughcolor=colors["heading_bg"], background=colors["primary"], bordercolor=colors["border"],
            lightcolor=colors["primary"], darkcolor=colors["primary"],
        )
        self.progress = ttk.Progressbar(run, mode="determinate", maximum=100, style="Audit.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=2, sticky="ew", padx=(0, 12))
        self.order_button = ttk.Button(run, text="Ordem sugerida...", command=self.show_order, state="disabled")
        self.order_button.grid(row=0, column=3, padx=(0, 6))
        self.plan_button = ttk.Button(run, text="Gerar plano completo...", command=self.generate_plan, state="disabled")
        self.plan_button.grid(row=0, column=4, padx=(0, 6))
        self.export_button = ttk.Button(run, text="Exportar CSV...", command=self.export, state="disabled")
        self.export_button.grid(row=0, column=5)
        ttk.Label(frame, textvariable=self.status_var, wraplength=1040, justify="left").grid(
            row=3, column=0, sticky="w", pady=(8, 0)
        )

        # cartoes de resumo (clicam para filtrar) e busca
        summary = ttk.Frame(frame)
        summary.grid(row=4, column=0, sticky="ew", pady=(10, 8))
        self.tiles = {}
        for column, (key, label, color) in enumerate(
            (
                (ALL, "Todos", colors["text"]),
                (STATUS_LABELS[STATUS_MISSING], "Falta rodar", colors["warn"]),
                (STATUS_LABELS[STATUS_OK], "OK", colors["ok"]),
                (STATUS_LABELS[STATUS_NO_STRUCTURE], "Sem campos", colors["muted"]),
                (STATUS_LABELS[STATUS_DUPLICATE], "Cópias", colors["muted"]),
                (STATUS_LABELS[STATUS_ERROR], "Erro", colors["error"]),
            )
        ):
            self.tiles[key] = self._tile(summary, column, key, label, color)
        search = ttk.Frame(summary)
        search.grid(row=0, column=6, sticky="e", padx=(16, 0))
        summary.columnconfigure(6, weight=1)
        ttk.Label(search, text="Buscar:").grid(row=0, column=0)
        ttk.Entry(search, textvariable=self.search_var, width=26).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(search, textvariable=self.count_var, style="Muted.TLabel").grid(row=0, column=2, padx=(10, 0))
        for variable in (self.filter_var, self.search_var):
            variable.trace_add("write", lambda *_args: self.apply_filters())
        for variable in (self.host_var, self.port_var):
            variable.trace_add("write", lambda *_args: self._refresh_forget())
        self._refresh_forget()

        table_frame = ttk.Frame(frame)
        table_frame.grid(row=5, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("script", "result", "detail")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        widths = {"script": 360, "result": 130, "detail": 520}
        headings = {"script": "Script", "result": "Resultado", "detail": "Detalhe (o que falta rodar)"}
        for column in columns:
            self.table.heading(column, text=headings[column], anchor="w")
            self.table.column(column, width=widths[column], anchor="w", stretch=column in ("script", "detail"))
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.tag_configure("missing", background=colors["row_warning"])
        self.table.tag_configure("error", background=colors["row_error"])
        self.table.tag_configure("none", foreground=colors["muted"])
        self.table.bind("<<TreeviewSelect>>", lambda _event: self.show_detail())
        self.table.bind("<Double-1>", lambda _event: self.open_script())

        ttk.Label(frame, text="Detalhe do script", font=section).grid(row=6, column=0, sticky="w", pady=(10, 2))
        self.detail = tk.Text(frame, height=5, wrap="word", state="disabled", relief="solid", borderwidth=1)
        self.detail.grid(row=7, column=0, sticky="nsew")

        footer = ttk.Frame(frame)
        footer.grid(row=8, column=0, sticky="e", pady=(10, 0))
        self.fix_button = ttk.Button(footer, text="Gerar script do que falta...", command=self.generate_missing_script)
        self.fix_button.grid(row=0, column=0, padx=(0, 6))
        ttk.Button(footer, text="Abrir script", command=self.open_script).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(footer, text="Fechar", command=self.close).grid(row=0, column=2)

    def _tile(self, parent, column, key, label, color):
        """Cartao de resumo: numero grande + rotulo; clicar filtra a lista."""
        colors = theme.COLORS
        box = tk.Frame(parent, bg=colors["surface"], highlightthickness=1, highlightbackground=colors["border"], cursor="hand2")
        box.grid(row=0, column=column, padx=(0, 8))
        number = tk.Label(box, text="–", bg=colors["surface"], fg=color, font=("Segoe UI", 15, "bold"), padx=14)
        number.grid(row=0, column=0, pady=(4, 0))
        caption = tk.Label(box, text=label, bg=colors["surface"], fg=colors["muted"], font=("Segoe UI", 9), padx=14)
        caption.grid(row=1, column=0, pady=(0, 4))
        for widget in (box, number, caption):
            widget.bind("<Button-1>", lambda _event, k=key: self.filter_by(k))
        return SimpleNamespace(box=box, number=number, key=key)

    def filter_by(self, key):
        """Filtra a lista pelo resultado do cartao clicado (`Todos` mostra tudo)."""
        self.filter_var.set(key)

    def _refresh_tiles(self):
        counts = {ALL: len(self.run.results) if self.run else 0}
        if self.run:
            for status, count in self.run.counts().items():
                counts[STATUS_LABELS[status]] = count
        current = self.filter_var.get()
        colors = theme.COLORS
        for key, tile in self.tiles.items():
            tile.number.config(text=str(counts.get(key, 0)) if self.run else "–")
            tile.box.config(highlightbackground=colors["primary"] if key == current else colors["border"], highlightthickness=2 if key == current else 1)

    def toggle_setup(self, show=None):
        visible = bool(self.setup.winfo_manager())
        show = (not visible) if show is None else show
        if show:
            self.setup.grid()
            self.setup_summary.config(text="")
        else:
            self.setup.grid_remove()
            folder = self.directory
            self.setup_summary.config(
                text=f"{folder.name or folder} · {self.host_var.get().strip()}:{self.port_var.get().strip()} · banco {self.db_var.get().strip()}"
            )
        self.setup_button.config(text="▾ Configuração" if show else "▸ Configuração")

    # ---- fila / ocupado ----
    def _set_busy(self, busy):
        self.busy = busy
        idle = not busy
        state = "normal" if idle else "disabled"
        for button in (self.compare_button, self.connect_button, self.choose_button):
            button.config(state=state)
        self.cancel_button.config(state="normal" if busy == "comparando" else "disabled")
        self.export_button.config(state="normal" if idle and self.run is not None and self.run.results else "disabled")
        self.order_button.config(state="normal" if idle and self.run is not None and self.run.results else "disabled")
        has_pending = self.run is not None and any(item.status == STATUS_MISSING for item in self.run.results)
        self.plan_button.config(state="normal" if idle and has_pending else "disabled")
        if idle:
            self.progress.config(value=0)

    def _start(self, kind, target):
        self._set_busy(kind)

        def worker():
            try:
                self.queue.put(target())
            except Exception as error:  # nunca deixa a janela presa em "ocupado"
                self.queue.put(("error", f"Falha inesperada: {error}"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        if not self.window.winfo_exists():
            return
        try:
            while True:
                self._handle(self.queue.get_nowait())
        except queue.Empty:
            pass
        self.window.after(120, self._poll)

    def _handle(self, message):
        kind = message[0]
        if kind == "progress":
            self.progress.config(value=message[1])
            self.status_var.set(message[2])
        elif kind == "done":
            self._set_busy("")
            self._finish(message[1])
        elif kind == "databases":
            self._set_busy("")
            self._on_databases(message[1], message[2], message[3])
        elif kind == "info":
            self._set_busy("")
            self.status_var.set(message[1])
        elif kind == "error":
            self._set_busy("")
            note = self._auth_failed(message[1])
            self.status_var.set("✖ " + message[1] + note)
            messagebox.showerror(TITLE, message[1] + note, parent=self.window)

    # ---- passo 1: pasta ----
    def choose_directory(self):
        current = self.dir_var.get().strip()
        chosen = filedialog.askdirectory(
            title="Pasta dos scripts a comparar", initialdir=current if current and Path(current).exists() else None, parent=self.window
        )
        if chosen:
            self.dir_var.set(chosen)

    # ---- senha lembrada ----
    def _ask_login(self, host, port):
        """Login (usa a senha lembrada, se houver; senao pergunta, com a opcao de lembrar)."""
        login = saved_login.get_login(
            self.window, host, port, load_model_db()["username"], LOGIN_MESSAGE, "Banco modelo - login", ask_credentials,
            store=secret_store.default_store(),
        )
        self._login = login
        return login

    @staticmethod
    def _login_note(login):
        if login.from_saved:
            return f" Usando a senha salva de {login.username}."
        if login.remembered_now:
            return f" Senha de {login.username} lembrada neste Windows."
        return ""

    def _refresh_forget(self):
        store = secret_store.default_store()
        host, port = self.host_var.get().strip(), self.port_var.get().strip()
        enabled = store.available and port.isdigit() and saved_login.has_saved(host, int(port), load_model_db()["username"], store)
        self.forget_button.config(state="normal" if enabled else "disabled")

    def forget_saved(self):
        server = self._server()
        if server is None:
            return
        if saved_login.forget(server[0], server[1], load_model_db()["username"], secret_store.default_store()):
            self.status_var.set("✔ Senha salva apagada. A próxima conexão pede a senha de novo.")
        else:
            messagebox.showinfo(TITLE, "Não há senha salva para este servidor e usuário.", parent=self.window)
        self._refresh_forget()

    def _auth_failed(self, text):
        """Senha lembrada que o banco recusou: apaga, para nao ficar tentando uma senha velha."""
        login = self._login
        if login is None or not login.from_saved or not saved_login.is_auth_failure(text):
            return ""
        server = self._server_silent()
        if server is not None:
            saved_login.forget(server[0], server[1], login.username, secret_store.default_store())
        self._refresh_forget()
        return " A senha salva não funcionou e foi apagada: informe a senha de novo."

    def _server_silent(self):
        host, port = self.host_var.get().strip(), self.port_var.get().strip()
        return (host, int(port)) if host and port.isdigit() else None

    # ---- passo 2: servidor e banco ----
    def _server(self):
        """(host, porta) validos ou None (mostra o motivo)."""
        host = self.host_var.get().strip()
        port = self.port_var.get().strip()
        if not host or not port.isdigit() or not (0 < int(port) < 65536):
            messagebox.showinfo(TITLE, "Informe o servidor e uma porta válida (número de 1 a 65535).", parent=self.window)
            return None
        return host, int(port)

    def connect_server(self):
        """Pede usuario e senha, conecta so ao servidor e lista os bancos que o usuario enxerga."""
        if self.busy:
            return
        server = self._server()
        if server is None:
            return
        host, port = server
        login = self._ask_login(host, port)
        if login is None:
            return
        save_model_db(host, port, self.db_var.get().strip(), login.username)  # o usuario guia a senha lembrada
        self.status_var.set(f"Conectando em {host}:{port}..." + self._login_note(login))
        self._refresh_forget()
        self._start(
            "conectando",
            lambda: ("databases", *model_check.list_model_databases(host, port, login.username, login.password)),
        )

    def _on_databases(self, ok, names, text):
        if not ok:
            self.status_var.set("✖ " + text + self._auth_failed(text))
            return
        note = self._login_note(self._login) if self._login is not None else ""
        self.server_databases = names
        self.db_box.config(values=names)
        wanted = self.db_var.get().strip().lower()
        match = next((name for name in names if name.lower() == wanted), None)
        if match is not None:
            self.db_var.set(match)
            self.status_var.set(f"✔ {text} Banco '{match}' encontrado. Clique em Comparar.{note}")
        else:
            if wanted:
                self.status_var.set(f"✔ {text} O banco '{self.db_var.get().strip()}' não está na lista: escolha o banco modelo.{note}")
            else:
                self.status_var.set(f"✔ {text} Escolha o banco modelo na lista.{note}")
            self.db_box.selection_clear()

    # ---- passo 3: comparar ----
    def compare(self):
        if self.busy:
            return
        directory = self.directory
        if not self.dir_var.get().strip() or not directory.is_dir():
            messagebox.showinfo(TITLE, "Escolha a pasta dos scripts que será comparada (passo 1).", parent=self.window)
            return
        server = self._server()
        if server is None:
            return
        database = self.db_var.get().strip()
        if not database:
            messagebox.showinfo(TITLE, "Informe ou escolha o banco modelo (passo 2).", parent=self.window)
            return
        known = {name.lower() for name in self.server_databases}
        if known and database.lower() not in known and not messagebox.askyesno(
            TITLE,
            f"O banco '{database}' não está na lista carregada do servidor.\n\n"
            "Se ele realmente não existir, a comparação falha. Continuar mesmo assim?",
            parent=self.window,
        ):
            return
        host, port = server
        login = self._ask_login(host, port)
        if login is None:
            return
        save_model_db(host, port, database, login.username)  # o mesmo "banco modelo" do Executar no banco
        self._refresh_forget()
        save_model_check_directory(directory)
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self.status_var.set("Lendo os scripts...")
        self.progress.config(value=0)

        def progress(done, total):
            self.queue.put(("progress", round(done * 70 / max(total, 1)), f"Interpretando scripts ({done}/{total})..."))

        def phase(name):
            text, value = {
                "reading_database": ("Lendo a estrutura do banco modelo...", 78),
                "comparing": ("Conferindo cada script com o banco...", 94),
            }[name]
            self.queue.put(("progress", value, text))

        def work():
            try:
                names = model_check.sql_script_names(directory)
                run = model_check.run_model_check(
                    directory, names, host, port, database, login.username, login.password,
                    cancel_event=cancel, progress=progress, on_phase=phase,
                )
            except ModelCheckError as error:
                return ("error", str(error))
            return ("done", run)

        self._start("comparando", work)

    def cancel(self):
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.status_var.set("Cancelando...")

    def _finish(self, run):
        self.run = run
        self.filter_var.set(ALL)
        self.search_var.set("")
        if run.cancelled:
            self.status_var.set("Comparação cancelada.")
        else:
            self.status_var.set(
                f"✔ {model_check.summary_text(run)} (banco {run.database}, {run.tables} tabela(s), MySQL {run.server_version}). "
                "OK = tudo o que o script cria já está no banco; não prova que ele foi executado."
            )
        self.apply_filters()
        self._set_busy("")
        if not run.cancelled:
            self.toggle_setup(False)  # a lista ganha a tela; "Configuracao" reabre
        write_log(model_check.audit_line(self.user, self.directory, run, "cancelada" if run.cancelled else "concluida"))

    # ---- lista ----
    def apply_filters(self):
        self.table.delete(*self.table.get_children())
        self.shown = {}
        self._refresh_tiles()
        if self.run is None:
            self.count_var.set("")
            return
        wanted = {label: status for status, label in STATUS_LABELS.items()}.get(self.filter_var.get())
        needle = self.search_var.get().strip().lower()
        matching = []
        for item in self.run.results:
            if wanted and item.status != wanted:
                continue
            if needle and needle not in (item.script + " " + item.detail).lower():
                continue
            matching.append(item)
        # o que falta rodar primeiro; dentro do grupo, ordem numerica do nome
        order = {STATUS_MISSING: 0, STATUS_ERROR: 1, STATUS_OK: 2, STATUS_NO_STRUCTURE: 3, STATUS_DUPLICATE: 4}
        matching.sort(key=lambda item: (order.get(item.status, 9), _natural(item.script)))
        for item in matching[:MAX_ROWS]:
            tag = {STATUS_MISSING: "missing", STATUS_ERROR: "error", STATUS_NO_STRUCTURE: "none", STATUS_DUPLICATE: "none"}.get(item.status, "")
            self.table.insert("", "end", iid=item.script, values=(item.script, item.label, item.detail), tags=(tag,) if tag else ())
            self.shown[item.script] = item
        total = len(self.run.results)
        self.count_var.set(f"{min(len(matching), MAX_ROWS)} de {len(matching)}" + (f" (total {total})" if len(matching) != total else ""))
        self._set_detail("")

    def show_detail(self):
        selection = self.table.selection()
        item = self.shown.get(selection[0]) if selection else None
        if item is None:
            self._set_detail("")
            return
        lines = [f"Script: {item.script}", f"Resultado: {item.label}"]
        if item.status == STATUS_ERROR:
            lines.append(item.error)
        if item.missing:
            lines.append(f"Falta rodar ({len(item.missing)}):")
            lines.extend("  • " + missing.full_text for missing in item.missing)
        if item.depends_on:
            lines.append("Rode ANTES (eles criam o que este script usa):")
            for dependency in item.depends_on:
                other = self.shown.get(dependency) or next((r for r in self.run.results if r.script == dependency), None)
                state = "também falta rodar" if other is not None and other.status == STATUS_MISSING else "confira o resultado dele"
                lines.append(f"  → {dependency} ({state})")
        if item.status == STATUS_OK:
            lines.append(f"Tudo o que o script cria já existe no banco ({item.present} item(ns) conferido(s)).")
        if item.status == STATUS_NO_STRUCTURE:
            lines.append("O script não cria tabelas, campos ou índices que dê para conferir (ex.: só dados).")
        if item.status == STATUS_DUPLICATE:
            lines.append(f"Este arquivo tem o mesmo conteúdo de {item.duplicate_of}. Vale o resultado daquele; este não entra na ordem de execução.")
        if item.notes:
            lines.append("Observações:")
            lines.extend("  • " + note for note in item.notes)
        if item.unverifiable:
            lines.append(f"{item.unverifiable} comando(s) que o sistema não consegue conferir (dados, procedures...).")
        self._set_detail("\n".join(lines))

    def _set_detail(self, text):
        self.detail.config(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.config(state="disabled")

    def show_order(self):
        """Janela com a ordem sugerida para rodar o que falta (quem cria vem antes de quem precisa)."""
        if self.run is None:
            return
        order = model_check.run_order(self.run.results)
        if not order:
            messagebox.showinfo(TITLE, "Nenhum script falta rodar.", parent=self.window)
            return
        lines = []
        for number, (name, dependencies) in enumerate(order, 1):
            lines.append(f"{number}. {name}" + (f"    (depois de: {', '.join(dependencies)})" if dependencies else ""))
        text = "\n".join(lines)
        dialog = tk.Toplevel(self.window)
        dialog.title("Ordem sugerida para rodar")
        dialog.transient(self.window)
        theme.size_window(dialog, 760, 460)
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        ttk.Label(
            body,
            text=f"{len(order)} script(s) faltam rodar. Quem cria o que outro usa vem antes; "
            "fora isso vale a ordem numérica do nome. Rode no banco modelo, na ordem.",
            wraplength=720, justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        box = tk.Text(body, wrap="none", relief="solid", borderwidth=1)
        box.insert("1.0", text)
        box.config(state="disabled")
        box.grid(row=1, column=0, sticky="nsew")
        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="e", pady=(10, 0))

        def copy():
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
            self.status_var.set("✔ Ordem sugerida copiada para a área de transferência.")

        ttk.Button(buttons, text="Copiar", command=copy).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Fechar", command=dialog.destroy).grid(row=0, column=1)
        self.order_dialog = dialog

    def generate_plan(self):
        """Grava UM .sql (so para revisao) com o que falta de todos os scripts pendentes, na ordem sugerida."""
        if self.run is None or not any(item.status == STATUS_MISSING for item in self.run.results):
            messagebox.showinfo(TITLE, "Nenhum script falta rodar: não há plano a gerar.", parent=self.window)
            return
        from datetime import datetime

        name = f"plano-banco-modelo-{datetime.now():%Y%m%d-%H%M}.sql"
        path = filedialog.asksaveasfilename(
            title="Salvar o plano do que falta rodar (somente para revisão)", defaultextension=".sql", initialfile=name,
            filetypes=[("Script SQL", "*.sql")], parent=self.window,
        )
        if not path:
            return
        run, user, directory = self.run, self.user, self.directory

        def work():
            try:
                text = model_fix.write_plan_script(run.results, path, database=run.database, user=user)
            except (OSError, ValueError) as error:
                return ("info", f"✖ Não foi possível gravar o plano: {error}")
            scripts = len(model_check.run_order(run.results))
            commands = sum(1 for line in text.splitlines() if line.rstrip().endswith(";") and not line.startswith("--"))
            write_log(model_check.audit_line(user, directory, run, "plano_faltante", f"{scripts} script(s), {commands} comando(s) para revisao"))
            return (
                "info",
                f"✔ Plano gravado em {path}: {scripts} script(s), {commands} comando(s), na ordem sugerida. REVISE antes de usar; "
                "o sistema não executa nada. Depois de rodar, compare de novo.",
            )

        self.status_var.set("Gravando...")
        self._start("exportando", work)

    def generate_missing_script(self):
        """Grava um .sql (so para revisao) com os comandos do script selecionado que ainda faltam no banco."""
        selection = self.table.selection()
        item = self.shown.get(selection[0]) if selection else None
        if item is None or item.status != STATUS_MISSING:
            messagebox.showinfo(
                TITLE,
                "Selecione na lista um script com resultado \"Falta rodar\" para gerar o que falta.",
                parent=self.window,
            )
            return
        name = f"faltando-{Path(item.script).stem}.sql"
        path = filedialog.asksaveasfilename(
            title="Salvar o que falta rodar (somente para revisão)", defaultextension=".sql", initialfile=name,
            filetypes=[("Script SQL", "*.sql")], parent=self.window,
        )
        if not path:
            return
        run, user, directory = self.run, self.user, self.directory

        def work():
            try:
                text = model_fix.write_missing_script(item, path, database=run.database, user=user)
            except (OSError, ValueError) as error:
                return ("info", f"✖ Não foi possível gravar o script: {error}")
            commands = sum(1 for line in text.splitlines() if line.rstrip().endswith(";") and not line.startswith("--"))
            write_log(model_check.audit_line(user, directory, run, "script_faltante", f"{item.script}: {commands} comando(s) para revisao"))
            return (
                "info",
                f"✔ Gravado em {path}: {commands} comando(s) do que falta rodar. REVISE antes de usar; "
                "o sistema não executa nada. Rode um por vez, na ordem.",
            )

        self.status_var.set("Gravando...")
        self._start("exportando", work)

    def open_script(self):
        selection = self.table.selection()
        if not selection:
            return
        path = self.directory / selection[0]
        if path.exists() and hasattr(os, "startfile"):
            os.startfile(path)

    # ---- exportar ----
    def export(self):
        if self.run is None or not self.run.results:
            messagebox.showinfo(TITLE, "Faça uma comparação antes de exportar.", parent=self.window)
            return
        from datetime import datetime

        name = f"comparacao-banco-modelo-{datetime.now():%Y%m%d-%H%M}.csv"
        path = filedialog.asksaveasfilename(
            title="Exportar comparação", defaultextension=".csv", initialfile=name,
            filetypes=[("Planilha CSV", "*.csv")], parent=self.window,
        )
        if not path:
            return
        run = self.run

        def work():
            try:
                total = model_check.write_results_csv(run, path)
            except (OSError, ValueError) as error:
                return ("info", f"✖ Não foi possível exportar: {error}")
            return ("info", f"✔ {total} script(s) gravado(s) em {path}")

        self.status_var.set("Exportando...")
        self._start("exportando", work)

    def close(self):
        if self.busy == "comparando":
            if not messagebox.askyesno(TITLE, "A comparação está em andamento. Cancelar e fechar?", parent=self.window):
                return
            self.cancel()
        self.window.destroy()
