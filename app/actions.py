import hashlib
import json
import os
import queue
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from tkinter import Listbox, TclError, Toplevel, messagebox, ttk

from app import logging_utils, theme, zip_memory
from app.autcom_test import resolve_test_layout, run_autcom_test
from app.closing_runner import (
    PENDING_SUFFIXES,
    ClosingManager,
    format_elapsed,
    pending_file_sizes,
    run_closing_bat,
    zipped_percent,
)
from app.config import (
    CLOSING_MAX_CONCURRENT,
    CLOSING_TIMEOUT_SECONDS,
    CLOUD_MIN_AUTCOM_MB,
    LOCAL_MAX_AUTCOM_MB,
)
from app.pattern import save_project_pattern
from app.scanner import validate_zip_names
from app.settings import load_copy_target_directories

POLL_INTERVAL_MS = 100
CLEANUP_RETRY_ATTEMPTS = 5
CLEANUP_RETRY_DELAY_SECONDS = 1
_PATTERN_LOCK = threading.Lock()


class CleanupError(OSError):
    def __init__(self, message, cleaned_items=0):
        super().__init__(message)
        self.cleaned_items = cleaned_items


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



def _closing_block_reason(project, expect_cloud):
    if _is_cloud_project(project) != expect_cloud:
        if expect_cloud:
            return "projeto local nao pode usar fechamento cloud"
        return "projeto CLOUD nao pode usar fechamento local"
    if CLOSING_MANAGER.is_active(project):
        return "fechamento ja esta na fila ou em andamento"

    zip_name_errors = validate_zip_names(project.path)
    if zip_name_errors:
        return (
            f"{len(zip_name_errors)} ZIP(s) com nome incorreto: "
            + "; ".join(zip_name_errors[:3])
        )

    commands_path = project.path / "comandosCMD"
    bat_path = commands_path / "_FechamentoArquivos.bat"
    if not commands_path.exists():
        return f"pasta comandosCMD nao encontrada: {commands_path}"
    if not bat_path.exists():
        return f"BAT nao encontrado: {bat_path}"
    return None


def _start_closings(projects, title, expect_cloud):
    """Valida os projetos e coloca os aptos na fila de fechamento.

    Retorna (nomes enfileirados, [(nome, motivo)] bloqueados). So mostra
    mensagem quando algum projeto foi bloqueado.
    """
    if not isinstance(projects, (list, tuple)):
        projects = [projects]

    enqueued = []
    blocked = []
    for project in projects:
        bat_path = project.path / "comandosCMD" / "_FechamentoArquivos.bat"
        reason = _closing_block_reason(project, expect_cloud)
        if reason is None and not CLOSING_MANAGER.enqueue(project, title, bat_path):
            reason = "fechamento ja esta na fila ou em andamento"
        if reason is not None:
            _write_action_audit(
                title,
                "bloqueado",
                project,
                bat_path=bat_path,
                reason=reason,
            )
            blocked.append((project.folder_name, reason))
            continue
        _write_action_audit(title, "na_fila", project, bat_path=bat_path)
        enqueued.append(project.folder_name)

    if blocked:
        details = "\n".join(f"- {name}: {reason}" for name, reason in blocked[:10])
        if len(blocked) > 10:
            details += f"\n... +{len(blocked) - 10} projeto(s)"
        if enqueued:
            messagebox.showwarning(
                title,
                f"{len(enqueued)} fechamento(s) adicionado(s) a fila.\n\n"
                f"Projetos bloqueados:\n{details}",
            )
        else:
            messagebox.showerror(
                title,
                f"Fechamento nao iniciado.\n\n{details}",
            )
    return enqueued, blocked


def _closing_log_path(project, now=None):
    now = now or datetime.now()
    return (
        logging_utils.current_log_file(now).parent
        / "fechamentos_bat"
        / f"{project.folder_name}_{now:%Y%m%d_%H%M%S}.log"
    )


