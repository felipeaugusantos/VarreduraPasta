import os
import queue
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from tkinter import TclError, Toplevel, messagebox, ttk

from app.pattern import save_project_pattern
from app.runtime import application_directory
from app.settings import load_copy_target_directory

LOG_FILE = application_directory() / "logs" / "fechamentos.log"
POLL_INTERVAL_MS = 100


def _is_cloud_project(project):
    return project.folder_name.upper().endswith("_CLOUD")


def _write_log(message):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(f"[{timestamp}] {message}\n")


def _run_closing_bat(project, title):
    commands_path = project.path / "comandosCMD"
    bat_path = commands_path / "_FechamentoArquivos.bat"
    if not commands_path.exists():
        messagebox.showerror(
            title,
            f"Pasta comandosCMD nao encontrada:\n{commands_path}",
        )
        return
    if not bat_path.exists():
        messagebox.showerror(
            title,
            f"BAT nao encontrado:\n{bat_path}",
        )
        return

    progress = Toplevel()
    progress.title(title)
    progress.geometry("580x120")
    progress.resizable(False, False)
    progress.protocol("WM_DELETE_WINDOW", lambda: None)
    progress.grab_set()
    frame = ttk.Frame(progress, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text=f"Gravando padrão de arquivos para {project.folder_name}...",
    ).pack(anchor="w")
    progress_bar = ttk.Progressbar(frame, mode="indeterminate")
    progress_bar.pack(fill="x", pady=(12, 0))
    progress_bar.start(10)

    def worker():
        learn_error = None
        process = None
        start_error = None
        try:
            _learn_project_pattern(project, title)
        except Exception as error:
            learn_error = error

        if learn_error is not None:
            _write_log(
                f"{title} erro ao gravar padrao | projeto={project.folder_name} | "
                f"erro={learn_error}"
            )

        _write_log(f"{title} iniciado | projeto={project.folder_name} | bat={bat_path}")
        try:
            process = subprocess.Popen(
                ["cmd.exe", "/k", str(bat_path)],
                cwd=commands_path,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except OSError as error:
            start_error = error

        _safe_progress_after(
            progress,
            lambda: _finish_closing_start(
                progress,
                project,
                title,
                process,
                learn_error,
                start_error,
            ),
        )

    threading.Thread(target=worker, daemon=True).start()


def _safe_progress_after(progress, callback):
    try:
        progress.after(0, callback)
    except (RuntimeError, TclError):
        pass


def _safe_destroy(window):
    try:
        if window.winfo_exists():
            window.destroy()
    except TclError:
        pass


def _learn_project_pattern(project, title):
    try:
        saved, key = save_project_pattern(project)
    except Exception as error:
        _write_log(
            f"{title} padrao nao gravado | projeto={project.folder_name} | erro={error}"
        )
        return

    if saved:
        _write_log(
            f"{title} padrao gravado | projeto={project.folder_name} | chave={key}"
        )
    else:
        _write_log(
            f"{title} padrao nao gravado | projeto={project.folder_name} | "
            "nenhum arquivo com versao esperada"
        )


def _finish_closing_start(progress, project, title, process, learn_error, start_error):
    _safe_destroy(progress)
    if start_error is not None:
        _write_log(
            f"{title} erro ao iniciar | projeto={project.folder_name} | erro={start_error}"
        )
        messagebox.showerror(title, f"Erro ao executar BAT:\n{start_error}")
        return

    _write_log(f"{title} processo aberto | projeto={project.folder_name} | pid={process.pid}")


def fechamento_local(project):
    if _is_cloud_project(project):
        messagebox.showerror(
            "Fechamento Local",
            "Projeto CLOUD nao pode executar fechamento local.",
        )
        return

    _run_closing_bat(project, "Fechamento Local")


def fechamento_cloud(project):
    if not _is_cloud_project(project):
        messagebox.showerror(
            "Fechamento Cloud",
            "Projeto local nao pode executar fechamento cloud.",
        )
        return

    _run_closing_bat(project, "Fechamento Cloud")


def copiar_local(project):
    if not project.local_copy_allowed:
        messagebox.showerror(
            "Copiar Local",
            "Autcom.exe precisa estar abaixo de 100 MB para copia local.",
        )
        return
    _prepare_copy(project, "Copiar Local")


def copiar_cloud(project):
    if not project.cloud_copy_allowed:
        messagebox.showerror(
            "Copiar Cloud",
            "Autcom.exe precisa estar acima de 200 MB para copia cloud.",
        )
        return
    _prepare_copy(project, "Copiar Cloud")


def _prepare_copy(project, title):
    target_root = load_copy_target_directory()
    if not target_root.exists():
        messagebox.showerror(title, f"Destino de rede nao encontrado:\n{target_root}")
        return

    progress = Toplevel()
    progress.title(title)
    progress.geometry("560x130")
    progress.resizable(False, False)
    progress.protocol("WM_DELETE_WINDOW", lambda: None)
    progress.grab_set()
    frame = ttk.Frame(progress, padding=12)
    frame.pack(fill="both", expand=True)
    message_label = ttk.Label(
        frame,
        text=f"Procurando destino para {project.folder_name}...",
        wraplength=520,
        justify="left",
    )
    message_label.pack(anchor="w")
    progress_bar = ttk.Progressbar(frame, mode="indeterminate")
    progress_bar.pack(fill="x", pady=(12, 0))
    progress_bar.start(10)

    search_queue = queue.Queue()

    def worker():
        destination = _find_destination_folder(
            target_root,
            project.folder_name,
            search_queue,
        )
        search_queue.put(("done", destination))

    def poll():
        scanned_path = None
        outcome = ()
        try:
            while True:
                kind, payload = search_queue.get_nowait()
                if kind == "scanning":
                    scanned_path = payload
                else:
                    outcome = (payload,)
                    break
        except queue.Empty:
            pass

        if not progress.winfo_exists():
            return
        if not outcome:
            if scanned_path is not None:
                message_label.config(text=f"Procurando em:\n{scanned_path}")
            progress.after(POLL_INTERVAL_MS, poll)
            return
        _finish_copy_search(progress, project, outcome[0], title)

    threading.Thread(target=worker, daemon=True).start()
    progress.after(POLL_INTERVAL_MS, poll)


def _find_destination_folder(target_root, folder_name, search_queue=None):
    folder_name_lower = folder_name.lower()

    def on_walk_error(error):
        _write_log(
            f"Busca de destino: erro ao acessar {getattr(error, 'filename', target_root)} | "
            f"erro={error}"
        )

    for current_path, dir_names, _file_names in os.walk(target_root, onerror=on_walk_error):
        if search_queue is not None:
            search_queue.put(("scanning", current_path))
        for dir_name in dir_names:
            if dir_name.lower() == folder_name_lower:
                return Path(current_path) / dir_name
    return None


def _finish_copy_search(progress, project, destination, title):
    _safe_destroy(progress)
    if destination is None:
        messagebox.showerror(
            title,
            "Nao foi encontrada pasta de destino com o mesmo nome do projeto:\n"
            f"{project.folder_name}",
        )
        return

    _show_copy_confirmation(project, destination, title)


def _show_copy_confirmation(project, destination, title):
    window = Toplevel()
    window.title(title)
    window.geometry("760x260")
    window.resizable(False, False)
    window.grab_set()

    frame = ttk.Frame(window, padding=12)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)

    ttk.Label(frame, text="Origem:").grid(row=0, column=0, sticky="nw")
    ttk.Label(frame, text=str(project.path), wraplength=620).grid(
        row=0,
        column=1,
        sticky="w",
        padx=(8, 0),
    )
    ttk.Label(frame, text="Destino:").grid(row=1, column=0, sticky="nw", pady=(12, 0))
    ttk.Label(frame, text=str(destination), wraplength=620).grid(
        row=1,
        column=1,
        sticky="w",
        padx=(8, 0),
        pady=(12, 0),
    )
    ttk.Label(
        frame,
        text="A copia ira substituir arquivos com o mesmo nome no destino.",
    ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(18, 0))

    button_bar = ttk.Frame(frame)
    button_bar.grid(row=3, column=0, columnspan=2, sticky="e", pady=(24, 0))
    ttk.Button(
        button_bar,
        text="Continuar",
        command=lambda: _start_copy(window, project, destination, title),
    ).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(button_bar, text="Cancelar", command=window.destroy).grid(row=0, column=1)


