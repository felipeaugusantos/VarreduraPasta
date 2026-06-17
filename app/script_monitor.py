import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.config import SCRIPT_MONITOR_DIRECTORY
from app.logging_utils import write_log
from app.settings import load_script_monitor_state, save_script_monitor_state


@dataclass
class ScriptMonitorResult:
    directory: Path
    checked: bool
    first_run: bool
    total_files: int
    new_files: list[str]
    error: str
    last_check_date: str


def _today_text(today=None):
    return (today or date.today()).isoformat()


def _relative_file_list(directory):
    directory = Path(directory)
    files = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative_path = path.relative_to(directory)
        except ValueError:
            relative_path = path.name
        files.append(str(relative_path))
    return sorted(files, key=str.lower)


def should_check_today(today=None):
    state = load_script_monitor_state()
    return state.get("last_check_date") != _today_text(today)


def check_scripts(force=False, today=None, directory=SCRIPT_MONITOR_DIRECTORY):
    today_text = _today_text(today)
    state = load_script_monitor_state()
    if not force and state.get("last_check_date") == today_text:
        return ScriptMonitorResult(
            directory=Path(directory),
            checked=False,
            first_run=False,
            total_files=len(state.get("known_files", [])),
            new_files=[],
            error="",
            last_check_date=state.get("last_check_date", ""),
        )

    directory = Path(directory)
    if not directory.exists():
        error = f"Pasta de scripts nao encontrada: {directory}"
        _write_monitor_log("erro", directory, error)
        return ScriptMonitorResult(
            directory=directory,
            checked=True,
            first_run=False,
            total_files=0,
            new_files=[],
            error=error,
            last_check_date=state.get("last_check_date", ""),
        )

    try:
        current_files = _relative_file_list(directory)
    except OSError as error:
        error_text = f"Erro ao verificar scripts: {error}"
        _write_monitor_log("erro", directory, error_text)
        return ScriptMonitorResult(
            directory=directory,
            checked=True,
            first_run=False,
            total_files=0,
            new_files=[],
            error=error_text,
            last_check_date=state.get("last_check_date", ""),
        )

    known_files = set(state.get("known_files", []))
    first_run = not known_files
    new_files = [] if first_run else [
        file_name for file_name in current_files if file_name not in known_files
    ]
    save_script_monitor_state(today_text, current_files)

    if new_files:
        _write_monitor_log(
            "novo_script",
            directory,
            f"{len(new_files)} script(s) novo(s): {', '.join(new_files[:10])}",
        )
    else:
        reason = "baseline gravado" if first_run else "nenhum script novo"
        _write_monitor_log("verificado", directory, reason)

    return ScriptMonitorResult(
        directory=directory,
        checked=True,
        first_run=first_run,
        total_files=len(current_files),
        new_files=new_files,
        error="",
        last_check_date=today_text,
    )


def _write_monitor_log(result, directory, reason):
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "desconhecido"
    write_log(
        "Monitoramento Scripts "
        f"{result} | usuario={user} | projeto= | tipo= | origem={directory} | "
        f"destino= | bat= | fileversion= | productversion= | autcom_mb= | "
        f"motivo={reason}"
    )
