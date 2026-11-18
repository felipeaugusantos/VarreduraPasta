"""Janela `Scripts Banco Modelo > Comparar com o banco`: so desenho; a logica fica em `sqlaudit/model_check`.

Mostra, para cada script da pasta, `OK` (tudo o que ele cria ja esta no banco modelo) ou
`Falta rodar: campo T.C...`. Somente leitura. Usuario e senha sao pedidos a cada
comparacao e nunca gravados. Rede e leitura de arquivos rodam em thread + fila; o
Tkinter so e tocado pela thread principal.
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
from app.model_db_dialogs import ask_credentials, open_db_settings
from app.settings import load_model_db, save_model_db
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


def _natural(text):
    """Chave de ordenacao que trata numeros como numeros (GER_2 antes de GER_11)."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def open_model_check_window(app, parent, directory, user=""):
    existing = getattr(app, "model_check_window", None)
    if existing is not None and existing.window.winfo_exists():
        existing.window.focus()
        existing.window.lift()
        return existing
    window = ModelCheckWindow(app, parent, directory, user)
    app.model_check_window = window
    return window


class ModelCheckWindow:
    def __init__(self, app, parent, directory, user=""):
        self.app = app
        self.directory = Path(directory)
        self.user = user
        self.window = tk.Toplevel(parent)
        self.window.title(TITLE)
        theme.size_window(self.window, 1100, 700)
        self.window.minsize(860, 520)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.queue = queue.Queue()
        self.cancel_event = None
        self.busy = ""
        self.run = None
        self.shown = {}

        self.status_var = tk.StringVar(value="Clique em Comparar para ler o banco modelo (somente leitura).")
        self.filter_var = tk.StringVar(value=ALL)
        self.search_var = tk.StringVar()
        self.count_var = tk.StringVar()
        self._build()
        self.window.after(120, self._poll)

    # ---- construcao ----
    def _build(self):
        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=3)
        frame.rowconfigure(5, weight=1)
        section = getattr(self.app, "fonts", {}).get("section")

        folder = ttk.Frame(frame)
        folder.grid(row=0, column=0, sticky="w")
        ttk.Label(folder, text="Pasta de scripts", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(folder, text=str(self.directory)).grid(row=0, column=1, sticky="w", padx=(10, 0))

        bar = ttk.Frame(frame)
        bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        bar.columnconfigure(2, weight=1)
        self.compare_button = ttk.Button(bar, text="Comparar", style="Primary.TButton", command=self.compare)
        self.compare_button.grid(row=0, column=0, padx=(0, 6))
        self.cancel_button = ttk.Button(bar, text="Cancelar", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=0, column=1, padx=(0, 12))
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=2, sticky="ew", padx=(0, 12))
        ttk.Button(bar, text="Banco...", command=lambda: open_db_settings(self.window)).grid(row=0, column=3, padx=(0, 6))
        self.export_button = ttk.Button(bar, text="Exportar CSV...", command=self.export, state="disabled")
        self.export_button.grid(row=0, column=4)

        ttk.Label(frame, textvariable=self.status_var, wraplength=1040, justify="left").grid(
            row=2, column=0, sticky="w", pady=(8, 6)
        )

        table_frame = ttk.Frame(frame)
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(1, weight=1)
        filters = ttk.Frame(table_frame)
        filters.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(filters, text="Mostrar:").grid(row=0, column=0)
        self.filter_box = ttk.Combobox(filters, textvariable=self.filter_var, values=FILTERS, state="readonly", width=24)
        self.filter_box.grid(row=0, column=1, padx=(6, 12))
        ttk.Label(filters, text="Buscar:").grid(row=0, column=2)
        ttk.Entry(filters, textvariable=self.search_var, width=26).grid(row=0, column=3, padx=(6, 12))
        ttk.Label(filters, textvariable=self.count_var, style="Muted.TLabel").grid(row=0, column=4, sticky="w")
        for variable in (self.filter_var, self.search_var):
            variable.trace_add("write", lambda *_args: self.apply_filters())

        columns = ("script", "result", "detail")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        widths = {"script": 360, "result": 130, "detail": 520}
        headings = {"script": "Script", "result": "Resultado", "detail": "Detalhe (o que falta rodar)"}
        for column in columns:
            self.table.heading(column, text=headings[column], anchor="w")
            self.table.column(column, width=widths[column], anchor="w", stretch=column in ("script", "detail"))
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self.table.tag_configure("missing", background=theme.COLORS["row_warning"])
        self.table.tag_configure("error", background=theme.COLORS["row_error"])
        self.table.tag_configure("none", foreground=theme.COLORS["muted"])
        self.table.bind("<<TreeviewSelect>>", lambda _event: self.show_detail())
        self.table.bind("<Double-1>", lambda _event: self.open_script())

        ttk.Label(frame, text="Detalhe do script", font=section).grid(row=4, column=0, sticky="w", pady=(10, 2))
        self.detail = tk.Text(frame, height=7, wrap="word", state="disabled", relief="solid", borderwidth=1)
        self.detail.grid(row=5, column=0, sticky="nsew")

        footer = ttk.Frame(frame)
        footer.grid(row=6, column=0, sticky="e", pady=(10, 0))
        ttk.Button(footer, text="Abrir script", command=self.open_script).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(footer, text="Fechar", command=self.close).grid(row=0, column=1)

    # ---- fila / ocupado ----
    def _set_busy(self, busy):
        self.busy = busy
        idle = not busy
        self.compare_button.config(state="normal" if idle else "disabled")
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
        elif kind == "info":
            self._set_busy("")
            self.status_var.set(message[1])
        elif kind == "error":
            self._set_busy("")
            self.status_var.set("✖ " + message[1])
            messagebox.showerror(TITLE, message[1], parent=self.window)

    # ---- comparar ----
    def compare(self):
        database = load_model_db()
        if not database["host"] or not database["database"]:
            messagebox.showinfo(TITLE, "Configure o servidor e o banco modelo antes de comparar.", parent=self.window)
            open_db_settings(self.window)
            return
        credentials = ask_credentials(
            self.window,
            database["username"],
            message="Somente leitura: o sistema só consulta a estrutura do banco. Usuário e senha não são gravados.",
            title="Banco modelo - login",
        )
        if credentials is None:
            return
        save_model_db(database["host"], database["port"], database["database"], credentials[0])
        directory = self.directory
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
                    directory, names, database["host"], database["port"], database["database"],
                    credentials[0], credentials[1], cancel_event=cancel, progress=progress,
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
        # o que falta rodar primeiro; dentro do grupo, ordem de nome
        order = {STATUS_MISSING: 0, STATUS_ERROR: 1, STATUS_OK: 2, STATUS_NO_STRUCTURE: 3}
        matching.sort(key=lambda item: (order.get(item.status, 9), _natural(item.script)))
        for item in matching[:MAX_ROWS]:
            tag = {STATUS_MISSING: "missing", STATUS_ERROR: "error", STATUS_NO_STRUCTURE: "none"}.get(item.status, "")
            self.table.insert("", "end", iid=item.script, values=(item.script, item.label, item.detail), tags=(tag,) if tag else ())
            self.shown[item.script] = item
        self.count_var.set(f"{min(len(matching), MAX_ROWS)} de {len(matching)}" + (f" (total {len(self.run.results)})" if len(matching) != len(self.run.results) else ""))
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