def _start_copy(window, project, destination, title):
    window.destroy()
    progress = Toplevel()
    progress.title(title)
    progress.geometry("560x110")
    progress.resizable(False, False)
    progress.protocol("WM_DELETE_WINDOW", lambda: None)
    progress.grab_set()
    frame = ttk.Frame(progress, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=f"Copiando {project.folder_name}...").pack(anchor="w")
    progress_bar = ttk.Progressbar(frame, mode="indeterminate")
    progress_bar.pack(fill="x", pady=(12, 0))
    progress_bar.start(10)

    def worker():
        try:
            _copy_project_files(project.path, destination)
            error = None
        except OSError as copy_error:
            error = copy_error
        _safe_progress_after(
            progress,
            lambda: _finish_copy(progress, project, destination, title, error),
        )

    threading.Thread(target=worker, daemon=True).start()


def _copy_project_files(source, destination):
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)

    mismatches = _collect_copy_mismatches(source, destination)
    if mismatches:
        preview = ", ".join(mismatches[:5])
        if len(mismatches) > 5:
            preview += f" (+{len(mismatches) - 5})"
        raise OSError(
            f"Verificacao pos-copia encontrou {len(mismatches)} "
            f"arquivo(s) divergente(s) no destino: {preview}"
        )


def _collect_copy_mismatches(source, destination):
    mismatches = []
    for current_path, _dir_names, file_names in os.walk(source):
        relative = Path(current_path).relative_to(source)
        for file_name in file_names:
            source_file = Path(current_path) / file_name
            target_file = destination / relative / file_name
            try:
                if (
                    not target_file.exists()
                    or source_file.stat().st_size != target_file.stat().st_size
                ):
                    mismatches.append(str(relative / file_name))
            except OSError:
                mismatches.append(str(relative / file_name))
    return mismatches


def _finish_copy(progress, project, destination, title, error):
    _safe_destroy(progress)
    if error:
        _write_log(f"{title} erro | projeto={project.folder_name} | destino={destination} | erro={error}")
        messagebox.showerror(title, f"Erro ao copiar arquivos:\n{error}")
        return

    _write_log(f"{title} concluido | projeto={project.folder_name} | destino={destination}")
    messagebox.showinfo(title, f"Copia concluida:\n{destination}")