def _execute_closing_job(job):
    """Roda na thread do fechamento: aprende o padrao, executa o BAT e audita."""
    project = job.project
    started = time.monotonic()
    with _PATTERN_LOCK:
        try:
            _learn_project_pattern(project, job.title, job.bat_path)
        except Exception as error:
            _write_action_audit(
                job.title,
                "erro_padrao",
                project,
                bat_path=job.bat_path,
                reason=str(error),
            )

    job.log_path = _closing_log_path(project)
    _write_action_audit(
        job.title,
        "iniciado",
        project,
        bat_path=job.bat_path,
        reason=f"versao antes do fechamento; log={job.log_path}",
    )

    # O percentual compara o que ainda falta compactar com o tamanho inicial.
    try:
        initial_sizes = pending_file_sizes(project.path)
    except OSError:
        initial_sizes = {}
    job.percent = 0

    def update_percent():
        job.percent = zipped_percent(initial_sizes, pending_file_sizes(project.path))

    try:
        return_code = run_closing_bat(
            job.bat_path,
            job.log_path,
            CLOSING_TIMEOUT_SECONDS,
            on_tick=update_percent,
        )
    except Exception as error:
        _write_action_audit(
            job.title,
            "erro",
            project,
            bat_path=job.bat_path,
            reason=f"{error}; log={job.log_path}",
        )
        raise

    # O _FechamentoArquivos.bat sai com codigo 1 quando conclui (exit apos um
    # dir sem resultado) e com 0 quando estoura o proprio limite. Por isso o
    # resultado segue o criterio do BAT: nenhum exe/dll/bpl pendente na pasta.
    duration = format_elapsed(time.monotonic() - started)
    reason = f"codigo_bat={return_code}; duracao={duration}; log={job.log_path}"
    try:
        pending_files = _pending_closing_files(project.path)
    except OSError as error:
        _write_action_audit(
            job.title,
            "erro",
            project,
            bat_path=job.bat_path,
            reason=f"nao foi possivel conferir a pasta: {error}; {reason}",
        )
        raise
    if pending_files:
        job.percent = zipped_percent(
            initial_sizes,
            {name.lower() for name in pending_files},
        )
        preview = ", ".join(pending_files[:5])
        if len(pending_files) > 5:
            preview += f" +{len(pending_files) - 5}"
        _write_action_audit(
            job.title,
            "erro",
            project,
            bat_path=job.bat_path,
            reason=f"{len(pending_files)} arquivo(s) nao compactado(s): {preview}; {reason}",
        )
        raise OSError(f"{len(pending_files)} arquivo(s) nao compactado(s): {preview}")

    # Memoria por pasta: alerta quando este fechamento gera menos ZIPs que o
    # maior total ja registrado para a mesma pasta.
    try:
        job.zip_record = zip_memory.record_closing(
            project.folder_name,
            zip_memory.count_zip_files(project.path),
        )
        reason = (
            f"zips={job.zip_record.last_zips}; "
            f"maior_registrado={job.zip_record.max_zips}; {reason}"
        )
    except (OSError, ValueError) as error:
        reason = f"memoria_zips_falhou={error}; {reason}"

    _write_action_audit(
        job.title,
        "concluido_abaixo_maximo"
        if job.zip_record is not None and job.zip_record.below_max
        else "concluido",
        project,
        bat_path=job.bat_path,
        reason=reason,
    )
    return 0


def _pending_closing_files(project_path):
    return sorted(
        item.name
        for item in Path(project_path).iterdir()
        if item.is_file() and item.suffix.lower() in PENDING_SUFFIXES
    )


CLOSING_MANAGER = ClosingManager(_execute_closing_job, max_concurrent=CLOSING_MAX_CONCURRENT)


def fechamento_local(projects):
    return _start_closings(projects, "Fechamento Local", expect_cloud=False)


def fechamento_cloud(projects):
    return _start_closings(projects, "Fechamento Cloud", expect_cloud=True)


def _block_if_closing_active(project, title):
    """Impede acoes no projeto enquanto o BAT ainda esta gerando os ZIPs."""
    if not CLOSING_MANAGER.is_active(project):
        return False
    progress = CLOSING_MANAGER.progress_text(project)
    _write_action_audit(
        title,
        "bloqueado",
        project,
        reason=f"fechamento na fila ou em andamento ({progress})",
    )
    messagebox.showerror(
        title,
        "O fechamento deste projeto ainda nao terminou.\n\n"
        f"Situacao: {progress}\n\n"
        "Aguarde a coluna Fechamento mostrar Concluido.",
    )
    return True


