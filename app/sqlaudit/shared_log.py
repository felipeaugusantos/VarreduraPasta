"""Registro compartilhado da auditoria na pasta de controle (varios operadores no TS).

Cada operador tem seu proprio %LOCALAPPDATA%, entao o que vale para todos fica num
arquivo so de acrescimo (`auditoria_estrutura_sql.log`), nunca reescrito: uma linha
por evento, `data | usuario | versao | evento | detalhe`. E um arquivo PROPRIO, para
nao misturar com o controle de scripts feitos (`scripts_banco_modelo.log`).

Eventos:
- `auditoria`: resumo de uma analise concluida (contagens por estado; sem SQL, sem senha);
- `fork`: o operador declara que a versao nasceu da base ate `SCRIPT_<n>`. E a unica
  fonte certa do ponto de fork (os dumps nao sao guardados);
- `conhecida` / `desmarcada`: o operador aceita (ou deixa de aceitar) um achado
  DIVERGENTE/AUSENTE ja investigado. A chave e versao + objeto + assinatura do achado
  (estado e diferencas): se o banco mudar, a assinatura muda e o achado volta a aparecer.
"""

import hashlib

import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.settings import load_script_control_directory

FILE_NAME = "auditoria_estrutura_sql.log"
EVENT_AUDIT = "auditoria"
EVENT_FORK = "fork"
EVENT_KNOWN = "conhecida"
EVENT_UNKNOWN = "desmarcada"
MAX_REASON_CHARS = 200
SEPARATOR = " | "
APPEND_RETRY_ATTEMPTS = 5
APPEND_RETRY_DELAY_SECONDS = 0.3
LOCK_TIMEOUT_SECONDS = 8.0
LOCK_POLL_SECONDS = 0.02
# Byte que serve de trava, bem alem do fim real do arquivo: no Windows o bloqueio de
# intervalo e obrigatorio e travaria a leitura de quem estivesse lendo o inicio.
LOCK_OFFSET = 1 << 40
_WINDOWS = sys.platform == "win32"


class LockTimeout(OSError):
    """Outro operador manteve o arquivo travado por tempo demais."""


@dataclass(frozen=True)
class SharedEvent:
    timestamp: str
    user: str
    version: str
    event: str
    detail: str = ""


@dataclass(frozen=True)
class SharedFork:
    version: str
    base: str
    fork: int | None
    database: str
    user: str
    timestamp: str


def _clean(value):
    return str(value).replace("|", "/").replace("\r", " ").replace("\n", " ").strip()


def format_line(event):
    return SEPARATOR.join(_clean(v) for v in (event.timestamp, event.user, event.version, event.event, event.detail))


def parse_line(line):
    parts = [part.strip() for part in line.rstrip("\r\n").split(SEPARATOR)]
    if len(parts) < 4 or not parts[2]:
        return None
    return SharedEvent(parts[0], parts[1], parts[2], parts[3], SEPARATOR.join(parts[4:]))


def log_path(control_directory=None):
    return Path(control_directory or load_script_control_directory()) / FILE_NAME


