import hashlib
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from tkinter import TclError, Toplevel, messagebox, ttk

from app import logging_utils
from app.config import CLOUD_MIN_AUTCOM_MB, LOCAL_MAX_AUTCOM_MB
from app.pattern import save_project_pattern
from app.scanner import validate_zip_names
from app.settings import load_copy_target_directory

POLL_INTERVAL_MS = 100
CRITICAL_COPY_FILES = {"autcom.exe", "autcom.zip"}


def _is_cloud_project(project):
    return project.folder_name.upper().endswith("_CLOUD")


def _current_log_file(now=None):
    return logging_utils.current_log_file(now)


def _write_log(message):
    logging_utils.write_log(message)


def _project_type(project):
    return "Cloud" if _is_cloud_project(project) else "Local"


def _project_autcom_mb(project):
    return (
        f"{project.display_autcom_size_mb:.2f}"
        if project.display_autcom_size_mb is not None
        else ""
    )


def _write_action_audit(
    action,
    result,
    project,
    destination=None,
    bat_path=None,
    reason="",
):
    destination_text = destination if destination is not None else ""
    bat_text = bat_path if bat_path is not None else ""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "desconhecido"
    _write_log(
        f"{action} {result} | usuario={user} | projeto={project.folder_name} | "
        f"tipo={_project_type(project)} | "
        f"origem={project.path} | destino={destination_text} | "
        f"bat={bat_text} | "
        f"fileversion={project.display_file_version or ''} | "
        f"productversion={project.display_product_version or ''} | "
        f"autcom_mb={_project_autcom_mb(project)} | motivo={reason}"
    )


