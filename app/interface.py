import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app import __version__, actions
from app.audit import filter_log_entries, list_log_files, parse_log_entries, read_log_file
from app.config import COPY_TARGET_DIRECTORIES, SCAN_INTERVAL_MS
from app.pattern import generate_pattern_from_projects, save_pattern
from app.runtime import executable_generation_text, resource_path
from app.scanner import enrich_project_file_versions, scan_projects
from app.script_monitor import (
    check_scripts,
    load_pending_scripts,
    mark_pending_scripts_done,
    should_check_today,
)
from app.settings import (
    load_additional_copy_target_directories,
    load_base_directory,
    load_ignored_project_folders,
    save_base_directory,
    save_copy_target_directories,
    save_ignored_project_folders,
)


class VersionScannerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Sistema de Acompanhamento de Copia v{__version__}")
        self.geometry("1280x720")
        self.minsize(980, 560)
        self._set_window_icon()

        self.projects = []
        self.displayed_projects = []
        self.refresh_job = None
        self.scan_queue = queue.Queue()
        self.is_scanning = False
        self.scan_generation = 0
        self.base_directory_var = tk.StringVar(value=str(load_base_directory()))
        self.filter_var = tk.StringVar(value="Todos")
        self.search_var = tk.StringVar()
        self.sort_column = "folder"
        self.sort_reverse = False
        self.config_window = None
        self._build_menu()
        self._build_layout()
        self.after(100, self.refresh)
        self.after(2000, self.check_scripts_daily)

    def _set_window_icon(self):
        icon_path = resource_path(Path("assets") / "app-icon.ico")
        if icon_path.exists():
            try:
                self.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass

    def _build_menu(self):
        menu_bar = tk.Menu(self)
        settings_menu = tk.Menu(menu_bar, tearoff=False)
        settings_menu.add_command(
            label="Diretório-base",
            command=self.open_settings_window,
        )
        settings_menu.add_command(
            label="Padrão de Arquivos",
            command=self.open_pattern_window,
        )
        settings_menu.add_command(
            label="Pastas Ignoradas",
            command=self.open_ignored_folders_window,
        )
        menu_bar.add_cascade(label="Configurações", menu=settings_menu)

        audit_menu = tk.Menu(menu_bar, tearoff=False)
        audit_menu.add_command(label="Logs", command=self.open_audit_window)
        menu_bar.add_cascade(label="Auditoria", menu=audit_menu)

        monitor_menu = tk.Menu(menu_bar, tearoff=False)
        monitor_menu.add_command(
            label="Scripts Banco Modelo",
            command=self.open_script_monitor_window,
        )
        menu_bar.add_cascade(label="Monitoramento", menu=monitor_menu)

        help_menu = tk.Menu(menu_bar, tearoff=False)
        help_menu.add_command(
            label="Manual de Utilizacao",
            command=self.open_user_manual_window,
        )
        help_menu.add_command(label="Sobre", command=self.open_about_window)
        menu_bar.add_cascade(label="Ajuda", menu=help_menu)
        self.config(menu=menu_bar)

    def _build_layout(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, padding=(12, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="Diretório-base:").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.base_directory_var).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(8, 8),
        )
        self.refresh_button = ttk.Button(header, text="Atualizar", command=self.refresh)
        self.refresh_button.grid(row=0, column=2, sticky="e")

        filter_bar = ttk.Frame(self, padding=(12, 0, 12, 8))
        filter_bar.grid(row=1, column=0, sticky="ew")
        filter_bar.columnconfigure(3, weight=1)

        ttk.Label(filter_bar, text="Filtro:").grid(row=0, column=0, sticky="w")
        filter_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.filter_var,
            state="readonly",
            width=16,
            values=("Todos", "Somente OK", "Com erro", "Local", "Cloud"),
        )
        filter_combo.grid(row=0, column=1, sticky="w", padx=(8, 18))
        filter_combo.bind("<<ComboboxSelected>>", lambda _event: self._render_projects())

        ttk.Label(filter_bar, text="Buscar:").grid(row=0, column=2, sticky="w")
        search_entry = ttk.Entry(filter_bar, textvariable=self.search_var)
        search_entry.grid(row=0, column=3, sticky="ew", padx=(8, 8))
        ttk.Button(
            filter_bar,
            text="Limpar",
            command=self._clear_search,
            width=10,
        ).grid(row=0, column=4, sticky="e")
        self.search_var.trace_add("write", lambda *_args: self._render_projects())

        columns = (
            "folder",
            "type",
            "file_version",
            "product_version",
            "expected_file",
            "expected_product",
            "autcom_size",
            "zip",
            "status",
        )
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=16)
        headings = {
            "folder": "Projeto",
            "type": "Tipo",
            "file_version": "FileVersion",
            "product_version": "ProductVersion",
            "expected_file": "File esperado",
            "expected_product": "Product esperado",
            "autcom_size": "Autcom MB",
            "zip": "Origem",
            "status": "Status",
        }
        widths = {
            "folder": 180,
            "type": 70,
            "file_version": 110,
            "product_version": 120,
            "expected_file": 110,
            "expected_product": 120,
            "autcom_size": 90,
            "zip": 80,
            "status": 150,
        }
        for column in columns:
            self.tree.heading(
                column,
                text=headings[column],
                anchor="center",
                command=lambda current_column=column: self._sort_by_column(current_column),
            )
            self.tree.column(column, width=widths[column], anchor="center")

        self.tree.tag_configure("ok", background="#e9f7ef")
        self.tree.tag_configure("warning", background="#fff8d9")
        self.tree.tag_configure("error", background="#fdecec")

        self.tree.grid(row=2, column=0, sticky="nsew", padx=12)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_buttons())
        self.tree.bind("<Double-1>", lambda _event: self.open_selected_project_folder())

        footer = ttk.Frame(self, padding=12)
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        self.status_label = ttk.Label(footer, text="")
        self.status_label.grid(row=0, column=0, sticky="w")

        self.selection_hint_label = ttk.Label(
            footer,
            text="",
            wraplength=940,
            justify="left",
        )
        self.selection_hint_label.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(6, 0),
        )

        button_bar = ttk.Frame(footer)
        button_bar.grid(row=0, column=1, sticky="e")

        self.local_close_button = ttk.Button(
            button_bar,
            text="Fechamento Local",
            command=lambda: self._run_action(actions.fechamento_local),
        )
        self.local_close_button.grid(row=0, column=0, padx=4)

        self.cloud_close_button = ttk.Button(
            button_bar,
            text="Fechamento Cloud",
            command=lambda: self._run_action(actions.fechamento_cloud),
        )
        self.cloud_close_button.grid(row=0, column=1, padx=4)

        self.validate_close_button = ttk.Button(
            button_bar,
            text="Validar Fechamento",
            command=lambda: self._run_action(actions.validar_pos_fechamento),
        )
        self.validate_close_button.grid(row=0, column=2, padx=(4, 12))

        self.local_copy_button = ttk.Button(
            button_bar,
            text="Copiar Local",
            command=lambda: self._run_action(actions.copiar_local),
        )
        self.local_copy_button.grid(row=0, column=3, padx=4)

        self.cloud_copy_button = ttk.Button(
            button_bar,
            text="Copiar Cloud",
            command=lambda: self._run_action(actions.copiar_cloud),
        )
        self.cloud_copy_button.grid(row=0, column=4, padx=4)

        self.details_button = ttk.Button(
            button_bar,
            text="Detalhes",
            command=self.open_project_details,
        )
        self.details_button.grid(row=0, column=5, padx=(12, 4))

        self.open_folder_button = ttk.Button(
            button_bar,
            text="Abrir Pasta",
            command=self.open_selected_project_folder,
        )
        self.open_folder_button.grid(row=0, column=6, padx=4)

        self.buttons = (
            self.local_close_button,
            self.cloud_close_button,
            self.validate_close_button,
            self.local_copy_button,
            self.cloud_copy_button,
            self.details_button,
            self.open_folder_button,
        )
        self._sync_buttons()

    def refresh(self):
        if self.refresh_job is not None:
            self.after_cancel(self.refresh_job)
            self.refresh_job = None

        if self.is_scanning:
            return

        for item_id in self.tree.get_children():
            self.tree.delete(item_id)

        base_directory = Path(self.base_directory_var.get().strip())
        save_base_directory(base_directory)

        self.is_scanning = True
        self.scan_generation += 1
        generation = self.scan_generation
        self.projects = []
        self.status_label.config(text=f"Analisando {base_directory}...")
        self.refresh_button.config(state="disabled")
        self._sync_buttons()

        thread = threading.Thread(
            target=self._scan_in_background,
            args=(generation, base_directory),
            daemon=True,
        )
        thread.start()
        self.after(100, self._poll_scan_queue)

    def _scan_in_background(self, generation, base_directory):
        try:
            projects = scan_projects(base_directory)
            error = None
        except Exception as scan_error:
            projects = []
            error = scan_error

        self.scan_queue.put((generation, projects, error))

    def _poll_scan_queue(self):
        try:
            generation, projects, error = self.scan_queue.get_nowait()
        except queue.Empty:
            if self.is_scanning:
                self.after(100, self._poll_scan_queue)
            return

        self._finish_refresh(generation, projects, error)

    def _finish_refresh(self, generation, projects, error):
        if generation != self.scan_generation:
            return

        self.is_scanning = False
        self.refresh_button.config(state="normal")

        if error:
            self.projects = []
            self.displayed_projects = []
            self.status_label.config(text=str(error))
            self.selection_hint_label.config(text="")
            self._sync_buttons()
            self.refresh_job = self.after(SCAN_INTERVAL_MS, self.refresh)
            return

        self.projects = projects
        self._render_projects()
        self.refresh_job = self.after(SCAN_INTERVAL_MS, self.refresh)

    def _render_projects(self):
        for item_id in self.tree.get_children():
            self.tree.delete(item_id)

        filtered_projects = [
            project
            for project in self.projects
            if self._matches_search(project) and self._matches_filter(project)
        ]
        filtered_projects.sort(
            key=lambda project: self._sort_value(project, self.sort_column),
            reverse=self.sort_reverse,
        )
        self.displayed_projects = filtered_projects

        for index, project in enumerate(filtered_projects):
            size = (
                ""
                if project.display_autcom_size_mb is None
                else f"{project.display_autcom_size_mb:.2f}"
            )
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    project.folder_name,
                    self._project_type(project),
                    project.display_file_version or "",
                    project.display_product_version or "",
                    project.expected_file_version,
                    project.expected_product_version,
                    size,
                    self._short_zip_status(project),
                    self._short_status(project),
                ),
                tags=(self._status_tag(project),),
            )

        self.status_label.config(text=self._summary_text(filtered_projects))
        self._sync_buttons()

    def _clear_search(self):
        self.search_var.set("")

    def _matches_search(self, project):
        term = self.search_var.get().strip().lower()
        if not term:
            return True
        searchable = " ".join(
            (
                project.folder_name,
                project.display_file_version or "",
                project.display_product_version or "",
                project.expected_file_version or "",
                project.expected_product_version or "",
                self._short_zip_status(project),
                self._short_status(project),
                project.status,
            )
        ).lower()
        return term in searchable

    def _matches_filter(self, project):
        selected_filter = self.filter_var.get()
        if selected_filter == "Somente OK":
            return project.status == "OK"
        if selected_filter == "Com erro":
            return project.status != "OK"
        if selected_filter == "Local":
            return not self._is_cloud_project(project)
        if selected_filter == "Cloud":
            return self._is_cloud_project(project)
        return True

    def _short_status(self, project):
        if project.status == "OK":
            return "OK"
        normalized = project.status.lower()
        if "ausente" in normalized:
            return "Arquivo ausente"
        if "zip" in normalized:
            return "Pendência ZIP"
        if "fileversion" in normalized or "productversion" in normalized:
            return "Versão incorreta"
        return "Pendência"

    def _status_category(self, project):
        short_status = self._short_status(project)
        if short_status == "OK":
            return "ok"
        if short_status == "Arquivo ausente":
            return "missing"
        if short_status == "Versão incorreta":
            return "version"
        if short_status == "Pendência ZIP":
            return "zip"
        return "other"

    def _status_tag(self, project):
        category = self._status_category(project)
        if category == "ok":
            return "ok"
        if category in {"missing", "version", "zip"}:
            return "error"
        return "warning"

    def _short_zip_status(self, project):
        if project.zip_status == "Autcom zip OK":
            return "ZIP OK"
        if project.zip_status == "Sem autcom.zip":
            return "Somente EXE"
        if project.zip_status == "Autcom ausente":
            return "Sem Autcom"
        return "Pendência"

    def _project_type(self, project):
        return "Cloud" if self._is_cloud_project(project) else "Local"

    def _summary_text(self, projects):
        total = len(projects)
        ok_count = sum(1 for project in projects if self._status_category(project) == "ok")
        version_count = sum(
            1 for project in projects if self._status_category(project) == "version"
        )
        missing_count = sum(
            1 for project in projects if self._status_category(project) == "missing"
        )
        zip_count = sum(1 for project in projects if self._status_category(project) == "zip")
        other_count = sum(
            1 for project in projects if self._status_category(project) == "other"
        )
        analyzed_total = len(self.projects)
        now = datetime.now().strftime("%H:%M:%S")
        pending_parts = [
            f"{version_count} versão incorreta",
            f"{missing_count} arquivo ausente",
            f"{zip_count} pendência ZIP",
        ]
        if other_count:
            pending_parts.append(f"{other_count} outras pendências")
        return (
            f"{total} exibido(s) de {analyzed_total} analisado(s) | "
            f"{ok_count} OK | {' | '.join(pending_parts)} | Atualizado às {now}"
        )

    def _sort_value(self, project, column):
        values = {
            "folder": project.folder_name.lower(),
            "type": self._project_type(project),
            "file_version": project.display_file_version or "",
            "product_version": project.display_product_version or "",
            "expected_file": project.expected_file_version or "",
            "expected_product": project.expected_product_version or "",
            "autcom_size": project.display_autcom_size_mb or 0,
            "zip": self._short_zip_status(project),
            "status": self._short_status(project),
        }
        return values.get(column, "")

    def _sort_by_column(self, column):
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self._render_projects()

    def open_settings_window(self):
        if self.config_window and self.config_window.winfo_exists():
            self.config_window.focus()
            return

        self.config_window = tk.Toplevel(self)
        self.config_window.title("Configurações")
        self.config_window.geometry("820x360")
        self.config_window.resizable(False, False)
        self.config_window.transient(self)

        frame = ttk.Frame(self.config_window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        settings_base_var = tk.StringVar(value=self.base_directory_var.get())
        settings_copy_var = tk.StringVar()

        ttk.Label(frame, text="Diretório-base:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=settings_base_var).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(8, 8),
        )

        ttk.Button(
            frame,
            text="Procurar",
            command=lambda: self.choose_base_directory(settings_base_var),
        ).grid(row=0, column=2, sticky="e")

        ttk.Label(frame, text="Destinos padrão:").grid(
            row=1,
            column=0,
            sticky="nw",
            pady=(12, 0),
        )
        ttk.Label(
            frame,
            text="\n".join(str(path) for path in COPY_TARGET_DIRECTORIES),
            wraplength=620,
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=(12, 0))

        ttk.Label(frame, text="Destino adicional:").grid(
            row=2,
            column=0,
            sticky="w",
            pady=(12, 0),
        )
        ttk.Entry(frame, textvariable=settings_copy_var).grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(8, 8),
            pady=(12, 0),
        )

        ttk.Button(
            frame,
            text="Procurar",
            command=lambda: self.choose_base_directory(settings_copy_var),
        ).grid(row=2, column=2, sticky="e", pady=(12, 0))

        ttk.Label(frame, text="Destinos extras:").grid(
            row=3,
            column=0,
            sticky="nw",
            pady=(8, 0),
        )
        target_list = tk.Listbox(frame, height=5)
        target_list.grid(row=3, column=1, sticky="ew", padx=(8, 8), pady=(8, 0))
        for target in load_additional_copy_target_directories():
            target_list.insert(tk.END, str(target))

        target_buttons = ttk.Frame(frame)
        target_buttons.grid(row=3, column=2, sticky="n", pady=(8, 0))

        def add_target():
            value = settings_copy_var.get().strip()
            if not value:
                return
            existing = {
                target_list.get(index).lower()
                for index in range(target_list.size())
            }
            if value.lower() not in existing:
                target_list.insert(tk.END, value)
            settings_copy_var.set("")

        def remove_target():
            for index in reversed(target_list.curselection()):
                target_list.delete(index)

        ttk.Button(target_buttons, text="Adicionar", command=add_target).grid(
            row=0,
            column=0,
            sticky="ew",
        )
        ttk.Button(target_buttons, text="Remover", command=remove_target).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(8, 0),
        )

        button_bar = ttk.Frame(frame)
        button_bar.grid(row=4, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(
            button_bar,
            text="Salvar",
            command=lambda: self.save_settings(
                settings_base_var.get(),
                [target_list.get(index) for index in range(target_list.size())],
            ),
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            button_bar,
            text="Cancelar",
            command=self.config_window.destroy,
        ).grid(row=0, column=1)

    def open_pattern_window(self):
        pattern_window = tk.Toplevel(self)
        pattern_window.title("Padrão de Arquivos")
        pattern_window.geometry("760x170")
        pattern_window.resizable(False, False)
        pattern_window.transient(self)

        frame = ttk.Frame(pattern_window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        local_var = tk.StringVar()
        cloud_var = tk.StringVar()

        ttk.Label(frame, text="Pasta modelo Local:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=local_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(
            frame,
            text="Procurar",
            command=lambda: self.choose_base_directory(local_var),
        ).grid(row=0, column=2, sticky="e")

        ttk.Label(frame, text="Pasta modelo Cloud:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=cloud_var).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=8,
            pady=(8, 0),
        )
        ttk.Button(
            frame,
            text="Procurar",
            command=lambda: self.choose_base_directory(cloud_var),
        ).grid(row=1, column=2, sticky="e", pady=(8, 0))

        button_bar = ttk.Frame(frame)
        button_bar.grid(row=2, column=0, columnspan=3, sticky="e", pady=(18, 0))
        ttk.Button(
            button_bar,
            text="Salvar Padrão",
            command=lambda: self.save_file_pattern(
                pattern_window,
                local_var.get(),
                cloud_var.get(),
            ),
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_bar, text="Cancelar", command=pattern_window.destroy).grid(
            row=0,
            column=1,
        )

    def save_file_pattern(self, pattern_window, local_project_path, cloud_project_path):
        if not local_project_path and not cloud_project_path:
            messagebox.showerror(
                "Padrão de Arquivos",
                "Informe ao menos uma pasta modelo Local ou Cloud.",
            )
            return

        try:
            pattern = generate_pattern_from_projects(
                local_project_path.strip() or None,
                cloud_project_path.strip() or None,
            )
            save_pattern(pattern)
        except Exception as error:
            messagebox.showerror("Padrão de Arquivos", f"Erro ao gerar padrão:\n{error}")
            return

        pattern_window.destroy()
        messagebox.showinfo(
            "Padrão de Arquivos",
            "Padrão salvo em project_pattern.json\n\n"
            f"Projetos modelo: {len(pattern['source_projects'])}\n"
            f"Grupos essenciais: {len(pattern['required_groups'])}",
        )
        self.refresh()

    def open_ignored_folders_window(self):
        ignored_window = tk.Toplevel(self)
        ignored_window.title("Pastas Ignoradas")
        ignored_window.geometry("560x420")
        ignored_window.transient(self)

        frame = ttk.Frame(ignored_window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Pastas ignoradas na varredura:").grid(
            row=0,
            column=0,
            columnspan=3,
            sticky="w",
        )

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(8, 8))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        folder_list = tk.Listbox(list_frame, height=12)
        y_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=folder_list.yview)
        folder_list.configure(yscrollcommand=y_scroll.set)
        folder_list.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")

        def refresh_list(folders):
            folder_list.delete(0, "end")
            for folder in sorted(folders):
                folder_list.insert("end", folder)

        folders = set(load_ignored_project_folders())
        refresh_list(folders)

        new_folder_var = tk.StringVar()
        ttk.Entry(frame, textvariable=new_folder_var).grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(0, 8),
        )

        def add_folder():
            folder_name = new_folder_var.get().strip().lower()
            if not folder_name:
                return
            folders.add(folder_name)
            new_folder_var.set("")
            refresh_list(folders)

        def remove_selected():
            selection = folder_list.curselection()
            if not selection:
                return
            for index in reversed(selection):
                folders.discard(folder_list.get(index))
            refresh_list(folders)

        ttk.Button(frame, text="Adicionar", command=add_folder).grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(8, 0),
            pady=(0, 8),
        )
        ttk.Button(frame, text="Remover", command=remove_selected).grid(
            row=2,
            column=2,
            sticky="ew",
            padx=(8, 0),
            pady=(0, 8),
        )

        button_bar = ttk.Frame(frame)
        button_bar.grid(row=3, column=0, columnspan=3, sticky="e")

        def save_ignored_folders():
            save_ignored_project_folders(folders)
            ignored_window.destroy()
            self.refresh()

        ttk.Button(button_bar, text="Salvar", command=save_ignored_folders).grid(
            row=0,
            column=0,
            padx=(0, 8),
        )
        ttk.Button(button_bar, text="Cancelar", command=ignored_window.destroy).grid(
            row=0,
            column=1,
        )

    def open_audit_window(self):
        audit_window = tk.Toplevel(self)
        audit_window.title("Auditoria")
        audit_window.geometry("1180x560")
        audit_window.transient(self)

        frame = ttk.Frame(audit_window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)
        toolbar.columnconfigure(3, weight=1)

        ttk.Label(toolbar, text="Log:").grid(row=0, column=0, sticky="w")
        log_var = tk.StringVar()
        log_combo = ttk.Combobox(toolbar, textvariable=log_var, state="readonly")
        log_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Label(toolbar, text="Buscar:").grid(row=0, column=2, sticky="w")
        filter_var = tk.StringVar()
        filter_entry = ttk.Entry(toolbar, textvariable=filter_var)
        filter_entry.grid(row=0, column=3, sticky="ew", padx=(8, 8))
        result_var = tk.StringVar(value="Todos")
        result_combo = ttk.Combobox(
            toolbar,
            textvariable=result_var,
            state="readonly",
            width=12,
            values=(
                "Todos",
                "bloqueado",
                "cancelado",
                "concluido",
                "erro",
                "erro_limpeza",
                "erro_padrao",
                "iniciado",
                "padrao_gravado",
                "padrao_nao_gravado",
                "processo_aberto",
            ),
        )
        result_combo.grid(row=0, column=4, sticky="e", padx=(0, 8))

        table_frame = ttk.Frame(frame)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        columns = (
            "timestamp",
            "acao",
            "resultado",
            "usuario",
            "projeto",
            "tipo",
            "destino",
            "fileversion",
            "productversion",
            "autcom_mb",
            "motivo",
        )
        headings = {
            "timestamp": "Data/Hora",
            "acao": "Ação",
            "resultado": "Resultado",
            "usuario": "Usuário",
            "projeto": "Projeto",
            "tipo": "Tipo",
            "destino": "Destino",
            "fileversion": "FileVersion",
            "productversion": "ProductVersion",
            "autcom_mb": "Autcom MB",
            "motivo": "Motivo",
        }
        widths = {
            "timestamp": 140,
            "acao": 150,
            "resultado": 120,
            "usuario": 110,
            "projeto": 210,
            "tipo": 70,
            "destino": 360,
            "fileversion": 110,
            "productversion": 120,
            "autcom_mb": 90,
            "motivo": 520,
        }
        log_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        for column in columns:
            log_tree.heading(column, text=headings[column], anchor="center")
            log_tree.column(column, width=widths[column], anchor="center")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=log_tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=log_tree.xview)
        log_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        log_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

        current_log_content = {"entries": []}
        current_log_state = {"selected": "", "stamp": None}

        def refresh_logs(force=True):
            log_files = list_log_files()
            log_combo["values"] = [str(path.name) for path in log_files]
            if log_files and not log_var.get():
                log_var.set(log_files[-1].name)
            load_selected_log(force=force)

        def apply_log_filter(_event=None):
            for item_id in log_tree.get_children():
                log_tree.delete(item_id)
            entries = filter_log_entries(
                current_log_content["entries"],
                filter_var.get(),
                result_var.get(),
            )
            for index, entry in enumerate(entries):
                log_tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=tuple(entry.get(column, "") for column in columns),
                )

        def load_selected_log(_event=None, force=True):
            selected_name = log_var.get()
            selected_file = next(
                (path for path in list_log_files() if path.name == selected_name),
                None,
            )
            if selected_file:
                try:
                    stat = selected_file.stat()
                    stamp = (str(selected_file), stat.st_mtime_ns, stat.st_size)
                except OSError:
                    stamp = (str(selected_file), None, None)
                if not force and current_log_state["stamp"] == stamp:
                    return
                current_log_state["selected"] = selected_name
                current_log_state["stamp"] = stamp
                current_log_content["entries"] = parse_log_entries(
                    read_log_file(selected_file)
                )
            else:
                if not force and current_log_state["selected"] == "":
                    return
                current_log_state["selected"] = ""
                current_log_state["stamp"] = None
                current_log_content["entries"] = []
            apply_log_filter()

        def auto_refresh_logs():
            if not audit_window.winfo_exists():
                return
            refresh_logs(force=False)
            audit_window.after(2000, auto_refresh_logs)

        ttk.Button(toolbar, text="Atualizar", command=lambda: refresh_logs(force=True)).grid(
            row=0,
            column=5,
            sticky="e",
        )
        ttk.Button(toolbar, text="Fechar", command=audit_window.destroy).grid(
            row=0,
            column=6,
            sticky="e",
            padx=(8, 0),
        )
        log_combo.bind("<<ComboboxSelected>>", load_selected_log)
        filter_entry.bind("<KeyRelease>", apply_log_filter)
        result_combo.bind("<<ComboboxSelected>>", apply_log_filter)
        refresh_logs()
        audit_window.after(2000, auto_refresh_logs)

    def check_scripts_daily(self):
        if should_check_today():
            self._run_script_monitor_check(show_without_news=False)
        self.after(24 * 60 * 60 * 1000, self.check_scripts_daily)

    def _run_script_monitor_check(self, show_without_news=True, on_result=None):
        def worker():
            result = check_scripts(force=show_without_news)
            self.after(0, lambda: finish(result))

        def finish(result):
            if on_result:
                on_result(result)
            if result.error:
                if show_without_news:
                    messagebox.showerror("Monitoramento de Scripts", result.error)
                return
            if result.new_files:
                preview = "\n".join(result.new_files[:20])
                if len(result.new_files) > 20:
                    preview += f"\n... +{len(result.new_files) - 20} script(s)"
                messagebox.showwarning(
                    "Monitoramento de Scripts",
                    "Novos scripts encontrados para rodar no banco modelo:\n\n"
                    f"{preview}\n\n"
                    f"Caminho:\n{result.directory}",
                )
                return
            if show_without_news:
                messagebox.showinfo(
                    "Monitoramento de Scripts",
                    "Nenhum script novo encontrado.\n\n"
                    f"Total monitorado: {result.total_files}\n"
                    f"Caminho:\n{result.directory}",
                )

        threading.Thread(target=worker, daemon=True).start()

    def open_script_monitor_window(self):
        monitor_window = tk.Toplevel(self)
        monitor_window.title("Monitoramento de Scripts")
        monitor_window.geometry("860x420")
        monitor_window.transient(self)

        frame = ttk.Frame(monitor_window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(1, weight=1)

        status_var = tk.StringVar(value="Pronto para verificar.")
        path_var = tk.StringVar(value="")

        ttk.Label(frame, text="Pasta monitorada:").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=path_var, wraplength=600).grid(
            row=0,
            column=1,
            columnspan=2,
            sticky="w",
            padx=(8, 0),
        )
        ttk.Label(frame, textvariable=status_var).grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(10, 8),
        )

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=2, column=0, columnspan=3, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        script_list = tk.Listbox(list_frame)
        y_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=script_list.yview)
        script_list.configure(yscrollcommand=y_scroll.set)
        script_list.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")

        def update_result(result):
            path_var.set(str(result.directory))
            script_list.delete(0, tk.END)
            if result.error:
                status_var.set(result.error)
                return
            pending_files = result.pending_files or load_pending_scripts()
            if pending_files:
                status_var.set(
                    f"{len(pending_files)} script(s) pendente(s) para marcar como feito."
                )
                for file_name in pending_files:
                    script_list.insert(tk.END, file_name)
                return
            status_var.set(
                f"Nenhum script novo. Total monitorado: {result.total_files}."
            )

        def verify_now():
            status_var.set("Verificando scripts...")
            self._run_script_monitor_check(
                show_without_news=True,
                on_result=update_result,
            )

        def mark_done():
            if not load_pending_scripts():
                messagebox.showinfo(
                    "Monitoramento de Scripts",
                    "Nao existe script pendente para marcar como feito.",
                )
                return
            mark_pending_scripts_done()
            script_list.delete(0, tk.END)
            status_var.set("Scripts pendentes marcados como feito.")
            messagebox.showinfo(
                "Monitoramento de Scripts",
                "Scripts marcados como feito.",
            )

        def open_folder():
            directory = Path(path_var.get())
            if not directory.exists():
                messagebox.showerror(
                    "Monitoramento de Scripts",
                    f"Pasta nao encontrada:\n{directory}",
                )
                return
            os.startfile(directory)

        button_bar = ttk.Frame(frame)
        button_bar.grid(row=3, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(button_bar, text="Verificar agora", command=verify_now).grid(
            row=0,
            column=0,
            padx=(0, 8),
        )
        ttk.Button(button_bar, text="Abrir Pasta", command=open_folder).grid(
            row=0,
            column=1,
            padx=(0, 8),
        )
        ttk.Button(button_bar, text="Marcar como feito", command=mark_done).grid(
            row=0,
            column=2,
            padx=(0, 8),
        )
        ttk.Button(button_bar, text="Fechar", command=monitor_window.destroy).grid(
            row=0,
            column=3,
        )

        verify_now()

    def open_user_manual_window(self):
        manual_window = tk.Toplevel(self)
        manual_window.title("Manual de Utilizacao")
        manual_window.geometry("900x620")
        manual_window.transient(self)

        frame = ttk.Frame(manual_window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        manual_text = tk.Text(frame, wrap="word")
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=manual_text.yview)
        manual_text.configure(yscrollcommand=y_scroll.set)
        manual_text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")

        manual_text.insert("1.0", self._manual_content())
        manual_text.config(state="disabled")

        ttk.Button(frame, text="Fechar", command=manual_window.destroy).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="e",
            pady=(12, 0),
        )

    def _manual_content(self):
        return (
            "Manual de Utilizacao - Sistema de Acompanhamento de Copia\n\n"
            "1. Tela principal\n"
            "- O sistema analisa o Diretorio-base configurado, por padrao C:\\VERSOES_FECHADAS.\n"
            "- A lista mostra projetos Local e Cloud, versoes encontradas, versoes esperadas, "
            "tamanho do Autcom, origem e status.\n"
            "- Use Atualizar para forcar uma nova varredura.\n"
            "- Use Filtro e Buscar para localizar projetos rapidamente.\n\n"
            "2. Abrir projeto e detalhes\n"
            "- Clique duas vezes em uma linha ou use Abrir Pasta para abrir a pasta do projeto.\n"
            "- Use Detalhes para ver todos os arquivos essenciais, origem, versoes, tamanho e status.\n\n"
            "3. Fechamento Local e Cloud\n"
            "- Fechamento Local fica disponivel apenas para projeto sem _CLOUD.\n"
            "- Fechamento Cloud fica disponivel apenas para projeto com _CLOUD.\n"
            "- Ao clicar no fechamento, o sistema grava o padrao de arquivos e executa "
            "comandosCMD\\_FechamentoArquivos.bat em um novo console.\n"
            "- Depois que o BAT terminar, clique em Atualizar e em Validar Fechamento para "
            "registrar a versao/status final na Auditoria.\n\n"
            "4. Copiar Local e Copiar Cloud\n"
            "- Copiar Local exige Autcom abaixo de 100 MB.\n"
            "- Copiar Cloud exige Autcom acima de 200 MB.\n"
            "- A copia envia somente arquivos .zip para o destino.\n"
            "- Apos a copia ser verificada, a origem e limpa preservando apenas comandosCMD.\n"
            "- O caminho do destino e copiado automaticamente para a area de transferencia.\n"
            "- Se houver mais de um destino na rede, o sistema mostra uma tela para escolher "
            "o caminho correto antes de continuar.\n\n"
            "5. Configuracoes\n"
            "- Diretorio-base: define a pasta analisada.\n"
            "- Destinos extras: adiciona novas raizes de busca alem dos destinos padrao.\n"
            "- Padrao de Arquivos: permite aprender o padrao esperado a partir de pastas modelo.\n"
            "- Pastas Ignoradas: define pastas que nao devem aparecer na varredura.\n\n"
            "6. Auditoria\n"
            "- Auditoria > Logs exibe os registros mensais em tabela.\n"
            "- Use Buscar e Resultado para filtrar acoes, erros, bloqueios e conclusoes.\n"
            "- Os logs ficam na pasta logs ao lado do executavel.\n\n"
            "7. Monitoramento de Scripts\n"
            "- Monitoramento > Scripts Banco Modelo verifica a pasta de scripts do banco modelo.\n"
            "- A verificacao acontece uma vez ao dia ao abrir o sistema.\n"
            "- Tambem e possivel clicar em Verificar agora.\n"
            "- Quando houver scripts novos, eles ficam pendentes ate clicar em Marcar como feito.\n"
            "- O sistema apenas avisa quando existe script novo; nenhum script e executado automaticamente.\n"
        )

    def open_about_window(self):
        messagebox.showinfo(
            "Sobre",
            "Sistema de Acompanhamento de Copia\n\n"
            f"Versao: {__version__}\n"
            f"Geracao do exe: {executable_generation_text()}\n"
            "Criador: Felipe Santos\n"
            "Setor: Atualizacao",
        )

    def choose_base_directory(self, target_var):
        selected_directory = filedialog.askdirectory(
            title="Selecionar Diretório-base",
            initialdir=target_var.get() or "C:\\",
        )
        if selected_directory:
            target_var.set(selected_directory)

    def save_settings(self, base_directory, copy_target_directories):
        self.base_directory_var.set(base_directory.strip())
        save_base_directory(self.base_directory_var.get())
        save_copy_target_directories(copy_target_directories)
        if self.config_window and self.config_window.winfo_exists():
            self.config_window.destroy()
        self.refresh()

    def _selected_project(self):
        selection = self.tree.selection()
        if not selection:
            return None
        return self.displayed_projects[int(selection[0])]

    def _sync_buttons(self):
        selected = self._selected_project()
        state = "normal" if selected else "disabled"
        for button in self.buttons:
            button.config(state=state)

        if selected:
            is_cloud = self._is_cloud_project(selected)
            self.local_close_button.config(state="disabled" if is_cloud else "normal")
            self.cloud_close_button.config(state="normal" if is_cloud else "disabled")
            self.local_copy_button.config(
                state="normal" if selected.local_copy_allowed else "disabled"
            )
            self.cloud_copy_button.config(
                state="normal" if selected.cloud_copy_allowed else "disabled"
            )
            self.selection_hint_label.config(text=self._selection_hint(selected))
        else:
            self.selection_hint_label.config(text="")

    def _selection_hint(self, project):
        hints = [f"Projeto {self._project_type(project)} selecionado: {project.folder_name}."]
        if self._is_cloud_project(project):
            hints.append("Fechamento Local indisponível porque o projeto é Cloud.")
        else:
            hints.append("Fechamento Cloud indisponível porque o projeto é Local.")
        if not project.local_copy_allowed:
            hints.append("Copiar Local indisponível: Autcom precisa estar abaixo de 100 MB.")
        if not project.cloud_copy_allowed:
            hints.append("Copiar Cloud indisponível: Autcom precisa estar acima de 200 MB.")
        if project.status != "OK":
            hints.append(f"Detalhe: {self._summarize_project_detail(project.status)}")
        return "\n".join(hints)

    def _summarize_project_detail(self, status):
        parts = [part.strip() for part in status.split(";") if part.strip()]
        if len(parts) <= 4:
            return " | ".join(parts)
        visible_parts = " | ".join(parts[:4])
        remaining = len(parts) - 4
        return f"{visible_parts} | +{remaining} pendência(s). Abra Detalhes para ver tudo."

    def _run_action(self, action):
        selected = self._selected_project()
        if selected:
            action(selected)

    def _is_cloud_project(self, project):
        return project.folder_name.upper().endswith("_CLOUD")

    def open_selected_project_folder(self):
        selected = self._selected_project()
        if not selected:
            return

        if not selected.path.exists():
            messagebox.showerror("Abrir Pasta", f"Pasta nao encontrada:\n{selected.path}")
            return

        os.startfile(selected.path)

    def open_project_details(self):
        selected = self._selected_project()
        if not selected:
            return

        details_window = tk.Toplevel(self)
        details_window.title(f"Detalhes - {selected.folder_name}")
        details_window.geometry("980x360")
        details_window.transient(self)

        header = ttk.Frame(details_window, padding=(12, 10))
        header.pack(fill="x")
        ttk.Label(header, text=f"Projeto: {selected.folder_name}").pack(anchor="w")
        ttk.Label(header, text=f"Pasta: {selected.path}").pack(anchor="w")
        ttk.Label(header, text=f"Status: {selected.status}").pack(anchor="w")
        loading_label = ttk.Label(header, text="Carregando versões dos arquivos...")
        loading_label.pack(anchor="w", pady=(6, 0))

        details_frame = ttk.Frame(details_window)
        details_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        columns = (
            "group",
            "found",
            "source",
            "zip",
            "file_version",
            "product_version",
            "size",
            "status",
        )
        headings = {
            "group": "Grupo",
            "found": "Arquivo",
            "source": "Origem",
            "zip": "Zip",
            "file_version": "FileVersion",
            "product_version": "ProductVersion",
            "size": "MB",
            "status": "Status",
        }
        widths = {
            "group": 110,
            "found": 150,
            "source": 80,
            "zip": 170,
            "file_version": 110,
            "product_version": 120,
            "size": 70,
            "status": 220,
        }
        details_tree = self._build_details_tree(details_frame, columns, headings, widths)

        def worker():
            error = None
            try:
                enrich_project_file_versions(selected)
            except Exception as details_error:
                error = details_error
            try:
                self.after(
                    0,
                    lambda: self._finish_project_details(
                        details_window,
                        loading_label,
                        details_tree,
                        selected,
                        error,
                    ),
                )
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_project_details(self, details_window, loading_label, details_tree, selected, error):
        try:
            if not details_window.winfo_exists():
                return
        except tk.TclError:
            return

        if error:
            loading_label.config(text=f"Erro ao carregar versões: {error}")
            return

        loading_label.config(text="Versões carregadas.")
        for item_id in details_tree.get_children():
            details_tree.delete(item_id)
        for check in self._details_checks(selected, selected.file_checks):
            self._insert_file_check(details_tree, selected, check)

    def _build_details_tree(self, parent, columns, headings, widths):
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        tree = ttk.Treeview(container, columns=columns, show="headings", height=8)
        tree.tag_configure("ok", background="#e9f7ef")
        tree.tag_configure("error", background="#fdecec")
        x_scroll = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
        y_scroll = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)

        for column in columns:
            tree.heading(column, text=headings[column], anchor="center")
            tree.column(column, width=widths[column], anchor="center", stretch=False)

        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        return tree

    def _details_checks(self, project, checks):
        return sorted(
            checks,
            key=lambda check: (
                self._check_display_status(project, check) == "OK",
                check.group_name.lower(),
                (check.found_name or "").lower(),
            ),
        )

    def _check_display_status(self, project, check):
        if check.status != "OK":
            return check.status
        if not check.validate_version:
            return "OK"

        errors = []
        if project.expected_file_version and check.file_version != project.expected_file_version:
            errors.append("FileVersion incorreto")
        if (
            project.expected_product_version
            and check.product_version != project.expected_product_version
        ):
            errors.append("ProductVersion incorreto")
        return "; ".join(errors) if errors else "OK"

    def _insert_file_check(self, tree, project, check):
        display_status = self._check_display_status(project, check)
        tree.insert(
            "",
            "end",
            values=(
                check.group_name,
                check.found_name or "Nao encontrado",
                check.source,
                check.zip_path.name if check.zip_path else "",
                check.file_version or "",
                check.product_version or "",
                "" if check.size_mb is None else f"{check.size_mb:.2f}",
                display_status,
            ),
            tags=("ok" if display_status == "OK" else "error",),
        )
