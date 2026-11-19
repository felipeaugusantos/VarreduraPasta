"""Janela Consulta SVN (revisao/requisito), separada da interface principal.

`SvnSearchMixin` e misturado em `VersionScannerApp` e usa o `self` dela
(`self.svn_search_window`, `self.svn_result_entries`, etc.).
"""

import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from app import theme
from app.settings import load_svn_base_path, save_svn_base_path
from app.svn_search import search_svn


class SvnSearchMixin:
    def open_svn_search_window(self):
        if self.svn_search_window and self.svn_search_window.winfo_exists():
            self.svn_search_window.focus()
            return

        self.svn_search_window = tk.Toplevel(self)
        self.svn_search_window.title("Consulta SVN")
        theme.size_window(self.svn_search_window, 1180, 720)
        self.svn_search_window.minsize(980, 620)
        self.svn_search_window.transient(self)

        frame = ttk.Frame(self.svn_search_window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(5, weight=4)
        frame.rowconfigure(6, weight=2)
        frame.columnconfigure(2, weight=1)

        base_var = tk.StringVar(value=load_svn_base_path())
        revision_var = tk.StringVar()
        requirement_var = tk.StringVar()
        find_all_var = tk.BooleanVar(value=False)
        status_var = tk.StringVar(value="Informe uma revisao, um requisito ou os dois.")

        ttk.Label(frame, text="Caminho base SVN", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=base_var).grid(row=0, column=1, columnspan=2, sticky="ew", padx=(10, 8))
        ttk.Button(
            frame,
            text="Salvar base",
            command=lambda: self._save_svn_base_from_window(base_var.get(), status_var),
        ).grid(row=0, column=3, sticky="e")

        ttk.Separator(frame, orient="horizontal").grid(row=1, column=0, columnspan=4, sticky="ew", pady=(12, 12))

        # Busca: revisao, requisito e o botao Consultar na mesma linha.
        ttk.Label(frame, text="Revisão", style="Muted.TLabel").grid(row=2, column=0, sticky="w")
        revision_entry = ttk.Entry(frame, textvariable=revision_var, width=18)
        revision_entry.grid(row=2, column=1, sticky="w", padx=(10, 8))
        search_row = ttk.Frame(frame)
        search_row.grid(row=2, column=2, columnspan=2, sticky="ew")
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="Requisito", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(8, 8))
        requirement_entry = ttk.Entry(search_row, textvariable=requirement_var)
        requirement_entry.grid(row=0, column=1, sticky="ew")
        consult_button = ttk.Button(search_row, text="Consultar", style="Primary.TButton", command=lambda: consult())
        consult_button.grid(row=0, column=2, padx=(10, 6))
        cancel_button = ttk.Button(search_row, text="Cancelar", command=lambda: cancel_search(), state="disabled")
        cancel_button.grid(row=0, column=3)

        ttk.Checkbutton(
            frame,
            text="Buscar todas as ocorrências",
            variable=find_all_var,
        ).grid(row=3, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=(8, 0))

        ttk.Label(frame, textvariable=status_var, style="Muted.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(10, 8)
        )
        search_progress = ttk.Progressbar(frame, mode="indeterminate", length=180)
        search_progress.grid(row=4, column=3, sticky="e", pady=(10, 8))
        search_progress.grid_remove()

        result_frame = ttk.LabelFrame(frame, text="Resultados")
        result_frame.grid(row=5, column=0, columnspan=4, sticky="nsew")
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)

        result_columns = (
            "revision",
            "author",
            "date",
            "requirement",
            "version",
            "path",
            "summary",
        )
        result_tree = ttk.Treeview(
            result_frame,
            columns=result_columns,
            show="headings",
            height=8,
        )
        result_headings = {
            "revision": "Revisao",
            "author": "Autor",
            "date": "Data",
            "requirement": "Requisito",
            "version": "Versao",
            "path": "Caminho",
            "summary": "Resumo",
        }
        result_widths = {
            "revision": 80,
            "author": 120,
            "date": 140,
            "requirement": 100,
            "version": 210,
            "path": 430,
            "summary": 280,
        }
        for column in result_columns:
            result_tree.heading(column, text=result_headings[column], anchor="center")
            result_tree.column(column, width=result_widths[column], anchor="w")
        result_tree.column("revision", anchor="center")
        result_tree.column("author", anchor="center")
        result_tree.column("date", anchor="center")
        result_tree.column("requirement", anchor="center")

        tree_y_scroll = ttk.Scrollbar(
            result_frame,
            orient="vertical",
            command=result_tree.yview,
        )
        tree_x_scroll = ttk.Scrollbar(
            result_frame,
            orient="horizontal",
            command=result_tree.xview,
        )
        result_tree.configure(
            yscrollcommand=tree_y_scroll.set,
            xscrollcommand=tree_x_scroll.set,
        )
        result_tree.grid(row=0, column=0, sticky="nsew")
        tree_y_scroll.grid(row=0, column=1, sticky="ns")
        tree_x_scroll.grid(row=1, column=0, sticky="ew")

        detail_frame = ttk.LabelFrame(frame, text="Detalhes da revisao selecionada")
        detail_frame.grid(row=6, column=0, columnspan=4, sticky="nsew", pady=(10, 0))
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.columnconfigure(0, weight=1)

        detail_text = tk.Text(detail_frame, wrap="none", height=12, font=("Consolas", 9))
        detail_y_scroll = ttk.Scrollbar(
            detail_frame,
            orient="vertical",
            command=detail_text.yview,
        )
        detail_x_scroll = ttk.Scrollbar(
            detail_frame,
            orient="horizontal",
            command=detail_text.xview,
        )
        detail_text.configure(
            yscrollcommand=detail_y_scroll.set,
            xscrollcommand=detail_x_scroll.set,
        )
        detail_text.grid(row=0, column=0, sticky="nsew")
        detail_y_scroll.grid(row=0, column=1, sticky="ns")
        detail_x_scroll.grid(row=1, column=0, sticky="ew")
        svn_result_entries = []
        search_in_progress = False
        active_cancellation = [None]

        def set_detail(text):
            detail_text.config(state="normal")
            detail_text.delete("1.0", tk.END)
            detail_text.insert("1.0", text)
            detail_text.config(state="disabled")

        def clear_results():
            for item_id in result_tree.get_children():
                result_tree.delete(item_id)
            svn_result_entries.clear()
            set_detail("")

        def render_result(result):
            clear_results()
            if result.entries:
                for index, entry in enumerate(result.entries):
                    svn_result_entries.append(entry)
                    result_tree.insert(
                        "",
                        "end",
                        iid=str(index),
                        values=(
                            entry.revision,
                            entry.author,
                            entry.display_date,
                            entry.requirement,
                            entry.changed_version,
                            entry.changed_parent_path,
                            entry.summary,
                        ),
                    )
                result_tree.selection_set("0")
                result_tree.focus("0")
                set_detail(result.entries[0].detail_text)
                return
            set_detail("\n".join(result.lines))

        def show_selected_detail(_event=None):
            selection = result_tree.selection()
            if not selection:
                return
            index = int(selection[0])
            if index >= len(svn_result_entries):
                return
            set_detail(svn_result_entries[index].detail_text)

        result_tree.bind("<<TreeviewSelect>>", show_selected_detail)

        def selected_svn_entry():
            selection = result_tree.selection()
            if not selection:
                return None
            index = int(selection[0])
            if index >= len(svn_result_entries):
                return None
            return svn_result_entries[index]

        def copy_selected_revision():
            entry = selected_svn_entry()
            if not entry:
                messagebox.showwarning("Consulta SVN", "Selecione uma revisao para copiar.")
                return
            self.clipboard_clear()
            self.clipboard_append(entry.revision)
            status_var.set(f"Revisao {entry.revision} copiada.")

        def copy_selected_path():
            entry = selected_svn_entry()
            if not entry or not entry.paths:
                messagebox.showwarning(
                    "Consulta SVN",
                    "Selecione uma revisao com caminho alterado para copiar.",
                )
                return
            self.clipboard_clear()
            self.clipboard_append(entry.first_changed_path)
            status_var.set("Caminho alterado copiado.")

        def consult():
            nonlocal search_in_progress
            if search_in_progress:
                return

            base_path = base_var.get().strip()
            revision = revision_var.get().strip()
            requirement = requirement_var.get().strip()
            find_all = find_all_var.get()
            save_svn_base_path(base_path)
            search_in_progress = True
            cancellation_event = threading.Event()
            active_cancellation[0] = cancellation_event
            started_at = time.monotonic()
            status_var.set("Consultando SVN... 0 s")
            search_progress.grid()
            search_progress.start(10)
            consult_button.config(state="disabled")
            cancel_button.config(state="normal")
            clear_results()
            result_queue = queue.Queue()
            current_stage = ["Consultando SVN"]

            def worker():
                def report_progress(message):
                    result_queue.put(("progress", message))

                result = search_svn(
                    base_path,
                    revision,
                    requirement,
                    progress_callback=report_progress,
                    cancellation_event=cancellation_event,
                    find_all=find_all,
                )
                result_queue.put(("done", result))

            def poll():
                try:
                    event, payload = result_queue.get_nowait()
                except queue.Empty:
                    if self.svn_search_window and self.svn_search_window.winfo_exists():
                        elapsed = int(time.monotonic() - started_at)
                        status_var.set(f"{current_stage[0]}... {elapsed} s")
                        self.svn_search_window.after(100, poll)
                    return

                if event == "progress":
                    current_stage[0] = payload
                    elapsed = int(time.monotonic() - started_at)
                    status_var.set(f"{payload}... {elapsed} s")
                    self.svn_search_window.after(100, poll)
                    return

                result = payload
                search_in_progress = False
                active_cancellation[0] = None
                search_progress.stop()
                search_progress.grid_remove()
                consult_button.config(state="normal")
                cancel_button.config(state="disabled")
                if result.ok:
                    if result.entries:
                        result_count = len(result.entries)
                    elif result.source == "arquivos" and result.lines:
                        result_count = (
                            0
                            if result.lines[0].startswith("Nenhum arquivo")
                            else len(result.lines)
                        )
                    else:
                        result_count = 0
                    status_var.set(
                        f"Consulta concluida pela origem {result.source}. "
                        f"{result_count} registro(s) encontrado(s)."
                    )
                    render_result(result)
                    return
                status_var.set(result.error)
                set_detail(result.error)

            threading.Thread(target=worker, daemon=True).start()
            self.svn_search_window.after(100, poll)

        def cancel_search():
            event = active_cancellation[0]
            if event:
                event.set()
                status_var.set("Cancelando consulta SVN...")
                cancel_button.config(state="disabled")

        def close_svn_window():
            cancel_search()
            self.svn_search_window.destroy()

        def consult_on_enter(_event):
            consult()
            return "break"

        revision_entry.bind("<Return>", consult_on_enter)
        requirement_entry.bind("<Return>", consult_on_enter)

        def open_base():
            base_path = Path(base_var.get().strip())
            if not base_path.exists():
                messagebox.showwarning(
                    "Consulta SVN",
                    "O caminho base so pode ser aberto automaticamente quando for uma pasta local.",
                )
                return
            os.startfile(base_path)

        button_bar = ttk.Frame(frame)
        button_bar.grid(row=7, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(button_bar, text="Copiar Revisao", command=copy_selected_revision).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_bar, text="Copiar Caminho", command=copy_selected_path).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(button_bar, text="Abrir Base", command=open_base).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(button_bar, text="Fechar", command=close_svn_window).grid(row=0, column=3)
        result_tree.bind("<Double-1>", lambda _event: copy_selected_revision())
        revision_entry.focus_set()
        self.svn_search_window.protocol("WM_DELETE_WINDOW", close_svn_window)

    def _save_svn_base_from_window(self, svn_base_path, status_var):
        save_svn_base_path(svn_base_path)
        status_var.set("Caminho base SVN salvo.")
