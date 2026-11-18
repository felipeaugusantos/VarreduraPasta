"""Janela `Scripts Banco Modelo > Comparar com o banco`: so desenho; a logica fica em `sqlaudit/model_check`.

Passos: 1) escolher a pasta de scripts; 2) informar servidor e porta, conectar e escolher o
banco modelo na lista; 3) comparar. Mostra, para cada script, `OK` (tudo o que ele cria ja
esta no banco) ou `Falta rodar: campo T.C...`. Somente leitura. Usuario e senha sao pedidos a
cada conexao/comparacao e nunca gravados. Rede e arquivos rodam em thread + fila; o Tkinter
so e tocado pela thread principal.
"""

import os
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app import theme
from app.logging_utils import write_log
from app.model_db_dialogs import ask_credentials
from app.settings import load_model_check_directory, load_model_db, save_model_check_directory, save_model_db
from app.sqlaudit import model_check
from app.sqlaudit.model_check import (
    STATUS_ERROR,
    STATUS_LABELS,
    STATUS_MISSING,
    STATUS_NO_STRUCTURE,
    STATUS_OK,
    ModelCheckError,
)

TITLE = "Comparar com o banco modelo"
ALL = "Todos"
FILTERS = [ALL] + [STATUS_LABELS[s] for s in (STATUS_MISSING, STATUS_OK, STATUS_NO_STRUCTURE, STATUS_ERROR)]
MAX_ROWS = 5000
LOGIN_MESSAGE = "Somente leitura: o sistema só consulta a estrutura do banco. Usuário e senha não são gravados."