def _run_closing_bat(project, title):
    if _block_on_zip_name_errors(project, title):
        return

    commands_path = project.path / "comandosCMD"
    bat_path = commands_path / "_FechamentoArquivos.bat"
    if not commands_path.exists():
        _write_action_audit(
            title,
            "bloqueado",
            project,
            bat_path=bat_path,
            reason=f"pasta comandosCMD nao encontrada: {commands_path}",
        )
        messagebox.showerror(
            title,
            f"Pasta comandosCMD nao encontrada:\n{commands_path}",
        )
        return
    if not bat_path.exists():
        _write_action_audit(
            title,
            "bloqueado",
            project,
            bat_path=bat_path,
            reason=f"BAT nao encontrado: {bat_path}",
        )
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

    result_queue = queue.Queue()

    def worker():
        learn_error = None
        process = None
        start_error = None
        try:
            _learn_project_pattern(project, title, bat_path)
        except Exception as error:
            learn_error = error

        if learn_error is not None:
            _write_action_audit(
                title,
                "erro_padrao",
                project,
                bat_path=bat_path,
                reason=str(learn_error),
            )

        _write_action_audit(title, "iniciado", project, bat_path=bat_path)
        try:
            process = subprocess.Popen(
                ["cmd.exe", "/k", str(bat_path)],
                cwd=commands_path,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except OSError as error:
            start_error = error

        result_queue.put((process, learn_error, start_error))

    def poll():
        try:
            process, learn_error, start_error = result_queue.get_nowait()
        except queue.Empty:
            if progress.winfo_exists():
                progress.after(POLL_INTERVAL_MS, poll)
            return

        _finish_closing_start(
            progress,
            project,
            title,
            process,
            learn_error,
            start_error,
            bat_path,
        )

    threading.Thread(target=worker, daemon=True).start()
    progress.after(POLL_INTERVAL_MS, poll)


def _safe_destroy(window):
    try:
        if window.winfo_exists():
            window.destroy()
    except TclError:
        pass


def _learn_project_pattern(project, title, bat_path):
    try:
        saved, key = save_project_pattern(project)
    except Exception as error:
        _write_action_audit(
            title,
            "padrao_nao_gravado",
            project,
            bat_path=bat_path,
            reason=str(error),
        )
        return

    if saved:
        _write_action_audit(
            title,
            "padrao_gravado",
            project,
            bat_path=bat_path,
            reason=f"chave={key}",
        )
    else:
        _write_action_audit(
            title,
            "padrao_nao_gravado",
            project,
            bat_path=bat_path,
            reason="nenhum arquivo com versao esperada",
        )


def _finish_closing_start(
    progress,
    project,
    title,
    process,
    learn_error,
    start_error,
    bat_path,
):
    _safe_destroy(progress)
    if start_error is not None:
        _write_action_audit(
            title,
            "erro",
            project,
            bat_path=bat_path,
            reason=f"erro ao iniciar BAT: {start_error}",
        )
        messagebox.showerror(title, f"Erro ao executar BAT:\n{start_error}")
        return

    _write_action_audit(
        title,
        "processo_aberto",
        project,
        bat_path=bat_path,
        reason=f"pid={process.pid}",
    )


def fechamento_local(project):
    if _is_cloud_project(project):
        _write_action_audit(
            "Fechamento Local",
            "bloqueado",
            project,
            reason="projeto CLOUD nao pode usar fechamento local",
        )
        messagebox.showerror(
            "Fechamento Local",
            "Projeto CLOUD nao pode executar fechamento local.",
        )
        return

    _run_closing_bat(project, "Fechamento Local")


def fechamento_cloud(project):
    if not _is_cloud_project(project):
        _write_action_audit(
            "Fechamento Cloud",
            "bloqueado",
            project,
            reason="projeto local nao pode usar fechamento cloud",
        )
        messagebox.showerror(
            "Fechamento Cloud",
            "Projeto local nao pode executar fechamento cloud.",
        )
        return

    _run_closing_bat(project, "Fechamento Cloud")


def copiar_local(project):
    if _is_cloud_project(project):
        _write_action_audit(
            "Copiar Local",
            "bloqueado",
            project,
            reason="projeto CLOUD nao pode usar copia local",
        )
        messagebox.showerror(
            "Copiar Local",
            "Projeto CLOUD nao pode executar copia local.",
        )
        return
    if not project.local_copy_allowed:
        _write_action_audit(
            "Copiar Local",
            "bloqueado",
            project,
            reason=f"Autcom precisa estar abaixo de {LOCAL_MAX_AUTCOM_MB} MB",
        )
        messagebox.showerror(
            "Copiar Local",
            "Autcom.exe precisa estar abaixo de 100 MB para copia local.",
        )
        return
    if _block_on_zip_name_errors(project, "Copiar Local"):
        return
    _prepare_copy(project, "Copiar Local")


def copiar_cloud(project):
    if not _is_cloud_project(project):
        _write_action_audit(
            "Copiar Cloud",
            "bloqueado",
            project,
            reason="projeto local nao pode usar copia cloud",
        )
        messagebox.showerror(
            "Copiar Cloud",
            "Projeto local nao pode executar copia cloud.",
        )
        return
    if not project.cloud_copy_allowed:
        _write_action_audit(
            "Copiar Cloud",
            "bloqueado",
            project,
            reason=f"Autcom precisa estar acima de {CLOUD_MIN_AUTCOM_MB} MB",
        )
        messagebox.showerror(
            "Copiar Cloud",
            "Autcom.exe precisa estar acima de 200 MB para copia cloud.",
        )
        return
    if _block_on_zip_name_errors(project, "Copiar Cloud"):
        return
    _prepare_copy(project, "Copiar Cloud")


def _block_on_zip_name_errors(project, title):
    zip_name_errors = validate_zip_names(project.path)
    if not zip_name_errors:
        return False

    preview = "\n".join(zip_name_errors[:8])
    if len(zip_name_errors) > 8:
        preview += f"\n... +{len(zip_name_errors) - 8} ZIP(s) com nome incorreto"

    _write_action_audit(
        title,
        "bloqueado",
        project,
        reason=f"{len(zip_name_errors)} ZIP(s) com nome incorreto",
    )
    messagebox.showerror(
        title,
        "Operacao bloqueada: existem ZIPs com nome diferente do arquivo interno.\n\n"
        "Corrija os nomes antes de continuar.\n\n"
        f"{preview}",
    )
    return True


def _prepare_copy(project, title):
    target_root = load_copy_target_directory()
    if not target_root.exists():
        _write_action_audit(
            title,
            "bloqueado",
            project,
            reason=f"destino de rede nao encontrado: {target_root}",
        )
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
        destinations = _find_destination_folders(
            target_root,
            project.folder_name,
            search_queue,
        )
        search_queue.put(("done", destinations))

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
        _finish_copy_search(progress, project, target_root, outcome[0], title)

    threading.Thread(target=worker, daemon=True).start()
    progress.after(POLL_INTERVAL_MS, poll)


def _find_destination_folders(target_root, folder_name, search_queue=None):
    folder_name_lower = folder_name.lower()
    destinations = []

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
                destinations.append(Path(current_path) / dir_name)
    return destinations


def _finish_copy_search(progress, project, target_root, destinations, title):
    _safe_destroy(progress)
    if not destinations:
        _write_action_audit(
            title,
            "bloqueado",
            project,
            reason="pasta de destino nao encontrada",
        )
        messagebox.showerror(
            title,
            "Nao foi encontrada pasta de destino com o mesmo nome do projeto:\n"
            f"{project.folder_name}",
        )
        return

    if len(destinations) > 1:
        preview = "\n".join(str(path) for path in destinations[:8])
        if len(destinations) > 8:
            preview += f"\n... +{len(destinations) - 8} destino(s)"
        _write_action_audit(
            title,
            "bloqueado",
            project,
            reason=f"{len(destinations)} destinos encontrados com o mesmo nome",
        )
        messagebox.showerror(
            title,
            "Operacao bloqueada: foi encontrada mais de uma pasta de destino "
            "com o mesmo nome do projeto.\n\n"
            "Revise a rede antes de continuar:\n\n"
            f"{preview}",
        )
        return

    _show_copy_confirmation(project, destinations[0], target_root, title)


def _copy_action_mode(title):
    return "Cloud" if "cloud" in title.lower() else "Local"


def _is_relative_to(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (OSError, ValueError):
        try:
            Path(path).relative_to(Path(parent))
            return True
        except ValueError:
            return False


def _build_copy_safety_checks(project, destination, target_root, title):
    action_mode = _copy_action_mode(title)
    project_mode = "Cloud" if _is_cloud_project(project) else "Local"
    checks = []

    def add(label, ok, detail):
        checks.append((label, ok, detail))

    add(
        "Tipo do projeto",
        action_mode == project_mode,
        f"acao={action_mode}; projeto={project_mode}",
    )
    add(
        "Nome do destino",
        destination.name.lower() == project.folder_name.lower(),
        f"destino={destination.name}; esperado={project.folder_name}",
    )
    add(
        "Destino dentro da raiz configurada",
        _is_relative_to(destination, target_root),
        str(target_root),
    )
    add(
        "FileVersion",
        bool(project.display_file_version)
        and project.display_file_version == project.expected_file_version,
        f"atual={project.display_file_version or 'vazio'}; "
        f"esperado={project.expected_file_version or 'vazio'}",
    )
    add(
        "ProductVersion",
        bool(project.display_product_version)
        and project.display_product_version == project.expected_product_version,
        f"atual={project.display_product_version or 'vazio'}; "
        f"esperado={project.expected_product_version or 'vazio'}",
    )
    add(
        "Autcom MB",
        project.display_autcom_size_mb is not None,
        (
            f"{project.display_autcom_size_mb:.2f} MB"
            if project.display_autcom_size_mb is not None
            else "vazio"
        ),
    )
    if action_mode == "Local":
        add(
            "Limite Local",
            project.local_copy_allowed,
            f"Autcom abaixo de {LOCAL_MAX_AUTCOM_MB} MB",
        )
    else:
        add(
            "Limite Cloud",
            project.cloud_copy_allowed,
            f"Autcom acima de {CLOUD_MIN_AUTCOM_MB} MB",
        )
    add(
        "Status do projeto",
        project.status == "OK",
        project.status,
    )

    zip_name_errors = validate_zip_names(project.path)
    add(
        "Nome dos ZIPs",
        not zip_name_errors,
        "OK" if not zip_name_errors else f"{len(zip_name_errors)} erro(s)",
    )
    return checks


def _copy_block_reasons(checks):
    return [f"{label}: {detail}" for label, ok, detail in checks if not ok]


def _show_copy_confirmation(project, destination, target_root, title):
    safety_checks = _build_copy_safety_checks(project, destination, target_root, title)
    block_reasons = _copy_block_reasons(safety_checks)
    window = Toplevel()
    window.title(title)
    window.geometry("860x430")
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
    ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 0))

    checks_frame = ttk.LabelFrame(frame, text="Validacao de seguranca")
    checks_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    checks_frame.columnconfigure(2, weight=1)
    for row, (label, ok, detail) in enumerate(safety_checks):
        status_text = "OK" if ok else "Bloqueado"
        status_color = "#166534" if ok else "#991b1b"
        ttk.Label(checks_frame, text=label).grid(
            row=row,
            column=0,
            sticky="w",
            padx=(8, 12),
            pady=2,
        )
        ttk.Label(checks_frame, text=status_text, foreground=status_color).grid(
            row=row,
            column=1,
            sticky="w",
            padx=(0, 12),
            pady=2,
        )
        ttk.Label(checks_frame, text=detail, wraplength=540).grid(
            row=row,
            column=2,
            sticky="w",
            pady=2,
        )

    if block_reasons:
        _write_action_audit(
            title,
            "bloqueado",
            project,
            destination,
            reason="; ".join(block_reasons),
        )

    button_bar = ttk.Frame(frame)
    button_bar.grid(row=4, column=0, columnspan=2, sticky="e", pady=(16, 0))
    ttk.Button(
        button_bar,
        text="Continuar",
        command=lambda: _start_copy(window, project, destination, title),
        state="normal" if not block_reasons else "disabled",
    ).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(
        button_bar,
        text="Cancelar",
        command=lambda: _cancel_copy(window, project, destination, title),
    ).grid(row=0, column=1)