def validar_pos_fechamento(project):
    if _block_if_closing_active(project, "Validar Fechamento"):
        return
    result = "validado_ok" if project.status == "OK" else "validado_pendente"
    _write_action_audit(
        "Validar Fechamento",
        result,
        project,
        reason=f"status_atual={project.status}",
    )
    messagebox.showinfo(
        "Validar Fechamento",
        "Validacao registrada na Auditoria.\n\n"
        f"Projeto: {project.folder_name}\n"
        f"FileVersion: {project.display_file_version or ''}\n"
        f"ProductVersion: {project.display_product_version or ''}\n"
        f"Status: {project.status}",
    )


def limpar_pasta(project):
    if _block_if_closing_active(project, "Limpar Pasta"):
        return
    if not project.path.exists():
        _write_action_audit(
            "Limpar Pasta",
            "bloqueado",
            project,
            reason=f"pasta nao encontrada: {project.path}",
        )
        messagebox.showerror(
            "Limpar Pasta",
            f"Pasta nao encontrada:\n{project.path}",
        )
        return

    confirmed = messagebox.askyesno(
        "Limpar Pasta",
        "Deseja limpar a pasta do projeto?\n\n"
        f"Projeto: {project.folder_name}\n"
        f"Pasta: {project.path}\n\n"
        "Sera preservada a pasta comandosCMD.\n"
        "Todos os demais arquivos e pastas da raiz serao removidos.",
    )
    if not confirmed:
        _write_action_audit(
            "Limpar Pasta",
            "cancelado",
            project,
            reason="usuario cancelou",
        )
        return

    _start_manual_cleanup(project)


def _start_manual_cleanup(project):
    progress = Toplevel()
    progress.title("Limpar Pasta")
    theme.size_window(progress, 560, 120)
    progress.resizable(False, False)
    progress.protocol("WM_DELETE_WINDOW", lambda: None)
    progress.grab_set()
    frame = ttk.Frame(progress, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text=f"Limpando pasta {project.folder_name}...",
    ).pack(anchor="w")
    ttk.Label(
        frame,
        text="A pasta comandosCMD sera preservada.",
    ).pack(anchor="w", pady=(4, 0))
    progress_bar = ttk.Progressbar(frame, mode="indeterminate")
    progress_bar.pack(fill="x", pady=(12, 0))
    progress_bar.start(10)

    result_queue = queue.Queue()

    def worker():
        cleaned_items = 0
        cleanup_error = None
        try:
            cleaned_items = _cleanup_source_after_copy(project.path)
        except CleanupError as error:
            cleaned_items = error.cleaned_items
            cleanup_error = error
        except Exception as error:
            cleanup_error = error
        result_queue.put((cleaned_items, cleanup_error))

    def poll():
        try:
            cleaned_items, cleanup_error = result_queue.get_nowait()
        except queue.Empty:
            if progress.winfo_exists():
                progress.after(POLL_INTERVAL_MS, poll)
            return

        _finish_manual_cleanup(progress, project, cleaned_items, cleanup_error)

    threading.Thread(target=worker, daemon=True).start()
    progress.after(POLL_INTERVAL_MS, poll)


def _finish_manual_cleanup(progress, project, cleaned_items, cleanup_error):
    _safe_destroy(progress)
    if isinstance(cleanup_error, CleanupError):
        _write_action_audit(
            "Limpar Pasta",
            "erro",
            project,
            reason=f"{cleanup_error}; removido={cleanup_error.cleaned_items} item(ns)",
        )
        messagebox.showwarning(
            "Limpar Pasta",
            "A limpeza falhou parcialmente e deve ser conferida manualmente.\n\n"
            f"{cleanup_error}",
        )
        return

    if cleanup_error is not None:
        _write_action_audit(
            "Limpar Pasta",
            "erro",
            project,
            reason=str(cleanup_error),
        )
        messagebox.showerror(
            "Limpar Pasta",
            f"Nao foi possivel limpar a pasta:\n{cleanup_error}",
        )
        return

    _write_action_audit(
        "Limpar Pasta",
        "concluido",
        project,
        reason=f"{cleaned_items} item(ns) removido(s); preservado=comandosCMD",
    )
    messagebox.showinfo(
        "Limpar Pasta",
        "Limpeza concluida.\n\n"
        f"{cleaned_items} item(ns) removido(s).\n"
        "A pasta comandosCMD foi preservada.",
    )