def _natural(text):
    """Chave de ordenacao que trata numeros como numeros (GER_2 antes de GER_11)."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


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
        frame.rowconfigure(5, weight=3)
        frame.rowconfigure(7, weight=1)
        section = getattr(self.app, "fonts", {}).get("section")

        # 1. pasta de scripts
        folder = ttk.Frame(frame)
        folder.grid(row=0, column=0, sticky="ew")
        folder.columnconfigure(0, weight=1)
        ttk.Label(folder, text="1  Pasta dos scripts a comparar", font=section).grid(row=0, column=0, columnspan=2, sticky="w")
        self.dir_entry = ttk.Entry(folder, textvariable=self.dir_var)
        self.dir_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.choose_button = ttk.Button(folder, text="Escolher...", command=self.choose_directory)
        self.choose_button.grid(row=1, column=1, padx=(8, 0), pady=(4, 0))

        # 2. servidor e banco modelo
        server = ttk.Frame(frame)
        server.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        server.columnconfigure(7, weight=1)
        ttk.Label(server, text="2  Servidor e banco modelo (somente leitura)", font=section).grid(row=0, column=0, columnspan=9, sticky="w")
        ttk.Label(server, text="Servidor:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.host_entry = ttk.Entry(server, textvariable=self.host_var, width=18)
        self.host_entry.grid(row=1, column=1, padx=(6, 12), pady=(4, 0))
        ttk.Label(server, text="Porta:").grid(row=1, column=2, sticky="w", pady=(4, 0))
        self.port_entry = ttk.Entry(server, textvariable=self.port_var, width=7)
        self.port_entry.grid(row=1, column=3, padx=(6, 12), pady=(4, 0))
        for entry in (self.host_entry, self.port_entry):
            entry.bind("<Return>", lambda _event: self.connect_server())
        self.connect_button = ttk.Button(server, text="Conectar e listar bancos...", command=self.connect_server)
        self.connect_button.grid(row=1, column=4, pady=(4, 0))
        ttk.Label(server, text="Banco modelo:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.db_box = ttk.Combobox(server, textvariable=self.db_var, values=[], width=44)
        self.db_box.grid(row=2, column=1, columnspan=4, sticky="w", padx=(6, 0), pady=(8, 0))
        ttk.Label(
            server,
            text="Conectar pede usuário e senha (não são gravados) e lista os bancos que o usuário enxerga. Também dá para digitar o nome.",
            style="Muted.TLabel",
        ).grid(row=3, column=0, columnspan=9, sticky="w", pady=(2, 0))

        # 3. comparar
        bar = ttk.Frame(frame)
        bar.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        bar.columnconfigure(2, weight=1)
        ttk.Label(bar, text="3  Comparar", font=section).grid(row=0, column=0, columnspan=5, sticky="w")
        self.compare_button = ttk.Button(bar, text="Comparar", style="Primary.TButton", command=self.compare)
        self.compare_button.grid(row=1, column=0, padx=(0, 6), pady=(4, 0))
        self.cancel_button = ttk.Button(bar, text="Cancelar", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=1, column=1, padx=(0, 12), pady=(4, 0))
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self.progress.grid(row=1, column=2, sticky="ew", padx=(0, 12), pady=(4, 0))
        self.export_button = ttk.Button(bar, text="Exportar CSV...", command=self.export, state="disabled")
        self.export_button.grid(row=1, column=3, pady=(4, 0))

        ttk.Label(frame, textvariable=self.status_var, wraplength=1040, justify="left").grid(
            row=3, column=0, sticky="w", pady=(8, 6)
        )

        filters = ttk.Frame(frame)
        filters.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(filters, text="Mostrar:").grid(row=0, column=0)
        self.filter_box = ttk.Combobox(filters, textvariable=self.filter_var, values=FILTERS, state="readonly", width=24)
        self.filter_box.grid(row=0, column=1, padx=(6, 12))
        ttk.Label(filters, text="Buscar:").grid(row=0, column=2)
        ttk.Entry(filters, textvariable=self.search_var, width=26).grid(row=0, column=3, padx=(6, 12))
        ttk.Label(filters, textvariable=self.count_var, style="Muted.TLabel").grid(row=0, column=4, sticky="w")
        for variable in (self.filter_var, self.search_var):
            variable.trace_add("write", lambda *_args: self.apply_filters())

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
        self.table.tag_configure("missing", background=theme.COLORS["row_warning"])
        self.table.tag_configure("error", background=theme.COLORS["row_error"])
        self.table.tag_configure("none", foreground=theme.COLORS["muted"])
        self.table.bind("<<TreeviewSelect>>", lambda _event: self.show_detail())
        self.table.bind("<Double-1>", lambda _event: self.open_script())

        ttk.Label(frame, text="Detalhe do script", font=section).grid(row=6, column=0, sticky="w", pady=(10, 2))
        self.detail = tk.Text(frame, height=6, wrap="word", state="disabled", relief="solid", borderwidth=1)
        self.detail.grid(row=7, column=0, sticky="nsew")

        footer = ttk.Frame(frame)
        footer.grid(row=8, column=0, sticky="e", pady=(10, 0))
        ttk.Button(footer, text="Abrir script", command=self.open_script).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(footer, text="Fechar", command=self.close).grid(row=0, column=1)

    # ---- fila / ocupado ----
    def _set_busy(self, busy):
        self.busy = busy
        idle = not busy
        state = "normal" if idle else "disabled"
        for button in (self.compare_button, self.connect_button, self.choose_button):
            button.config(state=state)
        self.cancel_button.config(state="normal" if busy == "comparando" else "disabled")
        self.export_button.config(state="normal" if idle and self.run is not None and self.run.results else "disabled")
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
            self.status_var.set("✖ " + message[1])
            messagebox.showerror(TITLE, message[1], parent=self.window)

    # ---- passo 1: pasta ----
    def choose_directory(self):
        current = self.dir_var.get().strip()
        chosen = filedialog.askdirectory(
            title="Pasta dos scripts a comparar", initialdir=current if current and Path(current).exists() else None, parent=self.window
        )
        if chosen:
            self.dir_var.set(chosen)

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
        credentials = ask_credentials(self.window, load_model_db()["username"], message=LOGIN_MESSAGE, title="Banco modelo - login")
        if credentials is None:
            return
        host, port = server
        self.status_var.set(f"Conectando em {host}:{port}...")
        self._start(
            "conectando",
            lambda: ("databases", *model_check.list_model_databases(host, port, credentials[0], credentials[1])),
        )

    def _on_databases(self, ok, names, text):
        if not ok:
            self.status_var.set("✖ " + text)
            return
        self.server_databases = names
        self.db_box.config(values=names)
        wanted = self.db_var.get().strip().lower()
        match = next((name for name in names if name.lower() == wanted), None)
        if match is not None:
            self.db_var.set(match)
            self.status_var.set(f"✔ {text} Banco '{match}' encontrado. Clique em Comparar.")
        else:
            if wanted:
                self.status_var.set(f"✔ {text} O banco '{self.db_var.get().strip()}' não está na lista: escolha o banco modelo.")
            else:
                self.status_var.set(f"✔ {text} Escolha o banco modelo na lista.")
            self.db_box.focus_set()

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
        credentials = ask_credentials(self.window, load_model_db()["username"], message=LOGIN_MESSAGE, title="Banco modelo - login")
        if credentials is None:
            return
        host, port = server
        save_model_db(host, port, database, credentials[0])  # o mesmo "banco modelo" do Executar no banco
        save_model_check_directory(directory)
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self.status_var.set("Lendo os scripts...")
        self.progress.config(value=0)

        def progress(done, total):
            self.queue.put(("progress", round(done * 80 / max(total, 1)), f"Interpretando scripts ({done}/{total})..."))

        def work():
            try:
                names = model_check.sql_script_names(directory)
                run = model_check.run_model_check(
                    directory, names, host, port, database, credentials[0], credentials[1],
                    cancel_event=cancel, progress=progress,
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
        write_log(model_check.audit_line(self.user, self.directory, run, "cancelada" if run.cancelled else "concluida"))

    # ---- lista ----
    def apply_filters(self):
        self.table.delete(*self.table.get_children())
        self.shown = {}
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
        order = {STATUS_MISSING: 0, STATUS_ERROR: 1, STATUS_OK: 2, STATUS_NO_STRUCTURE: 3}
        matching.sort(key=lambda item: (order.get(item.status, 9), _natural(item.script)))
        for item in matching[:MAX_ROWS]:
            tag = {STATUS_MISSING: "missing", STATUS_ERROR: "error", STATUS_NO_STRUCTURE: "none"}.get(item.status, "")
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
            lines.extend("  • " + missing.text for missing in item.missing)
        if item.status == STATUS_OK:
            lines.append(f"Tudo o que o script cria já existe no banco ({item.present} objeto(s) conferido(s)).")
        if item.status == STATUS_NO_STRUCTURE:
            lines.append("O script não cria tabelas, campos ou índices que dê para conferir (ex.: só dados).")
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
