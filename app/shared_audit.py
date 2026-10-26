"""Auditoria compartilhada entre os operadores do Terminal Server.

Cada operador tem seu proprio %LOCALAPPDATA% (e portanto seu proprio log).
Alem do log local, toda linha de Auditoria tambem e acrescentada a um arquivo
mensal na pasta de controle compartilhada (auditoria-AAAA-MM.log), para dar a
visao de quem fez o que em todo o sistema.

Gravar numa pasta de rede pode demorar ou falhar; por isso a gravacao fica numa
thread propria, com fila limitada: a interface nunca espera pela rede e, se a
pasta estiver indisponivel, as linhas ficam na fila e sao gravadas depois.
"""

import atexit
import os
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

ENV_DISABLE = "VARREDURA_SEM_AUDITORIA_COMPARTILHADA"
FILE_PREFIX = "auditoria-"
MAX_PENDING = 2000
RETRY_SECONDS = 30
EXIT_FLUSH_SECONDS = 3


def shared_log_name(now):
    return f"{FILE_PREFIX}{now:%Y-%m}.log"


def list_shared_log_files(directory):
    """Logs compartilhados da pasta de controle, do mais antigo ao mais recente."""
    return sorted(Path(directory).glob(f"{FILE_PREFIX}*.log"))


class SharedAuditWriter:
    def __init__(self, directory_provider, retry_seconds=RETRY_SECONDS):
        self._provider = directory_provider
        self._retry_seconds = retry_seconds
        self._pending = deque(maxlen=MAX_PENDING)
        self._condition = threading.Condition()
        self._writing = False
        self._thread = None
        self._stop = threading.Event()

    def enqueue(self, now, line):
        with self._condition:
            self._pending.append((now, line))
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._worker, name="auditoria-compartilhada", daemon=True
                )
                self._thread.start()
            self._condition.notify()

    def flush(self, timeout):
        """Espera a fila esvaziar (ate `timeout` s). Retorna True se esvaziou."""
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._pending and not self._writing, timeout
            )

    def stop(self):
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def _worker(self):
        while not self._stop.is_set():
            with self._condition:
                while not self._pending and not self._stop.is_set():
                    self._condition.wait()
                if self._stop.is_set():
                    return
                batch = list(self._pending)
                self._pending.clear()
                self._writing = True
            failed = self._write_batch(batch)
            with self._condition:
                if failed:
                    # Volta para o inicio da fila, mantendo a ordem original.
                    self._pending.extendleft(reversed(failed))
                self._writing = False
                self._condition.notify_all()
            if failed:
                self._stop.wait(self._retry_seconds)

    def _write_batch(self, batch):
        """Grava o lote; devolve as linhas que nao puderam ser gravadas."""
        try:
            directory = Path(self._provider())
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return batch
        by_file = {}
        for item in batch:
            by_file.setdefault(directory / shared_log_name(item[0]), []).append(item)
        failed = []
        for path, items in by_file.items():
            try:
                with path.open("a", encoding="utf-8") as log:
                    log.write("".join(line for _now, line in items))
            except OSError:
                failed.extend(items)
        failed.sort(key=lambda item: batch.index(item))
        return failed


def _control_directory():
    from app.settings import load_script_control_directory

    return load_script_control_directory()


_default_writer = SharedAuditWriter(_control_directory)
_exit_hook_registered = False


def enqueue(now, line):
    """Envia a linha de Auditoria para a pasta compartilhada (em segundo plano)."""
    global _exit_hook_registered
    if os.environ.get(ENV_DISABLE):
        return
    if not _exit_hook_registered:
        _exit_hook_registered = True
        atexit.register(_default_writer.flush, EXIT_FLUSH_SECONDS)
    _default_writer.enqueue(now, line)


def timestamp_now():
    return datetime.now()