def copiar_local(project):
    if _block_if_closing_active(project, "Copiar Local"):
        return
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
    if _block_if_closing_active(project, "Copiar Cloud"):
        return
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
            reason=f"Autcom precisa ter pelo menos {CLOUD_MIN_AUTCOM_MB} MB",
        )
        messagebox.showerror(
            "Copiar Cloud",
            f"Autcom.exe precisa ter pelo menos {CLOUD_MIN_AUTCOM_MB} MB "
            "para copia cloud.",
        )
        return
    if _block_on_zip_name_errors(project, "Copiar Cloud"):
        return
    _prepare_copy(project, "Copiar Cloud")


def testar_autcom(project):
    title = "Testar Abertura do Autcom"
    if _block_if_closing_active(project, title):
        return
    if project.status != "OK":
        _write_action_audit(
            title,
            "bloqueado",
            project,
            reason=f"projeto com pendencias: {project.status}",
        )
        messagebox.showerror(
            title,
            "O teste exige que o projeto esteja com status OK.",
        )
        return
    if _block_on_zip_name_errors(project, title):
        return

    try:
        layout = resolve_test_layout(project.path)
    except ValueError as error:
        _write_action_audit(title, "bloqueado", project, reason=str(error))
        messagebox.showerror(title, str(error))
        return

    if not messagebox.askyesno(
        title,
        "Os arquivos ZIP da versao serao sincronizados com a pasta de teste.\n"
        "Os ZIPs antigos existentes na pasta exe serao substituidos.\n\n"
        "O Autcom sera aberto e autenticado com as credenciais de teste.\n\n"
        f"Projeto:\n{project.path}\n\n"
        f"Pasta de teste:\n{layout.root}\n\n"
        "Deseja continuar?",
    ):
        _write_action_audit(
            title,
            "cancelado",
            project,
            destination=layout.root,
            bat_path=layout.bat_path,
            reason="operador cancelou a preparacao",
        )
        return

    progress = Toplevel()
    progress.title(title)
    theme.size_window(progress, 600, 140)
    progress.resizable(False, False)
    progress.protocol("WM_DELETE_WINDOW", lambda: None)
    progress.grab_set()
    frame = ttk.Frame(progress, padding=12)
    frame.pack(fill="both", expand=True)
    message_label = ttk.Label(
        frame,
        text=f"Preparando {layout.root.name}...",
        wraplength=560,
        justify="left",
    )
    message_label.pack(anchor="w")
    progress_bar = ttk.Progressbar(frame, mode="indeterminate")
    progress_bar.pack(fill="x", pady=(14, 0))
    progress_bar.start(10)
    result_queue = queue.Queue()

    def worker():
        try:
            result = run_autcom_test(
                project.path,
                progress_callback=lambda message: result_queue.put(
                    ("progress", message)
                ),
            )
            result_queue.put(("done", result))
        except Exception as error:
            result_queue.put(("error", error))

    def poll():
        try:
            kind, payload = result_queue.get_nowait()
        except queue.Empty:
            if progress.winfo_exists():
                progress.after(POLL_INTERVAL_MS, poll)
            return

        if kind == "progress":
            message_label.config(text=payload)
            progress.after(POLL_INTERVAL_MS, poll)
            return

        progress_bar.stop()
        _safe_destroy(progress)
        if kind == "error":
            _write_action_audit(
                title,
                "erro",
                project,
                destination=layout.root,
                bat_path=layout.bat_path,
                reason=str(payload),
            )
            messagebox.showerror(title, f"O teste falhou:\n\n{payload}")
            return

        result = payload
        reason = (
            f"status={result.status}; bat_codigo={result.bat_return_code}; "
            f"pid={result.process_id}; janela={result.window_title}; "
            f"detalhe={result.message}"
        )
        _write_action_audit(
            title,
            result.status.lower(),
            project,
            destination=layout.root,
            bat_path=layout.bat_path,
            reason=reason,
        )
        message = (
            f"Resultado: {result.status}\n\n{result.message}\n\n"
            f"Pasta de teste:\n{layout.root}"
        )
        if result.window_title:
            message += f"\n\nJanela: {result.window_title}"
        if result.ok:
            messagebox.showinfo(title, message)
        elif result.status == "INCONCLUSIVO":
            messagebox.showwarning(title, message)
        else:
            messagebox.showerror(title, message)

    threading.Thread(target=worker, daemon=True).start()
    progress.after(POLL_INTERVAL_MS, poll)


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
    target_roots = load_copy_target_directories()
    available_target_roots = [root for root in target_roots if root.exists()]
    if not available_target_roots:
        targets_text = "\n".join(str(root) for root in target_roots)
        _write_action_audit(
            title,
            "bloqueado",
            project,
            reason=f"destinos de rede nao encontrados: {targets_text}",
        )
        messagebox.showerror(
            title,
            "Nenhum destino de rede configurado foi encontrado:\n\n"
            f"{targets_text}",
        )
        return

    progress = Toplevel()
    progress.title(title)
    theme.size_window(progress, 560, 130)
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
            available_target_roots,
            project.folder_name,
            title=title,
            search_queue=search_queue,
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
        _finish_copy_search(progress, project, available_target_roots, outcome[0], title)

    threading.Thread(target=worker, daemon=True).start()
    progress.after(POLL_INTERVAL_MS, poll)


