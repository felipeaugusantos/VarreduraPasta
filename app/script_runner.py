"""Roda scripts pendentes no banco modelo e registra o resultado para todos.

Une `model_db` (execucao no MySQL) e `script_monitor` (controle compartilhado):
cada script que roda com sucesso vira evento `executado` na hora, entao uma
falha mais adiante nao faz ninguem repetir o que ja foi aplicado.
"""

from dataclasses import replace
from pathlib import Path

from app import model_db
from app.script_monitor import (
    EVENT_EXECUTED,
    _write_monitor_log,
    current_user,
    mark_scripts_done,
)
from app.settings import load_script_directory


def execute_scripts(
    names,
    username,
    password,
    directory=None,
    control_directory=None,
    connector=model_db.connect,
    settings=None,
):
    """Executa `names` (relativos a pasta de scripts) em ordem; para no primeiro erro."""
    directory = Path(directory or load_script_directory())
    operator = current_user()

    warnings = {}

    def on_done(result):
        if not result.ok:
            first_line = result.message.splitlines()[0] if result.message else ""
            _write_monitor_log("erro_execucao", directory, f"{result.name}: {first_line}")
            return
        _write_monitor_log("executado", directory, f"{result.name} ({result.statements} comando(s)) por {operator}")
        try:
            # na hora: se um script seguinte falhar, este nao sera repetido por ninguem
            mark_scripts_done(
                [result.name],
                control_directory=control_directory,
                directory=directory,
                event=EVENT_EXECUTED,
                detail=f"executado no banco modelo por {operator}",
            )
        except OSError as error:
            warnings[result.name] = str(error)

    results = model_db.run_scripts(
        directory, names, username, password, on_done=on_done, connector=connector, settings=settings
    )
    return [
        replace(
            result,
            message=f"{result.message} Mas nao foi possivel registrar no controle compartilhado: {warnings[result.name]}",
        )
        if result.name in warnings
        else result
        for result in results
    ]