def now_text(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _lock(handle):
    """Trava exclusiva (entre threads, processos e maquinas via SMB); espera ate LOCK_TIMEOUT_SECONDS.

    `open("a")` NAO basta: no Windows o fim do arquivo e calculado antes da escrita, entao
    dois gravadores simultaneos escrevem na mesma posicao e uma linha sobrescreve a outra.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            if _WINDOWS:
                import msvcrt

                handle.seek(LOCK_OFFSET)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as error:
            if time.monotonic() >= deadline:
                raise LockTimeout(f"arquivo em uso por outro operador ({error})") from error
            time.sleep(LOCK_POLL_SECONDS)


def _unlock(handle):
    try:
        if _WINDOWS:
            import msvcrt

            handle.seek(LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass  # fechar o arquivo libera a trava de qualquer forma


def _append_locked(path, data):
    with path.open("ab") as handle:
        _lock(handle)
        try:
            handle.seek(0, os.SEEK_END)
            handle.write(data)
            handle.flush()
        finally:
            _unlock(handle)


def append_events(events, control_directory=None):
    """Acrescenta eventos sob trava (cria pasta/arquivo se preciso). Levanta OSError se a rede falhar."""
    if not events:
        return
    path = log_path(control_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(format_line(event) + "\n" for event in events).encode("utf-8")
    last_error = None
    for attempt in range(APPEND_RETRY_ATTEMPTS):
        try:
            _append_locked(path, data)
            return
        except LockTimeout:
            raise
        except PermissionError as error:  # abertura recusada (antivirus, outro processo)
            last_error = error
            if attempt < APPEND_RETRY_ATTEMPTS - 1:
                time.sleep(APPEND_RETRY_DELAY_SECONDS)
    raise last_error


def read_events(control_directory=None):
    path = log_path(control_directory)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    return [event for event in map(parse_line, lines) if event is not None]


def _fields(detail):
    fields = {}
    for part in str(detail).split("; "):
        key, separator, value = part.partition("=")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def fork_event(user, version, base, fork, database="", now=None):
    detail = f"base={base}; fork={'' if fork is None else fork}; banco={database}"
    return SharedEvent(now_text(now), user, version, EVENT_FORK, detail)


def audit_event(user, audit, now=None):
    """Resumo de uma versao auditada: contagens por estado, sem objetos nem SQL."""
    if audit.error:
        detail = f"banco={audit.database}; erro={audit.error[:200]}"
    else:
        counts = audit.comparison.object_counts() if audit.comparison else {}
        summary = ",".join(f"{status.lower()}:{count}" for status, count in sorted(counts.items()))
        detail = f"banco={audit.database}; mysql={audit.server_version}; scripts={audit.scripts_total}; objetos={summary or '-'}"
    return SharedEvent(now_text(now), user, audit.version, EVENT_AUDIT, detail)


@dataclass(frozen=True)
class SharedKnown:
    key: tuple
    state: str
    reason: str
    user: str
    timestamp: str


def signature(state, differences):
    """Identifica o achado: se o estado ou as diferencas mudarem, deixa de ser o mesmo achado."""
    text = f"{state}|{differences}".encode("utf-8")
    return hashlib.sha256(text).hexdigest()[:12]


def known_key(version, kind, table, column, sig):
    return (str(version), str(kind), str(table).lower(), str(column or "").lower(), str(sig))


def row_key(row):
    """Chave de uma linha de `report.object_rows` (ou do historico)."""
    return known_key(
        row["versao"], row["tipo"], row["tabela"], row["coluna"], signature(row["estado"], row["diferencas"])
    )


def known_event(user, row, reason="", marked=True, now=None):
    reason = " ".join(str(reason).replace("; ", ", ").split())[:MAX_REASON_CHARS]
    detail = (
        f"tipo={row['tipo']}; tabela={row['tabela']}; coluna={row['coluna'] or ''}; "
        f"assinatura={signature(row['estado'], row['diferencas'])}; estado={row['estado']}; motivo={reason}"
    )
    return SharedEvent(now_text(now), user, row["versao"], EVENT_KNOWN if marked else EVENT_UNKNOWN, detail)


def latest_known(events):
    """Achados aceitos: o ultimo evento de cada chave vence (`desmarcada` remove)."""
    result = {}
    for event in events:
        if event.event not in (EVENT_KNOWN, EVENT_UNKNOWN):
            continue
        text = event.detail
        reason = ""
        head, separator, tail = text.partition("; motivo=")
        if separator:
            reason = tail.strip()
        fields = _fields(head)
        if not fields.get("assinatura"):
            continue
        key = known_key(event.version, fields.get("tipo", ""), fields.get("tabela", ""), fields.get("coluna", ""), fields["assinatura"])
        if event.event == EVENT_UNKNOWN:
            result.pop(key, None)
        else:
            result[key] = SharedKnown(key, fields.get("estado", ""), reason, event.user, event.timestamp)
    return result


def latest_forks(events):
    """Ultimo `fork` declarado de cada versao (o mais recente vence)."""
    result = {}
    for event in events:
        if event.event != EVENT_FORK:
            continue
        fields = _fields(event.detail)
        fork_text = fields.get("fork", "")
        result[event.version] = SharedFork(
            version=event.version,
            base=fields.get("base", ""),
            fork=int(fork_text) if fork_text.isdigit() else None,
            database=fields.get("banco", ""),
            user=event.user,
            timestamp=event.timestamp,
        )
    return result
