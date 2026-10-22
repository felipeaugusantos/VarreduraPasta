"""Monitoramento dos scripts do banco modelo com controle compartilhado.

Varios operadores usam o sistema no TS, cada um com seu usuario do Windows
(e seu %LOCALAPPDATA%). Por isso o que ja foi feito fica num registro
compartilhado (SCRIPT_LEDGER_FILE_NAME na pasta de controle): cada evento e
uma linha acrescentada, nunca reescrita, entao gravacoes simultaneas nao se
apagam. Um script e pendente enquanto nao tiver evento de "base", "feito" ou
"executado" no registro.
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.config import SCRIPT_LEDGER_FILE_NAME, SCRIPT_MONITOR_DIRECTORY
from app.logging_utils import write_log
from app.settings import (
    load_script_control_directory,
    load_script_monitor_state,
    save_script_monitor_state,
)


EVENT_START = "inicio"
EVENT_BASELINE = "base"
EVENT_DONE = "feito"
EVENT_EXECUTED = "executado"
DONE_EVENTS = {EVENT_BASELINE, EVENT_DONE, EVENT_EXECUTED}
HISTORY_LIMIT = 15
APPEND_RETRY_ATTEMPTS = 5
APPEND_RETRY_DELAY_SECONDS = 0.3


@dataclass(frozen=True)
class LedgerEvent:
    timestamp: str
    user: str
    script: str
    event: str
    detail: str = ""


@dataclass(frozen=True)
class PendingScript:
    name: str
    modified: str


@dataclass
class ScriptMonitorResult:
    directory: Path
    control_directory: Path
    total_files: int = 0
    pending: list = field(default_factory=list)
    new_pending: list = field(default_factory=list)
    history: list = field(default_factory=list)
    error: str = ""

    @property
    def pending_names(self):
        return [script.name for script in self.pending]


def current_user():
    return os.environ.get("USERNAME") or os.environ.get("USER") or "desconhecido"


def ledger_path(control_directory=None):
    return Path(control_directory or load_script_control_directory()) / SCRIPT_LEDGER_FILE_NAME


def _clean(value):
    return str(value).replace("|", "/").replace("\r", " ").replace("\n", " ").strip()


def format_event(event):
    return " | ".join(
        _clean(value)
        for value in (event.timestamp, event.user, event.script, event.event, event.detail)
    )


def parse_event(line):
    parts = [part.strip() for part in line.rstrip("\r\n").split(" | ")]
    if len(parts) < 4 or not parts[2]:
        return None
    detail = " | ".join(parts[4:]) if len(parts) > 4 else ""
    return LedgerEvent(parts[0], parts[1], parts[2], parts[3], detail)


def read_ledger(control_directory=None):
    path = ledger_path(control_directory)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    return [event for event in map(parse_event, lines) if event is not None]


def append_events(events, control_directory=None):
    """Acrescenta eventos ao registro compartilhado (cria pasta/arquivo se preciso)."""
    if not events:
        return
    path = ledger_path(control_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(format_event(event) + "\n" for event in events)
    last_error = None
    for attempt in range(APPEND_RETRY_ATTEMPTS):
        try:
            with path.open("a", encoding="utf-8") as ledger:
                ledger.write(text)
            return
        except PermissionError as error:
            # Outro operador gravando no mesmo instante: tenta de novo.
            last_error = error
            if attempt < APPEND_RETRY_ATTEMPTS - 1:
                time.sleep(APPEND_RETRY_DELAY_SECONDS)
    raise last_error


def done_scripts(events):
    return {event.script.lower() for event in events if event.event in DONE_EVENTS}


def _relative_file_list(directory):
    directory = Path(directory)
    files = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative_path = path.relative_to(directory)
        except ValueError:
            relative_path = Path(path.name)
        files.append(str(relative_path))
    return sorted(files, key=str.lower)


def _modified_text(directory, relative_name):
    try:
        modified = (Path(directory) / relative_name).stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(modified).strftime("%d/%m/%Y %H:%M")


def _now_text(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _baseline_events(current_files, now=None):
    """Primeiro uso do registro: o que ja existia vira base, exceto o que este
    operador ainda tinha como pendente na versao anterior (local)."""
    state = load_script_monitor_state()
    still_pending = set()
    if state.get("initialized"):
        still_pending = {name.lower() for name in state.get("pending_files", [])}
    timestamp = _now_text(now)
    user = current_user()
    # O marcador garante que o registro nunca fica vazio depois do primeiro uso,
    # mesmo com a pasta de scripts vazia (senao o proximo script viraria base).
    marker = LedgerEvent(timestamp, user, "-", EVENT_START, "inicio do controle compartilhado")
    return [marker] + [
        LedgerEvent(timestamp, user, name, EVENT_BASELINE, "existia antes do controle compartilhado")
        for name in current_files
        if name.lower() not in still_pending
    ]


def check_scripts(directory=None, control_directory=None, now=None):
    """Lista os scripts pendentes e os que surgiram desde o ultimo aviso deste operador."""
    directory = Path(directory or SCRIPT_MONITOR_DIRECTORY)
    control_directory = Path(control_directory or load_script_control_directory())
    result = ScriptMonitorResult(directory=directory, control_directory=control_directory)

    if not directory.exists():
        result.error = f"Pasta de scripts nao encontrada: {directory}"
        _write_monitor_log("erro", directory, result.error)
        return result

    try:
        current_files = _relative_file_list(directory)
        events = read_ledger(control_directory)
        if not events:
            baseline = _baseline_events(current_files, now)
            append_events(baseline, control_directory)
            events = baseline
    except OSError as error:
        result.error = f"Erro ao verificar scripts: {error}"
        _write_monitor_log("erro", directory, result.error)
        return result

    done = done_scripts(events)
    result.total_files = len(current_files)
    result.pending = [
        PendingScript(name, _modified_text(directory, name))
        for name in current_files
        if name.lower() not in done
    ]
    meaningful = [event for event in events if event.event not in (EVENT_START, EVENT_BASELINE)]
    result.history = list(reversed(meaningful[-HISTORY_LIMIT:]))

    state = load_script_monitor_state()
    already_notified = {name.lower() for name in state.get("pending_files", [])}
    result.new_pending = [
        name for name in result.pending_names if name.lower() not in already_notified
    ]
    # O estado local guarda so o que ja foi avisado a este operador.
    save_script_monitor_state(_now_text(now)[:10], [], result.pending_names)

    if result.new_pending:
        _write_monitor_log(
            "novo_script",
            directory,
            f"{len(result.new_pending)} script(s) novo(s): {', '.join(result.new_pending[:10])}",
        )
    return result


def mark_scripts_done(scripts, control_directory=None, now=None):
    """Registra os scripts como feitos. Devolve (marcados, ja_feitos_por_outro)."""
    control_directory = Path(control_directory or load_script_control_directory())
    already_done = done_scripts(read_ledger(control_directory))
    to_mark = [name for name in scripts if name.lower() not in already_done]
    skipped = [name for name in scripts if name.lower() in already_done]

    timestamp = _now_text(now)
    user = current_user()
    append_events(
        [LedgerEvent(timestamp, user, name, EVENT_DONE, "marcado manualmente") for name in to_mark],
        control_directory,
    )
    if to_mark:
        _write_monitor_log(
            "marcado_feito",
            control_directory,
            f"{len(to_mark)} script(s): {', '.join(to_mark)}",
        )
    return to_mark, skipped


def _write_monitor_log(result, directory, reason):
    write_log(
        "Monitoramento Scripts "
        f"{result} | usuario={current_user()} | projeto= | tipo= | origem={directory} | "
        f"destino= | bat= | fileversion= | productversion= | autcom_mb= | "
        f"motivo={reason}"
    )