def _expected_destination_names(folder_name, title=None):
    if title is None:
        title = (
            "Copiar Cloud"
            if folder_name.upper().endswith("_CLOUD")
            else "Copiar Local"
        )

    names = [folder_name]
    if _copy_action_mode(title) == "Local":
        local_name = f"{folder_name}_LOCAL"
        if local_name.lower() not in {name.lower() for name in names}:
            names.append(local_name)
    return names


def _find_destination_folders(target_root, folder_name, search_queue=None, title=None):
    expected_names_lower = {
        name.lower() for name in _expected_destination_names(folder_name, title)
    }
    target_roots = _as_path_list(target_root)
    destinations = []

    def on_walk_error(root):
        def write_error(error):
            _write_log(
                f"Busca de destino: erro ao acessar {getattr(error, 'filename', root)} | "
                f"erro={error}"
            )
        return write_error

    for root in target_roots:
        for current_path, dir_names, _file_names in os.walk(root, onerror=on_walk_error(root)):
            if search_queue is not None:
                search_queue.put(("scanning", current_path))
            for dir_name in dir_names:
                if dir_name.lower() in expected_names_lower:
                    destinations.append(Path(current_path) / dir_name)
    return destinations


def _as_path_list(paths):
    if isinstance(paths, (str, Path)):
        return [Path(paths)]
    return [Path(path) for path in paths]


def _format_target_roots(target_roots):
    return "\n".join(str(root) for root in _as_path_list(target_roots))


def _is_relative_to_any(path, parents):
    return any(_is_relative_to(path, parent) for parent in _as_path_list(parents))


def _find_target_root_for_destination(destination, target_roots):
    for root in _as_path_list(target_roots):
        if _is_relative_to(destination, root):
            return root
    return None


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
        _write_action_audit(
            title,
            "destinos_encontrados",
            project,
            reason=f"{len(destinations)} destinos encontrados; aguardando escolha",
        )
        _show_destination_choice(project, target_root, destinations, title)
        return

    _show_copy_confirmation(project, destinations[0], target_root, title)