def _cancel_copy(window, project, destination, title):
    _write_action_audit(title, "cancelado", project, destination, reason="usuario cancelou")
    window.destroy()


def _start_copy(window, project, destination, title):
    window.destroy()
    _write_action_audit(title, "iniciado", project, destination)
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

    result_queue = queue.Queue()

    def worker():
        copied_files = 0
        hash_summary = {}
        try:
            copied_files, hash_summary = _copy_project_files(project.path, destination)
            error = None
        except OSError as copy_error:
            error = copy_error
        result_queue.put((error, copied_files, hash_summary))

    def poll():
        try:
            error, copied_files, hash_summary = result_queue.get_nowait()
        except queue.Empty:
            if progress.winfo_exists():
                progress.after(POLL_INTERVAL_MS, poll)
            return

        _finish_copy(
            progress,
            project,
            destination,
            title,
            error,
            copied_files,
            hash_summary,
        )

    threading.Thread(target=worker, daemon=True).start()
    progress.after(POLL_INTERVAL_MS, poll)


def _copy_project_files(source, destination):
    copied_files = 0
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
            copied_files += sum(1 for path in item.rglob("*") if path.is_file())
        else:
            shutil.copy2(item, target)
            copied_files += 1

    mismatches = _collect_copy_mismatches(source, destination)
    hash_summary, hash_mismatches = _verify_critical_file_hashes(source, destination)
    mismatches.extend(hash_mismatches)
    if mismatches:
        preview = ", ".join(mismatches[:5])
        if len(mismatches) > 5:
            preview += f" (+{len(mismatches) - 5})"
        raise OSError(
            f"Verificacao pos-copia encontrou {len(mismatches)} "
            f"arquivo(s) divergente(s) no destino: {preview}"
        )
    return copied_files, hash_summary


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


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_critical_file_hashes(source, destination):
    hashes = {}
    mismatches = []
    for current_path, _dir_names, file_names in os.walk(source):
        relative = Path(current_path).relative_to(source)
        for file_name in file_names:
            if file_name.lower() not in CRITICAL_COPY_FILES:
                continue

            source_file = Path(current_path) / file_name
            target_file = destination / relative / file_name
            relative_name = str(relative / file_name)
            try:
                if not target_file.exists():
                    mismatches.append(relative_name)
                    continue
                source_hash = _sha256_file(source_file)
                target_hash = _sha256_file(target_file)
            except OSError:
                mismatches.append(relative_name)
                continue

            hashes[relative_name] = source_hash
            if source_hash != target_hash:
                mismatches.append(relative_name)
    return hashes, mismatches


def _format_hash_summary(hash_summary):
    parts = []
    for relative_name, digest in sorted(hash_summary.items()):
        key = Path(relative_name).name.lower().replace(".", "_")
        parts.append(f"sha256_{key}={digest}")
    return "; ".join(parts)


def _finish_copy(progress, project, destination, title, error, copied_files, hash_summary):
    _safe_destroy(progress)
    if error:
        _write_action_audit(
            title,
            "erro",
            project,
            destination,
            reason=str(error),
        )
        messagebox.showerror(title, f"Erro ao copiar arquivos:\n{error}")
        return

    reason = f"{copied_files} arquivo(s) copiado(s) e verificado(s)"
    hash_text = _format_hash_summary(hash_summary)
    if hash_text:
        reason = f"{reason}; {hash_text}"

    _write_action_audit(
        title,
        "concluido",
        project,
        destination,
        reason=reason,
    )
    messagebox.showinfo(title, f"Copia concluida:\n{destination}")
