import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app import __version__, actions, logging_utils, project_view, theme, zip_memory
from app.audit import filter_log_entries, list_log_files, parse_log_entries, read_log_file
from app.config import (
    CLOSING_MAX_CONCURRENT,
    CLOUD_MIN_AUTCOM_MB,
    COPY_TARGET_DIRECTORIES,
    SCAN_INTERVAL_MS,
    SCRIPT_CHECK_INTERVAL_MS,
    SCRIPT_MONITOR_DIRECTORY,
    SCRIPT_RETRY_INTERVAL_MS,
)
from app.error_handler import ErrorReporter
from app.jenkins import (
    get_jenkins_jobs_status,
    job_label,
    normalize_jenkins_url,
    sort_jobs_for_display,
    test_jenkins_connection,
)
from app.learning import insights_for_project, learn_from_projects
from app.pattern import generate_pattern_from_projects, save_pattern
from app.project_lock import describe_owner, read_owner
from app.runtime import executable_generation_text, resource_path
from app.scanner import enrich_project_file_versions, scan_projects
from app.script_monitor import ScriptMonitorResult, check_scripts, mark_scripts_done
from app.settings import (
    load_additional_copy_target_directories,
    load_base_directory,
    load_ignored_project_folders,
    load_jenkins_config,
    load_script_control_directory,
    load_svn_base_path,
    save_base_directory,
    save_copy_target_directories,
    save_ignored_project_folders,
    save_jenkins_config,
    save_script_control_directory,
    save_svn_base_path,
)
from app.svn_search import search_svn


CLOSING_POLL_MS = 1000
JENKINS_REFRESH_MS = 15 * 1000


class VersionScannerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        # Antes de tudo: erro em botao/thread vai para logs\erros.log e aparece na tela.
        self.error_reporter = ErrorReporter(self).install()
        self.title(f"Sistema de Acompanhamento de Copia v{__version__}")
        self.geometry("1380x820")
        self.minsize(1180, 680)
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
        self.jenkins_window = None
        self.jenkins_jobs_window = None
        self.svn_search_window = None
        self.closing_finished_seen = 0
        self.pending_scripts_count = None
        self.scripts_check_error = ""
        self.first_scan_done = False
        self.projects_shown_once = False
        self.script_monitor_window = None
        self._build_menu()
        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self.refresh)
        self.after(2000, self.check_scripts_periodically)
        self.after(CLOSING_POLL_MS, self._update_closing_column)

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
        monitor_menu.add_command(
            label="Consulta SVN",
            command=self.open_svn_search_window,
        )
        menu_bar.add_cascade(label="Monitoramento", menu=monitor_menu)

        jenkins_menu = tk.Menu(menu_bar, tearoff=False)
        jenkins_menu.add_command(
            label="Configurações",
            command=self.open_jenkins_settings_window,
        )
        jenkins_menu.add_command(
            label="Abrir Jenkins",
            command=self.open_jenkins_url,
        )
        jenkins_menu.add_command(
            label="Status dos Jobs",
            command=self.open_jenkins_jobs_window,
        )
        menu_bar.add_cascade(label="Jenkins", menu=jenkins_menu)

        help_menu = tk.Menu(menu_bar, tearoff=False)
        help_menu.add_command(
            label="Manual de Utilizacao",
            command=self.open_user_manual_window,
        )
        help_menu.add_command(label="Sobre", command=self.open_about_window)
        menu_bar.add_cascade(label="Ajuda", menu=help_menu)
        self.config(menu=menu_bar)

    def _build_layout(self):
        self.fonts = theme.apply_theme(self)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        self._build_header()
        self._build_counter_cards()
        self._build_filter_bar()
        self._build_content()
        self._build_action_groups()
        self._build_status_bar()

        self.buttons = (
            self.local_close_button,
            self.cloud_close_button,
            self.validate_close_button,
            self.clean_folder_button,
            self.local_copy_button,
            self.cloud_copy_button,
            self.autcom_test_button,
            self.details_button,
            self.open_folder_button,
        )
        self._sync_buttons()

    def _build_header(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(20, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(
            title_box,
            text="Acompanhamento de Cópia",
            style="HeaderTitle.TLabel",
        ).pack(anchor="w")
        subtitle = ttk.Frame(title_box, style="Header.TFrame")
        subtitle.pack(anchor="w", pady=(2, 0))
        ttk.Label(
            subtitle,
            text=f"Versão {__version__}   •   Diretório-base:",
            style="HeaderMuted.TLabel",
        ).pack(side="left")
        ttk.Label(
            subtitle,
            textvariable=self.base_directory_var,
            style="HeaderMuted.TLabel",
        ).pack(side="left", padx=(4, 0))

        self.refresh_button = ttk.Button(
            header,
            text="⟳  Atualizar",
            style="Primary.TButton",
            command=self.refresh,
        )
        self.refresh_button.grid(row=0, column=1, sticky="e")

    def _build_counter_cards(self):
        cards = ttk.Frame(self, padding=(16, 14, 16, 4))
        cards.grid(row=1, column=0, sticky="ew")
        self.counter_cards = {}
        definitions = (
            ("total", "Projetos analisados", "primary"),
            ("ok", "OK", "ok"),
            ("pending", "Com pendência", "error"),
            ("closing", "Em fechamento", "closing"),
            ("zip_alert", "Alerta de ZIPs", "zip"),
            ("scripts", "Scripts pendentes", "warn"),
        )
        for column, (key, caption, color) in enumerate(definitions):
            cards.columnconfigure(column, weight=1, uniform="cards")
            card = tk.Frame(
                cards,
                background=theme.COLORS["surface"],
                highlightthickness=1,
                highlightbackground=theme.COLORS["border"],
                highlightcolor=theme.COLORS["border"],
            )
            card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 10, 0))
            strip = tk.Frame(card, background=theme.COLORS[color], width=4)
            strip.pack(side="left", fill="y")
            body = ttk.Frame(card, style="CardBody.TFrame", padding=(12, 8, 12, 10))
            body.pack(side="left", fill="both", expand=True)
            caption_label = ttk.Label(body, text=caption, style="CardCaption.TLabel")
            caption_label.pack(anchor="center")
            value = ttk.Label(body, text="…", style=f"CardValue{color.title()}.TLabel")
            value.pack(anchor="center")
            detail = ttk.Label(body, text="analisando…", style="CardDetail.TLabel")
            detail.pack(anchor="center")
            self.counter_cards[key] = {
                "card": card,
                "strip": strip,
                "value": value,
                "detail": detail,
                "color": color,
            }
            if key == "scripts":
                action = lambda _event: self.open_script_monitor_window()
            else:
                action = lambda _event, card_key=key: self._toggle_card_filter(card_key)
            for widget in (card, strip, body, caption_label, value, detail):
                widget.configure(cursor="hand2")
                widget.bind("<Button-1>", action)

    def _toggle_card_filter(self, key):
        """Clique no cartao filtra a tabela; clicar de novo volta para Todos."""
        target = project_view.CARD_FILTERS[key]
        if self.filter_var.get() == target:
            target = "Todos"
        self.filter_var.set(target)
        self._render_projects()

    def _build_filter_bar(self):
        filter_bar = ttk.Frame(self, padding=(16, 10, 16, 8))
        filter_bar.grid(row=2, column=0, sticky="ew")
        filter_bar.columnconfigure(3, weight=1)

        ttk.Label(filter_bar, text="Filtro", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        filter_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.filter_var,
            state="readonly",
            width=16,
            values=("Todos", "Somente OK", "Com erro", "Em fechamento", "Alerta de ZIPs", "Local", "Cloud"),
        )
        filter_combo.grid(row=0, column=1, sticky="w", padx=(8, 20))
        filter_combo.bind("<<ComboboxSelected>>", lambda _event: self._render_projects())

        ttk.Label(filter_bar, text="Buscar", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        search_entry = ttk.Entry(filter_bar, textvariable=self.search_var)
        search_entry.grid(row=0, column=3, sticky="ew", padx=(8, 8))
        ttk.Button(
            filter_bar,
            text="Limpar",
            command=self._clear_search,
        ).grid(row=0, column=4, sticky="e")
        self.search_var.trace_add("write", lambda *_args: self._render_projects())

    def _build_content(self):
        content = ttk.Frame(self, padding=(16, 0, 16, 0))
        content.grid(row=3, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        table_frame = ttk.Frame(content)
        table_frame.grid(row=0, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = (
            "folder",
            "type",
            "file_version",
            "product_version",
            "autcom_size",
            "zip",
            "status",
            "closing",
        )
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)
        headings = {
            "folder": "Projeto",
            "type": "Tipo",
            "file_version": "FileVersion",
            "product_version": "ProductVersion",
            "autcom_size": "Autcom MB",
            "zip": "Origem",
            "status": "Status",
            "closing": "Fechamento",
        }
        widths = {
            "folder": 215,
            "type": 60,
            "file_version": 105,
            "product_version": 115,
            "autcom_size": 95,
            "zip": 95,
            "status": 150,
            "closing": 165,
        }
        for column in columns:
            self.tree.heading(
                column,
                text=headings[column],
                anchor="center",
                command=lambda current_column=column: self._sort_by_column(current_column),
            )
            self.tree.column(column, width=widths[column], minwidth=60, anchor="center")

        self.tree.tag_configure("ok", background=theme.COLORS["surface"])
        self.tree.tag_configure("warning", background=theme.COLORS["row_warning"])
        self.tree.tag_configure("error", background=theme.COLORS["row_error"])
        # Fechamento gerou menos ZIPs que o maior total ja registrado para a pasta.
        self.tree.tag_configure("zip_below", background=theme.COLORS["row_zip_below"])

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_buttons())
        self.tree.bind("<Double-1>", lambda _event: self.open_selected_project_folder())

        self._build_details_panel(content)

    def _build_details_panel(self, parent):
        panel = tk.Frame(
            parent,
            background=theme.COLORS["surface"],
            highlightthickness=1,
            highlightbackground=theme.COLORS["border"],
            width=330,
        )
        panel.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        panel.grid_propagate(False)
        panel.pack_propagate(False)

        body = ttk.Frame(panel, style="CardBody.TFrame", padding=(16, 14))
        body.pack(fill="both", expand=True)

        self.detail_title = ttk.Label(
            body,
            style="PanelTitle.TLabel",
            wraplength=290,
            justify="left",
        )
        self.detail_title.pack(anchor="w")
        self.detail_badge = ttk.Label(body, style="CardMuted.TLabel", wraplength=290, justify="left")
        self.detail_badge.pack(anchor="w", pady=(2, 10))
        ttk.Separator(body).pack(fill="x")

        fields = ttk.Frame(body, style="CardBody.TFrame")
        fields.pack(fill="x", pady=(10, 10))
        fields.columnconfigure(1, weight=1)
        self.detail_values = {}
        self.detail_expected = {}
        for row, (key, caption) in enumerate(
            (
                ("file_version", "FileVersion"),
                ("product_version", "ProductVersion"),
                ("autcom", "Autcom"),
                ("origin", "Origem"),
                ("closing", "Fechamento"),
            )
        ):
            ttk.Label(fields, text=caption, style="CardMuted.TLabel").grid(
                row=row * 2, column=0, sticky="nw", pady=(4, 0)
            )
            value = ttk.Label(fields, style="Card.TLabel")
            value.grid(row=row * 2, column=1, sticky="w", padx=(12, 0), pady=(4, 0))
            self.detail_values[key] = value
            if key in ("file_version", "product_version"):
                expected = ttk.Label(fields, style="CardMuted.TLabel")
                expected.grid(row=row * 2 + 1, column=1, sticky="w", padx=(12, 0))
                self.detail_expected[key] = expected

        ttk.Separator(body).pack(fill="x")
        ttk.Label(body, text="Observações", style="PanelSection.TLabel").pack(anchor="w", pady=(10, 4))
        self.selection_hint_label = ttk.Label(
            body,
            style="Card.TLabel",
            wraplength=290,
            justify="left",
        )
        self.selection_hint_label.pack(anchor="w", fill="x")

    def _build_action_groups(self):
        bar = ttk.Frame(self, padding=(16, 12, 16, 10))
        bar.grid(row=4, column=0, sticky="ew")

        def group(column, title):
            frame = ttk.LabelFrame(bar, text=f" {title} ", style="Group.TLabelframe", padding=(10, 6, 10, 10))
            frame.grid(row=0, column=column, sticky="w", padx=(0 if column == 0 else 14, 0))
            return frame

        closing = group(0, "Fechamento")
        self.local_close_button = ttk.Button(
            closing,
            text="Fechamento Local",
            style="Primary.TButton",
            command=lambda: self._run_closing(actions.fechamento_local),
        )
        self.cloud_close_button = ttk.Button(
            closing,
            text="Fechamento Cloud",
            style="Primary.TButton",
            command=lambda: self._run_closing(actions.fechamento_cloud),
        )
        self.validate_close_button = ttk.Button(
            closing,
            text="Validar Fechamento",
            command=lambda: self._run_action(actions.validar_pos_fechamento),
        )

        copy = group(1, "Cópia")
        self.local_copy_button = ttk.Button(
            copy,
            text="Copiar Local",
            command=lambda: self._run_action(actions.copiar_local),
        )
        self.cloud_copy_button = ttk.Button(
            copy,
            text="Copiar Cloud",
            command=lambda: self._run_action(actions.copiar_cloud),
        )
        self.clean_folder_button = ttk.Button(
            copy,
            text="Limpar Pasta",
            command=lambda: self._run_action(actions.limpar_pasta),
        )

        project = group(2, "Projeto")
        self.autcom_test_button = ttk.Button(
            project,
            text="Testar Autcom",
            command=lambda: self._run_action(actions.testar_autcom),
        )
        self.details_button = ttk.Button(
            project,
            text="Detalhes",
            command=self.open_project_details,
        )
        self.open_folder_button = ttk.Button(
            project,
            text="Abrir Pasta",
            command=self.open_selected_project_folder,
        )

        for frame in (closing, copy, project):
            for index, button in enumerate(frame.winfo_children()):
                button.grid(row=0, column=index, padx=(0 if index == 0 else 6, 0))

    def _build_status_bar(self):
        status_bar = tk.Frame(
            self,
            background=theme.COLORS["surface"],
            highlightthickness=1,
            highlightbackground=theme.COLORS["border"],
        )
        status_bar.grid(row=5, column=0, sticky="ew")
        self.status_label = ttk.Label(status_bar, text="", style="Status.TLabel", padding=(16, 6))
        self.status_label.pack(side="left")
        ttk.Label(
            status_bar,
            text=f"Fechamentos simultâneos: até {CLOSING_MAX_CONCURRENT}",
            style="Status.TLabel",
            padding=(16, 6),
        ).pack(side="right")

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
        else:
            try:
                learn_from_projects(projects)
            except Exception as learning_error:
                try:
                    logging_utils.write_log(
                        f"APRENDIZADO_LOCAL FALHA erro={learning_error}"
                    )
                except OSError:
                    pass

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
        self.first_scan_done = True
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
                    project_view.version_display(
                        project.display_file_version,
                        project.expected_file_version,
                    ),
                    project_view.version_display(
                        project.display_product_version,
                        project.expected_product_version,
                    ),
                    size,
                    self._short_zip_status(project),
                    project_view.status_display(project),
                    self._closing_text(project),
                ),
                tags=(self._row_tag(project),),
            )

        self.status_label.config(text=self._summary_text(filtered_projects))
        self.projects_shown_once = self.projects_shown_once or self.first_scan_done
        self._update_counters()
        self._sync_buttons()

    def _update_counters(self):
        running, queued = actions.CLOSING_MANAGER.running_and_queued()
        loaded = self.first_scan_done and not (self.is_scanning and not self.projects_shown_once)
        cards = project_view.dashboard_cards(
            self.projects,
            loaded=loaded,
            closing_running=running,
            closing_queued=queued,
            zip_alerts=sum(1 for project in self.projects if self._zip_record_below_max(project)),
            pending_scripts=self.pending_scripts_count,
            scripts_error=self.scripts_check_error,
        )
        active_filter = self.filter_var.get()
        for key, widgets in self.counter_cards.items():
            if self.is_scanning and self.projects_shown_once and key in ("total", "ok", "pending", "zip_alert"):
                # Durante as varreduras seguintes, mantem os numeros anteriores.
                continue
            value_text, detail_text, emphasized = cards[key]
            color = widgets["color"]
            widgets["value"].config(
                text=value_text,
                style=f"CardValue{color.title()}.TLabel" if emphasized else "CardValueMuted.TLabel",
            )
            widgets["detail"].config(text=detail_text)
            widgets["strip"].config(
                background=theme.COLORS[color] if emphasized else theme.COLORS["border_strong"]
            )
        for key, widgets in self.counter_cards.items():
            selected = project_view.CARD_FILTERS.get(key) == active_filter and active_filter != "Todos"
            widgets["card"].config(
                highlightthickness=2 if selected else 1,
                highlightbackground=theme.COLORS["primary"] if selected else theme.COLORS["border"],
            )

    def _clear_search(self):
        self.search_var.set("")

    def _matches_search(self, project):
        return project_view.matches_search(project, self.search_var.get())

    def _matches_filter(self, project):
        selected = self.filter_var.get()
        if selected == "Em fechamento":
            return actions.CLOSING_MANAGER.is_active(project)
        if selected == "Alerta de ZIPs":
            return self._zip_record_below_max(project) is not None
        return project_view.matches_filter(project, selected)

    def _short_status(self, project):
        return project_view.short_status(project)

    def _status_category(self, project):
        return project_view.status_category(project)

    def _status_tag(self, project):
        return project_view.status_tag(project)

    def _zip_record_below_max(self, project):
        record = zip_memory.get_record(project.folder_name)
        return record if record is not None and record.below_max else None

    def _row_tag(self, project):
        if not actions.CLOSING_MANAGER.is_active(project) and self._zip_record_below_max(project):
            return "zip_below"
        return self._status_tag(project)

    def _other_operator_lock(self, project):
        """Trava de outro sistema (outro operador do TS) neste projeto, se houver."""
        if actions.CLOSING_MANAGER.is_active(project):
            return None
        try:
            return read_owner(project.path)
        except OSError:
            return None

    def _closing_text(self, project):
        text = actions.CLOSING_MANAGER.progress_text(project)
        if actions.CLOSING_MANAGER.is_active(project):
            return text
        owner = self._other_operator_lock(project)
        if owner is not None:
            return f"Em uso: {owner.get('action', '')} ({owner.get('user', '')})"
        record = self._zip_record_below_max(project)
        if record is None:
            return text
        zips_text = f"{record.last_zips}/{record.max_zips} ZIPs"
        return f"{text} ({zips_text})" if text else zips_text

    def _short_zip_status(self, project):
        return project_view.short_zip_status(project)

    def _project_type(self, project):
        return project_view.project_type(project)

    def _summary_text(self, projects):
        return project_view.summary_text(projects, len(self.projects))

    def _sort_value(self, project, column):
        return project_view.sort_value(project, column)

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
        theme.size_window(self.config_window, 820, 420)
        self.config_window.resizable(False, False)
        self.config_window.transient(self)

        frame = ttk.Frame(self.config_window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        settings_base_var = tk.StringVar(value=self.base_directory_var.get())
        settings_copy_var = tk.StringVar()
        settings_svn_var = tk.StringVar(value=load_svn_base_path())
        settings_control_var = tk.StringVar(value=str(load_script_control_directory()))

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

        ttk.Label(frame, text="Caminho base SVN:").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(12, 0),
        )
        ttk.Entry(frame, textvariable=settings_svn_var).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(8, 8),
            pady=(12, 0),
        )
        ttk.Button(
            frame,
            text="Procurar",
            command=lambda: self.choose_base_directory(settings_svn_var),
        ).grid(row=1, column=2, sticky="e", pady=(12, 0))

        ttk.Label(frame, text="Controle de scripts:").grid(
            row=2,
            column=0,
            sticky="w",
            pady=(12, 0),
        )
        ttk.Entry(frame, textvariable=settings_control_var).grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(8, 8),
            pady=(12, 0),
        )
        ttk.Button(
            frame,
            text="Procurar",
            command=lambda: self.choose_base_directory(settings_control_var),
        ).grid(row=2, column=2, sticky="e", pady=(12, 0))
        ttk.Label(
            frame,
            text="Pasta compartilhada onde todos os operadores gravam os scripts já feitos.",
            style="Muted.TLabel",
        ).grid(row=3, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(2, 0))

        ttk.Label(frame, text="Destinos padrão:").grid(
            row=4,
            column=0,
            sticky="nw",
            pady=(12, 0),
        )
        ttk.Label(
            frame,
            text="\n".join(str(path) for path in COPY_TARGET_DIRECTORIES),
            wraplength=620,
        ).grid(row=4, column=1, columnspan=2, sticky="w", pady=(12, 0))

        ttk.Label(frame, text="Destino adicional:").grid(
            row=5,
            column=0,
            sticky="w",
            pady=(12, 0),
        )
        ttk.Entry(frame, textvariable=settings_copy_var).grid(
            row=5,
            column=1,
            sticky="ew",
            padx=(8, 8),
            pady=(12, 0),
        )

        ttk.Button(
            frame,
            text="Procurar",
            command=lambda: self.choose_base_directory(settings_copy_var),
        ).grid(row=5, column=2, sticky="e", pady=(12, 0))

        ttk.Label(frame, text="Destinos extras:").grid(
            row=6,
            column=0,
            sticky="nw",
            pady=(8, 0),
        )
        target_list = tk.Listbox(frame, height=5)
        target_list.grid(row=6, column=1, sticky="ew", padx=(8, 8), pady=(8, 0))
        for target in load_additional_copy_target_directories():
            target_list.insert(tk.END, str(target))

        target_buttons = ttk.Frame(frame)
        target_buttons.grid(row=6, column=2, sticky="n", pady=(8, 0))

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
        button_bar.grid(row=7, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(
            button_bar,
            text="Salvar",
            command=lambda: self.save_settings(
                settings_base_var.get(),
                [target_list.get(index) for index in range(target_list.size())],
                settings_svn_var.get(),
                settings_control_var.get(),
            ),
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            button_bar,
            text="Cancelar",
            command=self.config_window.destroy,
        ).grid(row=0, column=1)

    def open_jenkins_settings_window(self):
        if self.jenkins_window and self.jenkins_window.winfo_exists():
            self.jenkins_window.focus()
            return

        config = load_jenkins_config()
        self.jenkins_window = tk.Toplevel(self)
        self.jenkins_window.title("Jenkins")
        theme.size_window(self.jenkins_window, 620, 220)
        self.jenkins_window.resizable(False, False)
        self.jenkins_window.transient(self)

        frame = ttk.Frame(self.jenkins_window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        url_var = tk.StringVar(value=config["url"])
        username_var = tk.StringVar(value=config["username"])
        password_var = tk.StringVar(value=config["password"])
        status_var = tk.StringVar(
            value="Disponivel apenas na maquina onde o Jenkins local esta rodando."
        )

        ttk.Label(frame, text="URL Jenkins:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=url_var).grid(
            row=0,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(8, 0),
        )

        ttk.Label(frame, text="Usuario:").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(10, 0),
        )
        ttk.Entry(frame, textvariable=username_var).grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(8, 0),
            pady=(10, 0),
        )

        ttk.Label(frame, text="Senha:").grid(
            row=2,
            column=0,
            sticky="w",
            pady=(10, 0),
        )
        ttk.Entry(frame, textvariable=password_var, show="*").grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(8, 0),
            pady=(10, 0),
        )

        ttk.Label(frame, textvariable=status_var, wraplength=560).grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(14, 0),
        )

        button_bar = ttk.Frame(frame)
        button_bar.grid(row=4, column=0, columnspan=3, sticky="e", pady=(18, 0))

        test_button = ttk.Button(button_bar, text="Testar conexao")
        test_button.grid(row=0, column=0, padx=(0, 8))

        ttk.Button(
            button_bar,
            text="Abrir Jenkins",
            command=lambda: webbrowser.open(normalize_jenkins_url(url_var.get())),
        ).grid(row=0, column=1, padx=(0, 8))

        ttk.Button(
            button_bar,
            text="Salvar",
            command=lambda: self.save_jenkins_settings(
                url_var.get(),
                username_var.get(),
                password_var.get(),
            ),
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(
            button_bar,
            text="Cancelar",
            command=self.jenkins_window.destroy,
        ).grid(row=0, column=3)

        def test_connection():
            result_queue = queue.Queue()
            test_button.config(state="disabled")
            status_var.set("Testando conexao com Jenkins...")

            def worker():
                result_queue.put(
                    test_jenkins_connection(
                        url_var.get(),
                        username_var.get(),
                        password_var.get(),
                    )
                )

            def poll():
                try:
                    ok, message = result_queue.get_nowait()
                except queue.Empty:
                    if self.jenkins_window and self.jenkins_window.winfo_exists():
                        self.jenkins_window.after(100, poll)
                    return

                result = "online" if ok else "erro"
                self._write_jenkins_audit(
                    "teste_conexao",
                    result,
                    normalize_jenkins_url(url_var.get()),
                    message,
                )
                status_var.set(message)
                test_button.config(state="normal")
                if ok:
                    messagebox.showinfo("Jenkins", message)
                else:
                    messagebox.showwarning("Jenkins", message)

            threading.Thread(target=worker, daemon=True).start()
            self.jenkins_window.after(100, poll)

        test_button.config(command=test_connection)

    def save_jenkins_settings(self, url, username, password):
        normalized_url = normalize_jenkins_url(url)
        save_jenkins_config(normalized_url, username, password)
        self._write_jenkins_audit(
            "configuracao",
            "salvo",
            normalized_url,
            f"usuario={username or ''}",
        )
        if self.jenkins_window and self.jenkins_window.winfo_exists():
            self.jenkins_window.destroy()
        messagebox.showinfo("Jenkins", "Configuracao do Jenkins salva.")

    def open_jenkins_url(self):
        config = load_jenkins_config()
        url = normalize_jenkins_url(config["url"])
        self._write_jenkins_audit("abrir", "solicitado", url, "navegador")
        webbrowser.open(url)

    def open_jenkins_jobs_window(self):
        if self.jenkins_jobs_window and self.jenkins_jobs_window.winfo_exists():
            self.jenkins_jobs_window.focus()
            return

        window = tk.Toplevel(self)
        self.jenkins_jobs_window = window
        window.title("Jenkins - Status dos Jobs")
        theme.size_window(window, 980, 460)
        window.transient(self)

        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        status_var = tk.StringVar(value="Consultando jobs do Jenkins...")
        ttk.Label(frame, textvariable=status_var).grid(row=0, column=0, sticky="w")

        columns = ("job", "status", "build", "duracao", "data", "url")
        headings = {
            "job": "Job",
            "status": "Situação",
            "build": "Build",
            "duracao": "Duração",
            "data": "Data",
            "url": "URL",
        }
        widths = {"job": 210, "status": 190, "build": 70, "duracao": 100, "data": 150, "url": 230}
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for column in columns:
            anchor = "w" if column in ("job", "status", "url") else "center"
            tree.heading(column, text=headings[column], anchor=anchor)
            tree.column(column, width=widths[column], anchor=anchor)
        tree.tag_configure("failure", background=theme.COLORS["row_error"])
        tree.tag_configure("unstable", background=theme.COLORS["row_warning"])
        tree.tag_configure("building", background=theme.COLORS["row_info"])
        tree.tag_configure("aborted", foreground=theme.COLORS["muted"])
        tree.tag_configure("never", foreground=theme.COLORS["muted"])
        tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        tree.configure(yscrollcommand=scrollbar.set)

        button_bar = ttk.Frame(frame)
        button_bar.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))

        refresh_button = ttk.Button(button_bar, text="Atualizar")
        refresh_button.grid(row=0, column=0, padx=(0, 8))

        def open_selected_build():
            selection = tree.selection()
            if not selection:
                messagebox.showwarning("Jenkins", "Selecione um job para abrir.", parent=window)
                return
            values = tree.item(selection[0], "values")
            build_url = values[5] if len(values) >= 6 else ""
            if not build_url:
                messagebox.showwarning("Jenkins", "Este job nao possui build para abrir.", parent=window)
                return
            self._write_jenkins_audit("abrir_build", "solicitado", build_url, values[0])
            webbrowser.open(build_url)

        ttk.Button(
            button_bar,
            text="Abrir Build",
            command=open_selected_build,
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            button_bar,
            text="Fechar",
            command=window.destroy,
        ).grid(row=0, column=2)

        state = {"running": False}

        def render(jobs):
            selected = set(tree.selection())
            tree.delete(*tree.get_children())
            for job in sort_jobs_for_display(jobs):
                iid = job["name"] or str(id(job))
                tree.insert(
                    "",
                    "end",
                    iid=iid,
                    values=(
                        job["name"],
                        job_label(job),
                        job["number"],
                        job["duration"],
                        job["timestamp"],
                        job["url"],
                    ),
                    tags=(job.get("state", "unknown"),),
                )
                if iid in selected:
                    tree.selection_add(iid)

        def summary(jobs):
            failures = sum(1 for job in jobs if job.get("state") == "failure")
            building = sum(1 for job in jobs if job.get("state") == "building")
            parts = [f"{len(jobs)} job(s)"]
            if failures:
                parts.append(f"{failures} com falha")
            if building:
                parts.append(f"{building} em andamento")
            parts.append(
                f"atualizado às {datetime.now():%H:%M:%S} "
                f"(automático a cada {JENKINS_REFRESH_MS // 1000} s)"
            )
            return "   •   ".join(parts)

        def refresh_jobs(manual=True):
            if state["running"] or not window.winfo_exists():
                return
            state["running"] = True
            config = load_jenkins_config()
            result_queue = queue.Queue()
            refresh_button.config(state="disabled")
            if manual:
                status_var.set("Consultando jobs do Jenkins...")

            def worker():
                try:
                    result = get_jenkins_jobs_status(
                        config["url"],
                        config["username"],
                        config["password"],
                    )
                except Exception as error:
                    result = (False, f"Nao foi possivel consultar o Jenkins: {error}", [])
                result_queue.put(result)

            def poll():
                if not window.winfo_exists():
                    return
                try:
                    ok, message, jobs = result_queue.get_nowait()
                except queue.Empty:
                    window.after(100, poll)
                    return

                state["running"] = False
                refresh_button.config(state="normal")
                if ok:
                    render(jobs)
                    status_var.set(summary(jobs))
                else:
                    # Consulta automatica nao abre mensagem: so mostra o erro na tela.
                    status_var.set(f"{message}   •   nova tentativa em {JENKINS_REFRESH_MS // 1000} s")
                    if manual:
                        messagebox.showwarning("Jenkins", message, parent=window)

            threading.Thread(target=worker, daemon=True).start()
            window.after(100, poll)

        def auto_refresh():
            if not window.winfo_exists():
                return
            refresh_jobs(manual=False)
            window.after(JENKINS_REFRESH_MS, auto_refresh)

        refresh_button.config(command=refresh_jobs)
        refresh_jobs()
        window.after(JENKINS_REFRESH_MS, auto_refresh)

    def _write_jenkins_audit(self, action, result, url, reason):
        # Mesmo padrao das demais linhas ("<acao> <resultado> | campo=valor"),
        # para a tela de Auditoria mostrar a acao (ex.: "Jenkins teste_conexao").
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "desconhecido"
        logging_utils.write_log(
            f"Jenkins {action} {result} | usuario={user} | destino={url} | motivo={reason}"
        )

    def open_pattern_window(self):
        pattern_window = tk.Toplevel(self)
        pattern_window.title("Padrão de Arquivos")
        theme.size_window(pattern_window, 760, 170)
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
        theme.size_window(ignored_window, 560, 420)
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
        theme.size_window(audit_window, 1180, 560)
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

    def check_scripts_periodically(self):
        """Verifica a pasta de scripts a cada 30 min (15 min se a rede falhar)."""

        def schedule_next(result):
            delay = SCRIPT_RETRY_INTERVAL_MS if result.error else SCRIPT_CHECK_INTERVAL_MS
            self.after(delay, self.check_scripts_periodically)

        self._run_script_monitor_check(show_without_news=False, on_result=schedule_next)

    def _run_script_monitor_check(self, show_without_news=True, on_result=None):
        result_queue = queue.Queue()

        def worker():
            try:
                result = check_scripts()
            except Exception as error:
                result = ScriptMonitorResult(
                    directory=SCRIPT_MONITOR_DIRECTORY,
                    control_directory=load_script_control_directory(),
                    error=f"Erro ao verificar scripts: {error}",
                )
            result_queue.put(result)

        def poll():
            try:
                result = result_queue.get_nowait()
            except queue.Empty:
                if self.winfo_exists():
                    self.after(100, poll)
                return
            finish(result)

        def finish(result):
            self.scripts_check_error = result.error
            if not result.error:
                self.pending_scripts_count = len(result.pending)
            self._update_counters()
            if on_result:
                on_result(result)
            if result.error:
                if show_without_news:
                    messagebox.showerror("Scripts Banco Modelo", result.error)
                return
            if result.new_pending:
                preview = "\n".join(result.new_pending[:20])
                if len(result.new_pending) > 20:
                    preview += f"\n... +{len(result.new_pending) - 20} script(s)"
                messagebox.showwarning(
                    "Scripts Banco Modelo",
                    "Novos scripts para rodar no banco modelo:\n\n"
                    f"{preview}\n\n"
                    "Abra Monitoramento > Scripts Banco Modelo para marcar cada um "
                    "como feito.",
                )

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, poll)

    def open_script_monitor_window(self):
        if self.script_monitor_window and self.script_monitor_window.winfo_exists():
            self.script_monitor_window.focus()
            return

        monitor_window = tk.Toplevel(self)
        self.script_monitor_window = monitor_window
        monitor_window.title("Scripts Banco Modelo")
        theme.size_window(monitor_window, 960, 620)
        monitor_window.transient(self)

        frame = ttk.Frame(monitor_window, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=3)
        frame.rowconfigure(6, weight=2)

        path_var = tk.StringVar(value=str(SCRIPT_MONITOR_DIRECTORY))
        control_var = tk.StringVar(value=str(load_script_control_directory()))
        status_var = tk.StringVar(value="Verificando scripts...")

        ttk.Label(frame, text="Pasta de scripts", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=path_var).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(frame, text="Controle compartilhado", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Label(frame, textvariable=control_var).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(4, 0))
        ttk.Label(frame, textvariable=status_var, font=self.fonts["section"]).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(12, 6)
        )

        def build_table(row, columns, headings, widths, height, selectmode):
            table_frame = ttk.Frame(frame)
            table_frame.grid(row=row, column=0, columnspan=2, sticky="nsew")
            table_frame.columnconfigure(0, weight=1)
            table_frame.rowconfigure(0, weight=1)
            table = ttk.Treeview(
                table_frame, columns=columns, show="headings", height=height, selectmode=selectmode
            )
            for column in columns:
                anchor = "w" if column in ("script", "detail") else "center"
                table.heading(column, text=headings[column], anchor=anchor)
                table.column(column, width=widths[column], anchor=anchor, stretch=column in ("script", "detail"))
            scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
            table.configure(yscrollcommand=scrollbar.set)
            table.grid(row=0, column=0, sticky="nsew")
            scrollbar.grid(row=0, column=1, sticky="ns")
            return table

        pending_table = build_table(
            3,
            ("script", "modified"),
            {"script": "Script pendente", "modified": "Data do arquivo"},
            {"script": 680, "modified": 150},
            10,
            "extended",
        )
        ttk.Label(
            frame,
            text="Selecione um ou mais scripts (Ctrl/Shift) para marcar como feito. "
            "Duplo clique abre o script.",
            style="Muted.TLabel",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Label(frame, text="Últimos registros (todos os operadores)", font=self.fonts["section"]).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(14, 6)
        )
        history_table = build_table(
            6,
            ("when", "user", "script", "event"),
            {"when": "Data/hora", "user": "Operador", "script": "Script", "event": "Registro"},
            {"when": 150, "user": 130, "script": 470, "event": 120},
            6,
            "none",
        )

        def update_result(result):
            if not monitor_window.winfo_exists():
                return
            path_var.set(str(result.directory))
            control_var.set(str(result.control_directory))
            pending_table.delete(*pending_table.get_children())
            history_table.delete(*history_table.get_children())
            if result.error:
                status_var.set(result.error)
                return
            for script in result.pending:
                pending_table.insert("", "end", iid=script.name, values=(script.name, script.modified))
            for event in result.history:
                history_table.insert(
                    "", "end", values=(event.timestamp, event.user, event.script, event.event)
                )
            if result.pending:
                status_var.set(f"{len(result.pending)} script(s) pendente(s) de {result.total_files} na pasta.")
            else:
                status_var.set(f"Nenhum script pendente. {result.total_files} script(s) na pasta.")

        def verify_now():
            status_var.set("Verificando scripts...")
            self._run_script_monitor_check(show_without_news=True, on_result=update_result)

        def mark_selected_done():
            selected = list(pending_table.selection())
            if not selected:
                messagebox.showinfo(
                    "Scripts Banco Modelo",
                    "Selecione na lista os scripts que ja foram rodados no banco modelo.",
                    parent=monitor_window,
                )
                return
            preview = "\n".join(selected[:15])
            if len(selected) > 15:
                preview += f"\n... +{len(selected) - 15} script(s)"
            if not messagebox.askyesno(
                "Scripts Banco Modelo",
                f"Marcar {len(selected)} script(s) como feito para todos os operadores?\n\n{preview}",
                parent=monitor_window,
            ):
                return
            try:
                marked, skipped = mark_scripts_done(selected)
            except OSError as error:
                messagebox.showerror(
                    "Scripts Banco Modelo",
                    f"Nao foi possivel gravar no controle compartilhado:\n{error}",
                    parent=monitor_window,
                )
                return
            if skipped:
                messagebox.showinfo(
                    "Scripts Banco Modelo",
                    "Estes scripts ja tinham sido marcados por outro operador:\n\n"
                    + "\n".join(skipped[:15]),
                    parent=monitor_window,
                )
            verify_now()

        def open_selected_script(_event=None):
            selected = pending_table.selection()
            if not selected:
                return
            script_path = Path(path_var.get()) / selected[0]
            if script_path.exists():
                os.startfile(script_path)

        def open_folder():
            directory = Path(path_var.get())
            if not directory.exists():
                messagebox.showerror(
                    "Scripts Banco Modelo",
                    f"Pasta nao encontrada:\n{directory}",
                    parent=monitor_window,
                )
                return
            os.startfile(directory)

        pending_table.bind("<Double-1>", open_selected_script)

        button_bar = ttk.Frame(frame)
        button_bar.grid(row=7, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(
            button_bar,
            text="Marcar selecionados como feito",
            style="Primary.TButton",
            command=mark_selected_done,
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_bar, text="Abrir script", command=open_selected_script).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(button_bar, text="Abrir pasta", command=open_folder).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(button_bar, text="Verificar agora", command=verify_now).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(button_bar, text="Fechar", command=monitor_window.destroy).grid(row=0, column=4)

        # Abrir a tela so consulta; nao grava "verificado" na Auditoria.
        self._run_script_monitor_check(show_without_news=False, on_result=update_result)

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
        frame.rowconfigure(4, weight=3)
        frame.rowconfigure(5, weight=2)
        frame.columnconfigure(1, weight=1)

        base_var = tk.StringVar(value=load_svn_base_path())
        revision_var = tk.StringVar()
        requirement_var = tk.StringVar()
        find_all_var = tk.BooleanVar(value=False)
        status_var = tk.StringVar(value="Informe uma revisao, um requisito ou os dois.")

        ttk.Label(frame, text="Caminho base SVN:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=base_var).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(8, 8),
        )
        ttk.Button(
            frame,
            text="Salvar base",
            command=lambda: self._save_svn_base_from_window(base_var.get(), status_var),
        ).grid(row=0, column=2, sticky="e")

        ttk.Label(frame, text="Revisao:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        revision_entry = ttk.Entry(frame, textvariable=revision_var, width=24)
        revision_entry.grid(
            row=1,
            column=1,
            sticky="w",
            padx=(8, 8),
            pady=(10, 0),
        )
        ttk.Checkbutton(
            frame,
            text="Buscar todas as ocorrencias",
            variable=find_all_var,
        ).grid(row=1, column=2, sticky="e", pady=(10, 0))

        ttk.Label(frame, text="Requisito:").grid(row=2, column=0, sticky="w", pady=(10, 0))
        requirement_entry = ttk.Entry(frame, textvariable=requirement_var)
        requirement_entry.grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(8, 0),
            pady=(10, 0),
        )

        ttk.Label(frame, textvariable=status_var).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(10, 8),
        )
        search_progress = ttk.Progressbar(
            frame,
            mode="indeterminate",
            length=180,
        )
        search_progress.grid(
            row=3,
            column=2,
            sticky="e",
            pady=(10, 8),
        )
        search_progress.grid_remove()

        result_frame = ttk.LabelFrame(frame, text="Resultados")
        result_frame.grid(row=4, column=0, columnspan=3, sticky="nsew")
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
        detail_frame.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
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
        button_bar.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))
        consult_button = ttk.Button(button_bar, text="Consultar", command=consult)
        consult_button.grid(
            row=0,
            column=0,
            padx=(0, 8),
        )
        cancel_button = ttk.Button(
            button_bar,
            text="Cancelar",
            command=cancel_search,
            state="disabled",
        )
        cancel_button.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(button_bar, text="Copiar Revisao", command=copy_selected_revision).grid(
            row=0,
            column=2,
            padx=(0, 8),
        )
        ttk.Button(button_bar, text="Copiar Caminho", command=copy_selected_path).grid(
            row=0,
            column=3,
            padx=(0, 8),
        )
        ttk.Button(button_bar, text="Abrir Base", command=open_base).grid(
            row=0,
            column=4,
            padx=(0, 8),
        )
        ttk.Button(button_bar, text="Fechar", command=close_svn_window).grid(
            row=0,
            column=5,
        )
        self.svn_search_window.protocol("WM_DELETE_WINDOW", close_svn_window)

    def _save_svn_base_from_window(self, svn_base_path, status_var):
        save_svn_base_path(svn_base_path)
        status_var.set("Caminho base SVN salvo.")

    def open_user_manual_window(self):
        manual_window = tk.Toplevel(self)
        manual_window.title("Manual de Utilizacao")
        theme.size_window(manual_window, 900, 620)
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
            "comandosCMD\\_FechamentoArquivos.bat em segundo plano, sem janela.\n"
            "- E possivel selecionar varios projetos do mesmo tipo (Ctrl/Shift + clique); "
            "os BATs rodam ate 2 por vez e os demais aguardam na fila.\n"
            "- A coluna Fechamento mostra Na fila, Em andamento com o percentual ja "
            "compactado (pelo tamanho dos arquivos), Concluido com o tempo total, ou Erro. "
            "A saida de cada BAT fica em logs\\fechamentos_bat.\n"
            "- O sistema guarda, por pasta, o maior total de ZIPs ja gerado. Se um "
            "fechamento gerar menos ZIPs, a linha fica amarela e a coluna mostra, por "
            "exemplo, (149/150 ZIPs).\n"
            "- Quando a fila termina, a tabela e atualizada; use Validar Fechamento para "
            "registrar a versao/status final na Auditoria.\n\n"
            "4. Copiar Local e Copiar Cloud\n"
            "- Limpar Pasta remove os arquivos da raiz do projeto preservando comandosCMD.\n"
            "- Copiar Local exige Autcom abaixo de 100 MB.\n"
            f"- Copiar Cloud exige Autcom a partir de {CLOUD_MIN_AUTCOM_MB} MB.\n"
            "- A copia envia somente arquivos .zip para o destino.\n"
            "- Apos a copia ser verificada, somente os ZIPs copiados sao removidos da origem; "
            "os demais itens sao mantidos e listados para conferencia.\n"
            "- O caminho do destino e copiado automaticamente para a area de transferencia.\n"
            "- Se houver mais de um destino na rede, o sistema mostra uma tela para escolher "
            "o caminho correto antes de continuar.\n\n"
            "5. Testar Autcom\n"
            "- Disponivel para projeto com status OK.\n"
            "- Usa as partes 5 e 6 da versao para localizar PastaTesteCopia correspondente.\n"
            "- Sincroniza os ZIPs na pasta exe, executa CopiaArquivos.bat e abre o Autcom "
            "da raiz da pasta de teste.\n"
            "- Preenche usuario 998, senha 998 e empresa 001 e aciona LOGIN.\n"
            "- O resultado confirma a tela principal responsiva ou informa "
            "falha/inconclusivo.\n\n"
            "6. Configuracoes\n"
            "- Diretorio-base: define a pasta analisada.\n"
            "- Caminho base SVN: define o repositorio ou working copy usado na Consulta SVN.\n"
            "- Destinos extras: adiciona novas raizes de busca alem dos destinos padrao.\n"
            "- Padrao de Arquivos: permite aprender o padrao esperado a partir de pastas modelo.\n"
            "- Pastas Ignoradas: define pastas que nao devem aparecer na varredura.\n\n"
            "7. Jenkins\n"
            "- Jenkins > Configuracoes salva URL, usuario e senha do Jenkins local.\n"
            "- Jenkins > Abrir Jenkins abre o painel configurado no navegador.\n"
            "- Jenkins > Status dos Jobs mostra o ultimo build de cada job.\n"
            "- O teste de conexao registra o resultado na Auditoria sem gravar senha.\n\n"
            "8. Auditoria\n"
            "- Auditoria > Logs exibe os registros mensais em tabela.\n"
            "- Use Buscar e Resultado para filtrar acoes, erros, bloqueios e conclusoes.\n"
            "- Os logs ficam em %LOCALAPPDATA%\\VarreduraSistema\\logs.\n\n"
            "9. Monitoramento de Scripts\n"
            "- Monitoramento > Scripts Banco Modelo verifica a pasta de scripts do banco modelo.\n"
            "- A verificacao acontece uma vez ao dia ao abrir o sistema.\n"
            "- Tambem e possivel clicar em Verificar agora.\n"
            "- Quando houver scripts novos, eles ficam pendentes ate clicar em Marcar como feito.\n"
            "- O sistema apenas avisa quando existe script novo; nenhum script e executado automaticamente.\n"
            "- Monitoramento > Consulta SVN busca historico por revisao, requisito ou ambos.\n"
            "- A consulta usa o comando svn quando disponivel; para pasta local, tambem busca "
            "o requisito em nomes e conteudo de arquivos.\n"
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

    def save_settings(
        self,
        base_directory,
        copy_target_directories,
        svn_base_path="",
        script_control_directory="",
    ):
        self.base_directory_var.set(base_directory.strip())
        save_base_directory(self.base_directory_var.get())
        save_copy_target_directories(copy_target_directories)
        save_svn_base_path(svn_base_path)
        save_script_control_directory(script_control_directory)
        if self.config_window and self.config_window.winfo_exists():
            self.config_window.destroy()
        self.refresh()

    def _selected_projects(self):
        return [
            self.displayed_projects[int(item_id)]
            for item_id in self.tree.selection()
            if int(item_id) < len(self.displayed_projects)
        ]

    def _selected_project(self):
        selected = self._selected_projects()
        return selected[0] if len(selected) == 1 else None

    def _sync_buttons(self):
        selected_projects = self._selected_projects()
        if len(selected_projects) > 1:
            self._sync_buttons_for_multiple(selected_projects)
            return

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
            self.autcom_test_button.config(
                state="normal" if selected.status == "OK" else "disabled"
            )
            if actions.CLOSING_MANAGER.is_active(selected) or self._other_operator_lock(selected):
                for button in self._buttons_blocked_while_closing():
                    button.config(state="disabled")
            self.selection_hint_label.config(text=self._selection_hint(selected))
            self._show_project_details(selected)
        else:
            self.selection_hint_label.config(text="")
            self._show_empty_details(
                "Nenhum projeto selecionado",
                "Selecione um projeto na tabela para ver os detalhes. "
                "Use Ctrl ou Shift para selecionar vários e fechar em lote.",
            )

    def _show_empty_details(self, title, subtitle):
        self.detail_title.config(text=title)
        self.detail_badge.config(text=subtitle, style="CardMuted.TLabel")
        for key, label in self.detail_values.items():
            label.config(text="—", style="Card.TLabel")
        for label in self.detail_expected.values():
            label.grid_remove()

    def _show_project_details(self, project):
        category = project_view.status_category(project)
        badge_style = {
            "ok": "CardOk.TLabel",
            "missing": "CardError.TLabel",
            "version": "CardError.TLabel",
            "zip": "CardError.TLabel",
        }.get(category, "CardWarn.TLabel")
        self.detail_title.config(text=project.folder_name)
        self.detail_badge.config(
            text=f"{self._project_type(project)}   •   {project_view.status_display(project)}",
            style=badge_style,
        )
        for key, found, expected in (
            ("file_version", project.display_file_version, project.expected_file_version),
            ("product_version", project.display_product_version, project.expected_product_version),
        ):
            mismatch = bool(found and expected and found != expected)
            self.detail_values[key].config(
                text=found or "—",
                style="CardError.TLabel" if mismatch else "Card.TLabel",
            )
            if mismatch:
                self.detail_expected[key].config(text=f"esperado {expected}")
                self.detail_expected[key].grid()
            else:
                self.detail_expected[key].grid_remove()

        size = project.display_autcom_size_mb
        self.detail_values["autcom"].config(
            text="—" if size is None else f"{size:.2f} MB",
            style="Card.TLabel",
        )
        self.detail_values["origin"].config(text=self._short_zip_status(project), style="Card.TLabel")
        closing_text = self._closing_text(project) or "—"
        if self._row_tag(project) == "zip_below":
            closing_style = "CardZip.TLabel"
        elif actions.CLOSING_MANAGER.is_active(project):
            closing_style = "CardClosing.TLabel"
        else:
            closing_style = "Card.TLabel"
        self.detail_values["closing"].config(text=closing_text, style=closing_style)

    def _buttons_blocked_while_closing(self):
        return (
            self.local_close_button,
            self.cloud_close_button,
            self.validate_close_button,
            self.clean_folder_button,
            self.local_copy_button,
            self.cloud_copy_button,
            self.autcom_test_button,
        )

    def _selection_hint(self, project):
        hints = []
        owner = self._other_operator_lock(project)
        if owner is not None:
            hints.append(
                f"Projeto em uso: {describe_owner(owner)}. Fechamento, cópia, "
                "limpeza, teste e validação ficam bloqueados até terminar."
            )
        if actions.CLOSING_MANAGER.is_active(project):
            hints.append(
                "Fechamento em andamento: Copiar, Limpar Pasta, Testar Autcom e "
                "Validar Fechamento ficam bloqueados até a coluna Fechamento "
                "mostrar Concluído."
            )
        if self._is_cloud_project(project):
            hints.append("Fechamento Local indisponível porque o projeto é Cloud.")
        else:
            hints.append("Fechamento Cloud indisponível porque o projeto é Local.")
        if not project.local_copy_allowed:
            hints.append("Copiar Local indisponível: Autcom precisa estar abaixo de 100 MB.")
        if not project.cloud_copy_allowed:
            hints.append(
                "Copiar Cloud indisponível: "
                f"Autcom precisa ter pelo menos {CLOUD_MIN_AUTCOM_MB} MB."
            )
        if project.status != "OK":
            hints.append(f"Pendência: {self._summarize_project_detail(project.status)}")
        zip_record = self._zip_record_below_max(project)
        if zip_record is not None and not actions.CLOSING_MANAGER.is_active(project):
            hints.append(
                f"Atenção: o último fechamento gerou {zip_record.last_zips} ZIP(s), "
                f"mas esta pasta já gerou {zip_record.max_zips}. "
                "Confira se falta algum arquivo."
            )
        closing_job = actions.CLOSING_MANAGER.job_for(project)
        if closing_job is not None and closing_job.log_path is not None:
            hints.append(f"Log do último fechamento: {closing_job.log_path}")
        return "\n\n".join(f"• {hint}" for hint in hints)

    def _summarize_project_detail(self, status):
        parts = [part.strip() for part in status.split(";") if part.strip()]
        if len(parts) <= 4:
            return " | ".join(parts)
        visible_parts = " | ".join(parts[:4])
        remaining = len(parts) - 4
        return f"{visible_parts} | +{remaining} pendência(s). Abra Detalhes para ver tudo."

    def _sync_buttons_for_multiple(self, projects):
        for button in self.buttons:
            button.config(state="disabled")
        cloud_count = sum(1 for project in projects if self._is_cloud_project(project))
        local_count = len(projects) - cloud_count
        if cloud_count == 0:
            self.local_close_button.config(state="normal")
        if local_count == 0:
            self.cloud_close_button.config(state="normal")

        self._show_empty_details(
            f"{len(projects)} projetos selecionados",
            f"{local_count} Local   •   {cloud_count} Cloud",
        )
        hints = []
        if local_count and cloud_count:
            hints.append(
                "Para fechar varios projetos de uma vez, selecione somente Local "
                "ou somente Cloud."
            )
        else:
            hints.append(
                "Com varios projetos, somente o fechamento fica disponivel. "
                f"Os BATs rodam em segundo plano, ate {CLOSING_MAX_CONCURRENT} por vez; "
                "acompanhe na coluna Fechamento."
            )
        self.selection_hint_label.config(text="\n\n".join(f"• {hint}" for hint in hints))

    def _run_action(self, action):
        selected = self._selected_project()
        if selected:
            action(selected)

    def _run_closing(self, action):
        selected = self._selected_projects()
        if not selected:
            return
        enqueued, _blocked = action(selected)
        if enqueued:
            self.status_label.config(
                text=f"{len(enqueued)} fechamento(s) adicionado(s) a fila."
            )
        self._update_closing_column(reschedule=False)

    def _update_closing_column(self, reschedule=True):
        manager = actions.CLOSING_MANAGER
        for item_id in self.tree.get_children():
            index = int(item_id)
            if index < len(self.displayed_projects):
                project = self.displayed_projects[index]
                self.tree.set(item_id, "closing", self._closing_text(project))
                self.tree.item(item_id, tags=(self._row_tag(project),))

        # Quando a fila esvazia, atualiza a varredura para mostrar as versoes finais.
        if (
            manager.finished_count != self.closing_finished_seen
            and manager.active_count() == 0
            and not self.is_scanning
        ):
            self.closing_finished_seen = manager.finished_count
            self.refresh()

        # Libera ou bloqueia os botoes conforme o fechamento do projeto selecionado.
        self._update_counters()
        self._sync_buttons()

        if reschedule:
            self.after(CLOSING_POLL_MS, self._update_closing_column)

    def _on_close(self):
        active = actions.CLOSING_MANAGER.active_count()
        if active and not messagebox.askyesno(
            "Sair",
            f"Ha {active} fechamento(s) na fila ou em andamento.\n\n"
            "Se sair agora, os que estao na fila nao serao iniciados e os que estao "
            "rodando continuam sem acompanhamento nem registro final na Auditoria.\n\n"
            "Deseja sair mesmo assim?",
        ):
            return
        self.destroy()

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
        theme.size_window(details_window, 980, 390)
        details_window.transient(self)

        header = ttk.Frame(details_window, padding=(12, 10))
        header.pack(fill="x")
        ttk.Label(header, text=f"Projeto: {selected.folder_name}").pack(anchor="w")
        ttk.Label(header, text=f"Pasta: {selected.path}").pack(anchor="w")
        ttk.Label(header, text=f"Status: {selected.status}").pack(anchor="w")
        loading_label = ttk.Label(header, text="Carregando versões dos arquivos...")
        loading_label.pack(anchor="w", pady=(6, 0))
        insight_label = ttk.Label(
            header,
            text="Aprendizado local: analisando historico...",
        )
        insight_label.pack(anchor="w", pady=(4, 0))

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
            insights = []
            try:
                enrich_project_file_versions(selected)
            except Exception as details_error:
                error = details_error
            if error is None:
                try:
                    insights = insights_for_project(selected)
                except Exception:
                    insights = []
            try:
                self.after(
                    0,
                    lambda: self._finish_project_details(
                        details_window,
                        loading_label,
                        insight_label,
                        details_tree,
                        selected,
                        error,
                        insights,
                    ),
                )
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_project_details(
        self,
        details_window,
        loading_label,
        insight_label,
        details_tree,
        selected,
        error,
        insights,
    ):
        try:
            if not details_window.winfo_exists():
                return
        except tk.TclError:
            return

        if error:
            loading_label.config(text=f"Erro ao carregar versões: {error}")
            insight_label.config(text="Aprendizado local: indisponivel.")
            return

        loading_label.config(text="Versões carregadas.")
        if insights:
            insight = insights[0]
            insight_label.config(
                text=(
                    f"Sugestao local ({insight.confidence:.0%} de confianca): "
                    f"{insight.message}"
                )
            )
        else:
            insight_label.config(
                text="Aprendizado local: nenhuma anomalia estatistica identificada."
            )
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