def _show_destination_choice(project, target_root, destinations, title):
    window = Toplevel()
    window.title(f"{title} - escolher destino")
    theme.size_window(window, 920, 360)
    window.resizable(False, False)
    window.grab_set()

    frame = ttk.Frame(window, padding=12)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)

    ttk.Label(
        frame,
        text=(
            "Foi encontrada mais de uma pasta de destino para este projeto. "
            "Selecione o destino correto antes de continuar."
        ),
        wraplength=880,
    ).grid(row=0, column=0, sticky="w")

    listbox = Listbox(frame, height=10)
    listbox.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
    for destination in destinations:
        listbox.insert(tk.END, str(destination))
    listbox.selection_set(0)

    button_bar = ttk.Frame(frame)
    button_bar.grid(row=2, column=0, sticky="e", pady=(14, 0))

    def continue_with_selected():
        selection = listbox.curselection()
        if not selection:
            messagebox.showerror(title, "Selecione um destino para continuar.")
            return
        destination = destinations[selection[0]]
        _write_action_audit(
            title,
            "destino_escolhido",
            project,
            destination,
            reason=f"{len(destinations)} destinos encontrados",
        )
        window.destroy()
        _show_copy_confirmation(project, destination, target_root, title)

    def cancel():
        _write_action_audit(
            title,
            "cancelado",
            project,
            reason="usuario cancelou escolha de destino",
        )
        window.destroy()

    ttk.Button(button_bar, text="Continuar", command=continue_with_selected).grid(
        row=0,
        column=0,
        padx=(0, 8),
    )
    ttk.Button(button_bar, text="Cancelar", command=cancel).grid(row=0, column=1)


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
    expected_destination_names = _expected_destination_names(project.folder_name, title)
    expected_destination_names_lower = {
        name.lower() for name in expected_destination_names
    }
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
        destination.name.lower() in expected_destination_names_lower,
        f"destino={destination.name}; esperado={' ou '.join(expected_destination_names)}",
    )
    add(
        "Destino dentro da raiz configurada",
        _is_relative_to_any(destination, target_root),
        _format_target_roots(target_root),
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
            f"Autcom a partir de {CLOUD_MIN_AUTCOM_MB} MB",
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
    theme.size_window(window, 860, 430)
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
        text=(
            "A copia ira enviar somente arquivos .zip para o destino. "
            "Apos a validacao, somente os ZIPs copiados serao removidos da origem."
        ),
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
        style="Primary.TButton",
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
    # Revalida: o fechamento pode ter sido iniciado enquanto a confirmacao estava aberta.
    if _block_if_closing_active(project, title):
        return
    _write_action_audit(title, "iniciado", project, destination)
    progress = Toplevel()
    progress.title(title)
    theme.size_window(progress, 560, 110)
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
        cleaned_items = 0
        cleanup_error = None
        remaining_items = []
        try:
            (
                copied_files,
                hash_summary,
                cleaned_items,
                cleanup_error,
                remaining_items,
            ) = _copy_project_files(project.path, destination)
            error = None
        except Exception as copy_error:
            error = copy_error
        result_queue.put(
            (
                error,
                copied_files,
                hash_summary,
                cleaned_items,
                cleanup_error,
                remaining_items,
            )
        )

    def poll():
        try:
            (
                error,
                copied_files,
                hash_summary,
                cleaned_items,
                cleanup_error,
                remaining_items,
            ) = result_queue.get_nowait()
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
            cleaned_items,
            cleanup_error,
            remaining_items,
        )

    threading.Thread(target=worker, daemon=True).start()
    progress.after(POLL_INTERVAL_MS, poll)


