"""Trava por projeto compartilhada entre operadores do TS.

Cada operador roda seu proprio sistema; a fila de fechamento e os bloqueios em
memoria nao enxergam os outros. Esta trava e um arquivo na pasta do projeto,
criado de forma atomica (O_EXCL): se dois tentarem ao mesmo tempo, so um
consegue. Quem segura a trava renova o arquivo a cada HEARTBEAT_SECONDS; se o
sistema fechar sem soltar, a trava expira apos STALE_SECONDS sem renovacao.
"""

import json
import os
import socket
import time
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

LOCK_FILE_NAME = ".varredura.lock"
HEARTBEAT_SECONDS = 30
STALE_SECONDS = 180


class ProjectLockedError(Exception):
    def __init__(self, owner):
        self.owner = owner
        super().__init__(describe_owner(owner))


def lock_path(project_path):
    return Path(project_path) / LOCK_FILE_NAME


def _current_user():
    return os.environ.get("USERNAME") or os.environ.get("USER") or "desconhecido"


def describe_owner(owner):
    user = owner.get("user") or "outro operador"
    action = owner.get("action") or "uso"
    started = owner.get("started") or ""
    text = f"{action} em andamento por {user}"
    return f"{text} desde {started}" if started else text


def read_owner(project_path, now=None):
    """Dono atual da trava, ou None se nao houver trava valida (ausente ou expirada)."""
    path = lock_path(project_path)
    try:
        age = (now or time.time()) - path.stat().st_mtime
        owner = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # Arquivo sendo criado por outro neste instante: trata como em uso.
        return {"user": "outro operador", "action": "Uso", "started": "", "token": ""}
    if age > STALE_SECONDS:
        return None
    return owner if isinstance(owner, dict) else None


class ProjectLock:
    def __init__(self, project_path, action):
        self.path = lock_path(project_path)
        self.action = action
        self.token = uuid4().hex
        self._stop = Event()
        self._thread = None

    def acquire(self):
        owner = {
            "user": _current_user(),
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "action": self.action,
            "started": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "token": self.token,
        }
        for _attempt in range(2):
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                current = read_owner(self.path.parent)
                if current is not None:
                    raise ProjectLockedError(current)
                # Trava expirada (sistema fechado sem soltar): remove e tenta de novo.
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(handle, "w", encoding="utf-8") as lock_file:
                json.dump(owner, lock_file, ensure_ascii=False)
            self._start_heartbeat()
            return self
        raise ProjectLockedError(read_owner(self.path.parent) or {})

    def _start_heartbeat(self):
        def beat():
            while not self._stop.wait(HEARTBEAT_SECONDS):
                try:
                    os.utime(self.path)
                except OSError:
                    return

        # Thread importada no carregamento do modulo: a renovacao precisa ser
        # sempre uma thread de verdade (nunca executada "em linha").
        self._thread = Thread(target=beat, daemon=True)
        self._thread.start()

    def release(self):
        self._stop.set()
        try:
            owner = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        # So remove se a trava ainda for nossa (pode ter expirado e sido assumida).
        if isinstance(owner, dict) and owner.get("token") == self.token:
            try:
                self.path.unlink()
            except OSError:
                pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()
