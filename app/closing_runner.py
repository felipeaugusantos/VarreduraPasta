import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
# Os processos filhos do BAT (compactadores etc.) herdam a prioridade, mantendo o PC responsivo.
BELOW_NORMAL_PRIORITY_CLASS = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
SW_HIDE = 0

# Arquivos que o _FechamentoArquivos.bat compacta e apaga; o fechamento termina
# quando nao resta nenhum na pasta do projeto.
PENDING_SUFFIXES = {".exe", ".dll", ".bpl"}
PROGRESS_TICK_SECONDS = 1

STATUS_QUEUED = "Na fila"
STATUS_RUNNING = "Em andamento"
STATUS_DONE = "Concluído"
STATUS_ERROR = "Erro"
ACTIVE_STATUSES = {STATUS_QUEUED, STATUS_RUNNING}


class ClosingTimeoutError(OSError):
    pass


@dataclass
class ClosingJob:
    key: str
    project: object
    title: str
    bat_path: Path
    status: str = STATUS_QUEUED
    started_at: float = None
    finished_at: float = None
    return_code: int = None
    message: str = ""
    log_path: Path = None
    percent: int = None
    zip_record: object = None


def project_key(project):
    return str(Path(project.path)).lower()


def format_elapsed(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def pending_file_sizes(folder):
    """Tamanho dos .exe/.dll/.bpl ainda nao compactados na raiz da pasta."""
    sizes = {}
    for item in Path(folder).iterdir():
        if item.suffix.lower() not in PENDING_SUFFIXES:
            continue
        try:
            if item.is_file():
                sizes[item.name.lower()] = item.stat().st_size
        except OSError:
            continue
    return sizes


def zipped_percent(initial_sizes, remaining_names):
    """Percentual ja compactado, ponderado pelo tamanho inicial de cada arquivo.

    Fica em no maximo 99 enquanto o BAT roda; 100 so vale com o fechamento
    concluido.
    """
    total = sum(initial_sizes.values())
    if total <= 0:
        return 0
    remaining = sum(
        size for name, size in initial_sizes.items() if name in remaining_names
    )
    return min(99, int((total - remaining) * 100 / total))


def run_closing_bat(bat_path, log_path, timeout, on_tick=None):
    """Executa o BAT em console oculto, gravando a saida no log. Retorna o codigo.

    Enquanto espera, chama `on_tick()` a cada segundo (usado para o percentual).

    O BAT precisa de um console proprio: comandos como `timeout` falham na hora
    quando a entrada nao e um console, transformando lacos de espera em lacos
    ocupados. Por isso o redirecionamento para o log e feito pelo proprio cmd,
    e nao por pipes do Python.
    """
    bat_path = Path(bat_path)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = SW_HIDE
    process = subprocess.Popen(
        f'cmd.exe /s /c ""{bat_path}" > "{log_path}" 2>&1"',
        cwd=bat_path.parent,
        creationflags=CREATE_NEW_CONSOLE | BELOW_NORMAL_PRIORITY_CLASS,
        startupinfo=startupinfo,
    )
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_process_tree(process)
            raise ClosingTimeoutError(
                f"BAT excedeu {format_elapsed(timeout)} e foi encerrado."
            )
        try:
            return process.wait(timeout=min(PROGRESS_TICK_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            if on_tick is not None:
                try:
                    on_tick()
                except Exception:
                    pass


def _kill_process_tree(process):
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            timeout=30,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


class ClosingManager:
    """Fila de fechamentos executados em segundo plano, sem acesso ao Tkinter.

    `execute(job)` roda na thread do fechamento e deve retornar o codigo de
    saida do BAT. A interface consulta `progress_text` periodicamente.
    """

    def __init__(self, execute, max_concurrent=2, clock=time.monotonic, start_thread=None):
        self._execute = execute
        self._max_concurrent = max_concurrent
        self._clock = clock
        self._start_thread = start_thread or self._default_start_thread
        self._lock = threading.Lock()
        self._jobs = {}
        self._pending = deque()
        self._running = 0
        self.finished_count = 0

    @staticmethod
    def _default_start_thread(target, job):
        threading.Thread(target=target, args=(job,), daemon=True).start()

    def is_active(self, project):
        with self._lock:
            job = self._jobs.get(project_key(project))
            return job is not None and job.status in ACTIVE_STATUSES

    def active_count(self):
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.status in ACTIVE_STATUSES)

    def running_and_queued(self):
        with self._lock:
            running = sum(1 for job in self._jobs.values() if job.status == STATUS_RUNNING)
            queued = sum(1 for job in self._jobs.values() if job.status == STATUS_QUEUED)
        return running, queued

    def enqueue(self, project, title, bat_path):
        key = project_key(project)
        with self._lock:
            current = self._jobs.get(key)
            if current is not None and current.status in ACTIVE_STATUSES:
                return False
            job = ClosingJob(key=key, project=project, title=title, bat_path=Path(bat_path))
            self._jobs[key] = job
            self._pending.append(job)
        self._start_next()
        return True

    def job_for(self, project):
        with self._lock:
            return self._jobs.get(project_key(project))

    def progress_text(self, project):
        with self._lock:
            job = self._jobs.get(project_key(project))
            if job is None:
                return ""
            if job.status == STATUS_QUEUED:
                return STATUS_QUEUED
            if job.status == STATUS_RUNNING:
                percent = job.percent if job.percent is not None else 0
                return f"{STATUS_RUNNING} {percent}%"
            if job.status == STATUS_DONE:
                elapsed = format_elapsed(job.finished_at - job.started_at)
                return f"{STATUS_DONE} {elapsed}"
            if job.percent is not None:
                return f"{STATUS_ERROR} em {job.percent}%"
            return STATUS_ERROR

    def _start_next(self):
        to_start = []
        with self._lock:
            while self._pending and self._running < self._max_concurrent:
                job = self._pending.popleft()
                job.status = STATUS_RUNNING
                job.started_at = self._clock()
                self._running += 1
                to_start.append(job)
        for job in to_start:
            self._start_thread(self._run_job, job)

    def _run_job(self, job):
        return_code = None
        message = ""
        try:
            return_code = self._execute(job)
        except Exception as error:
            message = str(error)
        with self._lock:
            job.finished_at = self._clock()
            job.return_code = return_code
            job.message = message
            job.status = STATUS_DONE if return_code == 0 and not message else STATUS_ERROR
            self._running -= 1
            self.finished_count += 1
        self._start_next()