def _copy_project_files(source, destination):
    source = Path(source)
    destination = Path(destination)
    zip_errors = validate_zip_names(source)
    if zip_errors:
        raise OSError("Copia bloqueada por ZIP invalido: " + "; ".join(zip_errors[:3]))

    copied_files = 0
    copied_paths = []
    source_stamps = {}
    items = _iter_copyable_zip_files(source)
    if not items:
        raise OSError("Nenhum arquivo .zip encontrado para copiar.")

    transaction = destination / f".varredura-{uuid4().hex}"
    staging = transaction / "novos"
    backup = transaction / "anteriores"
    staging.mkdir(parents=True)
    promoted = []
    backed_up = []
    remove_transaction = False
    try:
        for item in items:
            relative_path = item.relative_to(source)
            target = staging / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            source_stamps[relative_path] = _file_stamp(item)
            shutil.copy2(item, target)
            copied_paths.append(relative_path)
            copied_files += 1

        hash_summary, mismatches = _verify_file_hashes(
            source, staging, copied_paths
        )
        if mismatches:
            preview = ", ".join(mismatches[:5])
            raise OSError(
                f"Verificacao pos-copia encontrou {len(mismatches)} "
                f"arquivo(s) divergente(s): {preview}"
            )

        backup.mkdir(parents=True)
        manifest = {
            "source": str(source),
            "destination": str(destination),
            "files": [str(path) for path in copied_paths],
            "backup_directory": str(backup),
        }
        (transaction / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        for relative_path in copied_paths:
            final_path = destination / relative_path
            staged_path = staging / relative_path
            if final_path.exists():
                backup_path = backup / relative_path
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                final_path.replace(backup_path)
                backed_up.append(relative_path)
            staged_path.replace(final_path)
            promoted.append(relative_path)
        final_hashes, final_mismatches = _verify_file_hashes(
            source, destination, copied_paths
        )
        if final_mismatches:
            raise OSError(
                "Verificacao no destino definitivo falhou: "
                + ", ".join(final_mismatches[:5])
            )
        hash_summary = final_hashes
        remove_transaction = True
    except OSError as original_error:
        rollback_errors = []
        for relative_path in reversed(promoted):
            final_path = destination / relative_path
            try:
                if final_path.exists():
                    final_path.unlink()
            except OSError as rollback_error:
                rollback_errors.append(f"remover {final_path}: {rollback_error}")
        for relative_path in reversed(backed_up):
            backup_path = backup / relative_path
            try:
                if backup_path.exists():
                    backup_path.replace(destination / relative_path)
            except OSError as rollback_error:
                rollback_errors.append(
                    f"restaurar {backup_path}: {rollback_error}"
                )
        if rollback_errors:
            try:
                (transaction / "ROLLBACK_INCOMPLETO.txt").write_text(
                    "\n".join(rollback_errors),
                    encoding="utf-8",
                )
            except OSError:
                pass
            raise OSError(
                f"{original_error}. Rollback incompleto; backups preservados em "
                f"{transaction}. Detalhes: {'; '.join(rollback_errors[:3])}"
            ) from original_error
        remove_transaction = True
        raise
    finally:
        if remove_transaction:
            shutil.rmtree(transaction, ignore_errors=True)

    cleanup_error = None
    try:
        cleaned_items = _cleanup_copied_zip_files(source, source_stamps)
    except CleanupError as error:
        cleaned_items = error.cleaned_items
        cleanup_error = error
    except OSError as error:
        cleaned_items = 0
        cleanup_error = CleanupError(str(error), cleaned_items)
    remaining_items = _list_remaining_source_items(source)
    return copied_files, hash_summary, cleaned_items, cleanup_error, remaining_items


def _file_stamp(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def _cleanup_copied_zip_files(source, source_stamps):
    """Remove da origem apenas os ZIPs copiados que nao mudaram desde a copia."""
    source = Path(source)
    cleaned_items = 0
    for relative_path, stamp in source_stamps.items():
        item = source / relative_path
        try:
            if _file_stamp(item) != stamp:
                continue
        except FileNotFoundError:
            continue
        try:
            _remove_source_item_with_retry(item)
        except OSError as error:
            raise CleanupError(
                f"Erro ao limpar origem {item}: {error}",
                cleaned_items,
            ) from error
        cleaned_items += 1
    return cleaned_items


def _list_remaining_source_items(source):
    try:
        return sorted(
            item.name
            for item in Path(source).iterdir()
            if item.name.lower() != "comandoscmd"
        )
    except OSError:
        return []


def _iter_copyable_zip_files(source):
    source = Path(source)
    return sorted(
        item
        for item in source.iterdir()
        if item.is_file() and item.suffix.lower() == ".zip"
    )


def _cleanup_source_after_copy(source):
    source = Path(source)
    cleaned_items = 0
    for item in source.iterdir():
        if item.name.lower() == "comandoscmd":
            continue
        try:
            _remove_source_item_with_retry(item)
        except OSError as error:
            raise CleanupError(
                f"Erro ao limpar origem {item}: {error}",
                cleaned_items,
            ) from error
        cleaned_items += 1
    return cleaned_items


def _remove_source_item_with_retry(item):
    last_error = None
    for attempt in range(CLEANUP_RETRY_ATTEMPTS):
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
            return
        except OSError as error:
            last_error = error
            if attempt < CLEANUP_RETRY_ATTEMPTS - 1:
                time.sleep(CLEANUP_RETRY_DELAY_SECONDS)
    raise last_error


def _collect_copy_mismatches(source, destination, relative_paths=None):
    mismatches = []
    if relative_paths is None:
        relative_paths = [
            path.relative_to(source)
            for path in _iter_copyable_zip_files(source)
        ]

    for relative_path in relative_paths:
        source_file = Path(source) / relative_path
        target_file = Path(destination) / relative_path
        try:
            if (
                not target_file.exists()
                or source_file.stat().st_size != target_file.stat().st_size
            ):
                mismatches.append(str(relative_path))
        except OSError:
            mismatches.append(str(relative_path))
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


def _verify_critical_file_hashes(source, destination, relative_paths=None):
    return _verify_file_hashes(source, destination, relative_paths)


def _verify_file_hashes(source, destination, relative_paths=None):
    hashes = {}
    mismatches = []
    if relative_paths is None:
        relative_paths = [
            path.relative_to(source)
            for path in _iter_copyable_zip_files(source)
        ]

    for relative_path in relative_paths:
        source_file = Path(source) / relative_path
        target_file = Path(destination) / relative_path
        relative_name = str(relative_path)
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


def _copy_text_to_clipboard(widget, text):
    try:
        widget.clipboard_clear()
        widget.clipboard_append(str(text))
        widget.update()
        return True
    except (AttributeError, TclError):
        return False


def _finish_copy(
    progress,
    project,
    destination,
    title,
    error,
    copied_files,
    hash_summary,
    cleaned_items,
    cleanup_error,
    remaining_items=(),
):
    clipboard_ok = False
    if error is None:
        clipboard_ok = _copy_text_to_clipboard(progress, destination)

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

    reason = (
        f"{copied_files} arquivo(s) copiado(s) e verificado(s); "
        f"origem_limpa={cleaned_items} ZIP(s) copiado(s); preservado=comandosCMD"
    )
    if remaining_items:
        reason = f"{reason}; mantidos_na_origem={', '.join(remaining_items)}"
    hash_text = _format_hash_summary(hash_summary)
    if hash_text:
        reason = f"{reason}; {hash_text}"

    if cleanup_error:
        _write_action_audit(
            title,
            "erro_limpeza",
            project,
            destination,
            reason=f"{reason}; limpeza_falhou={cleanup_error}",
        )
        messagebox.showwarning(
            title,
            "Copia concluida e verificada no destino.\n\n"
            f"Destino:\n{destination}\n\n"
            f"{_clipboard_message(clipboard_ok)}\n\n"
            "A limpeza da pasta de origem falhou e deve ser feita manualmente, "
            "preservando comandosCMD.\n\n"
            f"{cleanup_error}",
        )
        return

    _write_action_audit(
        title,
        "concluido",
        project,
        destination,
        reason=reason,
    )
    if remaining_items:
        messagebox.showwarning(
            title,
            "Copia concluida e verificada no destino.\n\n"
            "Destino:\n"
            f"{destination}\n\n"
            f"{_clipboard_message(clipboard_ok)}\n\n"
            "Os ZIPs copiados foram removidos da origem. Os itens abaixo nao "
            "foram copiados e foram mantidos na origem:\n"
            f"{_format_remaining_items(remaining_items)}\n\n"
            "Confira-os e use Limpar Pasta se quiser remove-los.",
        )
        return

    messagebox.showinfo(
        title,
        "Copia concluida e verificada no destino.\n\n"
        "Destino:\n"
        f"{destination}\n\n"
        f"{_clipboard_message(clipboard_ok)}\n\n"
        "A pasta de origem foi limpa, mantendo apenas comandosCMD.",
    )


def _format_remaining_items(remaining_items, limit=10):
    lines = [f"- {name}" for name in remaining_items[:limit]]
    if len(remaining_items) > limit:
        lines.append(f"... e mais {len(remaining_items) - limit} item(ns)")
    return "\n".join(lines)


def _clipboard_message(clipboard_ok):
    if clipboard_ok:
        return "Caminho copiado para a area de transferencia."
    return "Nao foi possivel copiar o caminho automaticamente."
